"""
EPG Aggregator - Downloads and combines EPG sources into single XMLTV
"""

import requests
import gzip
import html
import re
import time
import threading
import concurrent.futures
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class EPGAggregator:
    """Downloads and combines external EPG sources into single XMLTV"""

    def __init__(self):
        self.cache        = None  # combined XMLTV, UTF-8 encoded
        self.cache_gz     = None
        self.cache_expiry = 0
        self.cache_lock   = threading.Lock()
        self._build_lock  = threading.Lock()
        self.cache_duration = 3600  # 1 hour

        # Per-provider cache: provider_name → xml bytes
        self._provider_cache:  dict = {}
        self._provider_expiry: dict = {}

        # Set by the aggregator so the guide can be filtered to the channel list
        self._channel_manager = None

        # Background refresh state
        self._refreshing          = False
        self._provider_refreshing: set = set()
        self._bg_started          = False

        self.epg_sources = {
            'plex':     'https://i.mjh.nz/Plex/all.xml',
            'pluto':    'https://i.mjh.nz/PlutoTV/all.xml',
            'samsung':  'https://i.mjh.nz/SamsungTVPlus/all.xml',
            'lg':       'https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz',
            'tubi':     'https://github.com/BuddyChewChew/tubi-scraper/raw/refs/heads/main/tubi_epg.xml',
            'xumo':     'https://raw.githubusercontent.com/BuddyChewChew/xumo-playlist-generator/main/playlists/xumo_epg.xml.gz',
            'roku':     'https://github.com/matthuisman/i.mjh.nz/raw/master/Roku/all.xml',
            'localnow': 'https://raw.githubusercontent.com/BuddyChewChew/localnow-playlist-generator/refs/heads/main/epg.xml',
        }

        self.fetch_workers = len(self.epg_sources)  # concurrent source downloads

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
        })

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _fetch_source(self, name: str, url: str) -> str:
        """Fetch a single EPG source URL, decompressing gzip if needed."""
        try:
            response = self.session.get(url, timeout=(10, 120))
            response.raise_for_status()

            content = response.content

            if url.endswith('.gz'):
                try:
                    content = gzip.decompress(content)
                except Exception:
                    pass

            xml_text = content.decode('utf-8')
            logger.info(f"Fetched EPG: {name} ({len(xml_text)} bytes)")
            return xml_text

        except Exception as e:
            logger.error(f"Failed to fetch EPG {name}: {e}")
            return ""

    def _extract_content(self, xml_text: str, tvg_ids: Optional[set] = None) -> tuple:
        """
        Extract channel and programme blocks from XMLTV as (id, raw block) pairs.

        When tvg_ids is given, blocks whose id / channel isn't in it are dropped.
        """
        channels   = []
        programmes = []

        try:
            channel_pattern   = re.compile(r'<channel\s[^>]*>.*?</channel>',   re.DOTALL)
            programme_pattern = re.compile(r'<programme\s[^>]*>.*?</programme>', re.DOTALL)
            channel_id_re     = re.compile(r'id="([^"]+)"')
            programme_ch_re   = re.compile(r'<programme(?=\s)[^>]*?\schannel="([^"]*)"')

            for match in channel_pattern.finditer(xml_text):
                block    = match.group(0)
                id_match = channel_id_re.search(block)
                ch_id    = html.unescape(id_match.group(1)) if id_match else ''
                if tvg_ids is not None and ch_id not in tvg_ids:
                    continue
                channels.append((ch_id, block))

            for match in programme_pattern.finditer(xml_text):
                block    = match.group(0)
                id_match = programme_ch_re.match(block)
                pr_id    = html.unescape(id_match.group(1)) if id_match else ''
                if tvg_ids is not None and pr_id not in tvg_ids:
                    continue
                programmes.append((pr_id, block))

        except Exception as e:
            logger.error(f"Error extracting EPG content: {e}")

        return channels, programmes

    def _build_xml(self, channels: list, programmes: list) -> bytes:
        """Wrap channel and programme blocks in a valid XMLTV envelope (UTF-8 bytes)."""
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
            f'<tv generator-info-name="KPTV-FAST" generated-ts="{datetime.now(timezone.utc).isoformat()}">',
        ]
        parts.extend(channels)
        parts.extend(programmes)
        parts.append('</tv>')
        return '\n'.join(parts).encode('utf-8')

    def _get_tvg_ids(self, provider_name: str = '') -> Optional[set]:
        """
        Return the tvg-ids in the channel list (optionally for one provider).

        Returns None when no channel manager is attached (no filtering).
        Blocks on a channel fetch only if no channels are cached yet.
        """
        if self._channel_manager is None:
            return None
        return self._channel_manager.get_tvg_ids(provider_name)

    # ── Public interface ──────────────────────────────────────────────────────

    def set_channel_manager(self, channel_manager) -> None:
        """Attach the channel manager so the guide is filtered to the channel list."""
        self._channel_manager = channel_manager

    def get_combined_epg(self, force_refresh: bool = False) -> bytes:
        """
        Return combined XMLTV (UTF-8 bytes) from all sources.

        A valid cache is returned as-is. An expired cache is still returned
        immediately while a background rebuild runs. Only an empty cache blocks.
        """
        with self.cache_lock:
            cached = self.cache
            valid  = bool(cached) and time.time() < self.cache_expiry

        if not force_refresh and valid:
            logger.debug("Returning cached EPG")
            return cached

        # Expired but present — serve stale and rebuild behind the scenes
        if not force_refresh and cached:
            self._refresh_combined_in_background()
            return cached

        return self._build_combined_epg(force_refresh)

    def _build_combined_epg(self, force_refresh: bool = False) -> bytes:
        """Build the combined XMLTV (UTF-8 bytes), or return the cache if another caller just did."""
        with self.cache_lock:
            if not force_refresh and self.cache and time.time() < self.cache_expiry:
                logger.debug("Returning cached EPG")
                return self.cache

        with self._build_lock:

            # Another caller may have finished a build while we waited
            with self.cache_lock:
                if not force_refresh and self.cache and time.time() < self.cache_expiry:
                    return self.cache

            # Only keep what is actually in the playlist
            tvg_ids = self._get_tvg_ids()
            if tvg_ids is not None and not tvg_ids:
                logger.warning("No channels available — skipping EPG build")
                with self.cache_lock:
                    stale = self.cache
                return stale or self._build_xml([], [])

            logger.info("Building combined EPG...")
            start_time = time.time()

            # Fetch + extract inside the worker so each raw XML string is released as soon as it's parsed
            def _fetch_and_extract(name: str, url: str) -> tuple:
                return self._extract_content(self._fetch_source(name, url), tvg_ids)

            all_channels   = []
            all_programmes = []
            seen_channel_ids: set = set()
            claimed: dict = {}

            with concurrent.futures.ThreadPoolExecutor(max_workers=self.fetch_workers) as executor:
                futures = {
                    name: executor.submit(_fetch_and_extract, name, url)
                    for name, url in self.epg_sources.items()
                }

                # Merge in source order so dedup stays deterministic
                for name in list(futures):
                    channels, programmes = futures.pop(name).result()
                    if not channels and not programmes:
                        continue

                    kept_channels   = 0
                    kept_programmes = 0

                    # An id belongs to the first source that provides it
                    for ch_id, ch in channels:
                        if not ch_id:
                            continue
                        claimed.setdefault(ch_id, name)
                        if ch_id not in seen_channel_ids:
                            seen_channel_ids.add(ch_id)
                            all_channels.append(ch)
                            kept_channels += 1

                    # Programmes only come from the source that owns the id
                    for pr_id, pr in programmes:
                        if claimed.setdefault(pr_id, name) == name:
                            all_programmes.append(pr)
                            kept_programmes += 1

                    logger.info(f"  {name}: {kept_channels} channels, {kept_programmes} programmes")
                    del channels, programmes

            # Never replace a good cache with an empty guide
            if not all_channels and not all_programmes:
                logger.warning("EPG build produced no data — keeping previous cache")
                with self.cache_lock:
                    stale = self.cache
                return stale or self._build_xml([], [])

            channel_count   = len(all_channels)
            programme_count = len(all_programmes)

            combined_xml = self._build_xml(all_channels, all_programmes)
            del all_channels, all_programmes

            combined_gz = gzip.compress(combined_xml, compresslevel=6)
            elapsed     = time.time() - start_time

            logger.info(
                f"Combined EPG: {channel_count} channels, "
                f"{programme_count} programmes in {elapsed:.1f}s"
            )

            with self.cache_lock:
                self.cache        = combined_xml
                self.cache_gz     = combined_gz
                self.cache_expiry = time.time() + self.cache_duration

            return combined_xml

    def get_combined_epg_gzipped(self, force_refresh: bool = False) -> bytes:
        """Return combined XMLTV as gzip-compressed bytes."""
        with self.cache_lock:
            gz    = self.cache_gz
            valid = bool(gz) and time.time() < self.cache_expiry

        if not force_refresh and gz:
            if not valid:
                self._refresh_combined_in_background()
            return gz

        xml = self.get_combined_epg(force_refresh)

        with self.cache_lock:
            if self.cache_gz:
                return self.cache_gz

        # Nothing was cached (empty channel list / no data) — compress what was built
        return gzip.compress(xml, compresslevel=6)

    def get_provider_epg(self, provider_name: str) -> bytes:
        """
        Return a single-provider XMLTV (UTF-8 bytes), filtered to that
        provider's channels in the channel list.

        Uses a per-provider cache with the same TTL as the combined cache.
        An expired entry is served while a background rebuild runs.
        Returns empty bytes if the provider has no EPG source or no channels.
        """
        provider_name = provider_name.lower().strip()

        if provider_name not in self.epg_sources:
            logger.warning(f"No EPG source configured for provider: {provider_name}")
            return b""

        with self.cache_lock:
            cached = self._provider_cache.get(provider_name)
            valid  = cached is not None and time.time() < self._provider_expiry.get(provider_name, 0)

        if cached is not None:
            if valid:
                logger.debug(f"Returning cached EPG for provider: {provider_name}")
            else:
                self._refresh_provider_in_background(provider_name)
            return cached

        return self._build_provider_epg(provider_name)

    def _build_provider_epg(self, provider_name: str) -> bytes:
        """Fetch, filter and cache a single provider's XMLTV (UTF-8 bytes)."""
        tvg_ids = self._get_tvg_ids(provider_name)
        if tvg_ids is not None and not tvg_ids:
            logger.warning(f"No channels in the channel list for provider: {provider_name}")
            return b""

        url      = self.epg_sources[provider_name]
        xml_text = self._fetch_source(provider_name, url)

        if not xml_text:
            return b""

        channels, programmes = self._extract_content(xml_text, tvg_ids)
        del xml_text

        provider_xml = self._build_xml(
            [ch for _, ch in channels],
            [pr for _, pr in programmes],
        )

        logger.info(
            f"Provider EPG [{provider_name}]: "
            f"{len(channels)} channels, {len(programmes)} programmes"
        )

        gz_key = f"{provider_name}_gz"

        with self.cache_lock:
            self._provider_cache[provider_name]  = provider_xml
            self._provider_expiry[provider_name] = time.time() + self.cache_duration
            has_gz = gz_key in self._provider_cache

        # Keep a previously requested gzip copy in step with the plain one
        if has_gz:
            gz = gzip.compress(provider_xml, compresslevel=6)
            with self.cache_lock:
                self._provider_cache[gz_key]  = gz
                self._provider_expiry[gz_key] = time.time() + self.cache_duration

        return provider_xml

    def get_provider_epg_gz(self, provider_name: str) -> bytes:
        """
        Return a single-provider XMLTV as gzip-compressed bytes.
        Reuses the per-provider cache, compressing on first call.
        """
        provider_name = provider_name.lower().strip()

        # Check compressed provider cache
        gz_key = f"{provider_name}_gz"
        with self.cache_lock:
            cached = self._provider_cache.get(gz_key)
            valid  = cached is not None and time.time() < self._provider_expiry.get(gz_key, 0)

        if cached is not None:
            if not valid:
                self._refresh_provider_in_background(provider_name)
            return cached

        # Fetch/reuse plain XML then compress
        xml = self.get_provider_epg(provider_name)
        if not xml:
            return b""

        gz = gzip.compress(xml, compresslevel=6)

        with self.cache_lock:
            self._provider_cache[gz_key]  = gz
            self._provider_expiry[gz_key] = time.time() + self.cache_duration

        return gz

    def _refresh_combined_in_background(self) -> None:
        """Start a single background rebuild of the combined EPG; does nothing if one is running."""
        with self.cache_lock:
            if self._refreshing:
                return
            self._refreshing = True

        def _worker():
            try:
                logger.info("EPG cache expired - serving stale, rebuilding in background...")
                self._build_combined_epg()
            except Exception as exc:
                logger.error(f"EPG background rebuild error: {exc}")
            finally:
                with self.cache_lock:
                    self._refreshing = False

        threading.Thread(target=_worker, daemon=True, name='epg-stale-refresh').start()

    def _refresh_provider_in_background(self, provider_name: str) -> None:
        """Start a single background rebuild of one provider's EPG; does nothing if one is running."""
        with self.cache_lock:
            if provider_name in self._provider_refreshing:
                return
            self._provider_refreshing.add(provider_name)

        def _worker():
            try:
                logger.info(f"Provider EPG [{provider_name}] expired - serving stale, rebuilding in background...")
                self._build_provider_epg(provider_name)
            except Exception as exc:
                logger.error(f"Provider EPG [{provider_name}] background rebuild error: {exc}")
            finally:
                with self.cache_lock:
                    self._provider_refreshing.discard(provider_name)

        threading.Thread(target=_worker, daemon=True, name=f'epg-refresh-{provider_name}').start()

    def start_background_refresh(self) -> None:
        """Start the EPG refresh daemon thread (once); rebuilds the combined EPG at 75% of its TTL."""
        with self.cache_lock:
            if self._bg_started:
                return
            self._bg_started = True

        # check often enough that the 75% mark always lands before expiry
        interval = min(300, self.cache_duration * 0.25)

        def _worker():
            while True:
                try:
                    time.sleep(interval)

                    # nothing cached yet is left to the warmer / first request
                    with self.cache_lock:
                        built = self.cache is not None
                        age   = time.time() - (self.cache_expiry - self.cache_duration) if built else 0
                        stale = built and age > (self.cache_duration * 0.75)

                    if stale:
                        logger.info("EPG background refresh starting...")
                        t = time.time()
                        self._build_combined_epg(force_refresh=True)
                        logger.info(f"EPG background refresh done in {time.time() - t:.1f}s")

                except Exception as exc:
                    logger.error(f"EPG background refresh error: {exc}")

        threading.Thread(target=_worker, daemon=True, name='epg-bg-refresh').start()
        logger.info("EPG background refresh thread started")

    def clear_cache(self) -> None:
        """Clear all EPG caches (combined and per-provider)."""
        with self.cache_lock:
            self.cache        = None
            self.cache_gz     = None
            self.cache_expiry = 0
            self._provider_cache.clear()
            self._provider_expiry.clear()
        logger.info("EPG cache cleared")


# Singleton
_instance = None
_lock     = threading.Lock()


def get_epg_aggregator() -> EPGAggregator:
    """Get the singleton EPGAggregator instance."""
    global _instance
    with _lock:
        if _instance is None:
            _instance = EPGAggregator()
        return _instance
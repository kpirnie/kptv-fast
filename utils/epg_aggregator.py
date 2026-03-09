"""
EPG Aggregator - Downloads and combines EPG sources into single XMLTV
"""

import requests
import gzip
import re
import time
import threading
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class EPGAggregator:
    """Downloads and combines external EPG sources into single XMLTV"""

    def __init__(self):
        self.cache        = None
        self.cache_gz     = None
        self.cache_expiry = 0
        self.cache_lock   = threading.Lock()
        self.cache_duration = 3600  # 1 hour

        # Per-provider cache: provider_name → xml string
        self._provider_cache:  dict = {}
        self._provider_expiry: dict = {}

        self.epg_sources = {
            'plex':     'https://i.mjh.nz/Plex/all.xml',
            'pluto':    'https://i.mjh.nz/PlutoTV/all.xml',
            'samsung':  'https://i.mjh.nz/SamsungTVPlus/all.xml',
            'stirr':    'https://i.mjh.nz/Stirr/all.xml',
            'lg':       'https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz',
            'distrotv': 'https://epgshare01.online/epgshare01/epg_ripper_DISTROTV1.xml.gz',
            'tubi':     'https://github.com/BuddyChewChew/tubi-scraper/raw/refs/heads/main/tubi_epg.xml',
            'xumo':     'https://raw.githubusercontent.com/BuddyChewChew/xumo-playlist-generator/main/playlists/xumo_epg.xml.gz',
            'roku':     'https://github.com/matthuisman/i.mjh.nz/raw/master/Roku/all.xml',
            'localnow': 'https://raw.githubusercontent.com/BuddyChewChew/localnow-playlist-generator/refs/heads/main/epg.xml',
        }

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

    def _extract_content(self, xml_text: str) -> tuple:
        """Extract channel and programme blocks from XMLTV as raw strings."""
        channels   = []
        programmes = []

        try:
            channel_pattern   = re.compile(r'<channel\s[^>]*>.*?</channel>',   re.DOTALL)
            programme_pattern = re.compile(r'<programme\s[^>]*>.*?</programme>', re.DOTALL)

            channels   = channel_pattern.findall(xml_text)
            programmes = programme_pattern.findall(xml_text)

        except Exception as e:
            logger.error(f"Error extracting EPG content: {e}")

        return channels, programmes

    def _build_xml(self, channels: list, programmes: list) -> str:
        """Wrap channel and programme blocks in a valid XMLTV envelope."""
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
            f'<tv generator-info-name="KPTV-FAST" generated-ts="{datetime.now(timezone.utc).isoformat()}">',
        ]
        parts.extend(channels)
        parts.extend(programmes)
        parts.append('</tv>')
        return '\n'.join(parts)

    # ── Public interface ──────────────────────────────────────────────────────

    def get_combined_epg(self, force_refresh: bool = False) -> str:
        """Return combined XMLTV from all sources, using cache when valid."""
        with self.cache_lock:
            if not force_refresh and self.cache and time.time() < self.cache_expiry:
                logger.debug("Returning cached EPG")
                return self.cache

        logger.info("Building combined EPG...")
        start_time = time.time()

        all_channels   = []
        all_programmes = []
        seen_channel_ids: set = set()

        for name, url in self.epg_sources.items():
            xml_text = self._fetch_source(name, url)
            if not xml_text:
                continue

            channels, programmes = self._extract_content(xml_text)

            for ch in channels:
                id_match = re.search(r'id="([^"]+)"', ch)
                if id_match:
                    ch_id = id_match.group(1)
                    if ch_id not in seen_channel_ids:
                        seen_channel_ids.add(ch_id)
                        all_channels.append(ch)

            all_programmes.extend(programmes)
            logger.info(f"  {name}: {len(channels)} channels, {len(programmes)} programmes")

        combined_xml = self._build_xml(all_channels, all_programmes)
        elapsed      = time.time() - start_time

        logger.info(
            f"Combined EPG: {len(all_channels)} channels, "
            f"{len(all_programmes)} programmes in {elapsed:.1f}s"
        )

        with self.cache_lock:
            self.cache        = combined_xml
            self.cache_gz     = gzip.compress(combined_xml.encode('utf-8'))
            self.cache_expiry = time.time() + self.cache_duration

        return combined_xml

    def get_combined_epg_gzipped(self, force_refresh: bool = False) -> bytes:
        """Return combined XMLTV as gzip-compressed bytes."""
        with self.cache_lock:
            if not force_refresh and self.cache_gz and time.time() < self.cache_expiry:
                return self.cache_gz

        self.get_combined_epg(force_refresh)

        with self.cache_lock:
            return self.cache_gz

    def get_provider_epg(self, provider_name: str) -> str:
        """
        Return a single-provider XMLTV string.

        Uses a per-provider cache with the same TTL as the combined cache.
        Returns an empty string if the provider has no EPG source.
        """
        provider_name = provider_name.lower().strip()

        if provider_name not in self.epg_sources:
            logger.warning(f"No EPG source configured for provider: {provider_name}")
            return ""

        with self.cache_lock:
            if (
                provider_name in self._provider_cache
                and time.time() < self._provider_expiry.get(provider_name, 0)
            ):
                logger.debug(f"Returning cached EPG for provider: {provider_name}")
                return self._provider_cache[provider_name]

        url      = self.epg_sources[provider_name]
        xml_text = self._fetch_source(provider_name, url)

        if not xml_text:
            return ""

        channels, programmes = self._extract_content(xml_text)
        provider_xml         = self._build_xml(channels, programmes)

        logger.info(
            f"Provider EPG [{provider_name}]: "
            f"{len(channels)} channels, {len(programmes)} programmes"
        )

        with self.cache_lock:
            self._provider_cache[provider_name]  = provider_xml
            self._provider_expiry[provider_name] = time.time() + self.cache_duration

        return provider_xml

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
"""
Channel cache, concurrent fetching, filtering, and deduplication.
"""

import os
import re
import signal
import time
import threading
import traceback
import concurrent.futures
import logging

logger = logging.getLogger(__name__)


class ChannelManager:
    """
    Owns the channel cache and all logic for fetching, filtering,
    deduplicating, and refreshing channels from multiple providers.
    """

    def __init__(self, providers: dict, debug_mode: bool = False):
        self.providers    = providers
        self.debug_mode   = debug_mode

        # Cache state
        self._channels_cache: dict  = {}
        self._cache_expiry: dict    = {}
        self._cache_lock            = threading.Lock()
        self._last_duplicates: dict = {}

        # Config from env
        self.cache_duration    = int(os.getenv('CACHE_DURATION',    7200))
        self.max_workers       = int(os.getenv('MAX_WORKERS',          5))
        self.provider_timeout  = int(os.getenv('PROVIDER_TIMEOUT',    45))

        self.channel_name_include = os.getenv('CHANNEL_NAME_INCLUDE', '')
        self.channel_name_exclude = os.getenv('CHANNEL_NAME_EXCLUDE', '')
        self.group_include        = os.getenv('GROUP_INCLUDE',         '')
        self.group_exclude        = os.getenv('GROUP_EXCLUDE',         '')

        self._start_background_refresh()

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def is_cache_valid(self, key: str = 'all_channels') -> bool:
        """Return True if the named cache entry exists and has not expired."""
        return key in self._cache_expiry and time.time() < self._cache_expiry[key]

    def clear_cache(self) -> None:
        """Evict all channel cache entries."""
        with self._cache_lock:
            self._channels_cache.clear()
            self._cache_expiry.clear()

    def get_cached_channels(self) -> list:
        """Return the cached channel list without triggering a refresh."""
        with self._cache_lock:
            return list(self._channels_cache.get('all_channels', []))

    @property
    def last_duplicates(self) -> dict:
        """Duplicate counts from the most recent dedup pass, keyed by provider."""
        return self._last_duplicates

    # ── Public fetch interface ────────────────────────────────────────────────

    def get_all_channels(self) -> list:
        """Return all channels, refreshing the cache if stale."""
        return self._get_all_channels_concurrent()

    # ── Internal fetch pipeline ───────────────────────────────────────────────

    def _get_all_channels_concurrent(self) -> list:
        """Fetch channels from all providers concurrently and cache the result."""
        cache_key = 'all_channels'

        with self._cache_lock:
            if self.is_cache_valid(cache_key):
                return self._channels_cache[cache_key]

        logger.info("Starting concurrent channel fetch from all providers")
        start = time.time()

        all_channels: list = []
        channel_number     = 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_map = {
                executor.submit(self._fetch_provider_channels, name, prov): name
                for name, prov in self.providers.items()
            }
            for future in concurrent.futures.as_completed(future_map):
                name = future_map[future]
                try:
                    result = future.result()
                    if result:
                        for ch in result:
                            ch['provider']       = name
                            ch['channel_number'] = ch.get('number', channel_number)
                            channel_number += 1
                        all_channels.extend(result)
                except Exception as exc:
                    logger.error(f"Error collecting results from {name}: {exc}")

        all_channels = self._apply_filters(all_channels)
        all_channels = self._remove_duplicates(all_channels)
        all_channels.sort(key=lambda x: x.get('channel_number', 999999))

        with self._cache_lock:
            self._channels_cache[cache_key] = all_channels
            self._cache_expiry[cache_key]   = time.time() + self.cache_duration

        logger.info(f"Concurrent fetch complete: {len(all_channels)} channels in {time.time() - start:.2f}s")
        return all_channels

    def _fetch_provider_channels(self, provider_name: str, provider) -> list:
        """Fetch channels from a single provider with a hard SIGALRM timeout."""
        try:
            if self.debug_mode:
                logger.debug(f"Fetching channels from {provider_name}")

            start = time.time()

            def _timeout_handler(signum, frame):
                raise TimeoutError(f"{provider_name} timed out")

            result: list = []
            try:
                if hasattr(signal, 'SIGALRM'):
                    signal.signal(signal.SIGALRM, _timeout_handler)
                    signal.alarm(self.provider_timeout)

                result = provider.get_channels()

                if hasattr(signal, 'SIGALRM'):
                    signal.alarm(0)

            except TimeoutError:
                logger.warning(f"⏰ {provider_name} timed out after {self.provider_timeout}s")
                return []
            except Exception as exc:
                logger.error(f"❌ {provider_name} failed: {exc}")
                if self.debug_mode:
                    logger.debug(traceback.format_exc())
                return []

            elapsed = time.time() - start
            if result:
                logger.info(f"✅ {provider_name}: {len(result)} channels in {elapsed:.1f}s")
            else:
                logger.warning(f"⚠️  {provider_name}: no channels in {elapsed:.1f}s")

            return result

        except Exception as exc:
            logger.error(f"❌ Unhandled error fetching {provider_name}: {exc}")
            if self.debug_mode:
                logger.debug(traceback.format_exc())
            return []

    # ── Filtering ─────────────────────────────────────────────────────────────

    def _apply_filters(self, channels: list) -> list:
        """Apply regex include/exclude filters by channel name and group."""
        if not any([
            self.channel_name_include, self.channel_name_exclude,
            self.group_include,        self.group_exclude,
        ]):
            return channels

        filtered = []
        for ch in channels:
            name  = ch.get('name',  '')
            group = ch.get('group', '')

            if self.channel_name_include and not re.search(self.channel_name_include, name,  re.IGNORECASE):
                continue
            if self.channel_name_exclude and     re.search(self.channel_name_exclude, name,  re.IGNORECASE):
                continue
            if self.group_include        and not re.search(self.group_include,        group, re.IGNORECASE):
                continue
            if self.group_exclude        and     re.search(self.group_exclude,        group, re.IGNORECASE):
                continue

            filtered.append(ch)

        return filtered

    # ── Deduplication ─────────────────────────────────────────────────────────

    def _remove_duplicates(self, channels: list) -> list:
        """Remove duplicate channels keyed on (lowercased name, stream URL)."""
        seen:    set  = set()
        unique:  list = []
        dropped: dict = {}

        for ch in channels:
            key = (
                ch.get('name', '').lower().strip(),
                ch.get('stream_url', ''),
            )
            provider = ch.get('provider', 'unknown')

            if key not in seen and key[0] and key[1]:
                seen.add(key)
                unique.append(ch)
            else:
                dropped[provider] = dropped.get(provider, 0) + 1

        total = len(channels) - len(unique)
        logger.info(f"Removed {total} duplicate channels")
        if dropped:
            logger.debug(f"Duplicates by provider: {dropped}")

        self._last_duplicates = dropped
        return unique

    # ── Background refresh ────────────────────────────────────────────────────

    def _start_background_refresh(self) -> None:
        """Spawn a daemon thread that pre-refreshes the channel cache at 75% TTL."""
        def _worker():
            while True:
                try:
                    time.sleep(300)

                    with self._cache_lock:
                        now    = time.time()
                        age    = now - (self._cache_expiry.get('all_channels', now) - self.cache_duration)
                        stale  = age > (self.cache_duration * 0.75)

                    if stale:
                        logger.info("🔄 Background refresh starting…")
                        t = time.time()
                        self._get_all_channels_concurrent()
                        logger.info(f"✅ Background refresh done in {time.time() - t:.1f}s")

                except Exception as exc:
                    logger.error(f"Background refresh error: {exc}")

                time.sleep(900)

        threading.Thread(
            target=_worker,
            daemon=True,
            name='channel-bg-refresh',
        ).start()
        logger.info("🔄 Background refresh thread started")

    # ── Startup cache warming ─────────────────────────────────────────────────

    def warm_cache(self, startup_delay: int = 10) -> None:
        """
        Pre-warm the channel cache in a daemon thread.
        Called once from the aggregator on boot.

        :param startup_delay: Seconds to wait before beginning the warm.
        """
        def _warmer():
            try:
                logger.info("⏳ Waiting for providers to be available…")
                max_wait, waited = 60, 0
                while waited < max_wait and not self.providers:
                    time.sleep(2)
                    waited += 2

                if not self.providers:
                    logger.warning("❌ No providers available for cache warming")
                    return

                logger.info(f"✅ {len(self.providers)} providers ready: {', '.join(self.providers)}")

                if startup_delay > 0:
                    logger.info(f"⏳ Waiting {startup_delay}s before warming cache…")
                    time.sleep(startup_delay)

                logger.info("🔥 Warming channel cache…")
                t        = time.time()
                channels = self._get_all_channels_concurrent()
                logger.info(f"✅ Channel cache warm: {len(channels)} channels in {time.time() - t:.1f}s")
                logger.info("🚀 First requests will now be instant!")

            except Exception as exc:
                logger.error(f"❌ Cache warming failed: {exc}")

        threading.Thread(
            target=_warmer,
            daemon=True,
            name='channel-cache-warmer',
        ).start()
        logger.info("🌟 Startup cache warming scheduled")
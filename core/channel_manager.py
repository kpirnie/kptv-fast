"""
Channel cache, concurrent fetching, filtering, and deduplication.
"""

import os
import re
import gevent  # type: ignore
import time
import threading
import traceback
import concurrent.futures
import logging

logger = logging.getLogger(__name__)


def channel_tvg_id(ch: dict) -> str:
    """Return a channel's tvg-id: its id without the "<provider>-" prefix."""
    ch_id    = ch.get('id', '')
    provider = ch.get('provider', '')
    if provider and ch_id.startswith(f'{provider}-'):
        ch_id = ch_id[len(provider) + 1:]
    return ch_id


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
        self._fetch_lock            = threading.Lock()
        self._last_duplicates: dict = {}
        self._refreshing: bool      = False

        # Config from env
        self.cache_duration    = int(os.getenv('CACHE_DURATION',    7200))
        self.max_workers       = int(os.getenv('MAX_WORKERS') or max(1, len(providers)))
        self.provider_timeout  = int(os.getenv('PROVIDER_TIMEOUT',    45))

        # Cap the cache at the shortest provider-declared max age
        caps = [p.max_cache_age for p in providers.values() if getattr(p, 'max_cache_age', None)]
        if caps:
            self.cache_duration = min(self.cache_duration, *caps)

        self.channel_name_include = self._compile_filter('CHANNEL_NAME_INCLUDE')
        self.channel_name_exclude = self._compile_filter('CHANNEL_NAME_EXCLUDE')
        self.group_include        = self._compile_filter('GROUP_INCLUDE')
        self.group_exclude        = self._compile_filter('GROUP_EXCLUDE')

        self._start_background_refresh()

    def _compile_filter(self, env_name: str):
        """Compile a filter regex from the environment once; an invalid pattern is logged and that filter disabled."""
        pattern = os.getenv(env_name, '')
        if not pattern:
            return None

        try:
            return re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            logger.error(f"❌ Invalid {env_name} regex '{pattern}' — filter disabled: {exc}")
            return None

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def is_cache_valid(self, key: str = 'all_channels') -> bool:
        """Return True if the named cache entry exists and has not expired."""
        return key in self._cache_expiry and time.time() < self._cache_expiry[key]

    def clear_cache(self) -> None:
        """Evict all channel cache entries and expire provider-level caches."""
        with self._cache_lock:
            self._channels_cache.clear()
            self._cache_expiry.clear()

        for name, prov in self.providers.items():
            try:
                prov.clear_cache()
            except Exception as exc:
                logger.error(f"Error clearing {name} cache: {exc}")

    def get_cached_channels(self) -> list:
        """Return the cached channel list without triggering a refresh."""
        with self._cache_lock:
            return list(self._channels_cache.get('all_channels', []))

    @property
    def last_duplicates(self) -> dict:
        """Duplicate counts from the most recent dedup pass, keyed by provider."""
        return self._last_duplicates

    # ── Public fetch interface ────────────────────────────────────────────────

    def get_all_channels(self, force: bool = False) -> list:
        """
        Return all channels.

        A valid cache is returned as-is. An expired cache is still returned
        immediately while a background refresh runs. Only an empty cache blocks.

        :param force: Skip the cache and always run a live fetch.
        """
        cache_key = 'all_channels'

        if force:
            return self._get_all_channels_concurrent(force=True)

        with self._cache_lock:
            cached = self._channels_cache.get(cache_key)
            valid  = self.is_cache_valid(cache_key)

        if valid:
            return cached

        # expired but present — serve stale and refresh behind the scenes
        if cached:
            self._refresh_in_background()
            return cached

        return self._get_all_channels_concurrent()

    def get_tvg_ids(self, provider: str = '') -> set:
        """
        Return the tvg-ids of the channel list, optionally for one provider.

        Blocks on a fetch only when nothing is cached yet.
        """
        provider = provider.lower().strip()
        return {
            channel_tvg_id(ch)
            for ch in self.get_all_channels()
            if not provider or ch.get('provider', '').lower() == provider
        }

    def _refresh_in_background(self) -> None:
        """Start a single background refresh; does nothing if one is already running."""
        with self._cache_lock:
            if self._refreshing:
                return
            self._refreshing = True

        def _worker():
            try:
                logger.info("🔄 Cache expired — serving stale, refreshing in background…")
                t = time.time()
                self._get_all_channels_concurrent()
                logger.info(f"✅ Stale refresh done in {time.time() - t:.1f}s")
            except Exception as exc:
                logger.error(f"Stale refresh error: {exc}")
            finally:
                with self._cache_lock:
                    self._refreshing = False

        threading.Thread(
            target=_worker,
            daemon=True,
            name='channel-stale-refresh',
        ).start()

    # ── Internal fetch pipeline ───────────────────────────────────────────────

    def _get_all_channels_concurrent(self, force: bool = False) -> list:
        """
        Fetch channels from all providers concurrently and cache the result.

        Only one fetch runs at a time; concurrent callers wait for it and
        receive the freshly cached result.

        :param force: Skip the cache check and always re-fetch.
        """
        cache_key = 'all_channels'

        with self._cache_lock:
            if not force and self.is_cache_valid(cache_key):
                return self._channels_cache[cache_key]

        with self._fetch_lock:

            # another caller may have finished a fetch while we waited
            with self._cache_lock:
                if not force and self.is_cache_valid(cache_key):
                    return self._channels_cache[cache_key]

            logger.info("Starting concurrent channel fetch from all providers")
            start = time.time()

            all_channels: list = []

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
                            # copy — providers hand back their own cached dicts
                            for ch in result:
                                ch             = dict(ch)
                                ch['provider'] = name
                                all_channels.append(ch)
                    except Exception as exc:
                        logger.error(f"Error collecting results from {name}: {exc}")

            all_channels = self._apply_filters(all_channels)
            all_channels = self._remove_duplicates(all_channels)
            all_channels = self._assign_channel_numbers(all_channels)
            all_channels.sort(key=lambda x: x.get('channel_number', 999999))

            # never replace a good cache with an empty result
            if not all_channels:
                with self._cache_lock:
                    previous = self._channels_cache.get(cache_key)
                if previous:
                    logger.warning("⚠️  Fetch returned no channels — keeping previous cache")
                    all_channels = previous

            with self._cache_lock:
                self._channels_cache[cache_key] = all_channels
                self._cache_expiry[cache_key]   = time.time() + self.cache_duration

            logger.info(f"Concurrent fetch complete: {len(all_channels)} channels in {time.time() - start:.2f}s")
            return all_channels
            
    def _fetch_provider_channels(self, provider_name: str, provider) -> list:
        """Fetch channels from a single provider with a per-greenlet timeout."""
        try:
            if self.debug_mode:
                logger.debug(f"Fetching channels from {provider_name}")

            start = time.time()

            result: list = []
            try:
                with gevent.Timeout(self.provider_timeout, TimeoutError(f"{provider_name} timed out")):
                    result = provider.get_channels()

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

    # ── Numbering ─────────────────────────────────────────────────────────────

    @staticmethod
    def _native_number(ch: dict):
        """Return a channel's own number as a positive int, or None if it has no usable one."""
        value = ch.get('number')
        if isinstance(value, bool):
            return None

        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return None

        return number if number > 0 else None

    def _assign_channel_numbers(self, channels: list) -> list:
        """
        Give every channel a unique, deterministic channel_number.

        Providers are handled alphabetically. Native numbers go first; a number
        that is already taken is bumped to the next free one above it. Channels
        with no native number then take the lowest unused numbers from 1.
        """
        by_provider: dict = {}
        for ch in channels:
            by_provider.setdefault(ch.get('provider', ''), []).append(ch)

        used:       set  = set()
        unnumbered: list = []

        for provider in sorted(by_provider):
            native: list = []
            others: list = []

            for ch in by_provider[provider]:
                number = self._native_number(ch)
                if number:
                    native.append((number, ch))
                else:
                    others.append(ch)

            native.sort(key=lambda item: (item[0], str(item[1].get('name', '')).lower(), str(item[1].get('id', ''))))
            others.sort(key=lambda ch: (str(ch.get('name', '')).lower(), str(ch.get('id', ''))))

            for number, ch in native:
                while number in used:
                    number += 1
                used.add(number)
                ch['channel_number'] = number

            unnumbered.extend(others)

        number = 1
        for ch in unnumbered:
            while number in used:
                number += 1
            used.add(number)
            ch['channel_number'] = number

        return channels

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

            if self.channel_name_include and not self.channel_name_include.search(name):
                continue
            if self.channel_name_exclude and     self.channel_name_exclude.search(name):
                continue
            if self.group_include        and not self.group_include.search(group):
                continue
            if self.group_exclude        and     self.group_exclude.search(group):
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
        # check often enough that the 75% mark always lands before expiry
        interval = min(300, self.cache_duration * 0.25)

        def _worker():
            while True:
                try:
                    time.sleep(interval)

                    with self._cache_lock:
                        now    = time.time()
                        age    = now - (self._cache_expiry.get('all_channels', now) - self.cache_duration)
                        stale  = age > (self.cache_duration * 0.75)

                    if stale:
                        logger.info("🔄 Background refresh starting…")
                        t = time.time()
                        self._get_all_channels_concurrent(force=True)
                        logger.info(f"✅ Background refresh done in {time.time() - t:.1f}s")

                except Exception as exc:
                    logger.error(f"Background refresh error: {exc}")

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
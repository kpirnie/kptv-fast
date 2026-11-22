"""
VOD cache — movies and series from every provider with an on-demand library.
"""

import os
import time
import threading
import traceback
import concurrent.futures
import logging
import gevent  # type: ignore

from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class VodManager:
    """
    Owns the VOD library cache. Only providers that override ``get_vod`` are
    fetched. The library is built in the background when the container starts
    and refreshed at 75% TTL; requests only ever read what's cached.
    """

    def __init__(self, providers: dict, debug_mode: bool = False):
        self.debug_mode = debug_mode

        # only providers that actually have a library
        self.providers = {
            name: prov for name, prov in providers.items()
            if type(prov).get_vod is not BaseProvider.get_vod
        }

        self.cache_duration = int(os.getenv('VOD_CACHE_DURATION', 86400))
        self.vod_timeout    = int(os.getenv('VOD_TIMEOUT',        300))
        self.startup_delay  = int(os.getenv('STARTUP_CACHE_DELAY', 10))

        self._lock          = threading.Lock()
        self._build_lock    = threading.Lock()
        self._library       = None
        self._expiry        = 0

        # (provider, series_id) -> (expiry, seasons)
        self._episodes: dict = {}
        self._episode_lock   = threading.Lock()

        self._start_background_refresh()

    # ── Public interface ──────────────────────────────────────────────────────

    def get_library(self) -> dict:
        """Return the cached library; empty until the startup build finishes."""
        with self._lock:
            library = self._library

        return library or {'movies': [], 'series': []}

    def get_series_episodes(self, provider_name: str, series_id: str) -> dict:
        """Return a series' seasons, cached per series for the VOD TTL."""
        key = (provider_name, series_id)

        with self._episode_lock:
            cached = self._episodes.get(key)
        if cached and time.time() < cached[0]:
            return cached[1]

        prov = self.providers.get(provider_name)
        if prov is None:
            return {}

        seasons: dict = {}
        try:
            with gevent.Timeout(self.vod_timeout, TimeoutError(f"{provider_name} episodes timed out")):
                seasons = prov.get_series_episodes(series_id) or {}
        except TimeoutError:
            logger.warning(f"⏰ {provider_name} episodes for {series_id} timed out after {self.vod_timeout}s")
        except Exception as exc:
            logger.error(f"❌ {provider_name} episodes for {series_id} failed: {exc}")
            if self.debug_mode:
                logger.debug(traceback.format_exc())

        # keep an older good copy over an empty result
        if not seasons and cached:
            return cached[1]

        if seasons:
            with self._episode_lock:
                self._episodes[key] = (time.time() + self.cache_duration, seasons)

        return seasons

    def clear_cache(self) -> None:
        """Drop the library and episode caches."""
        with self._lock:
            self._library = None
            self._expiry  = 0
        with self._episode_lock:
            self._episodes.clear()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fetch_provider_vod(self, name: str, prov) -> dict:
        """Fetch one provider's library with a per-greenlet timeout."""
        start = time.time()
        try:
            with gevent.Timeout(self.vod_timeout, TimeoutError(f"{name} VOD timed out")):
                result = prov.get_vod() or {}
        except TimeoutError:
            logger.warning(f"⏰ {name} VOD timed out after {self.vod_timeout}s")
            return {}
        except Exception as exc:
            logger.error(f"❌ {name} VOD failed: {exc}")
            if self.debug_mode:
                logger.debug(traceback.format_exc())
            return {}

        logger.info(
            f"✅ {name} VOD: {len(result.get('movies', []))} movies, "
            f"{len(result.get('series', []))} series in {time.time() - start:.1f}s"
        )
        return result

    def _build(self) -> None:
        """Fetch every provider's library concurrently and cache the combined result."""
        with self._build_lock:
            logger.info("Building VOD library…")
            start = time.time()

            movies: list = []
            series: list = []

            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(self.providers))) as executor:
                future_map = {
                    executor.submit(self._fetch_provider_vod, name, prov): name
                    for name, prov in self.providers.items()
                }
                for future in concurrent.futures.as_completed(future_map):
                    name = future_map[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        logger.error(f"Error collecting VOD from {name}: {exc}")
                        continue

                    # copy — providers hand back their own cached dicts
                    for item in result.get('movies', []):
                        if item.get('id') and item.get('name') and item.get('stream_url'):
                            movies.append({**item, 'provider': name})
                    for item in result.get('series', []):
                        if item.get('id') and item.get('name'):
                            series.append({**item, 'provider': name})

            # never replace a good library with an empty result
            with self._lock:
                previous = self._library
            if not movies and not series and previous:
                logger.warning("⚠️  VOD build returned nothing — keeping previous library")
                movies, series = previous['movies'], previous['series']

            with self._lock:
                self._library = {'movies': movies, 'series': series}
                self._expiry  = time.time() + self.cache_duration

            logger.info(f"VOD library: {len(movies)} movies, {len(series)} series in {time.time() - start:.1f}s")

    def _start_background_refresh(self) -> None:
        """Daemon thread that builds the library on startup and pre-refreshes it at 75% TTL."""
        if not self.providers:
            return

        interval = min(300, self.cache_duration * 0.25)

        def _worker():
            if self.startup_delay > 0:
                time.sleep(self.startup_delay)

            while True:
                try:
                    with self._lock:
                        library = self._library
                        built   = library is not None
                        age     = time.time() - (self._expiry - self.cache_duration) if built else 0

                    # an empty library (every provider failed) is retried on the next pass
                    empty = not built or not (library['movies'] or library['series'])
                    if empty or age > (self.cache_duration * 0.75):
                        self._build()

                except Exception as exc:
                    logger.error(f"VOD background refresh error: {exc}")

                time.sleep(interval)

        threading.Thread(target=_worker, daemon=True, name='vod-bg-refresh').start()
        logger.info("🔄 VOD background refresh thread started")
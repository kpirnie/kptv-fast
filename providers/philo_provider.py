"""
Philo Provider
==============
Serves Philo's free (110+) or paid (70+) live channels.
Stream URLs are resolved concurrently when the channel list is refreshed.

"""

import os
import time
import uuid
import threading
import concurrent.futures
from typing import Any, Dict, List, Optional, Tuple

from .base_provider import BaseProvider


# ── Persisted query hashes (captured 2026-03-01 via browser DevTools) ────────
_HASH_PAGE_V3           = "c4ca57e1aa513b9f60dc88cbe3d0a12fd031716ccd806f66e18f68ef6037f050"
_HASH_PLAYBACK_V3       = "b0a69d5a3fefcd5bdbe7217cfec109168941d3a81180259809027ee859f61940"
_HASH_ASSIGN_EXPERIMENT = "64e8cfaf6a2f468aae6fe2aa7480ab158a0fe0ec8a3570bbbff7fff0435da111"

_APP_VERSION = "assets_web_player-2026.02.25.985606"
_GRAPHQL_URL = "https://www.philo.com/graphql"
_API_GQL_URL = "https://www.philo.com/api/graphql"

# Update this if the login mutation name changes (check DevTools at /api/graphql)
_LOGIN_MUTATION_NAME = "loginWithEmailPassword"

_PAGE_CAPABILITIES = [
    "COLLECTION_TILE_GROUPS",
    "HERO_PROMOTION",
    "MOVIE_SHOWINGS",
    "GUIDE_FILTERS",
    "SEARCH_PAGE_RECS",
    "UNIFIED_SHOWS_MOVIES_SEARCH_RESULTS",
    "COLLECTION_GROUPS",
    "OUT_OF_PLAN_CONTENT",
]

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) "
        "Gecko/20100101 Firefox/148.0"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.philo.com",
    "Referer": "https://www.philo.com/player/guide",
}


# ─────────────────────────────────────────────────────────────────────────────
# Session manager
# ─────────────────────────────────────────────────────────────────────────────

class _PhiloSession:
    """
    Thread-safe Philo session manager.
    """

    def __init__(
        self,
        logger,
        session_ttl: int = 7200,
    ):
        self._logger      = logger
        self._session_ttl = session_ttl
        self._lock        = threading.Lock()

        self._session: Optional[Any] = None
        self._player_id: Optional[str] = None
        self._is_authenticated: bool = False
        self._session_expiry: float = 0.0

    @property
    def is_authenticated(self) -> bool:
        return self._is_authenticated

    def get(self) -> Tuple[Any, str]:
        """Return (requests.Session, player_id), refreshing if expired."""
        with self._lock:
            if time.time() >= self._session_expiry or self._session is None:
                self._refresh()
            return self._session, self._player_id

    # ── Private ───────────────────────────────────────────────────────────────

    def _refresh(self):
        """Create a new session using browser cookies from env vars. Caller must hold self._lock."""
        import requests as _req

        session_id        = os.getenv("PHILO_SESSION_ID", "").strip()
        hashed_session_id = os.getenv("PHILO_HASHED_SESSION_ID", "").strip()

        if not session_id or not hashed_session_id:
            raise RuntimeError(
                "PHILO_SESSION_ID and PHILO_HASHED_SESSION_ID must be set. "
                "Get them from DevTools → Application → Cookies → www.philo.com"
            )

        session = _req.Session()
        session.cookies.set("_session_id",        session_id,        domain="www.philo.com")
        session.cookies.set("hashed_session_id",  hashed_session_id, domain="www.philo.com")

        # registerPlayer (original mutation from d21spike/plugin.video.philo)
        device_uuid = str(uuid.uuid4())
        reg_body = [{
            "query": (
                "mutation ($captionsEnabled: Boolean!, $deviceName: String!, "
                "$deviceType: DeviceType!, $deviceIcon: DeviceIconType!, "
                "$uuid: String!, $volume: UnitInterval!) {\n"
                "  registerPlayer(captionsEnabled: $captionsEnabled, deviceName: $deviceName, "
                "deviceType: $deviceType, deviceIcon: $deviceIcon, uuid: $uuid, volume: $volume) {\n"
                "    nickname\n    id\n    lastUpdatedAt\n    deviceType\n    uuid\n"
                "    user { id displayName __typename }\n    __typename\n  }\n}\n"
            ),
            "variables": {
                "deviceName":      "Chrome on Windows",
                "deviceIcon":      "PC",
                "deviceType":      "WEB",
                "uuid":            device_uuid,
                "captionsEnabled": False,
                "volume":          0.75,
            },
            "operationName": None,
        }]

        self._logger.debug(f"Philo registerPlayerV2 body: {reg_body}")
        r = session.post(_GRAPHQL_URL, json=reg_body, headers=_BASE_HEADERS, timeout=15)
        if r.status_code == 401:
            raise RuntimeError(
                "Philo session cookies rejected (401) — cookies may be expired. "
                "Refresh PHILO_SESSION_ID and PHILO_HASHED_SESSION_ID from DevTools."
            )
        r.raise_for_status()
        data = r.json()

        player_id = None
        for block in data:
            pid = ((block.get("data") or {})
                   .get("registerPlayer", {})
                   .get("id"))
            if pid:
                player_id = pid
                break

        if not player_id:
            raise RuntimeError(
                f"registerPlayerV2 returned no player ID — response: {data}"
            )

        self._session        = session
        self._player_id      = player_id
        self._is_authenticated = True
        self._session_expiry = time.time() + self._session_ttl


# ─────────────────────────────────────────────────────────────────────────────
# Provider
# ─────────────────────────────────────────────────────────────────────────────

class PhiloProvider(BaseProvider):
    """
    Philo free (and optionally paid) FAST channel provider.

    Direct HLS URLs are resolved at M3U8 generation time and cached for
    PHILO_STREAM_TTL seconds. No proxy route is required.
    """

    def __init__(self):
        super().__init__("philo")

        self._channel_cache_ttl = 3600
        self._stream_ttl        = 1800
        self._session_ttl       = 7200
        self._stream_workers    = 10

        self._philo_session = _PhiloSession(
            logger=self.logger,
            session_ttl=self._session_ttl,
        )

        # Channel metadata cache
        self._channels_meta: List[Dict[str, Any]] = []
        self._channel_cache_expiry: float = 0.0

        # Per-callsign stream URL cache: {callsign: {"hls_url": str, "expiry": float}}
        self._stream_cache: Dict[str, Dict[str, Any]] = {}
        self._stream_lock = threading.Lock()

        mode = "authenticated"
        self.logger.info(f"PhiloProvider ready ({mode})")

    # ── GraphQL helper ────────────────────────────────────────────────────────

    def _gql(self, session, body, url: str = _GRAPHQL_URL) -> Any:
        r = session.post(url, json=body, headers=_BASE_HEADERS, timeout=20)
        r.raise_for_status()
        return r.json()

    # ── Guide fetch ───────────────────────────────────────────────────────────

    def _fetch_guide(self, session) -> List[Dict[str, Any]]:
        """Paginate pageV3(GUIDE) and return channel metadata (no stream URLs)."""
        channels: List[Dict[str, Any]] = []
        seen: set = set()
        end_cursor = None
        has_next   = True
        page       = 0

        while has_next and page < 20:
            page += 1
            body = [{
                "operationName": "pageV3",
                "variables": {
                    "pageType":             "GUIDE",
                    "typeId":               None,
                    "filterId":             None,
                    "filter":               None,
                    "sorterId":             None,
                    "endCursor":            end_cursor,
                    "startCursor":          None,
                    "firstGroups":          20,
                    "initialTiles":         1,
                    "lastGroups":           None,
                    "numSparseGroups":      300,
                    "includeTileDescription": False,
                    "includeTileChannel":   True,
                    "iconFormat":           "SVG",
                    "capabilities":         _PAGE_CAPABILITIES,
                    "startTime":            None,
                    "endTime":              None,
                },
                "extensions": {
                    "persistedQuery": {"version": 1, "sha256Hash": _HASH_PAGE_V3}
                },
            }]

            data = self._gql(session, body)
            groups = (data[0].get("data", {}).get("page", {}).get("groups", {}))
            page_info  = groups.get("pageInfo", {})
            has_next   = page_info.get("hasNextPage", False)
            end_cursor = page_info.get("endCursor")

            for edge in groups.get("edges", []):
                node = edge.get("node", {})
                if node.get("type") != "GUIDE":
                    continue

                ch_data  = node.get("channel") or {}
                callsign = ch_data.get("callsign") or ch_data.get("channelId")
                if not callsign or callsign in seen:
                    continue
                seen.add(callsign)

                display_name   = ch_data.get("displayName", callsign)
                channel_id_b64 = ch_data.get("channelId", "")

                color_logo = (ch_data.get("colorLogo") or {}).get("large", "")
                white_logo = (ch_data.get("whiteLogo") or {}).get("largeWhite", "")
                logo = color_logo or white_logo

                header_title = (node.get("header") or {}).get("title", "")
                group = f"Philo – {header_title}" if header_title else "Philo"

                channel = {
                    "id":          f"philo-{callsign.lower()}",
                    "name":        display_name,
                    "stream_url":  "",   # populated later
                    "logo":        logo,
                    "group":       group,
                    "number":      None,
                    "description": f"Philo: {display_name}",
                    "language":    "en",
                    "_callsign":   callsign,
                    "_channel_id": channel_id_b64,
                }

                # skip validate_channel here — stream_url is empty until _resolve_all runs
                norm = self.normalize_channel(channel)
                norm["_callsign"]   = callsign
                norm["_channel_id"] = channel_id_b64
                channels.append(norm)

            self.logger.debug(
                f"Philo guide page {page}: {len(channels)} channels total, "
                f"hasNextPage={has_next}"
            )
            self.logger.debug(f"Philo guide raw page {page} response: {data}")

        return channels

    # ── Stream URL resolution ─────────────────────────────────────────────────

    def _resolve_one(
        self, session, player_id: str, callsign: str, channel_id_b64: str
    ) -> Optional[str]:
        """
        Resolve HLS URL for a single channel.
          1. pageV3(CHANNEL) → current broadcastId
          2. createPlaybackSessionV3(broadcastId) → hlsURL
        """
        try:
            # Step 1 — current broadcast
            chan_body = [{
                "operationName": "pageV3",
                "variables": {
                    "pageType":             "CHANNEL",
                    "typeId":               channel_id_b64,
                    "filterId":             None,
                    "filter":               None,
                    "sorterId":             None,
                    "endCursor":            None,
                    "startCursor":          None,
                    "firstGroups":          1,
                    "initialTiles":         1,
                    "lastGroups":           None,
                    "numSparseGroups":      None,
                    "includeTileDescription": False,
                    "includeTileChannel":   False,
                    "iconFormat":           "SVG",
                    "capabilities":         _PAGE_CAPABILITIES,
                    "startTime":            None,
                    "endTime":              None,
                },
                "extensions": {
                    "persistedQuery": {"version": 1, "sha256Hash": _HASH_PAGE_V3}
                },
            }]

            chan_data    = self._gql(session, chan_body)
            tile         = (chan_data[0].get("data", {}).get("page", {}).get("tile")) or {}
            broadcast_id = tile.get("playableAssetId")

            if not broadcast_id:
                self.logger.debug(
                    f"Philo: no broadcastId for '{callsign}' — skipping"
                )
                return None

            # Step 2 — playback session
            pb_body = [
                {
                    "operationName": "assignExperiment",
                    "variables":     {"name": "cranstonAdBeaconFireLogic2024-06-17"},
                    "extensions":    {
                        "persistedQuery": {
                            "version": 1,
                            "sha256Hash": _HASH_ASSIGN_EXPERIMENT,
                        }
                    },
                },
                {
                    "operationName": "createPlaybackSessionV3",
                    "variables": {
                        "id":              broadcast_id,
                        "playerId":        player_id,
                        "idfa":            None,
                        "lat":             None,
                        "givn":            None,
                        "tileGroupId":     None,
                        "broadcastAt":     None,
                        "startAtOverride": None,
                        "isPreload":       False,
                    },
                    "extensions": {
                        "persistedQuery": {
                            "version": 1,
                            "sha256Hash": _HASH_PLAYBACK_V3,
                        }
                    },
                },
            ]

            pb_data = self._gql(session, pb_body)
            for block in pb_data:
                sess_v2 = (block.get("data") or {}).get("createPlaybackSessionV2") or {}
                hls_url = sess_v2.get("hlsURL")
                if hls_url:
                    return hls_url

            self.logger.debug(
                f"Philo: no hlsURL in playback response for '{callsign}'"
            )
            return None

        except Exception as exc:
            self.logger.warning(
                f"Philo: stream URL resolution failed for '{callsign}': {exc}"
            )
            return None

    def _resolve_all(
        self, session, player_id: str, channels: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        """
        Concurrently resolve HLS URLs for all channels.
        Updates self._stream_cache as results come in.
        Returns {callsign: hls_url}.
        """
        results: Dict[str, str] = {}
        expiry = time.time() + self._stream_ttl

        def _worker(ch):
            cs  = ch["_callsign"]
            cid = ch["_channel_id"]
            url = self._resolve_one(session, player_id, cs, cid)
            return cs, url

        self.logger.info(
            f"Philo: resolving {len(channels)} stream URLs "
        )
        t0 = time.time()

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self._stream_workers
        ) as pool:
            futures = {pool.submit(_worker, ch): ch["_callsign"] for ch in channels}
            for fut in concurrent.futures.as_completed(futures, timeout=120):
                try:
                    callsign, url = fut.result(timeout=30)
                    if url:
                        results[callsign] = url
                        with self._stream_lock:
                            self._stream_cache[callsign] = {
                                "hls_url": url,
                                "expiry":  expiry,
                            }
                except Exception as exc:
                    cs = futures.get(fut, "?")
                    self.logger.debug(f"Philo: worker error for '{cs}': {exc}")

        self.logger.info(
            f"Found {len(results)} channels from Philo API"
        )
        return results

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _stream_cache_valid(self) -> bool:
        """True only if every callsign in _channels_meta has a non-expired URL."""
        if not self._stream_cache or not self._channels_meta:
            return False
        now = time.time()
        with self._stream_lock:
            for ch in self._channels_meta:
                entry = self._stream_cache.get(ch.get("_callsign", ""))
                if not entry or entry["expiry"] <= now:
                    return False
        return True

    # ── BaseProvider interface ────────────────────────────────────────────────

    def get_channels(self) -> List[Dict[str, Any]]:
        """
        Return all Philo channels with direct HLS stream URLs embedded.

        Caching:
          Channel metadata  →  PHILO_CACHE_DURATION  (default 1 hr)
          Per-channel URLs  →  PHILO_STREAM_TTL       (default 30 min)
        Both refresh together on expiry.
        """
        try:
            now = time.time()

            # Fast path — everything still cached
            if (
                now < self._channel_cache_expiry
                and self._channels_meta
                and self._stream_cache_valid()
            ):
                self.logger.debug("Philo: returning fully cached channels")
                return self._build_output(self._channels_meta)

            session, player_id = self._philo_session.get()

            # Refresh channel metadata if stale
            if now >= self._channel_cache_expiry or not self._channels_meta:
                self.logger.info("Fetching philo channels")
                meta = self._fetch_guide(session)
                if not meta:
                    return self._build_output(self._channels_meta)
                self._channels_meta        = meta
                self._channel_cache_expiry = now + self._channel_cache_ttl
            else:
                self.logger.debug("Philo: reusing cached channel metadata")

            # Resolve fresh stream URLs for all channels
            stream_urls = self._resolve_all(session, player_id, self._channels_meta)

            # Fall back to still-valid cached URLs for any that failed
            now2 = time.time()
            with self._stream_lock:
                for ch in self._channels_meta:
                    cs = ch.get("_callsign", "")
                    if cs not in stream_urls:
                        cached = self._stream_cache.get(cs)
                        if cached and cached["expiry"] > now2:
                            stream_urls[cs] = cached["hls_url"]

            output = self._build_output(self._channels_meta, stream_urls)
            return output

        except Exception as exc:
            self.logger.error(f"Philo: {exc}".replace("Philo: Philo:", "Philo:"))
            return self._build_output(self._channels_meta)

    def _build_output(
        self,
        meta: List[Dict[str, Any]],
        stream_urls: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Merge channel metadata with stream URLs.
        If stream_urls is None, reads from self._stream_cache.
        Channels with no URL are omitted.
        """
        now = time.time()
        out: List[Dict[str, Any]] = []
        for ch in meta:
            cs = ch.get("_callsign", "")
            url = None

            if stream_urls is not None:
                url = stream_urls.get(cs)

            if not url:
                with self._stream_lock:
                    cached = self._stream_cache.get(cs)
                if cached and cached["expiry"] > now:
                    url = cached["hls_url"]

            if url:
                ch_out = dict(ch)
                ch_out["stream_url"] = url
                out.append(ch_out)

        return out

    def get_epg_data(self) -> Dict:
        """EPG not implemented (would require additional Philo API calls)."""
        return {}

"""
Philo Provider
==============
Serves Philo's free (110+) or paid (70+) live channels.
Stream URLs are resolved at play time via /play/philo/<callsign>.

"""

import os
import time
import uuid
import threading
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

    Direct HLS URLs are resolved at play time via /play/philo/<callsign>
    and cached per channel for 30 minutes.
    """

    def __init__(self):
        super().__init__("philo")

        # Philo needs browser cookies — refuse to load without them
        if (
            not os.getenv("PHILO_SESSION_ID", "").strip()
            or not os.getenv("PHILO_HASHED_SESSION_ID", "").strip()
        ):
            raise RuntimeError(
                "Philo not configured — PHILO_SESSION_ID and "
                "PHILO_HASHED_SESSION_ID must be set"
            )

        self._channel_cache_ttl = 3600
        self._stream_ttl        = 1800
        self._session_ttl       = 7200

        self._philo_session = _PhiloSession(
            logger=self.logger,
            session_ttl=self._session_ttl,
        )

        # Channel metadata cache
        self._channels_meta: List[Dict[str, Any]] = []
        self._channel_cache_expiry: float = 0.0

        # callsign -> channel_id_b64 for play-time resolution
        self._channel_ids: Dict[str, str] = {}

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
                    "stream_url":  f"/play/philo/{callsign}",
                    "logo":        logo,
                    "group":       group,
                    "number":      None,
                    "description": f"Philo: {display_name}",
                    "language":    "en",
                    "_callsign":   callsign,
                    "_channel_id": channel_id_b64,
                }

                if not self.validate_channel(channel):
                    continue

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

    # ── BaseProvider interface ────────────────────────────────────────────────

    def get_channels(self) -> List[Dict[str, Any]]:
        """
        Return all Philo channels with /play/philo/<callsign> stream URLs.

        Caching:
          Channel metadata  →  1 hr
        Stream URLs are resolved per channel at play time (30 min cache).
        """
        try:
            now = time.time()

            # Fast path — metadata still cached
            if now < self._channel_cache_expiry and self._channels_meta:
                self.logger.debug("Philo: returning cached channels")
                return self._channels_meta

            session, player_id = self._philo_session.get()

            self.logger.info("Fetching philo channels")
            meta = self._fetch_guide(session)
            if not meta:
                return self._channels_meta

            self._channels_meta        = meta
            self._channel_ids          = {ch["_callsign"]: ch["_channel_id"] for ch in meta}
            self._channel_cache_expiry = now + self._channel_cache_ttl

            self.logger.info(f"Found {len(meta)} channels from Philo API")
            return self._channels_meta

        except Exception as exc:
            self.logger.error(f"Philo: {exc}".replace("Philo: Philo:", "Philo:"))
            return self._channels_meta

    def resolve_stream(self, channel_id: str) -> Optional[str]:
        """Resolve the HLS URL for a callsign at play time (30 min per-channel cache)."""
        try:
            now = time.time()

            with self._stream_lock:
                cached = self._stream_cache.get(channel_id)
            if cached and cached["expiry"] > now:
                return cached["hls_url"]

            channel_id_b64 = self._channel_ids.get(channel_id)
            if channel_id_b64 is None:
                self.get_channels()
                channel_id_b64 = self._channel_ids.get(channel_id)
            if channel_id_b64 is None:
                return None

            session, player_id = self._philo_session.get()
            url = self._resolve_one(session, player_id, channel_id, channel_id_b64)

            if url:
                with self._stream_lock:
                    self._stream_cache[channel_id] = {
                        "hls_url": url,
                        "expiry":  time.time() + self._stream_ttl,
                    }
            return url

        except Exception as exc:
            self.logger.error(f"Philo: resolve failed for '{channel_id}': {exc}")
            return None

    def clear_cache(self) -> None:
        """Expire channel metadata so the next fetch re-pulls the guide."""
        self._channel_cache_expiry = 0.0
        
    def get_epg_data(self) -> Dict:
        """EPG not implemented (would require additional Philo API calls)."""
        return {}

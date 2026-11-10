"""
Roku Provider Implementation
Uses The Roku Channel API with apsattv.com M3U fallback
API stream URLs are resolved at play time via /play/roku/<id>
"""

import re
import time
from typing import List, Dict, Any, Optional
from .base_provider import BaseProvider


class RokuProvider(BaseProvider):
    """Provider for The Roku Channel - API first, apsattv fallback"""

    CHANNELS_JSON_URL = "https://i.mjh.nz/Roku/.channels.json"
    FALLBACK_M3U_URL  = "https://www.apsattv.com/rok.m3u"

    CSRF_URL      = "https://therokuchannel.roku.com/api/v1/csrf"
    CONTENT_URL   = ("https://therokuchannel.roku.com/api/v2/homescreen/content/"
                     "https%3A%2F%2Fcontent.sr.roku.com%2Fcontent%2Fv1%2Froku-trc%2F"
                     "{channel_id}%3Fexpand%3DviewOptions.channelId%252CviewOptions.playId"
                     "%252Cnext.viewOptions.channelId%252Cnext.viewOptions.playId")
    PLAYBACK_URL  = "https://therokuchannel.roku.com/api/v3/playback"

    def __init__(self):
        super().__init__("roku")

        self._base_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            ),
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin":          "https://therokuchannel.roku.com",
            "Referer":         "https://therokuchannel.roku.com/",
        }

        self._channels_cache: List[Dict[str, Any]] = []
        self._cache_expiry: float = 0
        self._cache_duration: int = 3600

        # channel ids served via /play/ (empty while the apsattv fallback is in use)
        self._api_ids: set = set()

        # Shared CSRF session + per-channel resolved URL cache: {id: {"url": str, "expiry": float}}
        self._roku_session = None
        self._stream_ttl: int = 1800
        self._stream_cache: Dict[str, Dict[str, Any]] = {}

    # ── Roku API helpers ──────────────────────────────────────────────────────

    def _make_roku_session(self):
        """Create a requests Session pre-loaded with CSRF token and cookies."""
        import requests
        session = requests.Session()
        session.headers.update(self._base_headers)
        try:
            r = session.get(self.CSRF_URL, timeout=10)
            r.raise_for_status()
            csrf = r.json().get("csrf")
            if csrf:
                session.headers.update({"csrf-token": csrf})
        except Exception as e:
            self.logger.warning(f"Roku: could not fetch CSRF token: {e}")
        return session

    def _resolve_stream(self, session, channel_id: str) -> Optional[str]:
        """Resolve a single Roku channel stream URL via the API."""
        try:
            content_url = self.CONTENT_URL.format(channel_id=channel_id)
            cr = session.get(content_url, timeout=15)
            cr.raise_for_status()
            view_opts = cr.json().get("viewOptions", [])
            if not view_opts:
                return None
            play_id = view_opts[0].get("playId")
            if not play_id:
                return None

            payload = {
                "rokuId":      channel_id,
                "playId":      play_id,
                "mediaFormat": "m3u",
                "drmType":     "widevine",
                "quality":     "fhd",
                "bifUrl":      None,
                "adPolicyId":  "",
                "providerId":  "rokuavod",
            }
            pb_headers = {"content-type": "application/json"}
            pr = session.post(self.PLAYBACK_URL, json=payload,
                              headers=pb_headers, timeout=15)
            if pr.status_code == 403:
                return None
            pr.raise_for_status()
            url = pr.json().get("url", "")
            if not url:
                return None

            # Transform URL
            if "https://osm.sr.roku.com/osm/v1/hls/master/" in url:
                url = url.replace(
                    "https://osm.sr.roku.com/osm/v1/hls/master/",
                    "https://aka-live1050.delivery.roku.com/"
                ).replace("/live.m3u8", "/t2-origin/out/v1/live.m3u8")
                url = url.split("?")[0]

            return url if url else None

        except Exception as e:
            self.logger.debug(f"Roku: stream resolve failed for {channel_id}: {e}")
            return None

    def _get_channels_from_api(self) -> List[Dict[str, Any]]:
        """Fetch channel list from i.mjh.nz; streams resolve at play time."""
        try:
            r = self.make_request("GET", self.CHANNELS_JSON_URL,
                                  headers=self._base_headers)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            self.logger.warning(f"Roku: failed to fetch channels JSON: {e}")
            return []

        channels_data = data.get("channels", {})
        if not channels_data:
            self.logger.warning("Roku: no channels in JSON response")
            return []

        channels: List[Dict[str, Any]] = []
        api_ids: set = set()

        for channel_id, ch in channels_data.items():
            groups = ch.get("groups", [])
            group  = groups[0] if groups else "The Roku Channel"
            name   = ch.get("name", "Unknown")

            channel = {
                "id":          f"roku-{channel_id}",
                "name":        name,
                "stream_url":  f"/play/roku/{channel_id}",
                "logo":        ch.get("logo", ""),
                "group":       group,
                "number":      ch.get("chno"),
                "description": f"The Roku Channel: {name}",
                "language":    "en",
            }
            if self.validate_channel(channel):
                channels.append(self.normalize_channel(channel))
                api_ids.add(channel_id)

        self._api_ids = api_ids

        self.logger.info(f"Roku: {len(channels)} channels from API")
        return channels

    # ── Fallback M3U ─────────────────────────────────────────────────────────

    def _parse_m3u(self, content: str) -> List[Dict[str, Any]]:
        channels: List[Dict[str, Any]] = []
        lines = content.strip().split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXTINF:"):
                try:
                    url_line = ""
                    j = i + 1
                    while j < len(lines):
                        candidate = lines[j].strip()
                        if candidate and not candidate.startswith("#"):
                            url_line = candidate
                            break
                        j += 1
                    if not url_line:
                        i += 1
                        continue

                    extinf = line[8:]
                    channel_name = ""
                    tvg_id = tvg_logo = group_title = tvg_chno = ""
                    if "," in extinf:
                        attr_part, name_part = extinf.split(",", 1)
                        channel_name = name_part.strip()
                        m = re.search(r'tvg-id="([^"]*)"',     attr_part)
                        if m: tvg_id    = m.group(1)
                        m = re.search(r'tvg-logo="([^"]*)"',   attr_part)
                        if m: tvg_logo  = m.group(1)
                        m = re.search(r'group-title="([^"]*)"', attr_part)
                        if m: group_title = m.group(1)
                        m = re.search(r'tvg-chno="([^"]*)"',   attr_part)
                        if m: tvg_chno  = m.group(1)
                    else:
                        channel_name = extinf.strip()

                    if channel_name and url_line:
                        raw_id = (tvg_id if tvg_id
                                  else channel_name.lower()
                                                   .replace(" ", "-")
                                                   .replace("&", "and"))
                        channel = {
                            "id":          f"roku-{raw_id}",
                            "name":        channel_name,
                            "stream_url":  url_line,
                            "logo":        tvg_logo,
                            "group":       group_title or "The Roku Channel",
                            "number":      int(tvg_chno) if tvg_chno and tvg_chno.isdigit() else None,
                            "description": f"The Roku Channel: {channel_name}",
                            "language":    "en",
                        }
                        if self.validate_channel(channel):
                            channels.append(self.normalize_channel(channel))
                    i = j + 1
                except Exception as e:
                    self.logger.debug(f"Roku M3U parse error: {e}")
                    i += 1
            else:
                i += 1
        return channels

    def _get_channels_from_fallback(self) -> List[Dict[str, Any]]:
        try:
            r = self.make_request("GET", self.FALLBACK_M3U_URL,
                                  headers={"User-Agent": self.get_user_agent()})
            r.raise_for_status()
            channels = self._parse_m3u(r.text)
            self.logger.info(f"Roku: fallback M3U returned {len(channels)} channels")
            return channels
        except Exception as e:
            self.logger.error(f"Roku: fallback M3U failed: {e}")
            return []

    # ── BaseProvider interface ────────────────────────────────────────────────

    def get_channels(self) -> List[Dict[str, Any]]:
        if time.time() < self._cache_expiry and self._channels_cache:
            return self._channels_cache

        channels = self._get_channels_from_api()

        if len(channels) < 10:
            self.logger.info("Roku: API returned too few channels, using fallback")
            self._api_ids = set()
            channels = self._get_channels_from_fallback()

        if channels:
            self._channels_cache = channels
            self._cache_expiry   = time.time() + self._cache_duration

        return channels

    def resolve_stream(self, channel_id: str) -> Optional[str]:
        """Resolve a Roku channel's stream URL at play time (30 min per-channel cache)."""
        cached = self._stream_cache.get(channel_id)
        if cached and cached["expiry"] > time.time():
            return cached["url"]

        if channel_id not in self._api_ids:
            self.get_channels()
            if channel_id not in self._api_ids:
                return None

        fresh = self._roku_session is None
        if fresh:
            self._roku_session = self._make_roku_session()

        url = self._resolve_stream(self._roku_session, channel_id)

        # a reused session may have gone stale — rebuild it and retry once
        if not url and not fresh:
            self._roku_session = self._make_roku_session()
            url = self._resolve_stream(self._roku_session, channel_id)

        if url:
            self._stream_cache[channel_id] = {
                "url":    url,
                "expiry": time.time() + self._stream_ttl,
            }
        return url

    def clear_cache(self) -> None:
        self._cache_expiry = 0
        
    def get_epg_data(self) -> Dict:
        return {}
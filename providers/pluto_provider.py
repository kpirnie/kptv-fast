"""
Pluto Provider Implementation
==============================
Pluto TV API v2 — anonymous boot, no credentials required.
Stream URLs built from bootData.servers.stitcher + channel.stitched.path
+ bootData.stitcherParams + jwt + masterJWTPassthrough=true.
"""

import uuid
import os
import urllib.parse
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from .base_provider import BaseProvider


# ── Region → geo-spoof IP ────────────────────────────────────────────────────
_REGION_IPS: Dict[str, str] = {
    "local":   "",
    "uk":      "178.238.11.6",
    "ca":      "192.206.151.131",
    "fr":      "193.169.64.141",
    "us_east": "108.82.206.181",
    "us_west": "76.81.9.69",
}

_REGION_COUNTRY: Dict[str, str] = {
    "local":   "US",
    "uk":      "GB",
    "ca":      "CA",
    "fr":      "FR",
    "us_east": "US",
    "us_west": "US",
}

_APP_VERSION  = "7.9.0-a9cca6b89aea4dc0998b92a51989d2adb9a9025d"
_BOOT_URL     = "https://boot.pluto.tv/v4/start"
_CHANNELS_URL = "https://service-channels.clusters.pluto.tv/v2/guide/channels"
_CATS_URL     = "https://service-channels.clusters.pluto.tv/v2/guide/categories"
_VOD_CATS_URL   = "https://api.pluto.tv/v3/vod/categories"
_VOD_SERIES_URL = "https://api.pluto.tv/v3/vod/series/{series_id}/seasons"
_IMAGE_HOST     = "https://images.pluto.tv"
_FILM_TYPES     = {"movie", "film", "feature"}

# Refresh session before Pluto's ~24h JWT expiry
_SESSION_TTL_SECONDS = 55 * 60  # 55 minutes


class PlutoProvider(BaseProvider):
    """Provider for Pluto TV channels (v2 anonymous streams)."""

    def __init__(self):
        super().__init__("pluto")

        self.region  = os.getenv("PLUTO_REGION", "us_west")
        self._country = _REGION_COUNTRY.get(self.region, "US")

        self.device_id = str(uuid.uuid1())

        # Cached session fields
        self._session_token:   Optional[str] = None
        self._session_id:      Optional[str] = None
        self._stitcher_host:   Optional[str] = None
        self._stitcher_params: Optional[str] = None
        self._session_expiry:  float         = 0.0

        self._base_headers = {
            "accept":           "*/*",
            "accept-language":  "en-US,en;q=0.9",
            "origin":           "https://pluto.tv",
            "referer":          "https://pluto.tv/",
            "user-agent":       (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/14.1.2 Safari/605.1.15"
            ),
        }

        forwarded = _REGION_IPS.get(self.region, "")
        if forwarded:
            self._base_headers["X-Forwarded-For"] = forwarded

    # ── Session management ────────────────────────────────────────────────────

    def _get_session(self) -> bool:
        """
        Anonymous boot against boot.pluto.tv/v4/start.
        Stores sessionToken, sessionID, servers.stitcher, stitcherParams.
        """
        if self._session_token and datetime.now(timezone.utc).timestamp() < self._session_expiry:
            return True

        client_time = datetime.now(timezone.utc).isoformat()

        params = {
            "appName":           "web",
            "appVersion":        _APP_VERSION,
            "deviceVersion":     "16.2.0",
            "deviceModel":       "web",
            "deviceMake":        "Chrome",
            "deviceType":        "web",
            "clientID":          self.device_id,
            "clientModelNumber": "1.0.0",
            "channelID":         "5a4d3a00ad95e4718ae8d8db",
            "serverSideAds":     "true",
            "constraints":       "",
            "drmCapabilities":   "",
            "blockingMode":      "",
            "clientTime":        client_time,
        }

        try:
            resp = self.session.get(
                _BOOT_URL,
                headers=self._base_headers,
                params=params,
                timeout=self.get_timeout(),
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error(f"Pluto boot request failed: {exc}")
            return False

        token = data.get("sessionToken")
        if not token:
            self.logger.error(
                f"Pluto boot response contained no sessionToken. "
                f"Response keys: {list(data.keys())}"
            )
            return False

        self._session_token   = token
        self._session_id      = data.get("sessionID", str(uuid.uuid4()))
        self._stitcher_host   = (data.get("servers") or {}).get("stitcher", "")
        self._stitcher_params = data.get("stitcherParams", "")
        self._session_expiry  = datetime.now(timezone.utc).timestamp() + _SESSION_TTL_SECONDS

        self.logger.info(
            f"Pluto session refreshed — region={self.region}  "
            f"stitcher={self._stitcher_host}  "
            f"expires in {_SESSION_TTL_SECONDS // 60} min"
        )
        return True

    # ── Stream URL construction ───────────────────────────────────────────────

    def _build_stream_url(self, channel: dict) -> str:
        """
        Build v2 HLS stream URL from boot data + channel.stitched.path.
        Matches the working implementation exactly:
          {servers.stitcher}/v2{stitched.path}?{stitcherParams}&jwt={sessionToken}&masterJWTPassthrough=true
        """
        stitched_path = (channel.get("stitched") or {}).get("path", "")
        if not stitched_path or not self._stitcher_host:
            return ""

        return (
            f"{self._stitcher_host}/v2{stitched_path}"
            f"?{self._stitcher_params}"
            f"&jwt={self._session_token}"
            f"&masterJWTPassthrough=true"
        )

    # ── Channel / category fetch ──────────────────────────────────────────────

    def _get_categories(self, channel_headers: dict) -> Dict[str, str]:
        """Map channel_id → category name."""
        try:
            resp = self.session.get(
                _CATS_URL,
                headers=channel_headers,
                timeout=self.get_timeout(),
            )
            resp.raise_for_status()
            result: Dict[str, str] = {}
            for elem in resp.json().get("data", []):
                category = elem.get("name", "General")
                for cid in elem.get("channelIDs", []):
                    result[cid] = category
            self.logger.info(f"Loaded {len(result)} Pluto channel categories")
            return result
        except Exception as exc:
            self.logger.warning(f"Error fetching Pluto categories: {exc}")
            return {}

    # ── Public interface ──────────────────────────────────────────────────────

    def get_channels(self) -> List[Dict[str, Any]]:
        """Return Pluto TV channels with v2 anonymous stream URLs."""
        if not self._get_session():
            return []

        channel_headers = {
            "accept":           "*/*",
            "accept-language":  "en-US,en;q=0.9",
            "authorization":    f"Bearer {self._session_token}",
            "origin":           "https://pluto.tv",
            "referer":          "https://pluto.tv/",
            "user-agent":       self._base_headers["user-agent"],
        }

        forwarded = _REGION_IPS.get(self.region, "")
        if forwarded:
            channel_headers["X-Forwarded-For"] = forwarded

        channel_params = {
            "channelIds": "",
            "offset":     "0",
            "limit":      "1000",
            "sort":       "number:asc",
        }

        try:
            resp = self.session.get(
                _CHANNELS_URL,
                params=channel_params,
                headers=channel_headers,
                timeout=self.get_timeout(),
            )
            resp.raise_for_status()
            channel_data = resp.json().get("data", [])
        except Exception as exc:
            self.logger.error(f"Error fetching Pluto channel list: {exc}")
            return []

        if not channel_data:
            self.logger.error("No channel data received from Pluto")
            return []

        categories = self._get_categories(channel_headers)
        processed: List[Dict[str, Any]] = []

        for ch in channel_data:
            try:
                channel_id = ch.get("id")
                name       = ch.get("name")
                number     = ch.get("number", 0)
                summary    = ch.get("summary", "")

                if not channel_id or not name:
                    continue

                # Logo — prefer colorLogoPNG, fall back to first image
                logo = ""
                images = ch.get("images", [])
                for img in images:
                    if img.get("type") == "colorLogoPNG":
                        logo = img.get("url", "")
                        break
                if not logo and images:
                    logo = images[0].get("url", "")

                group      = categories.get(channel_id, "General")
                stream_url = self._build_stream_url(ch)

                if not stream_url:
                    self.logger.debug(f"No stream URL for Pluto channel: {name}")
                    continue

                channel_info = {
                    "id":          str(channel_id),
                    "name":        name,
                    "stream_url":  stream_url,
                    "logo":        logo,
                    "group":       group,
                    "number":      int(number) if number else None,
                    "description": (
                        f"Pluto TV channel: {name}"
                        + (f" - {summary}" if summary else "")
                    ),
                    "language":    "en",
                }

                if self.validate_channel(channel_info):
                    processed.append(self.normalize_channel(channel_info))

            except Exception as exc:
                self.logger.warning(f"Error processing Pluto channel: {exc}")
                continue

        self.logger.info(
            f"Successfully processed {len(processed)} Pluto channels "
            f"(region: {self.region})"
        )
        return processed

    def get_epg_data(self) -> dict:
        """EPG handled by aggregator."""
        return {}


    # ── VOD ───────────────────────────────────────────────────────────────────

    def _api_headers(self) -> dict:
        """Headers for the authorized JSON APIs."""
        headers = {
            "accept":          "application/json",
            "accept-language": "en-US,en;q=0.9",
            "authorization":   f"Bearer {self._session_token}",
            "origin":          "https://pluto.tv",
            "referer":         "https://pluto.tv/",
            "user-agent":      self._base_headers["user-agent"],
        }

        forwarded = _REGION_IPS.get(self.region, "")
        if forwarded:
            headers["X-Forwarded-For"] = forwarded

        return headers

    @staticmethod
    def _image_url(url: str) -> str:
        """Make a catalogue image path absolute."""
        url = (url or "").strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return f"{_IMAGE_HOST}{url}"
        return url

    def _vod_image(self, item: dict) -> str:
        """Poster for a VOD item: portrait first, then 16:9, then whatever is there."""
        covers = item.get("covers") or []
        for ratio in ("347:500", "16:9"):
            for cover in covers:
                src = cover.get("url") or cover.get("path")
                if cover.get("aspectRatio") == ratio and src:
                    return self._image_url(src)

        if covers:
            return self._image_url(covers[0].get("url") or covers[0].get("path") or "")
        return ""

    @staticmethod
    def _secs(ms) -> int:
        """Catalogue durations are milliseconds."""
        try:
            return max(0, int(ms) // 1000)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _is_series(item: dict) -> bool:
        """A title is a show when it says so or carries season numbers."""
        return str(item.get("type", "")).lower() == "series" or bool(item.get("seasonsNumbers"))

    def get_vod(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return Pluto's on-demand movies and series."""
        empty = {"movies": [], "series": []}
        if not self._get_session():
            return empty

        # offset is the category page size on this endpoint, 1000 gets them all
        try:
            resp = self.session.get(
                _VOD_CATS_URL,
                headers=self._api_headers(),
                params={"includeItems": "true", "deviceType": "web", "offset": "1000", "limit": "1000"},
                timeout=(10, 120),
            )
            resp.raise_for_status()
            categories = resp.json().get("categories") or []
        except Exception as exc:
            self.logger.error(f"Error fetching Pluto VOD categories: {exc}")
            return empty

        items = [item for cat in categories for item in (cat.get("items") or [])]

        # some regions label nothing as a film; there every non-series title counts as one
        loose = not any(str(i.get("type", "")).lower() in _FILM_TYPES for i in items)

        movies: List[Dict[str, Any]] = []
        series: List[Dict[str, Any]] = []
        seen:   set                  = set()

        for item in items:
            item_id = str(item.get("_id") or item.get("id") or "")
            name    = (item.get("name") or "").strip()
            if not item_id or not name or item_id in seen:
                continue

            genre = (item.get("genre") or "").strip()
            base  = {
                "id":   item_id,
                "name": name,
                "logo": self._vod_image(item),
                "plot": (item.get("description") or item.get("summary") or "").strip(),
            }

            if self._is_series(item):
                seen.add(item_id)
                series.append({**base, "group": genre or "Shows"})

            elif loose or str(item.get("type", "")).lower() in _FILM_TYPES:
                seen.add(item_id)
                movies.append({
                    **base,
                    "group":         genre or "Movies",
                    "stream_url":    f"/play/pluto/vod:{item_id}",
                    "duration_secs": self._secs(item.get("duration")),
                })

        self.logger.info(f"Pluto VOD: {len(movies)} movies, {len(series)} series")
        return {"movies": movies, "series": series}

    def get_series_episodes(self, series_id: str) -> Dict[int, List[Dict[str, Any]]]:
        """Return a Pluto series' episodes by season."""
        if not series_id or not self._get_session():
            return {}

        try:
            resp = self.session.get(
                _VOD_SERIES_URL.format(series_id=urllib.parse.quote(series_id, safe="")),
                headers=self._api_headers(),
                params={"includeItems": "true", "deviceType": "web"},
                timeout=self.get_timeout(),
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.logger.error(f"Error fetching Pluto series {series_id}: {exc}")
            return {}

        seasons: Dict[int, List[Dict[str, Any]]] = {}
        for season in data.get("seasons") or []:
            try:
                season_num = int(season.get("number"))
            except (TypeError, ValueError):
                continue

            episodes = []
            for ep in season.get("episodes") or []:
                ep_id = str(ep.get("_id") or ep.get("id") or "")
                if not ep_id:
                    continue

                try:
                    ep_num = int(ep.get("number") or 0)
                except (TypeError, ValueError):
                    ep_num = 0

                episodes.append({
                    "id":            ep_id,
                    "title":         (ep.get("name") or "").strip(),
                    "episode_num":   ep_num,
                    "stream_url":    f"/play/pluto/vod:{ep_id}",
                    "plot":          (ep.get("description") or ep.get("summary") or "").strip(),
                    "logo":          self._vod_image(ep),
                    "duration_secs": self._secs(ep.get("duration")),
                })

            if episodes:
                seasons[season_num] = episodes

        return seasons

    def resolve_stream(self, channel_id: str) -> Optional[str]:
        """Resolve /play/pluto/vod:<id> to a stitched HLS URL on a fresh session."""
        if not channel_id.startswith("vod:"):
            return None

        item_id = channel_id[4:]
        if not item_id or not self._get_session() or not self._stitcher_host:
            return None

        # movies and episodes both stitch as episodes; the full param set avoids the "unavailable" slate
        return (
            f"{self._stitcher_host}/v2/stitch/hls/episode/{urllib.parse.quote(item_id, safe='')}/master.m3u8"
            f"?{(self._stitcher_params or '').lstrip('?')}"
            f"&jwt={self._session_token}"
            f"&masterJWTPassthrough=true"
            f"&includeExtendedEvents=true"
        )
"""
Pluto Provider Implementation
==============================
Pluto TV API v2 — effective 2026-01-26, v1 streams are dead.
"""

import requests
import json
import uuid
import os
from datetime import datetime
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

# Country code implied by each region key (used in stream URL params)
_REGION_COUNTRY: Dict[str, str] = {
    "local":   "US",
    "uk":      "GB",
    "ca":      "CA",
    "fr":      "FR",
    "us_east": "US",
    "us_west": "US",
}

# Stream CDN host (HLS live channels)
_STITCH_HOST = (
    "https://cfd-v4-service-channel-stitcher-use1-1.prd.pluto.tv"
)

# Boot / channel-list endpoints
_BOOT_URL     = "https://boot.pluto.tv/v4/start"
_CHANNELS_URL = "https://service-channels.clusters.pluto.tv/v2/guide/channels"
_CATS_URL     = "https://service-channels.clusters.pluto.tv/v2/guide/categories"

_APP_VERSION  = "9.0.0-111b2b9dc00bd0bea9030b30662159ed9e7c8bc6"

# Session TTL — refresh token before Pluto's ~24 h JWT expiry
_SESSION_TTL_SECONDS = 55 * 60   # 55 minutes


class PlutoProvider(BaseProvider):
    """Provider for Pluto TV channels (v2 authenticated streams)."""

    def __init__(self):
        super().__init__("pluto")

        self.region   = os.getenv("PLUTO_REGION", "us_west")
        self.username = (os.getenv("PLUTO_USERNAME") or "").strip()
        self.password = (os.getenv("PLUTO_PASSWORD") or "").strip()

        if not self.username or not self.password:
            self.logger.error(
                "PLUTO_USERNAME and PLUTO_PASSWORD are required as of 2026-01-26. "
                "Pluto TV v2 streams will be unavailable without credentials."
            )

        self.device_id = str(uuid.uuid1())
        self._country  = _REGION_COUNTRY.get(self.region, "US")

        # Cached session fields
        self._session_token:  Optional[str]   = None
        self._user_id:        Optional[str]   = None
        self._session_id:     Optional[str]   = None
        self._session_expiry: float           = 0.0

        self._base_headers = {
            "authority":        "boot.pluto.tv",
            "accept":           "*/*",
            "accept-language":  "en-US,en;q=0.9",
            "origin":           "https://pluto.tv",
            "referer":          "https://pluto.tv/",
            "user-agent":       self.get_user_agent(),
        }
        forwarded = _REGION_IPS.get(self.region, "")
        if forwarded:
            self._base_headers["X-Forwarded-For"] = forwarded

    # ── Session management ────────────────────────────────────────────────────

    def _get_session(self) -> bool:
        """
        Obtain (or renew) a session via boot.pluto.tv/v4/start.

        Stores ``_session_token`` (= JWT), ``_user_id``, ``_session_id``.
        Returns True on success.
        """
        if self._session_token and datetime.now().timestamp() < self._session_expiry:
            return True

        if not self.username or not self.password:
            self.logger.error(
                "Cannot authenticate with Pluto TV — credentials missing. "
                "Set PLUTO_USERNAME and PLUTO_PASSWORD."
            )
            return False

        client_id  = str(uuid.uuid4())
        session_id = str(uuid.uuid4())

        params = {
            "appName":           "web",
            "appVersion":        _APP_VERSION,
            "deviceVersion":     "124.0.0",
            "deviceModel":       "web",
            "deviceMake":        "chrome",
            "deviceType":        "web",
            "clientID":          client_id,
            "clientModelNumber": "1.0.0",
            "serverSideAds":     "true",
            "drmCapabilities":   "widevine:L3",
            "blockingMode":      "",
            "notificationVersion": "1",
            "appLaunchCount":    "",
            "lastAppLaunchDate": "",
            "username":          self.username,
            "password":          self.password,
        }

        try:
            response = requests.get(
                _BOOT_URL,
                headers=self._base_headers,
                params=params,
                timeout=self.get_timeout(),
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            self.logger.error(f"Pluto boot request failed: {exc}")
            return False

        token = data.get("sessionToken")
        if not token:
            self.logger.error(
                f"Pluto boot response contained no sessionToken. "
                f"Check credentials. Response keys: {list(data.keys())}"
            )
            return False

        self._session_token  = token
        self._user_id        = data.get("userId", "")
        # Prefer a session ID from the response; fall back to the UUID we sent
        self._session_id     = data.get("sessionID") or session_id
        self._session_expiry = datetime.now().timestamp() + _SESSION_TTL_SECONDS

        self.logger.info(
            f"Pluto session refreshed — userId={self._user_id!r}  "
            f"region={self.region}  expires in {_SESSION_TTL_SECONDS // 60} min"
        )
        return True

    # ── Stream URL construction ───────────────────────────────────────────────

    def _build_stream_url(self, channel_id: str) -> str:
        """
        Return a v2 HLS stream URL for the given Pluto channel ID.

        Path change (2026-01-26):  /stitch/hls/  →  /v2/stitch/hls/
        New required params:       jwt, userId, sessionID, sid,
                                   country, marketingRegion
        """
        sid = str(uuid.uuid4())
        return (
            f"{_STITCH_HOST}/v2/stitch/hls/channel/{channel_id}/master.m3u8"
            f"?advertisingId="
            f"&appName=web"
            f"&appVersion={_APP_VERSION}"
            f"&clientID={self.device_id}"
            f"&clientModelNumber=1.0.0"
            f"&country={self._country}"
            f"&deviceDNT=0"
            f"&deviceId={self.device_id}"
            f"&deviceMake=chrome"
            f"&deviceModel=web"
            f"&deviceType=web"
            f"&deviceVersion=124.0.0"
            f"&marketingRegion={self._country}"
            f"&serverSideAds=true"
            f"&sessionID={self._session_id}"
            f"&sid={sid}"
            f"&userId={self._user_id}"
            f"&jwt={self._session_token}"
        )

    # ── Channel / category fetch ──────────────────────────────────────────────

    def _get_categories(self, headers: dict, params: dict) -> Dict[str, str]:
        """Map channel_id → category name."""
        try:
            resp = requests.get(
                _CATS_URL,
                params=params,
                headers=headers,
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
        """Return Pluto TV channels with v2 authenticated stream URLs."""
        if not self._get_session():
            return []

        channel_headers = {
            "authority":        "service-channels.clusters.pluto.tv",
            "accept":           "*/*",
            "accept-language":  "en-US,en;q=0.9",
            "authorization":    f"Bearer {self._session_token}",
            "origin":           "https://pluto.tv",
            "referer":          "https://pluto.tv/",
            "user-agent":       self.get_user_agent(),
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
            resp = requests.get(
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

        categories = self._get_categories(channel_headers, channel_params)
        processed: List[Dict[str, Any]] = []

        for ch in channel_data:
            try:
                channel_id = ch.get("id")
                name       = ch.get("name")
                number     = ch.get("number", 0)
                summary    = ch.get("summary", "")

                if not channel_id or not name:
                    continue

                # Logo — prefer colorLogoPNG
                logo = ""
                for img in ch.get("images", []):
                    if img.get("type") == "colorLogoPNG":
                        logo = img.get("url", "")
                        break

                group      = categories.get(channel_id, "General")
                stream_url = self._build_stream_url(channel_id)

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
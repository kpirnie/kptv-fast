"""
Roku Provider Implementation
Channel list from i.mjh.nz with apsattv.com M3U fallback
Streams play through the jmp2.uk redirector (jmp2.uk/rok-<id>.m3u8)
"""

import re
import time
from typing import List, Dict, Any
from .base_provider import BaseProvider


class RokuProvider(BaseProvider):
    """Provider for The Roku Channel - i.mjh.nz list first, apsattv fallback"""

    CHANNELS_JSON_URL = "https://i.mjh.nz/Roku/.channels.json"
    FALLBACK_M3U_URL  = "https://www.apsattv.com/rok.m3u"
    PLAYBACK_URL      = "https://jmp2.uk/rok-{channel_id}.m3u8"

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

    def _get_channels_from_api(self) -> List[Dict[str, Any]]:
        """Fetch channel list from i.mjh.nz; streams play through jmp2.uk."""
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

        for channel_id, ch in channels_data.items():
            groups = ch.get("groups", [])
            group  = groups[0] if groups else "The Roku Channel"
            name   = ch.get("name", "Unknown")

            channel = {
                "id":          f"roku-{channel_id}",
                "name":        name,
                "stream_url":  self.PLAYBACK_URL.format(channel_id=channel_id),
                "logo":        ch.get("logo", ""),
                "group":       group,
                "number":      ch.get("chno"),
                "description": f"The Roku Channel: {name}",
                "language":    "en",
            }
            if self.validate_channel(channel):
                channels.append(self.normalize_channel(channel))

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
            channels = self._get_channels_from_fallback()

        if channels:
            self._channels_cache = channels
            self._cache_expiry   = time.time() + self._cache_duration

        return channels

    def clear_cache(self) -> None:
        self._cache_expiry = 0
        
    def get_epg_data(self) -> Dict:
        return {}
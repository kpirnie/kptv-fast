"""
Whale TV+ Provider
Uses the browser-based API flow discovered via HAR analysis:
  1. GET /api/v1/auth/access?apiToken=<key>&langCode=<lang> → token + areaCode
  2. GET /api/device/browser/v1/category/channels?langCode=<lang>&countryCode=<cc> → channels (with chlUrl)
Fallback: apsattv.com M3U files
"""

import logging
import os
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Hardcoded API token extracted from watch.whaletvplus.com JS bundle
_API_TOKEN = "4ef13b5f3d2744e3b0a569feb8dde298"
_BASE_URL = "https://rlaxx.zeasn.tv/livetv"

# Cache
_cache: dict = {}
_cache_ttl = 3600  # seconds


def _is_cache_valid(key: str) -> bool:
    if key not in _cache:
        return False
    return time.time() - _cache[key]["ts"] < _cache_ttl


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Origin": "https://watch.whaletvplus.com",
        "Referer": "https://watch.whaletvplus.com/",
        "Accept": "application/json, text/plain, */*",
    })
    return session


def _auth(session: requests.Session, lang: str = "en") -> Optional[dict]:
    """Step 1: Exchange API token for a session token + areaCode."""
    url = f"{_BASE_URL}/api/v1/auth/access"
    params = {"uuid": "1", "apiToken": _API_TOKEN, "langCode": lang}
    try:
        resp = session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errorCode") not in (0, "0"):
            logger.error("Whale auth error: %s", data.get("errorMsg"))
            return None
        return data.get("data") or data
    except Exception as exc:
        logger.error("Whale auth request failed: %s", exc)
        return None


def _fetch_channels(
    session: requests.Session,
    token: str,
    lang: str = "en",
    country: str = "US",
) -> list:
    """Step 2: Fetch all category/channel data."""
    url = f"{_BASE_URL}/api/device/browser/v1/category/channels"
    params = {"langCode": lang, "countryCode": country.upper()}
    headers = {"token": token}
    try:
        resp = session.get(url, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errorCode") not in (0, "0"):
            logger.error("Whale channels error: %s", data.get("errorMsg"))
            return []
        categories = data.get("data") or []
        channels = []
        for cat in categories:
            for ch in cat.get("channels", []):
                ch["_category"] = cat.get("ctgName", "")
                channels.append(ch)
        return channels
    except Exception as exc:
        logger.error("Whale channels request failed: %s", exc)
        return []


def _channels_to_m3u(channels: list, provider_name: str = "WhaleTVPlus") -> str:
    """Convert API channel list to M3U playlist string."""
    lines = ["#EXTM3U"]
    for ch in channels:
        chl_id = ch.get("chlId", "")
        name = ch.get("chlName", "unknown")
        url = ch.get("chlUrl", "")
        logo = ch.get("imageIdentifier", "")
        group = ch.get("_category", provider_name)
        chl_num = ch.get("chlNum", "")

        if not url:
            continue

        # Build logo URL using the CDN pattern from the JS source
        if logo:
            logo_url = (
                f"https://d3b6luslimvglo.cloudfront.net/images/79/rlaxximages/"
                f"channels-rescaled/icon-white/{logo}_white.png"
            )
        else:
            logo_url = ""

        tvg_id = f"whale-{chl_id}" if chl_id else ""
        tvg_chno = f' tvg-chno="{chl_num}"' if chl_num else ""

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}"{tvg_chno} '
            f'tvg-logo="{logo_url}" group-title="{group}",{name}'
        )
        lines.append(url)

    return "\n".join(lines)


# ── M3U fallback helpers ────────────────────────────────────────────────────

def _fetch_m3u_fallback(country: str) -> str:
    """Try per-country then global M3U from apsattv.com."""
    cc = country.lower()
    urls = [
        f"https://www.apsattv.com/whaletvplus_{cc}.m3u",
        "https://www.apsattv.com/whaletvplus_all.m3u",
    ]
    for url in urls:
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200 and "#EXTM3U" in resp.text:
                logger.info("Whale: using M3U fallback %s", url)
                return resp.text
        except Exception as exc:
            logger.warning("Whale M3U fallback %s failed: %s", url, exc)
    return ""


# ── Public API ──────────────────────────────────────────────────────────────

class WhaleTVProvider:
    """
    Whale TV+ provider.

    Environment variables:
        WHALE_COUNTRY   Comma-separated ISO country codes (default: us)
                        e.g. WHALE_COUNTRY=us,gb,ca
        WHALE_LANG      Language code (default: en)
    """

    name = "whale"
    display_name = "Whale TV+"

    def __init__(self):
        raw = os.environ.get("WHALE_COUNTRY", "us")
        self.countries = [c.strip().upper() for c in raw.split(",") if c.strip()]
        if not self.countries:
            self.countries = ["US"]
        self.lang = os.environ.get("WHALE_LANG", "en")

    def get_channels(self) -> list:
        cache_key = f"whale_channels_{'_'.join(self.countries)}"
        if _is_cache_valid(cache_key):
            return _cache[cache_key]["data"]

        all_channels = []
        session = _make_session()

        # Authenticate once
        auth_data = _auth(session, self.lang)
        if auth_data:
            token = auth_data.get("token") or ""
            for country in self.countries:
                logger.info("Whale: fetching channels for country=%s", country)
                channels = _fetch_channels(session, token, self.lang, country)
                if channels:
                    all_channels.extend(channels)
                else:
                    logger.warning(
                        "Whale: no channels from API for %s, trying M3U fallback", country
                    )
                    m3u = _fetch_m3u_fallback(country)
                    if m3u:
                        all_channels.extend(_parse_m3u(m3u))
        else:
            logger.warning("Whale: auth failed, falling back to M3U")
            for country in self.countries:
                m3u = _fetch_m3u_fallback(country)
                if m3u:
                    all_channels.extend(_parse_m3u(m3u))

        # Deduplicate by URL
        seen = set()
        unique = []
        for ch in all_channels:
            url = ch.get("chlUrl") or ch.get("url", "")
            if url and url not in seen:
                seen.add(url)
                unique.append(ch)

        _cache[cache_key] = {"ts": time.time(), "data": unique}
        return unique

    def get_m3u(self) -> str:
        cache_key = f"whale_m3u_{'_'.join(self.countries)}"
        if _is_cache_valid(cache_key):
            return _cache[cache_key]["data"]

        channels = self.get_channels()
        if not channels:
            logger.error("Whale: no channels available")
            return "#EXTM3U\n"

        # Channels may be raw API dicts or already-normalised dicts from M3U parse
        m3u = _channels_to_m3u(channels, self.display_name)
        _cache[cache_key] = {"ts": time.time(), "data": m3u}
        return m3u


# ── M3U parser (used when API falls back to .m3u files) ─────────────────────

def _parse_m3u(content: str) -> list:
    """Parse an M3U file into a list of channel dicts compatible with _channels_to_m3u."""
    channels = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            info = line
            # Extract attributes
            name_match = re.search(r',(.+)$', info)
            name = name_match.group(1).strip() if name_match else "Unknown"

            tvg_id = re.search(r'tvg-id="([^"]*)"', info)
            tvg_logo = re.search(r'tvg-logo="([^"]*)"', info)
            group = re.search(r'group-title="([^"]*)"', info)
            tvg_chno = re.search(r'tvg-chno="([^"]*)"', info)

            # Next non-empty line is the stream URL
            url = ""
            j = i + 1
            while j < len(lines):
                candidate = lines[j].strip()
                if candidate and not candidate.startswith("#"):
                    url = candidate
                    i = j
                    break
                j += 1

            if url:
                channels.append({
                    "chlId": (tvg_id.group(1) if tvg_id else "").replace("whale-", ""),
                    "chlName": name,
                    "chlUrl": url,
                    "imageIdentifier": "",
                    "chlNum": tvg_chno.group(1) if tvg_chno else "",
                    "_category": group.group(1) if group else "Whale TV+",
                    "_logo": tvg_logo.group(1) if tvg_logo else "",
                })
        i += 1
    return channels
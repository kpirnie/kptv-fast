"""
Vizio WatchFree+ Provider Implementation
Uses the public WatchFree+ guide API (no auth, clear HLS)
"""

import time
import urllib.parse
from typing import List, Dict, Any
from .base_provider import BaseProvider


class VizioProvider(BaseProvider):
    """Provider for Vizio WatchFree+ via the native guide API"""

    CHANNELS_URL = "https://watchfreeplus-epg-prod.smartcasttv.com/api/channels"

    # Privacy-neutral values for the {MACRO} ad placeholders in stream URLs
    AD_MACROS = {
        'ADID':          '00000000-0000-0000-0000-000000000000',
        'USPRIVACY':     '1---',
        'IFATYPE':       'aaid',
        'LMT':           '0',
        'TARGETOPT':     'False',
        'APP_NAME':      'VIZIO',
        'APP_BUNDLE':    'com.vizio.vue.launcher',
        'APP_STORE_URL': 'https://play.google.com/store/apps/details?id=com.vizio.vue.launcher',
        'DOMAIN':        'https://www.vizio.com',
        'DNT':           '0',
        'COPPA':         '0',
        'DEVICE_MAKE':   'Google',
        'WIDTH':         '1080',
        'HEIGHT':        '1920',
        'DEVICE_MODEL':  'Pixel 7',
        'APP_VERSION':   '5.0.0',
        'DEVICE_TYPE':   'mobile',
        'SKIPPABLE':     '1',
    }

    def __init__(self):
        super().__init__("vizio")

        self.headers = {
            'User-Agent': 'okhttp/4.12.0',
            'Accept':     'application/json',
        }

        self._channels_cache: List[Dict[str, Any]] = []
        self._cache_expiry: float = 0
        self._cache_duration: int = 3600

    def _expand_macros(self, url: str) -> str:
        """Decode the URL, then substitute each {MACRO} with a URL-encoded value."""
        decoded = urllib.parse.unquote(url)
        for key, value in self.AD_MACROS.items():
            decoded = decoded.replace('{' + key + '}', urllib.parse.quote(value, safe=''))
        return decoded

    def _get_channels_from_api(self) -> List[Dict[str, Any]]:
        try:
            r = self.make_request('GET', self.CHANNELS_URL, headers=self.headers)
            r.raise_for_status()
            raw_channels = r.json().get('channels') or []
        except Exception as e:
            self.logger.error(f"Vizio: channel list request failed: {e}")
            return []

        channels = []
        for ch in raw_channels:
            ch_id = ch.get('channelId')
            name  = (ch.get('channelName') or '').strip()
            if not ch_id or not name:
                continue

            # Token-gated channels (e.g. NFL Channel) can't be played anonymously
            if ch.get('tokenUrl'):
                continue

            urls = ch.get('channelUrls') or []
            if not urls or not isinstance(urls[0], str):
                continue

            stream_url = self._expand_macros(urls[0])
            if not stream_url.startswith('http'):
                continue

            channel = {
                'id':          f"vizio-{ch_id}",
                'name':        name,
                'stream_url':  stream_url,
                'logo':        ch.get('channelIcon') or ch.get('portraitIcon') or ch.get('bwIcon') or '',
                'group':       ch.get('category') or 'Vizio WatchFree+',
                'number':      ch.get('channelNumber'),
                'description': (ch.get('channelDescription') or '').strip() or f"Vizio WatchFree+: {name}",
                'language':    'en',
            }

            if self.validate_channel(channel):
                channels.append(self.normalize_channel(channel))

        self.logger.info(f"Vizio: {len(channels)} channels from API")
        return channels

    # ── BaseProvider interface ────────────────────────────────────────────────

    def get_channels(self) -> List[Dict[str, Any]]:
        if time.time() < self._cache_expiry and self._channels_cache:
            return self._channels_cache

        channels = self._get_channels_from_api()

        if channels:
            self._channels_cache = channels
            self._cache_expiry   = time.time() + self._cache_duration

        return channels

    def clear_cache(self) -> None:
        self._cache_expiry = 0

    def get_epg_data(self) -> Dict:
        """EPG handled by aggregator."""
        return {}
        
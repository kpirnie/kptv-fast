"""
TCL TV+ Provider Implementation
Uses the native TCL TV+ (ideonow) API; stream URLs are resolved at play time via /play/tclplus/<id>
"""

import os
import random
import string
import time
from typing import List, Dict, Any, Optional
from .base_provider import BaseProvider


class TCLPlusProvider(BaseProvider):
    """Provider for TCL TV+ via the native ideonow gateway API"""

    BASE_URL   = "https://gateway-prod.ideonow.com"
    IMAGE_BASE = "https://tcl-channel-cdn.ideonow.com"
    ORIGIN     = "https://tcltv.plus"

    def __init__(self):
        super().__init__("tclplus")

        # Comma-separated US/CA
        raw = os.getenv('TCLPLUS_COUNTRY', 'us')
        self.countries = [c.strip().upper() for c in raw.split(',') if c.strip().upper() in ('US', 'CA')] or ['US']

        # Web-style device id: <ms timestamp>-<5 random chars>
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))
        self.device_id = f"{int(time.time() * 1000)}-{suffix}"

        self.headers = {
            'Accept':     'application/json, text/plain, */*',
            'Origin':     self.ORIGIN,
            'Referer':    f"{self.ORIGIN}/",
            'User-Agent': self.get_user_agent(),
        }

        # bundle_id -> {"source", "media", "country"} for play-time resolution
        self._streams: Dict[str, Dict[str, str]] = {}

        self._channels_cache: List[Dict[str, Any]] = []
        self._cache_expiry: float = 0
        self._cache_duration: int = 3600

    def _common_params(self, country: str) -> Dict[str, str]:
        return {
            'userId':       self.device_id,
            'device_type':  'web',
            'device_model': 'web',
            'device_id':    self.device_id,
            'app_version':  '1.0',
            'country_code': country,
            'state_code':   'OH',
        }

    def _get_json(self, path: str, params: Dict[str, Any]) -> Dict:
        r = self.make_request('GET', f"{self.BASE_URL}{path}", headers=self.headers, params=params)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            self.logger.debug(f"TCL TV+: non-object response from {path}: {str(data)[:200]}")
            return {}
        return data

    def _fix_url(self, url: Optional[str]) -> str:
        if not url:
            return ''
        return f"{self.IMAGE_BASE}{url}" if url.startswith('/') else url

    def _get_channels_from_api(self) -> List[Dict[str, Any]]:
        channels: Dict[str, Dict[str, Any]] = {}
        streams:  Dict[str, Dict[str, str]] = {}

        for country in self.countries:
            try:
                livetab = self._get_json('/api/metadata/v2/livetab', self._common_params(country))
            except Exception as e:
                self.logger.error(f"TCL TV+: livetab request failed for {country}: {e}")
                continue

            for cat in livetab.get('lines') or []:
                if not isinstance(cat, dict):
                    continue
                cat_id   = cat.get('id')
                cat_name = cat.get('name') or 'TCL TV+'
                if cat_id is None:
                    continue

                params = self._common_params(country)
                params['category_id'] = cat_id
                try:
                    payload = self._get_json('/api/metadata/v1/epg/programlist/by/category', params)
                except Exception as e:
                    self.logger.warning(f"TCL TV+: category {cat_name} failed: {e}")
                    continue

                for ch in payload.get('channels') or []:
                    if not isinstance(ch, dict):
                        continue
                    bundle_id = str(ch.get('bundle_id') or ch.get('id') or '')
                    name      = (ch.get('name') or '').strip()
                    media     = ch.get('media') or ''
                    if not bundle_id or not name or not media or bundle_id in channels:
                        continue

                    streams[bundle_id] = {
                        'source':  ch.get('source') or '',
                        'media':   media,
                        'country': country,
                    }

                    channel = {
                        'id':          f"tclplus-{bundle_id}",
                        'name':        name,
                        'stream_url':  f"/play/tclplus/{bundle_id}",
                        'logo':        self._fix_url(ch.get('logo_color') or ch.get('logo_white')),
                        'group':       cat_name,
                        'description': (ch.get('description') or '').strip() or f"TCL TV+: {name}",
                        'language':    'en',
                    }

                    if self.validate_channel(channel):
                        channels[bundle_id] = self.normalize_channel(channel)

        if streams:
            self._streams = streams

        self.logger.info(f"TCL TV+: {len(channels)} channels from API")
        return list(channels.values())

    # ── BaseProvider interface ────────────────────────────────────────────────

    def get_channels(self) -> List[Dict[str, Any]]:
        if time.time() < self._cache_expiry and self._channels_cache:
            return self._channels_cache

        channels = self._get_channels_from_api()

        if channels:
            self._channels_cache = channels
            self._cache_expiry   = time.time() + self._cache_duration

        return channels

    def resolve_stream(self, channel_id: str) -> Optional[str]:
        """Ask the gateway to format a playable URL; fall back to the raw media URL."""
        info = self._streams.get(channel_id)
        if info is None:
            self.get_channels()
            info = self._streams.get(channel_id)
        if info is None:
            return None

        payload = {
            'type':       'channel',
            'bundle_id':  channel_id,
            'device_id':  self.device_id,
            'source':     info['source'] or None,
            'stream_url': info['media'],
        }
        params = {
            'country_code': info['country'],
            'app_version':  '3.2.7',
        }

        try:
            r = self.make_request(
                'POST',
                f"{self.BASE_URL}/api/metadata/v1/format-stream-url",
                headers={**self.headers, 'Content-Type': 'application/json'},
                params=params,
                json=payload,
            )
            r.raise_for_status()
            return r.json().get('stream_url') or info['media']
        except Exception as e:
            self.logger.warning(f"TCL TV+: resolve failed for {channel_id}, using raw media URL: {e}")
            return info['media']

    def clear_cache(self) -> None:
        self._cache_expiry = 0

    def get_epg_data(self) -> Dict:
        """EPG handled by aggregator."""
        return {}
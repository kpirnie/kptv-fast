"""
Local Now Provider Implementation
Uses the native Local Now DSP API; stream URLs are resolved at play time via /play/localnow/<id>
US-only: the homepage bootstrap fails from non-US IPs
"""

import base64
import json
import os
import re
import time
from typing import List, Dict, Any, Optional
from urllib.parse import quote
from .base_provider import BaseProvider


class LocalNowProvider(BaseProvider):
    """Provider for Local Now via the native DSP API"""

    HOME_URL          = "https://localnow.com/"
    CHANNELS_PAGE_URL = "https://localnow.com/channels"
    DEFAULT_DSP_HOST  = "data-store-trans-cdn.api.cms.amdvids.com"

    # Known-good fallback market (New York City)
    FALLBACK_DMA    = "501"
    FALLBACK_MARKET = "nyNewYorkCity,pbs-wnet,pbs-wedh,pbs-wliw,pbs-wnjt"

    _CALL_SIGN_RE = re.compile(r'^[WK][A-Z]{2,4}\b')

    def __init__(self):
        super().__init__("localnow")

        self.dma_override    = os.getenv('LOCALNOW_DMA', '').strip()
        self.market_override = os.getenv('LOCALNOW_MARKET', '').strip()

        self.session.headers.update({
            'Accept':     'application/json, text/plain, */*',
            'Origin':     'https://localnow.com',
            'Referer':    'https://localnow.com/',
            'User-Agent': self.get_user_agent(),
        })

        # Runtime bootstrap state
        self._dsp_host: str = self.DEFAULT_DSP_HOST
        self._token_expires: float = 0
        self._dma: Optional[str] = None
        self._market: Optional[str] = None

        # video_id -> slug for play-time resolution
        self._slugs: Dict[str, str] = {}

        self._channels_cache: List[Dict[str, Any]] = []
        self._cache_expiry: float = 0
        self._cache_duration: int = 3600

    # ── Bootstrap ─────────────────────────────────────────────────────────────

    @staticmethod
    def _jwt_exp(token: str) -> Optional[float]:
        try:
            payload = token.split('.')[1]
            payload += '=' * (-len(payload) % 4)
            return float(json.loads(base64.urlsafe_b64decode(payload)).get('exp') or 0) or None
        except Exception:
            return None

    @staticmethod
    def _find(pattern: str, value: Any) -> Optional[str]:
        if not value:
            return None
        m = re.search(pattern, value if isinstance(value, str) else json.dumps(value))
        return m.group(1) if m else None

    def _bootstrap(self) -> bool:
        """Pull DSP host + token (and market if not overridden) from the homepage __NEXT_DATA__."""
        if time.time() < self._token_expires - 120:
            return True

        try:
            r = self.session.get(self.HOME_URL, timeout=self.get_timeout())
            if r.status_code in (403, 451):
                self.logger.warning("Local Now: homepage blocked (non-US IP?)")
                return False
            r.raise_for_status()

            m = re.search(r'<script id="__NEXT_DATA__" type="application/json">\s*(.*?)\s*</script>', r.text, re.S)
            if not m:
                self.logger.warning("Local Now: no __NEXT_DATA__ on homepage (non-US IP?)")
                return False
            next_data = json.loads(m.group(1))

            runtime = next_data.get('runtimeConfig') or {}
            token   = (json.loads(runtime.get('DSP_TOKEN') or '{}')).get('token')
            if not token:
                self.logger.error("Local Now: DSP_TOKEN missing from runtime config")
                return False

            self._dsp_host = runtime.get('DSP_API_URL') or self.DEFAULT_DSP_HOST
            self.session.headers['x-access-token'] = token
            self._token_expires = self._jwt_exp(token) or (time.time() + 12 * 3600)

            # Market: env override, then the server-detected cookies, then NYC
            cookies = (next_data.get('props', {}).get('pageProps', {}) or {}).get('serverCookies', {}) or {}
            sources = [cookies.get('_ln_myMarket'), cookies.get('_ln_myDetectedCity'), cookies.get('_ln_myCity')]

            dma    = next((d for d in (self._find(r'"?dmaId"?\s*[:=]\s*"?(\d+)', s) for s in sources) if d), None)
            market = next((v for v in (self._find(r'"?(?:market|marketSlug|citySlug|slug)"?\s*[:=]\s*"?([A-Za-z0-9_-]+)', s) for s in sources) if v), None)

            pbs = cookies.get('_ln_pbsMarkets')
            if market and isinstance(pbs, str) and pbs.strip():
                market = ','.join(dict.fromkeys([market] + [p.strip() for p in pbs.split(',') if p.strip()]))

            self._dma    = self.dma_override    or dma    or self.FALLBACK_DMA
            self._market = self.market_override or market or self.FALLBACK_MARKET

            self.logger.info(f"Local Now: bootstrapped dma={self._dma} market={self._market}")
            return True

        except Exception as e:
            self.logger.error(f"Local Now: bootstrap failed: {e}")
            return False

    # ── Channels ──────────────────────────────────────────────────────────────

    def _get_channels_from_api(self) -> List[Dict[str, Any]]:
        if not self._bootstrap():
            return []

        try:
            r = self.session.get(
                f"https://{self._dsp_host}/live/epg/US/website",
                params={'dma': self._dma, 'market': self._market},
                timeout=(10, 30),
            )
            r.raise_for_status()
            raw_channels = r.json().get('channels') or []
        except Exception as e:
            self.logger.error(f"Local Now: channel list request failed: {e}")
            return []

        channels = []
        slugs: Dict[str, str] = {}
        seen = set()

        for ch in raw_channels:
            video_id = str(ch.get('video_id') or ch.get('_id') or '').strip()
            name     = (ch.get('name') or '').strip()
            if not video_id or not name or video_id in seen:
                continue
            seen.add(video_id)

            # Skip subscription-locked channels
            access = ch.get('subscription_access') or {}
            if isinstance(access, dict) and access.get('unlocked') is False:
                continue

            slug       = (ch.get('slug') or '').strip()
            genres     = ch.get('genres') or []
            iab_genres = ch.get('iab_genres') or []

            if 'My City' in genres or 'hyperlocal' in slug.lower() or slug.lower().startswith('epg-local-now') or self._CALL_SIGN_RE.search(name):
                group = 'Local News'
            else:
                group = (iab_genres[0] if iab_genres else None) or (genres[0] if genres else None) or 'Local Now'

            slugs[video_id] = slug

            channel = {
                'id':          f"localnow-{video_id}",
                'name':        name,
                'stream_url':  f"/play/localnow/{video_id}",
                'logo':        ch.get('logo') or '',
                'group':       group,
                'number':      ch.get('channel_number'),
                'description': (ch.get('description') or '').strip() or f"Local Now: {name}",
                'language':    'en',
            }

            if self.validate_channel(channel):
                channels.append(self.normalize_channel(channel))

        if slugs:
            self._slugs = slugs

        self.logger.info(f"Local Now: {len(channels)} channels from API")
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

    def resolve_stream(self, channel_id: str) -> Optional[str]:
        """Fetch a session playback URL for the channel."""
        if not self._bootstrap():
            return None

        if channel_id not in self._slugs:
            self.get_channels()
        slug = self._slugs.get(channel_id, '')

        page_url = f"{self.CHANNELS_PAGE_URL}/{slug}" if slug else self.CHANNELS_PAGE_URL
        params = {
            'page_url':          quote(page_url, safe=''),
            'device_devicetype': 'desktop_web',
            'app_version':       '0.0.0',
            'app_bundle':        'web.localnow',
            'ccpa_us_privacy':   '1YNY',
        }

        try:
            r = self.session.get(
                f"https://{self._dsp_host}/video/play/{channel_id}/1920/1080",
                params=params,
                timeout=self.get_timeout(),
            )
            r.raise_for_status()
            data = r.json()
            return data.get('video_m3u8') or data.get('session_m3u8') or None
        except Exception as e:
            self.logger.warning(f"Local Now: resolve failed for {channel_id}: {e}")
            return None

    def clear_cache(self) -> None:
        self._cache_expiry = 0

    def get_epg_data(self) -> Dict:
        """EPG handled by aggregator."""
        return {}
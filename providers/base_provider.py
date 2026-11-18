"""
Base Provider Class - Optimized for Performance with urllib3 compatibility
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import logging
import sys
import re
import time
import uuid
import urllib.parse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

class BaseProvider(ABC):
    """Base class for all streaming service providers - optimized"""

    # per-provider overrides, layered key by key over BASE_AD_MACROS (subclasses set this)
    AD_MACROS: Dict[str, str] = {}

    # default values for ad-tag macros, keys are upper-case and matched case-insensitively
    BASE_AD_MACROS: Dict[str, str] = {
        **dict.fromkeys(('IFA', 'DEVICE_ID', 'ADID', 'DID', 'TV_AD_ID'), '00000000-0000-0000-0000-000000000000'),
        **dict.fromkeys(('IFA_TYPE', 'IFATYPE'), 'aaid'),
        **dict.fromkeys(('US_PRIVACY', 'USPRIVACY', 'CCPA_VALUE'), '1---'),
        **dict.fromkeys(('LMT', 'IS_LAT', 'DNT', 'COPPA', 'GDPR', 'TARGETOPT', 'DNS', 'TARGETAD_ALLOWED'), '0'),
        'COUNTRY': 'US',
        **dict.fromkeys(('VIEWSIZE', 'RESOLUTION'), '1920x1080'),
        'WIDTH': '1920',
        'HEIGHT': '1080',
        'DEVICE_TYPE': 'ctv',
        'DEVICE_LANGUAGE': 'en',
        **dict.fromkeys(('CONTENT_LIVE', 'CONTENT_LIVESTREAM', 'LIVESTREAM'), '1'),
        **dict.fromkeys((
            'GDPR_CONSENT', 'CONSENT', 'GPP', 'GPP_SID',
            'APP_NAME', 'APP_BUNDLE', 'APP_STOREURL', 'APP_STORE_URL', 'APP_DOMAIN', 'APP_CATEGORY', 'DOMAIN',
            'APP_VERSION', 'FIRMWARE_VERSION', 'WEBOS_VERSION', 'DEVICE_MAKE', 'DEVICE_MODEL',
            'IP', 'UA', 'URL', 'FCK', 'PCS', 'NONCE',
            'IC', 'IU', 'IDTYPE', 'PRODQ', 'SCHAIN', 'INV_PARTNER_DOMAIN', 'CTI', 'CC', 'CG',
            'POD_AD_SLOTS', 'POD_AD_SLOT', 'AD_POS', 'AD_POSITION', 'SKIP', 'SKIPPABLE',
            'HOTEL_TYPE', 'HOTELTYPE',
            'IAB_CATEGORY', 'IAB_CONTENT_CATEGORY', 'CONTENT_RATING', 'CONTENT_LANGUAGE', 'CONTENT_NETWORK',
            'CONTENT_ID', 'CONTENT_SEASON', 'CONTENT_EPISODE', 'CONTENT_DURATION', 'GOOGLE_PAL',
        ), ''),
    }

    # matches [KEY], %5BKEY%5D, {KEY} and %7BKEY%7D, each bracket style only pairs with itself
    AD_MACRO_PATTERN = re.compile(
        r'\[([A-Za-z_]+)\]|%5[Bb]([A-Za-z_]+)%5[Dd]|\{([A-Za-z_]+)\}|%7[Bb]([A-Za-z_]+)%7[Dd]'
    )
    
    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"providers.{name}")
        # Set recursion limit to prevent stack overflow
        if sys.getrecursionlimit() < 2000:
            sys.setrecursionlimit(2000)
        
        # Create optimized session with connection pooling
        self.session = self._create_optimized_session()

        # Optional upper bound (seconds) on how long the aggregated channel cache may hold this provider's output
        self.max_cache_age: Optional[int] = None
        
    def _create_optimized_session(self) -> requests.Session:
        """Create an optimized requests session with connection pooling and retries"""
        session = requests.Session()
        
        try:
            # Try newer urllib3 parameter name first
            retry_strategy = Retry(
                total=2,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["HEAD", "GET", "OPTIONS"],  # New parameter name
                backoff_factor=0.3
            )
        except TypeError:
            try:
                # Fall back to older parameter name
                retry_strategy = Retry(
                    total=2,
                    status_forcelist=[429, 500, 502, 503, 504],
                    method_whitelist=["HEAD", "GET", "OPTIONS"],  # Old parameter name
                    backoff_factor=0.3
                )
            except TypeError:
                # If both fail, create a simple retry without method restrictions
                retry_strategy = Retry(
                    total=2,
                    status_forcelist=[429, 500, 502, 503, 504],
                    backoff_factor=0.3
                )
        
        # Configure adapter with connection pooling
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=retry_strategy
        )
        
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Set default headers
        session.headers.update({
            'User-Agent': self.get_user_agent()
        })
        
        return session
    
    @abstractmethod
    def get_channels(self) -> List[Dict[str, Any]]:
        """Get list of available channels"""
        pass
    
    @abstractmethod
    def get_epg_data(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get EPG data for channels"""
        pass
    
    def clear_cache(self) -> None:
        """Expire any provider-level caches so the next fetch hits upstream"""
        pass
    
    def resolve_stream(self, channel_id: str) -> Optional[str]:
        """Resolve a playable stream URL at play time; providers using /play/ routes override this"""
        return None

    def expand_ad_macros(self, url: str, channel_name: str = '') -> str:
        """
        Expand ad-tag macros in a stream URL

        Replaces [KEY], %5BKEY%5D, {KEY} and %7BKEY%7D placeholders (case-insensitive)
        with values from BASE_AD_MACROS, layered with the provider's AD_MACROS.
        Unknown placeholders are left exactly as they are.

        @param url: str The stream URL to expand
        @param channel_name: str Channel name used for the channel/network name macros
        @return str: The URL with known macros filled in, or the original URL on any problem
        """

        # nothing to do for empty or non-string input
        if not url or not isinstance(url, str):
            return url

        # fast path, no placeholder markers at all
        lowered = url.lower()
        if not any(marker in lowered for marker in ('[', '{', '%5b', '%7b')):
            return url

        try:
            # start with the defaults, then layer the provider overrides on top
            macros = dict(self.BASE_AD_MACROS)
            for key, value in self.AD_MACROS.items():
                macros[str(key).upper()] = str(value)

            # dynamic values are computed once per url
            macros['SESSION_ID'] = str(uuid.uuid4())
            macros['CACHEBUSTER'] = str(int(time.time() * 1000))

            # channel name macros
            for key in ('CHANNEL_NAME', 'NETWORK_NAME', 'VIZIO_NETWORK_NAME'):
                macros[key] = channel_name or ''

            def _replace(match: 're.Match') -> str:
                # grab whichever group actually matched
                key = next(group for group in match.groups() if group is not None)
                value = macros.get(key.upper())

                # unknown macro, leave it exactly as it was
                if value is None:
                    return match.group(0)

                return urllib.parse.quote(value, safe='')

            return self.AD_MACRO_PATTERN.sub(_replace, url)

        except Exception as e:
            self.logger.error(f"Error expanding ad macros: {e}")
            return url

    def validate_channel(self, channel: Dict[str, Any]) -> bool:
        """Validate that a channel has required fields"""
        try:
            required_fields = ['id', 'name', 'stream_url']
            
            for field in required_fields:
                if not channel.get(field):
                    return False
                    
            return True
        except Exception:
            return False
    
    def normalize_channel(self, channel: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize channel data to ensure consistent format"""
        try:
            normalized = {}
            
            # Required fields
            normalized['id'] = str(channel.get('id', '')) if channel.get('id') is not None else ''
            normalized['name'] = str(channel.get('name', '')).strip() if channel.get('name') is not None else ''
            normalized['stream_url'] = str(channel.get('stream_url', '')).strip() if channel.get('stream_url') is not None else ''
            # fill in any ad-tag macros in the stream url
            normalized['stream_url'] = self.expand_ad_macros(normalized['stream_url'], normalized['name'])
            
            # Optional fields with defaults
            normalized['logo'] = str(channel.get('logo', '')).strip() if channel.get('logo') else ''
            normalized['group'] = str(channel.get('group', 'General')).strip() if channel.get('group') else 'General'
            normalized['description'] = str(channel.get('description', '')).strip() if channel.get('description') else ''
            normalized['language'] = str(channel.get('language', 'en')).strip() if channel.get('language') else 'en'
            
            # Handle number field
            if channel.get('number') is not None:
                try:
                    normalized['number'] = int(channel['number'])
                except (ValueError, TypeError):
                    pass
            
            # Remove empty strings except for required fields
            return {k: v for k, v in normalized.items() if v != '' or k in ['id', 'name', 'stream_url']}
            
        except Exception as e:
            self.logger.error(f"Error normalizing channel: {e}")
            return channel
    
    def validate_programme(self, programme: Dict[str, Any]) -> bool:
        """Validate that a programme has required fields"""
        try:
            required_fields = ['title', 'start', 'stop']
            return all(programme.get(field) for field in required_fields)
        except Exception:
            return False
    
    def normalize_programme(self, programme: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize programme data to ensure consistent format"""
        try:
            normalized = {}
            
            # Required fields
            normalized['title'] = str(programme.get('title', '')).strip()
            normalized['start'] = str(programme.get('start', '')).strip()
            normalized['stop'] = str(programme.get('stop', '')).strip()
            
            # Optional fields
            if programme.get('description'):
                normalized['description'] = str(programme['description']).strip()
            if programme.get('category'):
                normalized['category'] = str(programme['category']).strip()
            if programme.get('episode'):
                normalized['episode'] = str(programme['episode']).strip()
            
            return {k: v for k, v in normalized.items() if v}
            
        except Exception as e:
            self.logger.error(f"Error normalizing programme: {e}")
            return programme
    
    def get_user_agent(self) -> str:
        """Get a standard user agent string"""
        return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    
    def get_timeout(self) -> tuple:
        """Get standard timeout values for requests (connect, read)"""
        return (10, 30)  # Balanced timeout values
    
    def make_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make HTTP request using the optimized session"""
        try:
            # Set timeout if not provided
            if 'timeout' not in kwargs:
                kwargs['timeout'] = self.get_timeout()
            
            response = self.session.request(method, url, **kwargs)
            return response
        except Exception as e:
            self.logger.error(f"Request failed for {url}: {e}")
            raise
    
    def __del__(self):
        """Cleanup session when provider is destroyed"""
        if hasattr(self, 'session'):
            self.session.close()
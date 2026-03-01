"""
Apsattv Provider Implementations
Individual provider classes for each apsattv.com M3U source.
Each is registered in app.py under its own name in ENABLED_PROVIDERS:

  ENABLED_PROVIDERS=all
  ENABLED_PROVIDERS=pluto,plex,vizio,roku,localnow
"""

import re
import time
from typing import List, Dict, Any, Set
from .base_provider import BaseProvider


class ApsattvBaseProvider(BaseProvider):
    """
    Shared base for all single-URL apsattv.com M3U providers.
    Subclasses define SOURCE_URL, DEFAULT_GROUP, ID_PREFIX,
    and DESCRIPTION_LABEL as class attributes.
    """

    SOURCE_URL: str = ''
    DEFAULT_GROUP: str = ''
    ID_PREFIX: str = ''
    DESCRIPTION_LABEL: str = ''

    def __init__(self, provider_name: str):
        super().__init__(provider_name)

        self.headers = {
            'User-Agent': self.get_user_agent(),
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
        }

        self._channels_cache: List[Dict[str, Any]] = []
        self._cache_expiry: float = 0
        self._cache_duration: int = 3600  # 1 hour

    def _parse_m3u(self, content: str) -> List[Dict[str, Any]]:
        """Parse M3U playlist content into normalised channel dicts."""
        channels: List[Dict[str, Any]] = []
        seen_ids: Set[str] = set()
        lines = content.strip().split('\n')

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if line.startswith('#EXTINF:'):
                try:
                    # Find next non-comment, non-empty line as stream URL
                    url_line = ''
                    j = i + 1
                    while j < len(lines):
                        candidate = lines[j].strip()
                        if candidate and not candidate.startswith('#'):
                            url_line = candidate
                            break
                        j += 1

                    if not url_line:
                        i += 1
                        continue

                    extinf_content = line[8:]  # strip '#EXTINF:'

                    channel_name = ''
                    tvg_id = ''
                    tvg_logo = ''
                    group_title = ''
                    tvg_chno = ''

                    if ',' in extinf_content:
                        attr_part, name_part = extinf_content.split(',', 1)
                        channel_name = name_part.strip()

                        m = re.search(r'tvg-id="([^"]*)"', attr_part)
                        if m:
                            tvg_id = m.group(1)
                        m = re.search(r'tvg-logo="([^"]*)"', attr_part)
                        if m:
                            tvg_logo = m.group(1)
                        m = re.search(r'group-title="([^"]*)"', attr_part)
                        if m:
                            group_title = m.group(1)
                        m = re.search(r'tvg-chno="([^"]*)"', attr_part)
                        if m:
                            tvg_chno = m.group(1)
                    else:
                        channel_name = extinf_content.strip()

                    if not channel_name or not url_line:
                        i = j + 1
                        continue

                    raw_id = (
                        tvg_id
                        if tvg_id
                        else channel_name.lower()
                                         .replace(' ', '-')
                                         .replace('&', 'and')
                                         .replace('/', '-')
                    )
                    channel_id = f"{self.ID_PREFIX}-{raw_id}"

                    # Deduplicate within this source
                    if channel_id in seen_ids:
                        i = j + 1
                        continue
                    seen_ids.add(channel_id)

                    channel = {
                        'id': channel_id,
                        'name': channel_name,
                        'stream_url': url_line,
                        'logo': tvg_logo,
                        'group': group_title or self.DEFAULT_GROUP,
                        'number': int(tvg_chno) if tvg_chno and tvg_chno.isdigit() else None,
                        'description': f"{self.DESCRIPTION_LABEL}: {channel_name}",
                        'language': 'en',
                    }

                    if self.validate_channel(channel):
                        channels.append(self.normalize_channel(channel))

                    i = j + 1

                except Exception as e:
                    self.logger.debug(f"Error parsing M3U line: {e}")
                    i += 1
            else:
                i += 1

        return channels

    def get_channels(self) -> List[Dict[str, Any]]:
        """Fetch, parse, and cache channels from SOURCE_URL."""
        try:
            if time.time() < self._cache_expiry and self._channels_cache:
                self.logger.debug(f"Using cached {self.name} channels")
                return self._channels_cache

            self.logger.info(f"Fetching {self.name} channels")

            response = self.make_request('GET', self.SOURCE_URL, headers=self.headers)
            response.raise_for_status()

            content = response.text
            if not content.strip():
                self.logger.warning(f"Empty response from {self.name}")
                return []

            channels = self._parse_m3u(content)

            if channels:
                self._channels_cache = channels
                self._cache_expiry = time.time() + self._cache_duration
                self.logger.info(f"Successfully processed {len(channels)} {self.name} channels")
            else:
                self.logger.warning(f"No valid channels found for {self.name}")

            return channels

        except Exception as e:
            self.logger.error(f"Error fetching {self.name} channels: {e}")
            return []

    def get_epg_data(self):
        """EPG handled by aggregator."""
        return {}


# ---------------------------------------------------------------------------
# Individual provider classes — one per apsattv.com source
# ---------------------------------------------------------------------------

class VizioProvider(ApsattvBaseProvider):
    """Vizio WatchFree+ channels"""
    SOURCE_URL        = 'https://www.apsattv.com/vizio.m3u'
    DEFAULT_GROUP     = 'Vizio WatchFree+'
    ID_PREFIX         = 'vizio'
    DESCRIPTION_LABEL = 'Vizio WatchFree+'

    def __init__(self):
        super().__init__('vizio')


class RokuProvider(ApsattvBaseProvider):
    """The Roku Channel channels"""
    SOURCE_URL        = 'https://www.apsattv.com/rok.m3u'
    DEFAULT_GROUP     = 'The Roku Channel'
    ID_PREFIX         = 'roku'
    DESCRIPTION_LABEL = 'The Roku Channel'

    def __init__(self):
        super().__init__('roku')


class LocalNowProvider(ApsattvBaseProvider):
    """Local Now channels"""
    SOURCE_URL        = 'https://www.apsattv.com/localnow.m3u'
    DEFAULT_GROUP     = 'Local Now'
    ID_PREFIX         = 'localnow'
    DESCRIPTION_LABEL = 'Local Now'

    def __init__(self):
        super().__init__('localnow')


class TCLProvider(ApsattvBaseProvider):
    """TCL TV channels"""
    SOURCE_URL        = 'https://www.apsattv.com/tcl.m3u'
    DEFAULT_GROUP     = 'TCL TV'
    ID_PREFIX         = 'tcl'
    DESCRIPTION_LABEL = 'TCL TV'

    def __init__(self):
        super().__init__('tcl')

class TCLPlusProvider(ApsattvBaseProvider):
    """TCL TV Plus channels"""
    SOURCE_URL        = 'https://www.apsattv.com/tclplus.m3u'
    DEFAULT_GROUP     = 'TCL TV Plus'
    ID_PREFIX         = 'tclplus'
    DESCRIPTION_LABEL = 'TCL TV Plus'

    def __init__(self):
        super().__init__('tclplus')


class FireTVProvider(ApsattvBaseProvider):
    """Fire TV free channels"""
    SOURCE_URL        = 'https://www.apsattv.com/firetv.m3u'
    DEFAULT_GROUP     = 'Fire TV'
    ID_PREFIX         = 'firetv'
    DESCRIPTION_LABEL = 'Fire TV'

    def __init__(self):
        super().__init__('firetv')


class XiaomiProvider(ApsattvBaseProvider):
    """Xiaomi TV+ channels"""
    SOURCE_URL        = 'https://www.apsattv.com/xiaomi.m3u'
    DEFAULT_GROUP     = 'Xiaomi TV+'
    ID_PREFIX         = 'xiaomi'
    DESCRIPTION_LABEL = 'Xiaomi TV+'

    def __init__(self):
        super().__init__('xiaomi')

class TabloProvider(ApsattvBaseProvider):
    """Tablo channels"""
    SOURCE_URL        = 'https://www.apsattv.com/tablo.m3u'
    DEFAULT_GROUP     = 'Tablo'
    ID_PREFIX         = 'tablo'
    DESCRIPTION_LABEL = 'Tablo'

    def __init__(self):
        super().__init__('tablo')

class RedboxProvider(ApsattvBaseProvider):
    """Redbox channels"""
    SOURCE_URL        = 'https://www.apsattv.com/redbox.m3u'
    DEFAULT_GROUP     = 'Redbox'
    ID_PREFIX         = 'redbox'
    DESCRIPTION_LABEL = 'Redbox'

    def __init__(self):
        super().__init__('redbox')

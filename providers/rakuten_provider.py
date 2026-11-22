"""
Rakuten TV Provider Implementation
Channels from the BuddyChewChew/RakutenTV playlist (Rakuten TV UK),
whose tvg-ids match that repo's EPG.
"""

from .apsattv_provider import ApsattvBaseProvider


class RakutenProvider(ApsattvBaseProvider):
    """Rakuten TV UK channels"""
    SOURCE_URL        = 'https://raw.githubusercontent.com/BuddyChewChew/RakutenTV/main/playlist.m3u'
    DEFAULT_GROUP     = 'Rakuten TV'
    ID_PREFIX         = 'rakuten'
    DESCRIPTION_LABEL = 'Rakuten TV'

    def __init__(self):
        super().__init__('rakuten')

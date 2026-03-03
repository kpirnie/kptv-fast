"""
Providers package for Unified Streaming Aggregator
"""

from .base_provider import BaseProvider
from .xumo_provider import XumoProvider
from .tubi_provider import TubiProvider
from .pluto_provider import PlutoProvider
from .plex_provider import PlexProvider
from .samsung_provider import SamsungProvider
from .distrotv_provider import DistroTVProvider
from .lg_provider import LGProvider
from .git_providers import GitIptvProvider, GitFreetvProvider
from .stirr_provider import StirrProvider
from .apsattv_provider import (
    VizioProvider,
    LocalNowProvider,
    TCLProvider,
    TCLPlusProvider,
    FireTVProvider,
    XiaomiProvider,
    TabloProvider
)
from providers.philo_provider import PhiloProvider
from .roku_provider import RokuProvider
from .whale_provider import WhaleTVProvider

__all__ = [
    'BaseProvider',
    'XumoProvider',
    'TubiProvider',
    'PlutoProvider',
    'PlexProvider',
    'SamsungProvider',
    'DistroTVProvider',
    'LGProvider',
    'GitIptvProvider',
    'GitFreetvProvider',
    'StirrProvider',
    'VizioProvider',
    'LocalNowProvider',
    'TCLProvider',
    'TCLPlusProvider',
    'FireTVProvider',
    'XiaomiProvider',
    'PhiloProvider',
    'TabloProvider',
    'RokuProvider',
    'WhaleTVProvider',
]
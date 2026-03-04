"""
Provider discovery and instantiation.
"""

import logging
import traceback

logger = logging.getLogger(__name__)


def load_providers(enabled_providers: list) -> dict:
    """
    Import and instantiate every available provider class.

    :param enabled_providers: List of provider keys to enable, or ``['all']``.
    :returns: Dict mapping provider key → provider instance.
    """
    available: dict = {}

    # Single-class providers: (key, dotted.module.path, ClassName)
    _single = [
        ('xumo',      'providers.xumo_provider',     'XumoProvider'),
        ('tubi',      'providers.tubi_provider',     'TubiProvider'),
        ('pluto',     'providers.pluto_provider',    'PlutoProvider'),
        ('plex',      'providers.plex_provider',     'PlexProvider'),
        ('samsung',   'providers.samsung_provider',  'SamsungProvider'),
        ('distrotv',  'providers.distrotv_provider', 'DistroTVProvider'),
        ('lg',        'providers.lg_provider',       'LGProvider'),
        ('stirr',     'providers.stirr_provider',    'StirrProvider'),
        ('philo',     'providers.philo_provider',    'PhiloProvider'),
        ('roku',      'providers.roku_provider',     'RokuProvider'),
        ('whale',     'providers.whale_provider',    'WhaleTVProvider'),
    ]

    for key, module_path, class_name in _single:
        try:
            mod = __import__(module_path, fromlist=[class_name])
            available[key] = getattr(mod, class_name)
            logger.info(f"Imported {class_name}")
        except Exception as exc:
            logger.error(f"Failed to import {class_name}: {exc}")

    # Git providers — two classes in one module
    try:
        from providers.git_providers import GitIptvProvider, GitFreetvProvider
        available['git_iptv']   = GitIptvProvider
        available['git_freetv'] = GitFreetvProvider
        logger.info("Imported GitIptvProvider and GitFreetvProvider")
    except Exception as exc:
        logger.error(f"Failed to import git providers: {exc}")

    # Apsattv bundle — multiple classes in one module
    try:
        from providers.apsattv_provider import (
            VizioProvider,
            LocalNowProvider,
            TCLProvider,
            TCLPlusProvider,
            FireTVProvider,
            XiaomiProvider,
            TabloProvider,
        )
        available.update({
            'vizio':    VizioProvider,
            'localnow': LocalNowProvider,
            'tcl':      TCLProvider,
            'tclplus':  TCLPlusProvider,
            'firetv':   FireTVProvider,
            'xiaomi':   XiaomiProvider,
            'tablo':    TabloProvider,
        })
        logger.info("Imported apsattv providers (vizio, localnow, tcl, tclplus, firetv, xiaomi, tablo)")
    except Exception as exc:
        logger.error(f"Failed to import apsattv providers: {exc}")

    # Instantiate only the enabled ones
    providers: dict = {}
    for name, cls in available.items():
        if enabled_providers == ['all'] or name in enabled_providers:
            try:
                logger.info(f"Initializing {name} provider…")
                providers[name] = cls()
                logger.info(f"Initialized {name} provider")
            except Exception as exc:
                logger.error(f"Failed to initialize {name}: {exc}")
                logger.debug(traceback.format_exc())

    return providers
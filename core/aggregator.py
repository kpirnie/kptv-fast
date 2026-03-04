"""
UnifiedStreamingAggregator — top-level orchestrator.

Wires together providers, the channel manager, Flask blueprints,
and the gevent WSGI server.
"""

import os
import logging
from flask import Flask
from gevent.pywsgi import WSGIServer  # type: ignore

from core.provider_loader import load_providers
from core.channel_manager import ChannelManager
from routes.playlist import create_blueprint as playlist_blueprint
from routes.status   import create_blueprint as status_blueprint
from routes.admin    import create_blueprint as admin_blueprint

logger = logging.getLogger(__name__)


class UnifiedStreamingAggregator:
    """
    Top-level application object.  Reads config from env vars, initialises
    providers and the channel manager, registers Flask blueprints, and
    optionally warms the channel cache on startup.
    """

    def __init__(self):
        # ── Config ────────────────────────────────────────────────────────────
        self.cache_duration   = int(os.getenv('CACHE_DURATION',    7200))
        self.max_workers      = int(os.getenv('MAX_WORKERS',           5))
        self.provider_timeout = int(os.getenv('PROVIDER_TIMEOUT',     45))
        self.git_country      = os.getenv('GIT_COUNTRY', '')
        self.startup_delay    = int(os.getenv('STARTUP_CACHE_DELAY',  10))

        enabled_raw            = os.getenv('ENABLED_PROVIDERS', 'all')
        self.enabled_providers = [p.strip() for p in enabled_raw.split(',')]

        warm_on_startup = os.getenv('WARM_CACHE_ON_STARTUP', 'true').lower() == 'true'
        debug_mode      = os.getenv('DEBUG', 'false').lower() == 'true'

        # ── Providers ─────────────────────────────────────────────────────────
        self.providers = load_providers(self.enabled_providers)

        # ── Channel manager ───────────────────────────────────────────────────
        self.channel_manager = ChannelManager(
            providers  = self.providers,
            debug_mode = debug_mode,
        )

        # Config snapshot passed into status/debug blueprints (read-only display)
        self._config_snapshot = {
            'cache_duration':   self.cache_duration,
            'max_workers':      self.max_workers,
            'provider_timeout': self.provider_timeout,
            'git_country':      self.git_country,
            'providers':        self.providers,
        }

        # ── Flask app ─────────────────────────────────────────────────────────
        self.app = Flask(__name__)
        self._register_blueprints()

        # ── Optional startup cache warming ───────────────────────────────────
        if warm_on_startup:
            self.channel_manager.warm_cache(self.startup_delay)

    def _register_blueprints(self) -> None:
        """Register all route blueprints on the Flask app."""
        self.app.register_blueprint(playlist_blueprint(self.channel_manager))
        self.app.register_blueprint(status_blueprint(self.channel_manager, self._config_snapshot))
        self.app.register_blueprint(admin_blueprint(self.channel_manager))

    def run(self) -> None:
        """Start the gevent WSGI server on 0.0.0.0:8080."""
        logger.info("Starting KPTV FAST Streams")
        logger.info(f"Enabled providers : {list(self.providers.keys())}")
        logger.info(f"Workers           : {self.max_workers}  |  timeout: {self.provider_timeout}s")
        if self.git_country:
            logger.info(f"Git country filter: {self.git_country}")

        try:
            server = WSGIServer(('0.0.0.0', 8080), self.app, log=None)
            server.serve_forever()
        except Exception as exc:
            logger.error(f"Server error: {exc}")
            raise
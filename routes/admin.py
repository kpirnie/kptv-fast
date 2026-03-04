"""
Flask blueprint: /clear_cache, /refresh
"""

import time
import logging
from flask import Blueprint, Response

from utils.epg_aggregator import get_epg_aggregator

logger = logging.getLogger(__name__)


def create_blueprint(channel_manager) -> Blueprint:
    """
    Build and return the admin blueprint.

    :param channel_manager: ``ChannelManager`` instance shared across blueprints.
    """
    bp = Blueprint('admin', __name__)

    @bp.route('/clear_cache')
    def clear_cache():
        try:
            channel_manager.clear_cache()
            get_epg_aggregator().clear_cache()
            return Response("Cache cleared successfully", mimetype='text/plain')
        except Exception as exc:
            logger.error(f"Error clearing cache: {exc}")
            return Response(f"Error clearing cache: {exc}", status=500)

    @bp.route('/refresh')
    def force_refresh():
        try:
            channel_manager.clear_cache()
            t        = time.time()
            channels = channel_manager.get_all_channels()
            elapsed  = time.time() - t
            return Response(
                f"Refresh completed in {elapsed:.2f}s. Found {len(channels)} channels.",
                mimetype='text/plain',
            )
        except Exception as exc:
            logger.error(f"Error during refresh: {exc}")
            return Response(f"Error during refresh: {exc}", status=500)

    return bp
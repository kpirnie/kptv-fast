"""
Flask blueprint: /play/<provider>/<channel_id>

Resolves a provider's stream URL at play time and redirects the client to it.
"""

import logging
from flask import Blueprint, Response, redirect

logger = logging.getLogger(__name__)


def create_blueprint(channel_manager) -> Blueprint:
    """
    Build and return the play blueprint.

    :param channel_manager: ``ChannelManager`` instance shared across blueprints.
    """
    bp = Blueprint('play', __name__)

    @bp.route('/play/<provider>/<path:channel_id>')
    def play(provider: str, channel_id: str):
        prov = channel_manager.providers.get(provider)
        if prov is None:
            return Response(f"Unknown provider: {provider}", status=404, mimetype='text/plain')

        try:
            url = prov.resolve_stream(channel_id)
        except Exception as exc:
            logger.error(f"Error resolving {provider}/{channel_id}: {exc}")
            url = None

        if not url:
            return Response(f"Unable to resolve stream for {provider}/{channel_id}", status=502, mimetype='text/plain')

        return redirect(url, code=302)

    return bp
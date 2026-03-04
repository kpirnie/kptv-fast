"""
Flask blueprint: /playlist, /epg, /channels
"""

import json
import logging
from flask import Blueprint, Response, request

from utils.epg_aggregator import get_epg_aggregator

logger = logging.getLogger(__name__)


def create_blueprint(channel_manager) -> Blueprint:
    """
    Build and return the playlist blueprint.

    :param channel_manager: ``ChannelManager`` instance shared across blueprints.
    """
    bp = Blueprint('playlist', __name__)

    @bp.route('/playlist')
    def get_playlist():
        try:
            channels = channel_manager.get_all_channels()
            lines    = ['#EXTM3U']

            for ch in channels:
                attrs = []
                if ch.get('id'):             attrs.append(f'tvg-id="{ch["id"]}"')
                if ch.get('name'):           attrs.append(f'tvg-name="{ch["name"]}"')
                if ch.get('logo'):           attrs.append(f'tvg-logo="{ch["logo"]}"')
                if ch.get('group'):          attrs.append(f'group-title="{ch["group"]}"')
                if ch.get('channel_number'): attrs.append(f'tvg-chno="{ch["channel_number"]}"')
                if ch.get('provider'):       attrs.append(f'provider="{ch["provider"]}"')

                extinf = '#EXTINF:-1 ' + ' '.join(attrs) + f',{ch.get("name", "Unknown")}'
                lines.extend([extinf, ch.get('stream_url', ''), ''])

            return Response(
                '\n'.join(lines),
                mimetype='application/vnd.apple.mpegurl',
                headers={'Content-Disposition': 'attachment; filename=playlist.m3u'},
            )
        except Exception as exc:
            logger.error(f"Error generating playlist: {exc}")
            return Response(f"Error generating playlist: {exc}", status=500)

    @bp.route('/epg')
    def get_epg():
        try:
            aggregator = get_epg_aggregator()
            if 'gzip' in request.headers.get('Accept-Encoding', ''):
                return Response(
                    aggregator.get_combined_epg_gzipped(),
                    mimetype='application/xml',
                    headers={
                        'Content-Encoding': 'gzip',
                        'Content-Disposition': 'attachment; filename=epg.xml.gz',
                    },
                )
            return Response(
                aggregator.get_combined_epg(),
                mimetype='application/xml',
                headers={'Content-Disposition': 'attachment; filename=epg.xml'},
            )
        except Exception as exc:
            logger.error(f"Error generating EPG: {exc}")
            return Response(f"Error generating EPG: {exc}", status=500)

    @bp.route('/channels')
    def get_channels_json():
        try:
            channels = channel_manager.get_all_channels()
            return Response(
                json.dumps(channels, separators=(',', ':')),
                mimetype='application/json',
            )
        except Exception as exc:
            logger.error(f"Error generating channels JSON: {exc}")
            return Response(f"Error generating channels JSON: {exc}", status=500)

    return bp
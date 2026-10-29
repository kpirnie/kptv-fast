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
            provider_filter = request.args.get('provider', '').strip().lower()
            channels = channel_manager.get_all_channels()

            if provider_filter:
                channels = [
                    ch for ch in channels
                    if ch.get('provider', '').lower() == provider_filter
                ]

            lines = ['#EXTM3U']
            base_url = request.host_url.rstrip('/')

            # M3U has no escaping; swap double quotes and strip line breaks so attributes can't break the #EXTINF line
            def _attr(value) -> str:
                return str(value).replace('"', "'").replace('\r', ' ').replace('\n', ' ')

            for ch in channels:
                attrs = []
                # Strip provider prefix from tvg-id (e.g. "samsung-" from "samsung-US390001557")
                ch_id    = ch.get('id', '')
                provider = ch.get('provider', '')
                if provider and ch_id.startswith(f'{provider}-'):
                    ch_id = ch_id[len(provider) + 1:]
                if ch_id: attrs.append(f'tvg-id="{_attr(ch_id)}"')
                if ch.get('name'):           attrs.append(f'tvg-name="{_attr(ch["name"])}"')
                if ch.get('logo'):           attrs.append(f'tvg-logo="{_attr(ch["logo"])}"')
                if ch.get('group'):          attrs.append(f'group-title="{_attr(ch["group"])}"')
                if ch.get('channel_number'): attrs.append(f'tvg-chno="{_attr(ch["channel_number"])}"')
                if ch.get('provider'):       attrs.append(f'provider="{_attr(ch["provider"])}"')

                display_name = str(ch.get("name", "Unknown")).replace('\r', ' ').replace('\n', ' ')
                extinf = '#EXTINF:-1 ' + ' '.join(attrs) + f',{display_name}'
                stream_url = ch.get('stream_url', '')
                if stream_url.startswith('/play/'):
                    stream_url = base_url + stream_url
                lines.extend([extinf, stream_url, ''])

            filename = f'{provider_filter}-playlist.m3u' if provider_filter else 'playlist.m3u'
            return Response(
                '\n'.join(lines),
                mimetype='audio/x-mpegurl',
                headers={'Content-Disposition': f'attachment; filename={filename}'},
            )
        except Exception as exc:
            logger.error(f"Error generating playlist: {exc}")
            return Response(f"Error generating playlist: {exc}", status=500)

    @bp.route('/epg')
    def get_epg():
        try:
            provider_filter = request.args.get('provider', '').strip().lower()
            aggregator      = get_epg_aggregator()

            if provider_filter:
                xml = aggregator.get_provider_epg(provider_filter)
                if not xml:
                    return Response(
                        f"No EPG data available for provider: {provider_filter}",
                        status=404,
                        mimetype='text/plain',
                    )
                return Response(
                    xml,
                    mimetype='application/xml',
                    headers={'Content-Disposition': f'attachment; filename={provider_filter}-epg.xml'},
                )

            return Response(
                aggregator.get_combined_epg(),
                mimetype='application/xml',
                headers={'Content-Disposition': 'attachment; filename=epg.xml'},
            )
        except Exception as exc:
            logger.error(f"Error generating EPG: {exc}")
            return Response(f"Error generating EPG: {exc}", status=500)

    @bp.route('/epg-gz')
    def get_epg_gz():
        try:
            provider_filter = request.args.get('provider', '').strip().lower()
            aggregator      = get_epg_aggregator()

            if provider_filter:
                gz = aggregator.get_provider_epg_gz(provider_filter)
                if not gz:
                    return Response(
                        f"No EPG data available for provider: {provider_filter}",
                        status=404,
                        mimetype='text/plain',
                    )
                return Response(
                    gz,
                    mimetype='application/gzip',
                    headers={'Content-Disposition': f'attachment; filename={provider_filter}-epg.xml.gz'},
                )

            return Response(
                aggregator.get_combined_epg_gzipped(),
                mimetype='application/gzip',
                headers={'Content-Disposition': 'attachment; filename=epg.xml.gz'},
            )
        except Exception as exc:
            logger.error(f"Error generating gzipped EPG: {exc}")
            return Response(f"Error generating gzipped EPG: {exc}", status=500)

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
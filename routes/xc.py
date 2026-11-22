"""
Flask blueprint: Xtream Codes output — /player_api.php, /get.php, /live/<username>/<password>/<stream_id>
"""

import json
import re
import base64
import time
import hashlib
import threading
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote
from flask import Blueprint, Response, request, redirect

from core.channel_manager import channel_tvg_id
from utils.settings import get_settings
from utils.epg_aggregator import get_epg_aggregator

logger = logging.getLogger(__name__)

_MAX_ID = 0x7fffffff  # keep ids inside a signed 32-bit int for client compatibility


def _stable_id(key: str) -> int:
    """Hash a key to a stable positive 31-bit int."""
    value = int.from_bytes(hashlib.sha1(key.encode('utf-8')).digest()[:4], 'big') & _MAX_ID
    return value or 1


def _claim_id(key: str, used: set) -> int:
    """Return the key's stable id, stepping to the next free one on a collision."""
    value = _stable_id(key)
    while value in used:
        value = (value % _MAX_ID) + 1
    used.add(value)
    return value


def _category_name(ch: dict) -> str:
    """Category for a channel: "<provider> - <group>", first group only for multi-group channels."""
    group = str(ch.get('group') or '').split(';')[0].strip() or 'General'
    return f"{ch.get('provider', 'unknown')} - {group}"


def _attr(value) -> str:
    """M3U has no escaping; swap double quotes and strip line breaks."""
    return str(value).replace('"', "'").replace('\r', ' ').replace('\n', ' ')


class _LiveIndex:
    """XC view of the channel list, rebuilt whenever the channel cache changes."""

    def __init__(self):
        self._lock       = threading.Lock()
        self._source     = None
        self.categories: list = []
        self.streams:    list = []
        self.by_stream:  dict = {}

    def refresh(self, channels: list) -> None:
        """Rebuild the index when handed a different channel list than last time."""
        with self._lock:
            if channels is self._source:
                return

            # sort by id so collision bumps land the same way every build
            ordered = sorted(channels, key=lambda ch: str(ch.get('id', '')))

            cat_used:    set  = set()
            stream_used: set  = set()
            cat_ids:     dict = {}
            categories:  list = []
            streams:     list = []
            by_stream:   dict = {}

            for ch in ordered:
                cat_name = _category_name(ch)
                if cat_name not in cat_ids:
                    cat_ids[cat_name] = str(_claim_id(f"cat:{cat_name}", cat_used))
                    categories.append({
                        'category_id':   cat_ids[cat_name],
                        'category_name': cat_name,
                        'parent_id':     0,
                    })

                stream_id = _claim_id(f"live:{ch.get('id', '')}", stream_used)
                by_stream[stream_id] = ch
                streams.append({
                    'num':                 ch.get('channel_number', 0),
                    'name':                ch.get('name', 'Unknown'),
                    'stream_type':         'live',
                    'stream_id':           stream_id,
                    'stream_icon':         ch.get('logo', '') or '',
                    'epg_channel_id':      channel_tvg_id(ch),
                    'added':               '0',
                    'is_adult':            0,
                    'category_id':         cat_ids[cat_name],
                    'category_ids':        [int(cat_ids[cat_name])],
                    'custom_sid':          '',
                    'tv_archive':          0,
                    'direct_source':       '',
                    'tv_archive_duration': 0,
                })

            categories.sort(key=lambda c: c['category_name'].lower())
            streams.sort(key=lambda s: s['num'] or 999999)

            self.categories = categories
            self.streams    = streams
            self.by_stream  = by_stream
            self._source    = channels

_PROGRAMME_RE = re.compile(r'<programme\s[^>]*>.*?</programme>', re.DOTALL)


def _xmltv_ts(value: str) -> int:
    """Parse an XMLTV timestamp ("20261122120000 +0000") to a unix timestamp; 0 when unreadable."""
    value = (value or '').strip()
    if not value:
        return 0

    try:
        if ' ' in value:
            return int(datetime.strptime(value, '%Y%m%d%H%M%S %z').timestamp())
        return int(datetime.strptime(value[:14], '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return 0


def _xc_time(ts: int) -> str:
    """Unix timestamp to the XC "YYYY-mm-dd HH:MM:SS" UTC format."""
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _b64(value: str) -> str:
    """XC sends programme titles and descriptions base64-encoded."""
    return base64.b64encode(value.encode('utf-8')).decode('ascii')


class _EpgIndex:
    """Programmes by tvg-id, rebuilt whenever the combined EPG cache changes."""

    def __init__(self):
        self._lock      = threading.Lock()
        self._source    = None
        self.by_channel: dict = {}

    def refresh(self, xml: bytes) -> None:
        """Rebuild the index when handed a different combined EPG than last time."""
        with self._lock:
            if xml is self._source:
                return

            by_channel: dict = {}
            text = xml.decode('utf-8', errors='replace') if xml else ''

            # parse block by block so one bad programme can't sink the whole guide
            for match in _PROGRAMME_RE.finditer(text):
                try:
                    el = ET.fromstring(match.group(0))
                except ET.ParseError:
                    continue

                ch_id = el.get('channel', '')
                start = _xmltv_ts(el.get('start', ''))
                stop  = _xmltv_ts(el.get('stop', ''))
                if not ch_id or not start or stop <= start:
                    continue

                title_el = el.find('title')
                desc_el  = el.find('desc')
                title    = (title_el.text or '').strip() if title_el is not None else ''
                desc     = (desc_el.text  or '').strip() if desc_el  is not None else ''
                lang     = (title_el.get('lang', '') if title_el is not None else '') or 'en'

                by_channel.setdefault(ch_id, []).append((start, stop, title, desc, lang))

            for items in by_channel.values():
                items.sort()

            self.by_channel = by_channel
            self._source    = xml

def create_blueprint(channel_manager) -> Blueprint:
    """
    Build and return the XC blueprint.

    :param channel_manager: ``ChannelManager`` instance shared across blueprints.
    """
    bp    = Blueprint('xc', __name__)
    index = _LiveIndex()
    epg   = _EpgIndex()

    def _live_index() -> _LiveIndex:
        index.refresh(channel_manager.get_all_channels())
        return index


    def _epg_listings(stream_id_raw: str, limit_raw: str, full: bool) -> dict:
        """
        Build the XC epg_listings payload for a live stream.

        :param full: All programmes with now_playing flags (get_simple_data_table),
                     otherwise the next ``limit`` upcoming ones (get_short_epg).
        """
        empty = {'epg_listings': []}

        try:
            stream_id = int(stream_id_raw)
        except (TypeError, ValueError):
            return empty

        ch = _live_index().by_stream.get(stream_id)
        if ch is None:
            return empty

        epg_id = channel_tvg_id(ch)
        epg.refresh(get_epg_aggregator().get_combined_epg())
        items = epg.by_channel.get(epg_id, [])
        now   = int(time.time())

        if not full:
            try:
                limit = max(1, int(limit_raw))
            except (TypeError, ValueError):
                limit = 4
            items = [p for p in items if p[1] > now][:limit]

        listings = []
        for start, stop, title, desc, lang in items:
            entry = {
                'id':              f"{stream_id}{start}",
                'epg_id':          epg_id,
                'title':           _b64(title),
                'lang':            lang,
                'start':           _xc_time(start),
                'end':             _xc_time(stop),
                'description':     _b64(desc),
                'channel_id':      epg_id,
                'start_timestamp': str(start),
                'stop_timestamp':  str(stop),
            }
            if full:
                entry['now_playing'] = 1 if start <= now < stop else 0
                entry['has_archive'] = 0
            listings.append(entry)

        return {'epg_listings': listings}

    def _json(payload, status: int = 200) -> Response:
        return Response(json.dumps(payload, separators=(',', ':')), status=status, mimetype='application/json')

    def _disabled() -> Response:
        return Response('Not found', status=404, mimetype='text/plain')

    def _server_info() -> dict:
        now    = datetime.now(timezone.utc)
        scheme = request.scheme
        host   = request.host
        port   = '443' if scheme == 'https' else '80'
        if ':' in host and not host.endswith(']'):
            host, port = host.rsplit(':', 1)
        return {
            'url':             host,
            'port':            port,
            'https_port':      '443',
            'server_protocol': scheme,
            'rtmp_port':       '',
            'timezone':        'UTC',
            'timestamp_now':   int(now.timestamp()),
            'time_now':        now.strftime('%Y-%m-%d %H:%M:%S'),
        }

    def _user_info(username: str, password: str) -> dict:
        return {
            'username':               username,
            'password':               password,
            'message':                '',
            'auth':                   1,
            'status':                 'Active',
            'exp_date':               None,
            'is_trial':               '0',
            'active_cons':            '0',
            'created_at':             '0',
            'max_connections':        '1',
            'allowed_output_formats': ['m3u8', 'ts'],
        }

    @bp.route('/player_api.php', methods=['GET', 'POST'])
    def player_api():
        settings = get_settings()
        if not settings.xc_active():
            return _disabled()

        username = request.values.get('username', '')
        password = request.values.get('password', '')
        if not settings.xc_authenticate(username, password):
            return _json({'user_info': {'auth': 0, 'message': 'Invalid credentials'}}, status=401)

        action = request.values.get('action', '').strip()

        try:
            if not action:
                return _json({
                    'user_info':   _user_info(username, password),
                    'server_info': _server_info(),
                })

            if action == 'get_live_categories':
                return _json(_live_index().categories)

            if action == 'get_live_streams':
                streams     = _live_index().streams
                category_id = request.values.get('category_id', '').strip()
                if category_id:
                    streams = [s for s in streams if s['category_id'] == category_id]
                return _json(streams)

            if action == 'get_short_epg':
                return _json(_epg_listings(request.values.get('stream_id', ''), request.values.get('limit', ''), full=False))

            if action == 'get_simple_data_table':
                return _json(_epg_listings(request.values.get('stream_id', ''), '', full=True))
            
            # anything not supported yet answers with an empty list
            return _json([])

        except Exception as exc:
            logger.error(f"Error handling XC action '{action}': {exc}")
            return _json({'error': 'internal error'}, status=500)

    @bp.route('/get.php')
    def get_php():
        settings = get_settings()
        if not settings.xc_active():
            return _disabled()

        username = request.values.get('username', '')
        password = request.values.get('password', '')
        if not settings.xc_authenticate(username, password):
            return Response('Unauthorized', status=401, mimetype='text/plain')

        if request.values.get('type', 'm3u_plus') not in ('m3u', 'm3u_plus'):
            return Response('Unsupported output type', status=400, mimetype='text/plain')

        try:
            ext      = 'ts' if request.values.get('output', '') == 'ts' else 'm3u8'
            base_url = request.host_url.rstrip('/')
            user_q   = quote(username, safe='')
            pass_q   = quote(password, safe='')
            live     = _live_index()
            cat_map  = {c['category_id']: c['category_name'] for c in live.categories}

            lines = [f'#EXTM3U url-tvg="{base_url}/xmltv.php?username={user_q}&password={pass_q}"']
            for s in live.streams:
                attrs = []
                if s['epg_channel_id']: attrs.append(f'tvg-id="{_attr(s["epg_channel_id"])}"')
                attrs.append(f'tvg-name="{_attr(s["name"])}"')
                if s['stream_icon']:    attrs.append(f'tvg-logo="{_attr(s["stream_icon"])}"')
                attrs.append(f'group-title="{_attr(cat_map.get(s["category_id"], ""))}"')
                if s['num']:            attrs.append(f'tvg-chno="{_attr(s["num"])}"')

                display_name = str(s['name']).replace('\r', ' ').replace('\n', ' ')
                lines.extend([
                    '#EXTINF:-1 ' + ' '.join(attrs) + f',{display_name}',
                    f"{base_url}/live/{user_q}/{pass_q}/{s['stream_id']}.{ext}",
                    '',
                ])

            return Response(
                '\n'.join(lines),
                mimetype='audio/x-mpegurl',
                headers={'Content-Disposition': 'attachment; filename=playlist.m3u'},
            )
        except Exception as exc:
            logger.error(f"Error generating XC playlist: {exc}")
            return Response(f"Error generating playlist: {exc}", status=500)

    @bp.route('/xmltv.php')
    def xmltv_php():
        settings = get_settings()
        if not settings.xc_active():
            return _disabled()

        if not settings.xc_authenticate(request.values.get('username', ''), request.values.get('password', '')):
            return Response('Unauthorized', status=401, mimetype='text/plain')

        try:
            return Response(
                get_epg_aggregator().get_combined_epg(),
                mimetype='application/xml',
                headers={'Content-Disposition': 'attachment; filename=epg.xml'},
            )
        except Exception as exc:
            logger.error(f"Error generating XC EPG: {exc}")
            return Response(f"Error generating EPG: {exc}", status=500)
        
    @bp.route('/live/<username>/<password>/<stream>', methods=['GET', 'HEAD'])
    def live_stream(username: str, password: str, stream: str):
        settings = get_settings()
        if not settings.xc_active():
            return _disabled()

        if not settings.xc_authenticate(username, password):
            return Response('Unauthorized', status=401, mimetype='text/plain')

        # drop the .ts / .m3u8 extension
        raw_id = stream.rsplit('.', 1)[0]
        try:
            stream_id = int(raw_id)
        except ValueError:
            return Response('Invalid stream ID', status=400, mimetype='text/plain')

        ch = _live_index().by_stream.get(stream_id)
        if ch is None:
            return Response('Stream not found', status=404, mimetype='text/plain')

        stream_url = ch.get('stream_url', '')
        if not stream_url:
            return Response('Stream not found', status=404, mimetype='text/plain')

        # play-time resolved channels go through our own /play/ route
        if stream_url.startswith('/play/'):
            stream_url = request.host_url.rstrip('/') + stream_url

        return redirect(stream_url, code=302)

    return bp
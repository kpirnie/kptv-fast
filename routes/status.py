"""
Flask blueprint: /, /status, /debug
"""

import json
import socket
import sys
import time
import logging
from flask import Blueprint, Response, request

logger = logging.getLogger(__name__)

# Providers that have a known EPG source in the aggregator
_EPG_PROVIDERS = {
    'plex', 'pluto', 'samsung', 'stirr', 'lg',
    'distrotv', 'tubi', 'xumo', 'roku', 'localnow',
}

# SVG icons
_ICON_M3U = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<polygon points="23 7 16 12 23 17 23 7"/>'
    '<rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>'
    '</svg>'
)

_ICON_EPG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>'
    '<line x1="16" y1="2" x2="16" y2="6"/>'
    '<line x1="8" y1="2" x2="8" y2="6"/>'
    '<line x1="3" y1="10" x2="21" y2="10"/>'
    '<line x1="8" y1="14" x2="8" y2="14"/>'
    '<line x1="12" y1="14" x2="12" y2="14"/>'
    '<line x1="16" y1="14" x2="16" y2="14"/>'
    '<line x1="8" y1="18" x2="8" y2="18"/>'
    '<line x1="12" y1="18" x2="12" y2="18"/>'
    '</svg>'
)


def create_blueprint(channel_manager, aggregator_config: dict) -> Blueprint:
    bp = Blueprint('status', __name__)

    @bp.route('/')
    @bp.route('/status')
    def get_status():
        try:
            refresh = request.args.get('refresh', '').lower() in {'1', 'true', 'yes'}

            if refresh:
                channels        = channel_manager.get_all_channels()
                channels_source = 'live refresh'
            else:
                channels = channel_manager.get_cached_channels()
                valid    = channel_manager.is_cache_valid('all_channels')
                if valid:
                    channels_source = 'warm cache'
                elif channels:
                    channels_source = 'stale cache'
                else:
                    channels_source = 'not loaded yet'

            provider_stats: dict = {}
            for ch in channels:
                p = ch.get('provider', 'unknown')
                provider_stats[p] = provider_stats.get(p, 0) + 1

            duplicates = channel_manager.last_duplicates

            all_providers = sorted(
                set(provider_stats) | set(duplicates),
                key=lambda p: provider_stats.get(p, 0),
                reverse=True,
            )

            def dupe_cell(p: str) -> str:
                n = duplicates.get(p, 0)
                if n == 0:
                    return '<td class="dupes">&#8212;</td>'
                pct = n / (provider_stats.get(p, 0) + n) * 100
                return (
                    f'<td class="dupes warn">'
                    f'{n:,} <span class="pct">({pct:.0f}%)</span>'
                    f'</td>'
                )

            def export_cells(p: str) -> str:
                m3u_cell = (
                    f'<td class="export">'
                    f'<a href="/playlist?provider={p}" title="Download {p} M3U playlist" class="icon-link m3u" target="_blank">'
                    f'{_ICON_M3U}'
                    f'</a></td>'
                )
                if p in _EPG_PROVIDERS:
                    epg_cell = (
                        f'<td class="export">'
                        f'<a href="/epg?provider={p}" title="Download {p} EPG" class="icon-link epg" target="_blank">'
                        f'{_ICON_EPG}'
                        f'</a></td>'
                    )
                else:
                    epg_cell = '<td class="export"><span class="no-epg">&#8212;</span></td>'
                return m3u_cell + epg_cell

            provider_rows = ''.join(
                f'<tr>'
                f'<td>{p}</td>'
                f'<td class="chcount">{provider_stats.get(p, 0):,}</td>'
                f'{dupe_cell(p)}'
                f'{export_cells(p)}'
                f'</tr>'
                for p in all_providers
            )

            total_dupes = sum(duplicates.values())
            cache_ttl   = aggregator_config.get('cache_duration', 7200) // 60

            html = _STATUS_TEMPLATE.format(
                total_channels   = len(channels),
                active_providers = len(provider_stats),
                total_dupes      = total_dupes,
                dupes_class      = 'warn' if total_dupes > 0 else '',
                cache_ttl        = cache_ttl,
                provider_rows    = provider_rows,
            )
            return Response(html, mimetype='text/html')

        except Exception as exc:
            logger.error(f"Error generating status page: {exc}")
            return Response(f"Error generating status page: {exc}", status=500)

    @bp.route('/debug')
    def get_debug_info():
        try:
            channels        = channel_manager.get_all_channels()
            provider_stats: dict = {}
            for ch in channels:
                p = ch.get('provider', 'unknown')
                provider_stats[p] = provider_stats.get(p, 0) + 1

            info = {
                'total_channels':    len(channels),
                'provider_stats':    provider_stats,
                'enabled_providers': list(aggregator_config.get('providers', {}).keys()),
                'git_country_filter': aggregator_config.get('git_country', ''),
                'python_version':    sys.version,
                'recursion_limit':   sys.getrecursionlimit(),
                'hostname':          socket.gethostname(),
                'cache_status': {
                    'channels_cached': channel_manager.is_cache_valid('all_channels'),
                    'current_time':    time.time(),
                },
                'performance_settings': {
                    'max_workers':      aggregator_config.get('max_workers',      5),
                    'provider_timeout': aggregator_config.get('provider_timeout', 45),
                    'cache_duration':   aggregator_config.get('cache_duration',   7200),
                },
            }
            return Response(json.dumps(info, indent=2), mimetype='application/json')

        except Exception as exc:
            logger.error(f"Error generating debug info: {exc}")
            return Response(f"Error generating debug info: {exc}", status=500)

    return bp


_STATUS_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>KPTV FAST Streams</title>
  <link rel="icon" type="image/png" href="https://cdn.kevp.us/tv/kptv-icon.png">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: system-ui, -apple-system, sans-serif;
      background: #0d1117;
      color: #c9d1d9;
      min-height: 100vh;
      padding: 2rem 1rem;
    }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    header {{ display: flex; align-items: center; gap: 1rem; margin-bottom: 2rem; }}
    header img {{ width: 48px; height: 48px; }}
    header h1 {{ font-size: 1.6rem; color: #f0f6fc; }}
    header p {{ color: #8b949e; font-size: 0.9rem; margin-top: 0.2rem; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }}
    .card {{
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 1.2rem;
      text-align: center;
    }}
    .card .val {{ font-size: 2rem; font-weight: 700; color: #58a6ff; line-height: 1; }}
    .card .val.warn {{ color: #e3b341; }}
    .card .lbl {{ font-size: 0.75rem; color: #8b949e; margin-top: 0.4rem; text-transform: uppercase; letter-spacing: 0.04em; }}
    section {{ margin-bottom: 2rem; }}
    section h2 {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #8b949e;
      margin-bottom: 0.75rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      overflow: hidden;
      font-size: 0.875rem;
    }}
    thead tr {{ background: #0d1117; }}
    th {{
      padding: 0.6rem 1rem;
      text-align: left;
      color: #8b949e;
      font-weight: 600;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    th.right {{ text-align: right; }}
    th.center {{ text-align: center; }}
    td {{ padding: 0.55rem 1rem; border-top: 1px solid #21262d; }}
    tbody tr:hover td {{ background: #1c2128; }}
    td.chcount {{ text-align: right; color: #58a6ff; font-variant-numeric: tabular-nums; }}
    td.dupes {{ text-align: right; color: #8b949e; font-variant-numeric: tabular-nums; }}
    td.dupes.warn {{ color: #e3b341; }}
    .pct {{ font-size: 0.75rem; opacity: 0.7; }}
    td.export {{ text-align: center; width: 48px; padding: 0.4rem 0.5rem; }}
    .icon-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0.3rem;
      border-radius: 5px;
      transition: background 0.15s, color 0.15s;
      color: #8b949e;
      text-decoration: none;
    }}
    .icon-link.m3u:hover {{ background: #1f3a5f; color: #58a6ff; }}
    .icon-link.epg:hover {{ background: #2a3a20; color: #56d364; }}
    .no-epg {{ color: #30363d; font-size: 0.85rem; }}
    .links {{ display: flex; flex-wrap: wrap; gap: 0.5rem; justify-content: center; }}
    .links a {{
      background: #161b22;
      border: 1px solid #30363d;
      color: #58a6ff;
      text-decoration: none;
      padding: 0.4rem 0.9rem;
      border-radius: 6px;
      font-size: 0.85rem;
      transition: border-color 0.15s, background 0.15s;
    }}
    .links a:hover {{ background: #1c2128; border-color: #58a6ff; }}
    footer {{
      margin-top: 3rem;
      padding-top: 1.5rem;
      border-top: 1px solid #21262d;
      text-align: center;
      font-size: 0.8rem;
      color: #8b949e;
    }}
    footer a {{ color: #58a6ff; text-decoration: none; }}
    footer a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div class="container">

    <header>
      <img src="https://cdn.kevp.us/tv/kptv-icon.png" alt="KPTV">
      <div>
        <h1>KPTV FAST Streams</h1>
        <p>Free Ad-Supported TV aggregator</p>
      </div>
    </header>

    <div class="cards">
      <div class="card">
        <div class="val">{total_channels:,}</div>
        <div class="lbl">Total Channels</div>
      </div>
      <div class="card">
        <div class="val">{active_providers}</div>
        <div class="lbl">Active Providers</div>
      </div>
      <div class="card">
        <div class="val {dupes_class}">{total_dupes:,}</div>
        <div class="lbl">Dupes Dropped</div>
      </div>
      <div class="card">
        <div class="val">{cache_ttl}m</div>
        <div class="lbl">Cache TTL</div>
      </div>
    </div>

    <section>
      <div class="links">
        <a href="/playlist" target="_blank">M3U Playlist</a>
        <a href="/epg" target="_blank">EPG XML</a>
        <a href="/channels" target="_blank">Channels JSON</a>
        <a href="/debug" target="_blank">Debug Info</a>
        <a href="/refresh">Force Refresh</a>
        <a href="/clear_cache">Clear Cache</a>
        <a href="/?refresh=1">Status (live)</a>
      </div>
    </section>

    <section>
      <h2>Provider Breakdown</h2>
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th class="right">Channels</th>
            <th class="right">Dupes Dropped</th>
            <th class="center">M3U</th>
            <th class="center">EPG</th>
          </tr>
        </thead>
        <tbody>{provider_rows}</tbody>
      </table>
    </section>

    <footer>
      Copyright &copy; 2025
      <a href="https://kevinpirnie.com/" target="_blank" rel="noopener">Kevin Pirnie</a>.
      All rights reserved.
    </footer>

  </div>
</body>
</html>"""
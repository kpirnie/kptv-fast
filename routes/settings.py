"""
Flask blueprint: /settings
"""

import html
import logging
from flask import Blueprint, Response, request, redirect

from utils.settings import get_settings

logger = logging.getLogger(__name__)


def create_blueprint() -> Blueprint:
    """Build and return the settings blueprint."""
    bp = Blueprint('settings', __name__)

    @bp.route('/settings', methods=['GET', 'POST'])
    def settings_page():
        settings = get_settings()
        message, message_class = '', ''

        enabled  = settings.get('xc_enabled')
        username = settings.get('xc_username')

        if request.method == 'POST':
            enabled  = request.form.get('xc_enabled') == '1'
            username = request.form.get('xc_username', '').strip()
            password = request.form.get('xc_password', '')

            # a blank password keeps the saved one
            if not password:
                password = settings.get('xc_password')

            if enabled and not (username and password):
                message, message_class = 'A username and password are required to enable XC output.', 'error'
            else:
                try:
                    settings.update({
                        'xc_enabled':  enabled,
                        'xc_username': username,
                        'xc_password': password,
                    })
                    return redirect('/settings?saved=1', code=303)
                except OSError as exc:
                    logger.error(f"Error saving settings: {exc}")
                    message, message_class = f'Could not save settings: {exc}', 'error'

        elif request.args.get('saved'):
            message, message_class = 'Settings saved.', 'ok'

        has_password = bool(settings.get('xc_password'))

        page = _SETTINGS_TEMPLATE.format(
            message_html = f'<div class="msg {message_class}">{html.escape(message)}</div>' if message else '',
            checked      = 'checked' if enabled else '',
            username     = html.escape(username, quote=True),
            pw_holder    = 'leave blank to keep the current password' if has_password else '',
        )
        return Response(page, mimetype='text/html')

    return bp


_SETTINGS_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>KPTV FAST Streams - Settings</title>
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
    .container {{ max-width: 600px; margin: 0 auto; }}
    header {{ display: flex; align-items: center; gap: 1rem; margin-bottom: 2rem; }}
    header img {{ width: 48px; height: 48px; }}
    header h1 {{ font-size: 1.6rem; color: #f0f6fc; }}
    header p {{ color: #8b949e; font-size: 0.9rem; margin-top: 0.2rem; }}
    header a {{ color: #58a6ff; text-decoration: none; }}
    section h2 {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #8b949e;
      margin-bottom: 0.75rem;
    }}
    form {{
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 1.2rem;
      display: grid;
      gap: 1rem;
    }}
    label {{ display: grid; gap: 0.4rem; font-size: 0.85rem; color: #8b949e; }}
    label.toggle {{ display: flex; align-items: center; gap: 0.6rem; color: #c9d1d9; }}
    input[type=text], input[type=password] {{
      background: #0d1117;
      border: 1px solid #30363d;
      border-radius: 6px;
      color: #c9d1d9;
      padding: 0.5rem 0.7rem;
      font-size: 0.9rem;
    }}
    input:focus {{ outline: none; border-color: #58a6ff; }}
    button {{
      background: #1f3a5f;
      border: 1px solid #58a6ff;
      color: #f0f6fc;
      border-radius: 6px;
      padding: 0.5rem 1rem;
      font-size: 0.9rem;
      cursor: pointer;
      justify-self: start;
    }}
    button:hover {{ background: #24476f; }}
    .msg {{ border-radius: 6px; padding: 0.6rem 0.9rem; margin-bottom: 1rem; font-size: 0.85rem; }}
    .msg.ok {{ background: #12261a; border: 1px solid #2ea043; color: #56d364; }}
    .msg.error {{ background: #2d1416; border: 1px solid #da3633; color: #f85149; }}
  </style>
</head>
<body>
  <div class="container">

    <header>
      <img src="https://cdn.kevp.us/tv/kptv-icon.png" alt="KPTV">
      <div>
        <h1>Settings</h1>
        <p><a href="/">&larr; Back to status</a></p>
      </div>
    </header>

    {message_html}

    <section>
      <h2>Xtream Codes Output</h2>
      <form method="post" action="/settings">
        <label class="toggle">
          <input type="checkbox" name="xc_enabled" value="1" {checked}>
          Enable XC output
        </label>
        <label>
          Username
          <input type="text" name="xc_username" value="{username}" autocomplete="off">
        </label>
        <label>
          Password
          <input type="password" name="xc_password" placeholder="{pw_holder}" autocomplete="new-password">
        </label>
        <button type="submit">Save</button>
      </form>
    </section>

  </div>
</body>
</html>"""

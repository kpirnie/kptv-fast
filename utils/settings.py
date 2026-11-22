"""
Settings - Persistent app settings stored as JSON in the data directory
"""

import os
import json
import hmac
import threading
import logging

logger = logging.getLogger(__name__)

# <app root>/data — /app/data in the container
DATA_DIR      = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')


class Settings:
    """Loads, holds, and saves the app settings"""

    DEFAULTS = {
        'xc_enabled':  False,
        'xc_username': '',
        'xc_password': '',
    }

    def __init__(self, path: str = SETTINGS_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._data = dict(self.DEFAULTS)
        self._load()

    def _load(self) -> None:
        """Read the settings file; a missing or unreadable file leaves the defaults."""
        if not os.path.isfile(self._path):
            return

        try:
            with open(self._path, 'r', encoding='utf-8') as fh:
                stored = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.error(f"❌ Could not read settings from {self._path}: {exc}")
            return

        if not isinstance(stored, dict):
            logger.error(f"❌ Settings file {self._path} is not a JSON object — using defaults")
            return

        for key, default in self.DEFAULTS.items():
            if key in stored and isinstance(stored[key], type(default)):
                self._data[key] = stored[key]

    def get(self, key: str):
        """Return a setting's current value."""
        with self._lock:
            return self._data.get(key, self.DEFAULTS.get(key))

    def update(self, values: dict) -> None:
        """
        Merge known keys into the settings and write them to disk.

        :raises OSError: When the settings file can't be written.
        """
        with self._lock:
            data = dict(self._data)
            for key, default in self.DEFAULTS.items():
                if key in values and isinstance(values[key], type(default)):
                    data[key] = values[key]

            # write to a temp file and swap it in so a failed write never leaves a half file
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp_path = f"{self._path}.tmp"
            with open(tmp_path, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, indent=2)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self._path)

            self._data = data

    def xc_active(self) -> bool:
        """True when XC output is enabled and has a username and password."""
        with self._lock:
            return bool(self._data['xc_enabled'] and self._data['xc_username'] and self._data['xc_password'])

    def xc_authenticate(self, username: str, password: str) -> bool:
        """Check XC credentials; always False while XC output is off."""
        with self._lock:
            enabled       = self._data['xc_enabled']
            expected_user = self._data['xc_username']
            expected_pass = self._data['xc_password']

        if not (enabled and expected_user and expected_pass):
            return False

        # compare both so the timing doesn't reveal which one was wrong
        user_ok = hmac.compare_digest(str(username or '').encode('utf-8'), expected_user.encode('utf-8'))
        pass_ok = hmac.compare_digest(str(password or '').encode('utf-8'), expected_pass.encode('utf-8'))
        return user_ok and pass_ok


# Singleton
_instance = None
_lock     = threading.Lock()


def get_settings() -> Settings:
    """Get the singleton Settings instance."""
    global _instance
    with _lock:
        if _instance is None:
            _instance = Settings()
        return _instance
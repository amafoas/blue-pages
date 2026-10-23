"""User preferences, stored as JSON next to the server data."""
import json
import os

from .store import CONFIG_DIR

SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")

DEFAULTS = {
    # "window": always a new Files window; "tab": a new tab in an open Files window.
    "open_in": "window",
}


class Settings:
    def __init__(self):
        self._values = dict(DEFAULTS)
        try:
            with open(SETTINGS_FILE) as f:
                saved = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        self._values.update({k: v for k, v in saved.items() if k in DEFAULTS})

    def __getitem__(self, key):
        return self._values[key]

    def __setitem__(self, key, value):
        self._values[key] = value
        self._save()

    def _save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._values, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)

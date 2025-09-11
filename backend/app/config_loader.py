# backend/app/config_loader.py
from __future__ import annotations

# NOTE: this file is an obsolete note, it's not really used right now, as the Flask app will load it into itself on creation
# NOTE: on Windows, use `pip install tzdata`

import json
from pathlib import Path
from zoneinfo import ZoneInfo  # Python 3.9+
import logging
log = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
CONFIG_PATH = CONFIG_DIR / "appconfig.json"

def load_app_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Could not read %s; falling back to defaults", CONFIG_PATH, exc_info=True)
        return {}

def get_timezone(cfg = None):
    if cfg is None:
        cfg = load_app_config()
    name = (cfg.get("timezone") or "UTC").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        log.warning("Unknown timezone %r; falling back to UTC", name)
        return ZoneInfo("UTC")

class AppConfig(object):
    def __init__(self):
        self.data = load_app_config()

    def get_timezone(self):
        return get_timezone(self.data)

# backend/app/config_loader.py
from __future__ import annotations

# NOTE: this file is an obsolete note, it's not really used right now, as the Flask app will load it into itself on creation
# NOTE: on Windows, use `pip install tzdata`

import json
from pathlib import Path
from zoneinfo import ZoneInfo  # Python 3.9+
from typing import Optional
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

def get_private_dir_path(cfg: Optional[dict] = None) -> Optional[Path]:
    """
    Provide a central place to resolve where sensitive runtime files should live.

    The configuration may specify a "private_dir"; when present we interpret
    it as either an absolute path or one relative to the config directory.
    Returning None signals the caller to fall back to the project defaults.
    """
    if cfg is None:
        cfg = load_app_config()

    if not isinstance(cfg, dict):
        log.debug("App configuration did not load as a mapping; ignoring private_dir hint.")
        return None

    raw_value = cfg.get("private_dir")
    if not raw_value:
        return None

    try:
        candidate = Path(str(raw_value)).expanduser()
    except Exception:
        log.warning(
            "private_dir in %s could not be interpreted as a filesystem path; ignoring it.",
            CONFIG_PATH,
            exc_info=True,
        )
        return None

    if not candidate.is_absolute():
        # Helpful during development where the path might be expressed relative to the repo.
        candidate = (CONFIG_DIR / candidate).resolve()

    return candidate

class AppConfig(object):
    def __init__(self):
        self.data = load_app_config()

    def get_timezone(self):
        return get_timezone(self.data)

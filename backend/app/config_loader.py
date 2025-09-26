# backend/app/config_loader.py
from __future__ import annotations

import json
import logging
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Optional

from zoneinfo import ZoneInfo  # Python 3.9+

log = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
CONFIG_PATH = CONFIG_DIR / "appconfig.json"
_SECRETS_PATH = CONFIG_DIR / "secrets.json"

_PIN_OPEN_EXPIRY_CONFIG_KEY = "pin_open_expiry_hours"
_PIN_OPEN_EXPIRY_DEFAULT_HOURS = 36

def _read_json_file(path: Path) -> dict:
    """Read JSON from disk, returning an empty mapping on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Could not read %s; falling back to defaults", path, exc_info=True)
        return {}

def load_app_config() -> dict:
    """Return the raw JSON configuration for the application."""
    return _read_json_file(CONFIG_PATH)

def _coerce_positive_number(value: Any, fallback: int) -> int:
    """Convert unknown input into a positive integer number of hours."""
    try:
        numeric = float(value)
    except Exception:
        return int(fallback)
    if numeric <= 0:
        return int(fallback)
    return int(numeric)

def get_pin_open_expiry_hours(cfg: Optional[Mapping[str, Any]] = None) -> int:
    """Resolve the configured window (in hours) for keeping pins open."""
    if cfg is None:
        cfg = load_app_config()
    if isinstance(cfg, Mapping):
        candidate = cfg.get(_PIN_OPEN_EXPIRY_CONFIG_KEY)
    else:
        candidate = None
    hours = _coerce_positive_number(candidate, _PIN_OPEN_EXPIRY_DEFAULT_HOURS)
    return hours

def get_timezone(cfg: Optional[Mapping[str, Any]] = None) -> ZoneInfo:
    """Return the configured timezone, defaulting to UTC on any error."""
    if cfg is None:
        cfg = load_app_config()
    name = "UTC"
    if isinstance(cfg, Mapping):
        raw = cfg.get("timezone")
        if isinstance(raw, str) and raw.strip():
            name = raw.strip()
    try:
        return ZoneInfo(name)
    except Exception:
        log.warning("Unknown timezone %r; falling back to UTC", name, exc_info=True)
        return ZoneInfo("UTC")

def load_user_password_salt() -> Optional[str]:
    """Fetch the shared password salt from secrets.json when available."""
    secrets = _read_json_file(_SECRETS_PATH)
    value = secrets.get("user_password_salt") if isinstance(secrets, Mapping) else None
    if isinstance(value, str) and value:
        return value
    return None

def get_private_dir_path(cfg: Optional[Mapping[str, Any]] = None) -> Optional[Path]:
    """Resolve where sensitive runtime files should live."""
    if cfg is None:
        cfg = load_app_config()
    if not isinstance(cfg, Mapping):
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
        candidate = (CONFIG_DIR / candidate).resolve()
    return candidate

def initialize_app_config(app: Any) -> None:
    """Populate a Flask app instance with values derived from appconfig.json."""
    cfg = load_app_config()
    if isinstance(cfg, Mapping):
        app.config.update(cfg)
    hours = get_pin_open_expiry_hours(cfg)
    # Provide both the original key and uppercase variants so existing lookups keep working.
    app.config[_PIN_OPEN_EXPIRY_CONFIG_KEY] = hours
    app.config["PIN_OPEN_EXPIRY_HOURS"] = hours
    app.config["PIN_OPEN_EXPIRY_MS"] = hours * 60 * 60 * 1000
    app.config["TZ"] = get_timezone(cfg)
    salt = load_user_password_salt()
    if salt:
        app.config["SECRET_KEY"] = salt
    else:
        log.error("Unable to load user_password_salt from %s", _SECRETS_PATH)


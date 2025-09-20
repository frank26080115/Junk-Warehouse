from flask import Flask, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, text
import os
from pathlib import Path
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
import json
import logging
from app.logging_setup import start_log
from .errors import register_error_handlers
from .static_server import bp_overlay, get_public_html_path
from .imagehandler import bp_image
from .user_login import bp as bp_auth
import app.helpers as helpers
import app.db as db
from app.config_loader import CONFIG_PATH, CONFIG_DIR

# Load backend/.env explicitly (does nothing if file doesn't exist)
DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(DOTENV_PATH, override=False)

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "frontend" / "dist"

start_log(app_name="backend", level = logging.DEBUG if os.getenv("FLASK_ENV") == "development" else None)
log = logging.getLogger(__name__)

def create_app():
    app = Flask(__name__)
    CORS(app)

    log.info("Flask ENV: " + os.getenv("FLASK_ENV"))
    if os.getenv("FLASK_ENV") == "development":
        log.setLevel(logging.DEBUG)
        app.logger.setLevel(logging.DEBUG)
        log.debug("Start of logger debug level")

    app.register_blueprint(bp_auth)
    app.register_blueprint(bp_overlay)
    app.register_blueprint(bp_image)

    # Load JSON (silently ignore if missing/bad)
    try:
        app_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        app.config.update(app_cfg)
    except Exception:
        app.logger.warning("Could not read %s; using defaults", CONFIG_PATH)

    # Precompute helpful objects derived from config (e.g., ZoneInfo)
    tz_name = (app.config.get("timezone") or app.config.get("TIMEZONE") or "UTC").strip()
    try:
        app.config["TZ"] = ZoneInfo(tz_name)
    except Exception:
        app.logger.warning("Unknown timezone %r; falling back to UTC", tz_name)
        app.config["TZ"] = ZoneInfo("UTC")

    try:
        secrets = json.loads((CONFIG_DIR / "secrets.json").read_text(encoding="utf-8"))
        app.config["SECRET_KEY"] = secrets["user_password_salt"]
    except Exception as ex:
        app.logger.error(f"Unable to load user_password_salt. Exception: {ex!r}")

    register_error_handlers(app)
    return app


app = create_app()

@app.get("/api/health")
def health():
    s = db.get_or_create_session()
    s.execute(text("select 1"))
    return jsonify(ok=True)

@app.get("/api/config.json")
def config_json():
    return jsonify(json.loads(CONFIG_PATH.read_text()))

@app.teardown_appcontext
def db_cleanup(_exc):
    db.db_cleanup(_exc)

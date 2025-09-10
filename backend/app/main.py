from flask import Flask, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, text
import os
from pathlib import Path
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

import logging
from app.logging_setup import start_log
from .errors import register_error_handlers
import helpers
import db
from config_loader import CONFIG_PATH

# Load backend/.env explicitly (does nothing if file doesn't exist)
DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(DOTENV_PATH, override=False)

start_log(app_name="backend")
log = logging.getLogger(__name__)

def create_app():
    app = Flask(__name__)
    CORS(app)

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

    register_error_handlers(app)
    return app

app = create_app()

@app.get("/api/health")
def health():
    with db.get_db_conn() as conn:
        conn.execute(text("select 1"))
    return jsonify(ok=True)

@app.get("/config.json")
def config_json():
    path = pathlib.Path("../config/appconfig.json")
    return jsonify(json.loads(path.read_text()))

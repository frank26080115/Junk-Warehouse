from flask import Flask, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, text
import os
from pathlib import Path
from dotenv import load_dotenv

import logging
from app.logging_setup import start_log
from .errors import register_error_handlers
import helpers
import db

# Load backend/.env explicitly (does nothing if file doesn't exist)
DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(DOTENV_PATH, override=False)

start_log(app_name="backend")
log = logging.getLogger(__name__)

def create_app():
    app = Flask(__name__)
    CORS(app)
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

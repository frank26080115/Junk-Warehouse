from __future__ import annotations

from flask import Flask, jsonify
from flask_cors import CORS
from sqlalchemy import text
from datetime import timedelta
import os
from pathlib import Path
from dotenv import load_dotenv
import json
import logging
from app.logging_setup import start_log
from .errors import register_error_handlers
from .static_server import bp_overlay, get_public_html_path
from .image_handler import bp_image
from .user_login import bp as bp_auth
from .invoice_handlers import bp as bp_invoice, check_email_task
from .items import bp as bp_items
from .history import bp as bp_history
from .maint import bp as bp_maint
from .metatext import bp as bp_metatext
from .search import bp as bp_search
from .job_manager import JobManager, RepeatableJob, bp as bp_jobs
import app.helpers as helpers
import app.db as db
from app.db import bp as bp_dbstatus
from app.config_loader import CONFIG_PATH, initialize_app_config

# Load backend/.env explicitly (does nothing if file doesn't exist)
DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(DOTENV_PATH, override=False)

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "frontend" / "dist"

start_log(app_name="backend", level = logging.DEBUG if os.getenv("FLASK_ENV") == "development" else None)
log = logging.getLogger(__name__)

def create_app():
    """Instantiate and fully configure the Flask application instance."""

    # The explicit module name here gives Flask the correct import context for
    # locating static assets and templates when they are requested at runtime.
    app = Flask(__name__)

    # Enable permissive cross-origin requests so the React frontend can access
    # API routes when served from a different origin during development.
    CORS(app)

    # The job manager holds background jobs such as scheduled inbox polling.
    # We attach it to the Flask application so blueprints and views can reach
    # the manager without maintaining their own global state or imports.
    job_manager = JobManager()
    job_manager.attach_app(app)
    app.extensions["job_manager"] = job_manager

    # Schedule a repeatable job so the mailbox is checked automatically every
    # hour.  Using ``check_email_task`` keeps the scheduling logic centralized
    # and makes the job behavior identical to manual triggers in the UI.
    hourly_email_job = RepeatableJob(
        name="check-email",
        function=lambda: check_email_task({}),
        frequency=timedelta(hours=1),
    )
    job_manager.install_repeatable_job(hourly_email_job)

    log.info("Flask ENV: " + os.getenv("FLASK_ENV"))
    if os.getenv("FLASK_ENV") == "development":
        log.setLevel(logging.DEBUG)
        app.logger.setLevel(logging.DEBUG)
        log.debug("Start of logger debug level")

    # Register each blueprint explicitly so the available HTTP routes are easy
    # to audit.  Each blueprint bundles related view logic (auth, invoices, and
    # so on) which keeps the module responsibilities clearly separated.
    app.register_blueprint(bp_auth)
    app.register_blueprint(bp_overlay)
    app.register_blueprint(bp_image)
    app.register_blueprint(bp_search)
    app.register_blueprint(bp_invoice)
    app.register_blueprint(bp_items)
    app.register_blueprint(bp_history)
    app.register_blueprint(bp_maint)
    app.register_blueprint(bp_jobs)
    app.register_blueprint(bp_dbstatus)
    app.register_blueprint(bp_metatext)

    # Delegate configuration loading so the logic stays in one place.
    initialize_app_config(app)

    # Centralize error handler registration so every blueprint benefits from
    # the same JSON response format and logging behavior.
    register_error_handlers(app)
    return app


app = create_app()

@app.get("/api/health")
def health():
    """Provide a quick database reachability probe for monitoring."""

    s = db.get_or_create_session()
    s.execute(text("select 1"))
    return jsonify(ok=True)

@app.get("/api/config.json")
def config_json():
    """Expose the frontend configuration exactly as stored on disk."""

    return jsonify(json.loads(CONFIG_PATH.read_text()))

@app.teardown_appcontext
def db_cleanup(_exc):
    """Release scoped database resources after every request."""

    db.db_cleanup(_exc)

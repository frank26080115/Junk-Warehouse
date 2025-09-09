# backend/app/errors.py
from flask import jsonify, request
from werkzeug.exceptions import HTTPException
import json

# note about app.logger and the other loggers we started:
# Both propagate to the root logger by default, and since you called start_log(...) (which configures the root), they end up in the same rotating log files/console. The only difference is the %(name)s shown in each line.

def register_error_handlers(app):
    setup_signals(app)

    @app.errorhandler(HTTPException)
    def handle_http(e: HTTPException):
        app.logger.warning("HTTP %s on %s %s", e.code, request.method, request.path, exc_info=e)
        resp = e.get_response()
        payload = {
            "ok": False,
            "error": e.name,
            "code": e.code,
            "description": e.description,
            "path": request.path,
            "method": request.method,
        }
        resp.data = json.dumps(payload)
        resp.content_type = "application/json"
        return resp

    @app.errorhandler(Exception)
    def handle_uncaught(e: Exception):
        app.logger.exception("Unhandled exception")
        return jsonify(ok=False, error="Internal Server Error"), 500

    @app.teardown_request
    def log_teardown(exc):
        if exc is not None:
            app.logger.exception("Teardown exception", exc_info=exc)
        return None

from flask.signals import got_request_exception

def setup_signals(app):
    def on_exc(sender, exception, **extra):
        app.logger.exception("Signal caught exception")
    got_request_exception.connect(on_exc, app)

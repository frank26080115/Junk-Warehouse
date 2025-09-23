# /backend/app/user_login.py
from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import hmac
import json
from pathlib import Path
from typing import Dict, Any, Optional, List

from flask import Blueprint, request, session, jsonify, current_app
from functools import wraps

bp = Blueprint("auth", __name__, url_prefix="/api")

REPO_ROOT = Path(__file__).resolve().parents[2]

# Template you can copy into /config/users.json (keep this here for reference)
USERS_JSON_TEMPLATE = {
    "users": [
        {
            "username": "admin",
            # SHA-256 of (password + SALT). Replace this with a real hash.
            "hash": "<sha256_of_password_plus_salt_hex>"
        }
    ]
}

# -------- Utilities --------

def _users_json_path() -> Path:
    return REPO_ROOT / "config" / "users.json"

def _load_users() -> List[Dict[str, str]]:
    """
    Load users from /config/users.json. The expected shape is:
    { "users": [ {"username": "...", "hash": "hexsha256(...)"} ] }
    """
    p = _users_json_path()
    if not p.exists():
        # Don't create it automatically; fail loudly so you notice.
        raise FileNotFoundError(
            f"users.json not found at {p}. Create it with a structure like:\n{json.dumps(USERS_JSON_TEMPLATE, indent=2)}"
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "users" not in data or not isinstance(data["users"], list):
            raise ValueError("users.json is malformed; expected an object with a 'users' array.")
        # Normalize and validate minimal fields
        norm = []
        for entry in data["users"]:
            if not isinstance(entry, dict) or "username" not in entry or "hash" not in entry:
                raise ValueError("Each user entry must have 'username' and 'hash'.")
            norm.append({"username": str(entry["username"]), "hash": str(entry["hash"])})
        return norm
    except json.JSONDecodeError as e:
        raise ValueError(f"users.json is not valid JSON: {e}")

def _password_hash(password: str, salt: str) -> str:
    """
    Compute SHA-256(password + salt) hex digest.
    """
    return sha256((password + salt).encode("utf-8")).hexdigest()

def _find_user(users: List[Dict[str, str]], username: str) -> Optional[Dict[str, str]]:
    for u in users:
        if u["username"] == username:
            return u
    return None

def is_user_authenticated() -> bool:
    """
    Returns True if a user_id is present in the session.
    """
    return bool(session.get("user_id"))

def login_required(fn):
    """
    Decorator for protecting routes.
    If the user is not authenticated, return JSON 401 immediately.
    """

    """
# Example use
# /backend/app/some_api.py
from flask import Blueprint, jsonify
from backend.app.user_login import login_required

bp = Blueprint("some_api", __name__, url_prefix="/api")

@bp.route("/data", methods=["GET"])
@login_required
def get_data():
    # Only runs if authenticated
    return jsonify(
        message="Here is your protected data!",
        items=[1, 2, 3]
    ), 200

    """


    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_user_authenticated():
            return jsonify(error="Not authenticated."), 401
        return fn(*args, **kwargs)
    return wrapper


# -------- Session lifetime / refresh --------

@bp.record_once
def _configure_session_lifetime(setup_state):
    """
    Ensure the app uses a 30-day permanent session lifetime.
    Flask will refresh permanent sessions on each request as long as
    SESSION_REFRESH_EACH_REQUEST=True (default).
    """
    app = setup_state.app
    # Only set if not already configured elsewhere.
    if not app.config.get("PERMANENT_SESSION_LIFETIME"):
        app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

@bp.before_app_request
def _refresh_permanent_session():
    """
    When authenticated, mark the session permanent and 'modified' so the
    rolling 30-day expiry refreshes on activity.
    """
    if session.get("user_id"):
        session.permanent = True
        # Marking modified ensures the session cookie is reissued.
        session.modified = True

# -------- Routes --------

@bp.route("/login", methods=["POST"])
def login():
    """
    Body: JSON { "username": "...", "password": "..." }
    Behavior:
      * Validates against /config/users.json using SHA-256(password + SECRET_KEY)
      * On success: sets session["user_id"] = username and returns 200
      * On failure: 401
    """
    if not request.is_json:
        return jsonify(error="Expected JSON body."), 400

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password")

    if not username or password is None:
        return jsonify(error="Missing 'username' or 'password'."), 400

    # IMPORTANT: per your design, the salt is in SECRET_KEY and never sent to frontend.
    salt = str(current_app.config.get("SECRET_KEY") or "")
    if not salt:
        return jsonify(error="Server misconfiguration: SECRET_KEY (salt) is not set."), 500

    try:
        users = _load_users()
    except (FileNotFoundError, ValueError) as e:
        return jsonify(error=str(e)), 500

    user = _find_user(users, username)
    if not user:
        # Avoid leaking which usernames exist
        return jsonify(error="Invalid username or password."), 401

    # Constant-time compare to mitigate timing side-channels
    candidate = _password_hash(password, salt)
    if not hmac.compare_digest(candidate, user["hash"]):
        return jsonify(error="Invalid username or password."), 401

    # Success: establish session
    session["user_id"] = username
    session.permanent = True  # enables rolling 30-day expiry
    return jsonify(ok=True, user_id=username), 200

@bp.route("/logout", methods=["POST", "GET"])
def logout():
    """
    Clears the login session.
    """
    session.pop("user_id", None)
    # You can also session.clear() if you store more in the session.
    return jsonify(ok=True), 200

@bp.route("/whoami", methods=["GET"])
def whoami():
    """
    Returns {"user_id": "..."} if authenticated, else 401.
    """
    uid = session.get("user_id")
    if not uid:
        return jsonify(error="Not authenticated."), 401
    return jsonify(ok=True, user_id=uid), 200

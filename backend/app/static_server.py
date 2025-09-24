# backend/app/static_server.py
from __future__ import annotations
from pathlib import Path
from flask import Blueprint, send_from_directory, abort, Response

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "frontend" / "dist"

bp_overlay = Blueprint("overlay", __name__)

def _maybe_file(p: Path) -> bool:
    try:
        return p.is_file()
    except Exception:
        return False

@bp_overlay.get("/assets/<path:filename>")
def dist_assets(filename: str):
    return send_from_directory(DIST_DIR / "assets", filename)

@bp_overlay.get("/", defaults={"path": ""})
@bp_overlay.get("/<path:path>")
def overlay_root(path: str):
    # 1. Public runtime files
    pub_path = get_public_html_path() / path
    if _maybe_file(pub_path):
        resp: Response = send_from_directory(get_public_html_path(), path)
        resp.cache_control.public = True
        resp.cache_control.max_age = 3600
        return resp

    # 2. Built dist files
    dist_path = DIST_DIR / path
    if _maybe_file(dist_path):
        return send_from_directory(DIST_DIR, path)

    # 3. If looks like a client-side route (no ".ext"), serve SPA index
    if "." not in path:
        return send_from_directory(DIST_DIR, "index.html")

    # 4. Otherwise, not found
    abort(404)

def get_public_html_path() -> Path:
    from flask import current_app

    key = "public_html_path"
    default_path = REPO_ROOT / "var" / "public_html"
    configured_path = current_app.config.get(key)
    if configured_path:
        return Path(configured_path)
    return default_path

# backend/app/static_server.py
from __future__ import annotations
from pathlib import Path
from flask import Blueprint, send_from_directory, abort, Response
import logging
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "frontend" / "dist"

bp_overlay = Blueprint("overlay", __name__)

log = logging.getLogger(__name__)

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

    log.error(f'static server overlay_root 404 "{path}" -> "{pub_path}"')

    # 4. Otherwise, not found
    abort(404)

def get_public_html_path() -> Path:
    key = "public_html_path"
    default_path = REPO_ROOT / "var" / "public_html"

    # Attempt to read a runtime override from Flask's config so development and
    # production deployments can point at their own static directories.
    configured_path: Optional[object] = None
    try:
        from flask import current_app
        configured_path = current_app.config.get(key)
    except Exception:
        from app.config_loader import load_app_config
        app_cfg = load_app_config()
        configured_path = app_cfg.get(key)

    resolved_path: Optional[Path] = None

    if isinstance(configured_path, Path):
        resolved_path = configured_path
    elif configured_path is not None:
        try:
            raw_text = str(configured_path).strip()
        except Exception:
            raw_text = ""

        if raw_text:
            # Support repo-relative hints like "<REPO_ROOT>/frontend/public" so the
            # configuration file can stay portable between machines.
            repo_hint = "<REPO_ROOT>/"
            if raw_text.startswith(repo_hint):
                relative_text = raw_text[len(repo_hint):]
                resolved_path = (REPO_ROOT / relative_text).resolve()
            else:
                try:
                    candidate = Path(raw_text).expanduser()
                    if not candidate.is_absolute():
                        candidate = (REPO_ROOT / raw_text).resolve()
                    resolved_path = candidate
                except Exception:
                    log.warning(
                        "Configured public_html_path value %r could not be interpreted; falling back to default.",
                        configured_path,
                        exc_info=True,
                    )

    if resolved_path:
        return resolved_path
    return default_path

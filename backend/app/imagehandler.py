# backend/app/imagehandler.py
from __future__ import annotations

import io
import os
import uuid
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from PIL import Image, UnidentifiedImageError

from .db import get_db_conn
from .static_server import get_public_html_path

log = logging.getLogger(__name__)
bp_image = Blueprint("images", __name__)

# Acceptable file extensions (Pillow can read more; keep this conservative)
ALLOWED_EXTENSIONS = {
    "png", "jpg", "jpeg", "webp", "gif" #, "bmp", "tif", "tiff"
}

MAX_BASENAME_LEN = 64
THUMB_MAX_DIM = 400
MAX_FILES_PER_DIR = 200


def _ext_ok(filename: str) -> bool:
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def _ensure_dirs() -> None:
    TMP_DIR = get_public_html_path() / "tmp"
    IMGS_ROOT = get_public_html_path() / "imgs"
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    IMGS_ROOT.mkdir(parents=True, exist_ok=True)
    return TMP_DIR, IMGS_ROOT


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _latest_or_new_img_dir(imgs_root: Path) -> Path:
    """
    Use the last (sorted) subdirectory under IMGS_ROOT.
    If none exist, create today's.
    If chosen dir has > MAX_FILES_PER_DIR files, create a new today's dir.
    """
    _ensure_dirs()
    subdirs = sorted([p for p in imgs_root.iterdir() if p.is_dir()], key=lambda p: p.name)
    if not subdirs:
        target = imgs_root / _today_str()
        target.mkdir(exist_ok=True)
        return target

    target = subdirs[-1]
    try:
        file_count = sum(1 for p in target.iterdir() if p.is_file())
    except FileNotFoundError:
        file_count = 0

    if file_count > MAX_FILES_PER_DIR:
        target = imgs_root / _today_str()
        target.mkdir(exist_ok=True)

    return target


def _unique_name(directory: Path, desired_name: str) -> str:
    """
    Return a unique filename inside directory by appending _1, _2, ... if needed.
    """
    base = desired_name
    stem, dot, ext = base.partition(".")
    candidate = base
    counter = 1
    while (directory / candidate).exists():
        candidate = f"{stem}_{counter}"
        if ext:
            candidate = f"{candidate}.{ext}"
        counter += 1
    return candidate


def _truncate_basename(name: str) -> str:
    if len(name) <= MAX_BASENAME_LEN:
        return name
    # Keep extension if present
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        max_stem = max(1, MAX_BASENAME_LEN - (len(ext) + 1))
        return f"{stem[:max_stem]}.{ext}"
    return name[:MAX_BASENAME_LEN]


def _open_image_probe(path: Path) -> Tuple[int, int]:
    """
    Open with Pillow to ensure readability; return (width, height).
    Raise 415 on unsupported.
    """
    try:
        with Image.open(path) as im:
            im.verify()  # quick format check
        # reopen to access size (verify() leaves the file closed)
        with Image.open(path) as im2:
            width, height = im2.size
        return width, height
    except (UnidentifiedImageError, OSError) as e:
        log.warning("Image open failed for %s: %s", path, e)
        raise UnsupportedMedia("Unreadable or unsupported image") from e


def _save_thumbnail(src_path: Path, dst_dir: Path, base_no_ext: str) -> str:
    thumb_name = f"{base_no_ext}.thumbnail.jpg"
    thumb_path = dst_dir / thumb_name
    with Image.open(src_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        if w >= h:
            new_w = min(THUMB_MAX_DIM, w)
            new_h = int(h * (new_w / w))
        else:
            new_h = min(THUMB_MAX_DIM, h)
            new_w = int(w * (new_h / h))
        im = im.resize((new_w, new_h))
        im.save(thumb_path, "JPEG", quality=90, optimize=True, progressive=True)
    return thumb_name


def _download_to_tmp(url: str, tmp_dir: Path) -> Path:
    import requests  # local import to avoid dependency for users who don't need URL mode

    resp = requests.get(url, stream=True, timeout=20)
    if resp.status_code != 200:
        raise BadRequest(f"Failed to download image (HTTP {resp.status_code})")

    # Attempt to infer a name from URL path
    from urllib.parse import urlparse, unquote
    parsed = urlparse(url)
    raw_name = unquote(Path(parsed.path).name) or "downloaded"
    raw_name = secure_filename(raw_name)

    # Add extension from Content-Type if missing
    if "." not in raw_name:
        ct = resp.headers.get("Content-Type", "")
        guessed_ext = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "image/gif": "gif",
            "image/bmp": "bmp",
            "image/tiff": "tif",
        }.get(ct.lower(), "")
        raw_name = f"{raw_name}.{guessed_ext}" if guessed_ext else f"{raw_name}.jpg"

    if not _ext_ok(raw_name):
        raise UnsupportedMedia("URL points to an unsupported image type")

    raw_name = _truncate_basename(raw_name)
    tmp_path = tmp_dir / _unique_name(tmp_dir, raw_name)

    with open(tmp_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 128):
            if chunk:
                f.write(chunk)
    return tmp_path


class BadRequest(Exception):
    pass


class UnsupportedMedia(Exception):
    pass


def _validate_uuid(u: str) -> uuid.UUID:
    try:
        from helpers import normalize_pg_uuid
        return uuid.UUID(normalize_pg_uuid(u))
    except ValueError as ex:
        raise BadRequest(f"Invalid item_id \"{u}\", ValueError: {ex.message})")
    except Exception as ex:
        raise BadRequest(f"Invalid item_id \"{u}\", must be a UUID string, exception: {ex.message})")


@bp_image.post("/img_upload")
def img_upload():
    """
    POST /img_upload
    - Exactly one of:
        * file: <uploaded file>
        * url:  <image URL>
    - Required:
        * item_id: UUID string referencing items.id
    """
    # Validate presence of item_id
    item_id = request.form.get("item_id", "").strip()
    return handle_img_upload(item_id)


def handle_img_upload(item_id:str):
    tmp_dir, imgs_root = _ensure_dirs()

    if not item_id:
        return jsonify(error="Missing required field 'item_id'"), 400
    try:
        item_uuid = _validate_uuid(item_id)
    except BadRequest as e:
        return jsonify(error=str(e)), 400
 
    # Determine source: file or url (must have one or the other)
    upload = request.files.get("img_file")
    url = request.form.get("img_url", "").strip()

    if (not upload and not url) or (upload and url):
        return jsonify(error="Provide exactly one of 'img_file' or 'img_url'"), 400

    # Save source into tmp, validate extension & readability
    source_url: Optional[str] = None
    try:
        if upload:
            if upload.filename == "":
                raise BadRequest("Empty filename")
            original_name = secure_filename(upload.filename)
            if not _ext_ok(original_name):
                raise UnsupportedMedia("Unsupported file type")
            original_name = _truncate_basename(original_name)
            tmp_name = _unique_name(tmp_dir, original_name)
            tmp_path = tmp_dir / tmp_name
            upload.save(tmp_path)
        else:
            source_url = url
            tmp_path = _download_to_tmp(url, tmp_dir)
            original_name = tmp_path.name  # already secured/truncated

        # Probe image
        width, height = _open_image_probe(tmp_path)

    except BadRequest as e:
        log.info("BadRequest in handle_img_upload: %s", e)
        return jsonify(error=str(e)), 400
    except UnsupportedMedia as e:
        log.info("Unsupported media in handle_img_upload: %s", e)
        return jsonify(error=str(e)), 400
    except Exception as e:
        log.exception("Unexpected error saving image to tmp")
        return jsonify(error="Failed to receive image"), 500

    # Pick permanent directory
    try:
        target_dir = _latest_or_new_img_dir(imgs_root)
        dir_name = target_dir.name  # YYYY-MM-DD
    except Exception as e:
        log.exception("Failed to select/create target image directory")
        return jsonify(error="Server storage configuration error"), 500

    # Move original image to permanent dir (keep original format/name, ensure uniqueness)
    try:
        final_name = _unique_name(target_dir, original_name)
        final_path = target_dir / final_name
        shutil.move(str(tmp_path), final_path)
    except Exception as e:
        log.exception("Failed to move image from tmp to final")
        return jsonify(error="Failed to store image"), 500

    # Generate thumbnail (basename.thumbnail.jpg)
    try:
        base_no_ext = Path(final_name).stem
        thumb_name = _save_thumbnail(final_path, target_dir, base_no_ext)
    except Exception as e:
        log.exception("Failed to create thumbnail for %s", final_path)
        # Not fatal to the upload itself; you can decide to fail hard:
        return jsonify(error="Failed to create thumbnail"), 500

    # Insert DB rows
    try:
        with get_db_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Determine rank: 0 if first image for this item; otherwise 1
                cur.execute(
                    "SELECT 1 FROM img_relations WHERE item_id = %s LIMIT 1",
                    (str(item_uuid),),
                )
                has_any = cur.fetchone() is not None
                rank = 0 if not has_any else 1

                # Insert into images
                cur.execute(
                    """
                    INSERT INTO images
                      (dir, file_name, source_url, has_renamed, original_file_name,
                       notes, dim_width, dim_height)
                    VALUES
                      (%s, %s, %s, %s, %s,
                       %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        dir_name,
                        final_name,
                        source_url,
                        False,
                        original_name,
                        "",  # notes
                        width,
                        height,
                    ),
                )
                image_row = cur.fetchone()
                img_id = image_row["id"]

                # Insert relation
                cur.execute(
                    """
                    INSERT INTO img_relations
                      (item_id, img_id, rank)
                    VALUES
                      (%s, %s, %s)
                    RETURNING id
                    """,
                    (str(item_uuid), str(img_id), rank),
                )
                rel_row = cur.fetchone()
                rel_id = rel_row["id"]

        log.info(
            "Stored image '%s' (w=%d h=%d) in %s, img_id=%s, rel_id=%s, rank=%d",
            final_name, width, height, dir_name, img_id, rel_id, rank
        )

    except Exception as e:
        log.exception("Database operation failed for image %s", final_name)
        return jsonify(error="Database error while saving image"), 500

    # Success response
    return jsonify(
        ok=True,
        item_id=str(item_uuid),
        img={
            "id": str(img_id),
            "dir": dir_name,
            "file_name": final_name,
            "thumbnail": thumb_name,
            "source_url": source_url,
            "original_file_name": original_name,
            "dim_width": width,
            "dim_height": height,
        },
        relation={"id": str(rel_id), "rank": rank},
        public_paths={
            "image": f"/imgs/{dir_name}/{final_name}",
            "thumbnail": f"/imgs/{dir_name}/{thumb_name}",
        },
    ), 201

@bp_image.get("/img_upload")
def img_upload_2():
    return img_upload()

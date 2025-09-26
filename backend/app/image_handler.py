# backend/app/imagehandler.py
from __future__ import annotations

import uuid
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import base64
import binascii
import re

from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from PIL import Image, UnidentifiedImageError
from sqlalchemy import text

from .db import get_db_conn
from .static_server import get_public_html_path

log = logging.getLogger(__name__)
bp_image = Blueprint("images", __name__, url_prefix="/api")

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


def _ext_from_mimetype(mimetype: str) -> str:
    mapping = {
        "image/jpeg": "jpg",
        "image/pjpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
        "image/bmp": "bmp",
        "image/tiff": "tif",
        "image/x-icon": "ico",
        "image/heic": "heic",
        "image/heif": "heif",
    }
    return mapping.get((mimetype or "").lower(), "png")


class BadRequest(Exception):
    pass


class UnsupportedMedia(Exception):
    pass


def _validate_uuid(u: str) -> uuid.UUID:
    try:
        from .helpers import normalize_pg_uuid
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


def _write_data_url_to_tmp(data_url: str, tmp_dir: Path, *, default_stem: str) -> Tuple[Path, str, str]:
    match = re.match(r"data:(?P<mime>[^;]+);base64,(?P<data>.+)", data_url, re.DOTALL)
    if not match:
        raise BadRequest("Invalid data URL format")
    mimetype = (match.group("mime") or "image/png").strip()
    data_segment = match.group("data") or ""
    try:
        binary = base64.b64decode(data_segment, validate=True)
    except binascii.Error as exc:
        raise BadRequest("Failed to decode image data") from exc
    if not binary:
        raise BadRequest("Image data URL did not contain any data")
    ext = _ext_from_mimetype(mimetype)
    if not ext:
        ext = "png"
    base_name = secure_filename(f"{default_stem}.{ext}") or f"{default_stem}.{ext}"
    base_name = _truncate_basename(base_name)
    tmp_name = _unique_name(tmp_dir, base_name)
    tmp_path = tmp_dir / tmp_name
    with open(tmp_path, "wb") as handle:
        handle.write(binary)
    return tmp_path, base_name, mimetype


def store_image_for_item(
    *,
    item_uuid: uuid.UUID,
    upload: Optional[Any] = None,
    source_url: str = "",
    data_url: str = "",
    clipboard_upload: bool = False,
) -> Dict[str, Any]:
    tmp_dir, imgs_root = _ensure_dirs()

    provided = [bool(upload), bool(source_url), bool(data_url)]
    if sum(provided) != 1:
        raise BadRequest("Provide exactly one image source")

    try:
        with get_db_conn() as conn:
            row = conn.execute(
                text("SELECT short_id FROM items WHERE id = :item_id"),
                {"item_id": str(item_uuid)},
            ).first()
    except Exception as exc:
        log.exception("Failed to fetch item short_id prior to upload")
        raise RuntimeError("Database error while preparing image upload") from exc

    if row is None:
        raise FileNotFoundError("Unknown item for upload")

    short_value = row[0]
    try:
        short_id_hex = format(int(short_value or 0), "x")
    except (TypeError, ValueError):
        short_id_hex = str(short_value or "0")

    source_url_value = ""
    has_renamed = False
    try:
        if upload is not None:
            incoming_name = secure_filename(getattr(upload, "filename", "") or "")
            if incoming_name and not clipboard_upload:
                original_name = incoming_name
            else:
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
                ext = _ext_from_mimetype(getattr(upload, "mimetype", "image/png"))
                original_name = f"{short_id_hex}{timestamp}.{ext}"
                has_renamed = True
            if not _ext_ok(original_name):
                raise UnsupportedMedia("Unsupported file type")
            original_name = _truncate_basename(original_name)
            tmp_name = _unique_name(tmp_dir, original_name)
            tmp_path = tmp_dir / tmp_name
            upload.save(tmp_path)
        elif data_url:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            tmp_path, original_name, _ = _write_data_url_to_tmp(
                data_url,
                tmp_dir,
                default_stem=f"{short_id_hex}{timestamp}",
            )
            has_renamed = True
        else:
            source_url_value = source_url
            tmp_path = _download_to_tmp(source_url, tmp_dir)
            original_name = tmp_path.name

        width, height = _open_image_probe(tmp_path)

    except BadRequest:
        raise
    except UnsupportedMedia:
        raise
    except Exception as exc:
        log.exception("Unexpected error saving image to tmp")
        raise RuntimeError("Failed to receive image") from exc

    try:
        target_dir = _latest_or_new_img_dir(imgs_root)
        dir_name = target_dir.name
    except Exception as exc:
        log.exception("Failed to select/create target image directory")
        raise RuntimeError("Server storage configuration error") from exc

    try:
        final_name = _unique_name(target_dir, original_name)
        final_path = target_dir / final_name
        shutil.move(str(tmp_path), final_path)
    except Exception as exc:
        log.exception("Failed to move image from tmp to final")
        raise RuntimeError("Failed to store image") from exc

    try:
        base_no_ext = Path(final_name).stem
        thumb_name = _save_thumbnail(final_path, target_dir, base_no_ext)
    except Exception as exc:
        log.exception("Failed to create thumbnail for %s", final_path)
        raise RuntimeError("Failed to create thumbnail") from exc

    try:
        with get_db_conn() as conn:
            trans = conn.begin()
            try:
                has_any = (
                    conn.execute(
                        text("SELECT 1 FROM item_images WHERE item_id = :item_id LIMIT 1"),
                        {"item_id": str(item_uuid)},
                    ).first()
                    is not None
                )
                rank = 0 if not has_any else 1

                image_row = conn.execute(
                    text(
                        """
                        INSERT INTO images
                          (dir, file_name, source_url, has_renamed, original_file_name,
                           notes, dim_width, dim_height)
                        VALUES
                          (:dir, :file_name, :source_url, :has_renamed, :original_file_name,
                           :notes, :width, :height)
                        RETURNING id
                        """
                    ),
                    {
                        "dir": dir_name,
                        "file_name": final_name,
                        "source_url": source_url_value,
                        "has_renamed": has_renamed,
                        "original_file_name": original_name,
                        "notes": "",
                        "width": width,
                        "height": height,
                    },
                ).mappings().one()
                img_id = str(image_row["id"])

                relation_row = conn.execute(
                    text(
                        """
                        INSERT INTO item_images
                          (item_id, img_id, rank)
                        VALUES
                          (:item_id, :img_id, :rank)
                        RETURNING id
                        """
                    ),
                    {
                        "item_id": str(item_uuid),
                        "img_id": img_id,
                        "rank": rank,
                    },
                ).mappings().one()
                rel_id = str(relation_row["id"])
                trans.commit()
            except Exception:
                trans.rollback()
                raise

        log.info(
            "Stored image '%s' (w=%d h=%d) in %s, img_id=%s, rel_id=%s, rank=%d",
            final_name, width, height, dir_name, img_id, rel_id, rank
        )

    except Exception as exc:
        log.exception("Database operation failed for image %s", locals().get("final_name"))
        raise RuntimeError("Database error while saving image") from exc

    return {
        "item_id": str(item_uuid),
        "img": {
            "id": str(img_id),
            "dir": dir_name,
            "file_name": final_name,
            "thumbnail": thumb_name,
            "source_url": source_url_value,
            "original_file_name": original_name,
            "dim_width": width,
            "dim_height": height,
        },
        "relation": {"id": str(rel_id), "rank": rank},
        "public_paths": {
            "image": f"/imgs/{dir_name}/{final_name}",
            "thumbnail": f"/imgs/{dir_name}/{thumb_name}",
        },
    }


def handle_img_upload(item_id: str):
    if not item_id:
        return jsonify(error="Missing required field 'item_id'"), 400
    try:
        item_uuid = _validate_uuid(item_id)
    except BadRequest as e:
        return jsonify(error=str(e)), 400

    clipboard_flag = request.form.get("img_clipboard", "").strip()
    is_clipboard_upload = clipboard_flag not in {"", "0", "false", "False"}

    upload = request.files.get("img_file")
    url = request.form.get("img_url", "").strip()
    data_url = request.form.get("img_data", "").strip()

    try:
        result = store_image_for_item(
            item_uuid=item_uuid,
            upload=upload if upload else None,
            source_url=url,
            data_url=data_url,
            clipboard_upload=is_clipboard_upload or bool(data_url),
        )
    except FileNotFoundError as exc:
        return jsonify(error=str(exc)), 404
    except BadRequest as exc:
        log.info("BadRequest in handle_img_upload: %s", exc)
        return jsonify(error=str(exc)), 400
    except UnsupportedMedia as exc:
        log.info("Unsupported media in handle_img_upload: %s", exc)
        return jsonify(error=str(exc)), 400
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 500

    return jsonify(ok=True, **result), 201

@bp_image.get("/img_upload")
def img_upload_2():
    return img_upload()


@bp_image.get("/getimagesfor")
def get_images_for_item():
    item_id = request.args.get("item_id") or request.args.get("item") or ""
    item_id = item_id.strip()
    if not item_id:
        return jsonify(error="Missing item_id"), 400

    if item_id.lower() == "new":
        return jsonify({"images": []})

    try:
        item_uuid = _validate_uuid(item_id)
    except BadRequest as e:
        return jsonify(error=str(e)), 400

    try:
        with get_db_conn() as conn:
            rows = (
                conn.execute(
                    text(
                        """
                        SELECT ii.img_id, ii.rank, img.dir, img.file_name
                        FROM item_images AS ii
                        JOIN images AS img ON img.id = ii.img_id
                        WHERE ii.item_id = :item_id
                        ORDER BY ii.rank ASC, ii.date_updated DESC, img.date_updated DESC
                        """
                    ),
                    {"item_id": str(item_uuid)},
                )
                .mappings()
                .all()
            )
    except Exception:
        log.exception("Failed to fetch images for item %s", item_uuid)
        return jsonify(error="Failed to fetch images"), 500

    images = [
        {
            "uuid": str(row["img_id"]),
            "src": f"/imgs/{row['dir']}/{row['file_name']}",
            "rank": row["rank"],
        }
        for row in rows
    ]
    return jsonify({"images": images})


@bp_image.post("/deleteimagefor")
def delete_image_for_item():
    payload = request.get_json(silent=True) or {}
    item_id = (payload.get("item_id") or payload.get("item") or "").strip()
    img_id = (
        payload.get("img_id")
        or payload.get("image_id")
        or payload.get("uuid")
        or ""
    ).strip()

    if not item_id or not img_id:
        return jsonify(error="Missing item_id or img_id"), 400

    try:
        item_uuid = _validate_uuid(item_id)
        img_uuid = _validate_uuid(img_id)
    except BadRequest as e:
        return jsonify(error=str(e)), 400

    try:
        with get_db_conn() as conn:
            trans = conn.begin()
            try:
                result = conn.execute(
                    text(
                        "DELETE FROM item_images WHERE item_id = :item_id AND img_id = :img_id"
                    ),
                    {"item_id": str(item_uuid), "img_id": str(img_uuid)},
                )
                if result.rowcount == 0:
                    trans.rollback()
                    return jsonify(error="Image relationship not found"), 404
                trans.commit()
            except Exception:
                trans.rollback()
                raise
    except Exception:
        log.exception(
            "Failed to delete image %s for item %s", img_uuid, item_uuid
        )
        return jsonify(error="Failed to delete image"), 500

    return jsonify(ok=True)


@bp_image.post("/setmainimagesfor")
def set_main_image_for_item():
    payload = request.get_json(silent=True) or {}
    item_id = (payload.get("item_id") or payload.get("item") or "").strip()
    img_id = (
        payload.get("img_id")
        or payload.get("image_id")
        or payload.get("uuid")
        or ""
    ).strip()

    if not item_id or not img_id:
        return jsonify(error="Missing item_id or img_id"), 400

    try:
        item_uuid = _validate_uuid(item_id)
        img_uuid = _validate_uuid(img_id)
    except BadRequest as e:
        return jsonify(error=str(e)), 400

    img_uuid_str = str(img_uuid)

    try:
        with get_db_conn() as conn:
            trans = conn.begin()
            try:
                rows = (
                    conn.execute(
                        text(
                            """
                            SELECT img_id, rank
                            FROM item_images
                            WHERE item_id = :item_id
                            ORDER BY rank ASC, date_updated DESC
                            FOR UPDATE
                            """
                        ),
                        {"item_id": str(item_uuid)},
                    )
                    .mappings()
                    .all()
                )

                if not rows:
                    trans.rollback()
                    return jsonify(error="No images found for item"), 404

                image_ids = {str(row["img_id"]) for row in rows}
                if img_uuid_str not in image_ids:
                    trans.rollback()
                    return jsonify(error="Image not associated with item"), 404

                current_main = next((row for row in rows if row["rank"] == 0), None)
                if current_main and str(current_main["img_id"]) != img_uuid_str:
                    conn.execute(
                        text(
                            "UPDATE item_images SET rank = rank + 1 WHERE item_id = :item_id AND img_id = :img_id"
                        ),
                        {
                            "item_id": str(item_uuid),
                            "img_id": str(current_main["img_id"]),
                        },
                    )

                conn.execute(
                    text(
                        "UPDATE item_images SET rank = 0 WHERE item_id = :item_id AND img_id = :img_id"
                    ),
                    {"item_id": str(item_uuid), "img_id": img_uuid_str},
                )

                trans.commit()
            except Exception:
                trans.rollback()
                raise
    except Exception:
        log.exception(
            "Failed to set main image %s for item %s", img_uuid, item_uuid
        )
        return jsonify(error="Failed to update main image"), 500

    return jsonify(ok=True)

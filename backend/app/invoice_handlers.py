from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from flask import Blueprint, jsonify, request
from sqlalchemy import text
from werkzeug.datastructures import FileStorage

from automation.gmail_proc import (
    extract_text_content,
    get_full_message,
    gmail_date_x_days_query,
    list_message_ids,
)
from automation.order_num_extract import extract_order_number, extract_order_number_and_url
from app.db import get_engine, update_db_row_by_dict

from .user_login import login_required

bp = Blueprint("invoice_handlers", __name__, url_prefix="/api")

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRETS_PATH = REPO_ROOT / "config" / "secrets.json"


def _load_gmail_token() -> Dict[str, Any]:
    if not SECRETS_PATH.exists():
        raise FileNotFoundError(f"Missing secrets file at {SECRETS_PATH}")

    data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    token = data.get("gmail_api_token")
    if not isinstance(token, dict):
        raise ValueError("gmail_api_token must be a JSON object containing OAuth credentials")
    return token


def _build_gmail_service() -> Any:
    token_info = _load_gmail_token()

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - dependency provided in runtime env
        raise RuntimeError("Google API client libraries are required to poll Gmail") from exc

    scopes = token_info.get("scopes")
    if not scopes:
        scopes = ["https://www.googleapis.com/auth/gmail.readonly"]

    creds = Credentials.from_authorized_user_info(token_info, scopes=scopes)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return build("gmail", "v1", credentials=creds)


def _fetch_seen_ids() -> Sequence[str]:
    engine = get_engine()
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT email_uuid FROM gmail_seen"))
            return [str(row[0]) for row in result if row[0] is not None]
    except Exception:
        log.exception("Failed to load gmail_seen entries; treating as empty set")
        return []


def _normalize_gmail_id(message_id: Optional[str]) -> Optional[str]:
    if not message_id:
        return message_id

    cleaned = message_id.strip()
    hex_candidate = cleaned.replace("-", "")
    hex_chars = set("0123456789abcdefABCDEF")
    if 16 <= len(hex_candidate) <= 32 and set(hex_candidate).issubset(hex_chars):
        try:
            padded = hex_candidate.rjust(32, "0")
            return str(uuid.UUID(padded))
        except Exception:
            log.debug("Message id %s looked hex-like but failed UUID normalization", message_id)
    return cleaned


def _determine_lookback_days() -> int:
    engine = get_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT date_seen FROM gmail_seen ORDER BY date_seen DESC LIMIT 1")
            ).first()
    except Exception:
        log.exception("Failed to query gmail_seen for last seen date; defaulting to 7 days")
        return 7

    if not row or not row[0]:
        return 7

    last_seen = row[0]
    if isinstance(last_seen, datetime):
        last_dt = last_seen
    elif isinstance(last_seen, date):
        last_dt = datetime.combine(last_seen, datetime.min.time(), tzinfo=timezone.utc)
    else:
        last_dt = datetime.now(timezone.utc)

    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    days_since = max(0, (now - last_dt).days)
    return days_since + 7


def _parse_email_date(header_value: Optional[str]) -> datetime:
    if not header_value:
        return datetime.now(timezone.utc)

    try:
        parsed = parsedate_to_datetime(header_value)
    except (TypeError, ValueError):
        parsed = None

    if parsed is None:
        return datetime.now(timezone.utc)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _unwrap_db_payload(response: Any) -> Dict[str, Any]:
    if hasattr(response, "get_json"):
        try:
            payload = response.get_json()
        except Exception:
            payload = {}
    elif isinstance(response, dict):
        payload = response
    else:
        payload = {}

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
    return payload if isinstance(payload, dict) else {}


def _handle_gmail_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    headers = {h.get("name", "").lower(): h.get("value", "") for h in msg.get("payload", {}).get("headers", [])}
    subject = headers.get("subject", "")
    message_id = msg.get("id")
    normalized_id = _normalize_gmail_id(message_id)

    content = extract_text_content(msg.get("payload", {}))
    html_body = content.get("html") or ""
    text_body = content.get("text") or ""

    order_number = None
    order_url = None
    if html_body:
        order_number, order_url = extract_order_number_and_url(html_body)
    if not order_number:
        order_number = extract_order_number(subject) or extract_order_number(text_body)

    if order_number:
        order_number = order_number.strip()

    email_date = _parse_email_date(headers.get("date"))

    invoice_id: Optional[str] = None
    invoice_error: Optional[str] = None

    if order_number:
        gmail_url = f"https://mail.google.com/mail/u/0/#all/{message_id}"
        urls_value = gmail_url
        if order_url:
            urls_value = f"{urls_value};{order_url}"

        invoice_payload: Dict[str, Any] = {
            "date": email_date,
            "order_number": order_number,
            "shop_name": "",  # TODO: derive a shop/sender name from the message metadata.
            "urls": urls_value,
            "subject": subject,
            "html": html_body or text_body,
            "notes": "",
            "has_been_processed": False,
            "snooze": datetime.now(timezone.utc),
            "is_deleted": False,
        }

        engine = get_engine()
        invoice_resp, invoice_status = update_db_row_by_dict(engine, "invoices", "new", invoice_payload, fuzzy=False)
        if invoice_status >= 400:
            payload = _unwrap_db_payload(invoice_resp)
            try:
                invoice_error = json.dumps(payload)
            except TypeError:
                invoice_error = str(payload)
            log.error("Failed to insert invoice for Gmail message %s: %s", message_id, invoice_error)
        else:
            payload = _unwrap_db_payload(invoice_resp)
            invoice_id = payload.get("id") if isinstance(payload, dict) else None

    gmail_payload: Dict[str, Any] = {
        "email_uuid": normalized_id or message_id,
        "date_seen": email_date,
        "url1": None,
        "url2": None,
    }
    if invoice_id:
        gmail_payload["invoice_id"] = invoice_id

    engine = get_engine()
    gmail_resp, gmail_status = update_db_row_by_dict(engine, "gmail_seen", "new", gmail_payload, fuzzy=False)
    if gmail_status >= 400:
        payload = _unwrap_db_payload(gmail_resp)
        log.error("Failed to insert gmail_seen row for message %s: %s", message_id, payload)

    status = "invoice_created" if invoice_id else ("invoice_failed" if invoice_error else "no_order_number")

    return {
        "message_id": message_id,
        "normalized_id": normalized_id,
        "order_number": order_number,
        "invoice_id": invoice_id,
        "email_date": email_date.isoformat(),
        "invoice_error": invoice_error,
        "gmail_status": gmail_status,
        "status": status,
    }

def _ingest_invoice_file(file_storage: FileStorage) -> Dict[str, Any]:
    """Prepare metadata for a single uploaded invoice/email file.

    TODO: Parse MIME and HTML structures similar to backend/automation/gmail_proc.py.
    TODO: Reuse backend/automation/order_num_extract.py heuristics to derive order identifiers.
    TODO: Map parsed fields into the invoice tables defined in backend/schemas/schema.sql.
    """
    return {
        "filename": file_storage.filename or "",
        "status": "pending",
        "notes": "TODO: implement invoice ingestion pipeline.",
    }


@bp.route("/checkemail", methods=["POST"])
@login_required
def check_email() -> Any:
    log.info("Mailbox check requested")

    try:
        service = _build_gmail_service()
    except Exception as exc:
        log.exception("Unable to initialise Gmail client")
        return jsonify({"ok": False, "error": "Failed to initialise Gmail client", "detail": str(exc)}), 500

    lookback_days = _determine_lookback_days()
    query = gmail_date_x_days_query(lookback_days)

    try:
        message_ids = list_message_ids(service, query)
    except Exception as exc:  # pragma: no cover - network interaction
        log.exception("Unable to list Gmail messages for query %s", query)
        return jsonify({"ok": False, "error": "Failed to query Gmail", "detail": str(exc)}), 502

    seen_ids = list(_fetch_seen_ids())
    seen_normalized = {_normalize_gmail_id(value) for value in seen_ids if value}

    new_ids: List[str] = []
    for mid in message_ids:
        normalized = _normalize_gmail_id(mid)
        if mid in seen_ids or (normalized and normalized in seen_normalized):
            continue
        new_ids.append(mid)

    processed: List[Dict[str, Any]] = []
    for mid in new_ids:
        try:
            msg = get_full_message(service, mid)
        except Exception as exc:  # pragma: no cover - network interaction
            log.exception("Failed to fetch Gmail message %s", mid)
            processed.append({"message_id": mid, "status": "fetch_error", "error": str(exc)})
            continue

        try:
            result = _handle_gmail_message(msg)
            processed.append(result)
        except Exception as exc:
            log.exception("Failed to process Gmail message %s", mid)
            processed.append({"message_id": mid, "status": "processing_error", "error": str(exc)})

    summary = {
        "ok": True,
        "queried_days": lookback_days,
        "query": query,
        "checked": len(message_ids),
        "new_messages": len(new_ids),
        "processed": processed,
    }

    return jsonify(summary)


@bp.route("/invoiceupload", methods=["POST"])
@login_required
def invoice_upload() -> Any:
    """Accept uploaded invoice files and process them like inbound emails.

    TODO: Support bulk uploads by streaming each file into the same pipeline used for Gmail messages.
    TODO: Capture missing metadata (sender, subject, timestamps) with sensible defaults when absent.
    TODO: Persist invoice and attachment records according to backend/schemas/schema.sql.
    """
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify({"error": "No invoice files provided."}), 400

    processed: List[Dict[str, Any]] = []
    for storage in files:
        if not storage:
            continue
        processed.append(_ingest_invoice_file(storage))
        # TODO: Persist each result just like a processed Gmail message would be saved.

    return jsonify(
        {
            "ok": True,
            "processed": processed,
            "message": "Invoice upload accepted. TODO: persist invoices and metadata.",
        }
    )

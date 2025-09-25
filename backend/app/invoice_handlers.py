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
from automation.html_dom_finder import analyze as analyze_dom_report
from app.db import get_engine, update_db_row_by_dict

from .user_login import login_required

bp = Blueprint("invoice_handlers", __name__, url_prefix="/api")

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRETS_PATH = REPO_ROOT / "config" / "secrets.json"


def _load_gmail_token() -> Dict[str, Any]:
    token_path = SECRETS_PATH.with_name("gmail_token.json")

    if token_path.exists():
        raw_token = token_path.read_text(encoding="utf-8")
        try:
            token_data = json.loads(raw_token)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON content in Gmail token file at {token_path}"
            ) from exc
        if not isinstance(token_data, dict):
            raise ValueError("gmail_token.json must contain a JSON object with OAuth credentials")
        return token_data

    if not SECRETS_PATH.exists():
        raise FileNotFoundError(f"Missing secrets file at {SECRETS_PATH}")

    data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    token = data.get("gmail_api_token")
    if not isinstance(token, dict):
        raise ValueError("gmail_api_token must be a JSON object containing OAuth credentials")
    return token


def _build_gmail_service() -> Any:
    token_path = SECRETS_PATH.with_name("gmail_token.json")
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

    persist_token = not token_path.exists()

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        persist_token = True

    if persist_token:
        token_path.write_text(creds.to_json(), encoding="utf-8")

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


def _condense_dom_report(report: Dict[str, Any]) -> str:
    summary: List[Dict[str, str]] = []
    candidates = report.get("top_candidates") if isinstance(report, dict) else None
    if not isinstance(candidates, list):
        candidates = []

    for item in candidates:
        if not isinstance(item, dict):
            continue

        url = (item.get("url") or "").strip()
        preview_text = (item.get("preview_text") or "").strip()
        anchor_text = (item.get("anchor_text") or "").strip()

        chosen_text = ""
        if url:
            chosen_text = anchor_text if len(anchor_text) >= 12 else preview_text
            if len(chosen_text) < 12:
                chosen_text = ""
        else:
            if len(preview_text) >= 12:
                chosen_text = preview_text

        if chosen_text or url:
            summary.append({
                "text": chosen_text if chosen_text else "",
                "url": url,
            })

    return json.dumps(summary, ensure_ascii=False)


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
    if subject:
        order_number = extract_order_number(subject)
    if not order_number and html_body:
        order_number, order_url = extract_order_number_and_url(html_body)
    if not order_number:
        order_number = extract_order_number(text_body)

    if order_number:
        order_number = order_number.strip()

    email_date = _parse_email_date(headers.get("date"))

    invoice_id: Optional[str] = None
    invoice_error: Optional[str] = None

    auto_summary = "[]"
    if html_body:
        try:
            report, _ = analyze_dom_report(html_body)
            auto_summary = _condense_dom_report(report)
        except Exception:
            log.exception("Failed to generate DOM auto-summary for Gmail message")
            auto_summary = "[]"

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
            "auto_summary": auto_summary,
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
    """Prepare metadata for a single uploaded invoice/email file."""

    filename = file_storage.filename or ""
    try:
        raw_payload = file_storage.read()
    except Exception:
        log.exception("Failed to read uploaded invoice file %s", filename)
        return {
            "filename": filename,
            "status": "read_error",
            "order_number": None,
            "invoice_error": "Unable to read uploaded file.",
        }
    finally:
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass

    if isinstance(raw_payload, str):
        html_body = raw_payload
    else:
        html_body = ""
        if raw_payload:
            for encoding in ("utf-8", "utf-16", "latin-1"):
                try:
                    html_body = raw_payload.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if not html_body:
                html_body = raw_payload.decode("utf-8", errors="ignore")

    html_body = (html_body or "").lstrip("\ufeff")

    if not html_body.strip():
        return {
            "filename": filename,
            "status": "empty_file",
            "order_number": None,
        }

    order_number: Optional[str]
    order_url: Optional[str]
    dom_report_available = False
    dom_report: Optional[Dict[str, Any]] = None

    order_number, order_url = extract_order_number_and_url(html_body)
    if not order_number:
        order_number = extract_order_number(html_body)
        order_url = order_url or ""

    if order_number:
        order_number = order_number.strip()

    auto_summary = "[]"
    if html_body:
        try:
            report, _ = analyze_dom_report(html_body)
            dom_report = report
            auto_summary = _condense_dom_report(report)
            dom_report_available = True
        except Exception:
            log.exception(
                "Failed to generate DOM auto-summary for uploaded invoice %s",
                filename,
            )
            auto_summary = "[]"

    invoice_id: Optional[str] = None
    invoice_error: Optional[str] = None
    invoice_status: Optional[int] = None

    if order_number:
        urls_value = order_url or ""
        now = datetime.now(timezone.utc)
        invoice_payload: Dict[str, Any] = {
            "date": now,
            "order_number": order_number,
            "shop_name": "",
            "urls": urls_value,
            "subject": "",
            "html": html_body,
            "notes": f"Uploaded via invoice_upload: {filename}",
            "has_been_processed": False,
            "auto_summary": auto_summary,
            "snooze": now,
            "is_deleted": False,
        }

        engine = get_engine()
        invoice_resp, invoice_status = update_db_row_by_dict(
            engine, "invoices", "new", invoice_payload, fuzzy=False
        )
        if invoice_status >= 400:
            payload = _unwrap_db_payload(invoice_resp)
            try:
                invoice_error = json.dumps(payload)
            except TypeError:
                invoice_error = str(payload)
            log.error(
                "Failed to insert invoice for uploaded file %s: %s",
                filename,
                invoice_error,
            )
        else:
            payload = _unwrap_db_payload(invoice_resp)
            if isinstance(payload, dict):
                invoice_id = payload.get("id")

    status = "invoice_created" if invoice_id else (
        "invoice_failed" if invoice_error else "no_order_number"
    )

    result: Dict[str, Any] = {
        "filename": filename,
        "status": status,
        "order_number": order_number,
        "order_url": order_url or "",
        "auto_summary": auto_summary,
        "dom_report_available": dom_report_available,
    }
    if dom_report is not None:
        result["dom_report"] = dom_report
    if invoice_id:
        result["invoice_id"] = invoice_id
    if invoice_error:
        result["invoice_error"] = invoice_error
    if invoice_status is not None:
        result["invoice_status"] = invoice_status

    return result


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
    """Accept uploaded invoice files and process them like inbound emails."""

    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify({"error": "No invoice files provided."}), 400

    processed: List[Dict[str, Any]] = []
    for storage in files:
        if not storage:
            continue
        try:
            result = _ingest_invoice_file(storage)
        except Exception as exc:
            log.exception(
                "Failed to ingest uploaded invoice %s",
                getattr(storage, "filename", ""),
            )
            result = {
                "filename": getattr(storage, "filename", ""),
                "status": "processing_error",
                "error": str(exc),
            }
        processed.append(result)

    created = sum(1 for item in processed if item.get("status") == "invoice_created")
    failure_statuses = {"invoice_failed", "processing_error", "read_error"}
    failed = sum(1 for item in processed if item.get("status") in failure_statuses)
    missing = sum(1 for item in processed if item.get("status") == "no_order_number")
    empty = sum(1 for item in processed if item.get("status") == "empty_file")

    summary = {
        "ok": failed == 0,
        "processed": processed,
        "created": created,
        "failed": failed,
        "missing_order_number": missing,
        "empty_files": empty,
    }

    return jsonify(summary)

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from flask import Blueprint, jsonify, request, current_app
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
from automation.html_invoice_helpers import parse_mhtml_from_string, sniff_format
from lxml import html as lxml_html
from app.db import get_db_item_as_dict, get_engine, update_db_row_by_dict, unwrap_db_result
from .config_loader import get_private_dir_path
from .user_login import login_required
from .job_manager import get_job_manager

bp = Blueprint("invoice_handlers", __name__, url_prefix="/api")

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRETS_PATH = REPO_ROOT / "config" / "secrets.json"


def _gmail_token_path() -> Path:
    """
    Resolve the Gmail OAuth token cache location, honoring the optional private_dir hint.
    """
    private_dir = get_private_dir_path()
    if private_dir is not None:
        # Keep the token beside other private runtime artifacts when configured.
        return Path(private_dir) / "gmail_token.json"
    return SECRETS_PATH.with_name("gmail_token.json")


def _load_gmail_token() -> Dict[str, Any]:
    token_path = _gmail_token_path()

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
    token_path = _gmail_token_path()
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
        token_path.parent.mkdir(parents=True, exist_ok=True)
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


def _unwrap_db_payload(response: Any) -> Tuple[Dict[str, Any], str]:
    _, _, reply_obj, row_dict, _, message_text = unwrap_db_result(response)
    payload = row_dict if row_dict else reply_obj
    return payload, message_text


def _serialize_invoice_row(row: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(row)
    raw_id = data.get("id")
    if raw_id is not None:
        try:
            data["id"] = str(raw_id)
        except Exception:
            pass
    for key in ("date", "snooze"):
        value = data.get(key)
        if hasattr(value, "isoformat"):
            try:
                data[key] = value.isoformat()
            except Exception:
                pass
    return data

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
        (
            invoice_status,
            invoice_failed,
            _invoice_reply,
            invoice_row,
            invoice_pk,
            invoice_message,
        ) = unwrap_db_result(
            update_db_row_by_dict(engine, "invoices", "new", invoice_payload, fuzzy=False)
        )
        if invoice_failed:
            invoice_error = invoice_message
            log.error("Failed to insert invoice for Gmail message %s: %s", message_id, invoice_message)
        else:
            invoice_id = invoice_pk or invoice_row.get("id")

    gmail_payload: Dict[str, Any] = {
        "email_uuid": normalized_id or message_id,
        "date_seen": email_date,
    }
    if invoice_id:
        gmail_payload["invoice_id"] = invoice_id

    engine = get_engine()
    (
        gmail_status,
        gmail_failed,
        _gmail_reply,
        _gmail_row,
        _gmail_pk,
        gmail_message,
    ) = unwrap_db_result(
        update_db_row_by_dict(engine, "gmail_seen", "new", gmail_payload, fuzzy=False)
    )
    if gmail_failed:
        log.error("Failed to insert gmail_seen row for message %s: %s", message_id, gmail_message)

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

    sniffed_format: Optional[str] = None
    if html_body:
        try:
            sniffed_format = sniff_format(html_body)
        except Exception:
            log.exception(
                "Failed to determine uploaded invoice format for %s",
                filename,
            )
            sniffed_format = None

    if sniffed_format == "mhtml":
        try:
            parsed_root = parse_mhtml_from_string(html_body)
            html_body = lxml_html.tostring(parsed_root, encoding="unicode")
        except Exception:
            log.exception(
                "Failed to extract HTML from MHTML invoice %s",
                filename,
            )

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
        (
            invoice_status,
            invoice_failed,
            _invoice_reply,
            invoice_row,
            invoice_pk,
            invoice_message,
        ) = unwrap_db_result(
            update_db_row_by_dict(engine, "invoices", "new", invoice_payload, fuzzy=False)
        )
        if invoice_failed:
            invoice_error = invoice_message
            log.error(
                "Failed to insert invoice for uploaded file %s: %s",
                filename,
                invoice_message,
            )
        else:
            invoice_id = invoice_pk or invoice_row.get("id")

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


@bp.route("/getinvoice", methods=["POST"])
@login_required
def get_invoice_api() -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Invoice UUID is required."}), 400
    invoice_uuid = payload.get("uuid") or payload.get("id")
    if isinstance(invoice_uuid, str):
        invoice_uuid = invoice_uuid.strip()
    if not invoice_uuid:
        return jsonify({"error": "Invoice UUID is required."}), 400
    engine = get_engine()
    try:
        invoice_row = get_db_item_as_dict(engine, "invoices", invoice_uuid)
    except LookupError:
        return jsonify({"error": "Invoice not found."}), 404
    except ValueError:
        return jsonify({"error": "Invalid invoice UUID."}), 400
    except Exception:
        log.exception("Failed to load invoice %s", invoice_uuid)
        return jsonify({"error": "Failed to load invoice."}), 500
    return jsonify(_serialize_invoice_row(invoice_row)), 200


@bp.route("/setinvoice", methods=["POST"])
@login_required
def set_invoice_api() -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Invoice payload must be an object."}), 400
    invoice_uuid = payload.get("id") or payload.get("uuid")
    if isinstance(invoice_uuid, str):
        invoice_uuid = invoice_uuid.strip()
    cleaned_payload = dict(payload)
    cleaned_payload.pop("uuid", None)
    if "id" in cleaned_payload:
        raw_id = cleaned_payload["id"]
        if raw_id is None or (isinstance(raw_id, str) and not raw_id.strip()):
            cleaned_payload.pop("id", None)
    for key in ("date", "snooze"):
        value = cleaned_payload.get(key)
        if isinstance(value, str) and not value.strip():
            cleaned_payload[key] = None
    engine = get_engine()
    target_uuid = invoice_uuid if invoice_uuid else "new"
    update_result = update_db_row_by_dict(
        engine,
        "invoices",
        target_uuid,
        cleaned_payload,
        fuzzy=False,
    )
    (
        status_code,
        is_error,
        reply_obj,
        invoice_row,
        primary_key,
        _message_text,
    ) = unwrap_db_result(update_result)
    if is_error:
        return jsonify(reply_obj), status_code

    invoice_id = primary_key or invoice_row.get("id")
    if not invoice_id and invoice_uuid:
        lowered = str(invoice_uuid).lower()
        if lowered not in {"new", "insert"}:
            invoice_id = invoice_uuid
    if not invoice_id:
        invoice_id = cleaned_payload.get("id")

    reloaded_row = None
    if invoice_id:
        try:
            reloaded_row = get_db_item_as_dict(engine, "invoices", invoice_id)
        except Exception:
            log.exception("Invoice %s saved but failed to reload", invoice_id)
            reloaded_row = None
    final_row = reloaded_row or invoice_row or dict(cleaned_payload)
    if invoice_id:
        final_row.setdefault("id", invoice_id)
    return jsonify(_serialize_invoice_row(final_row)), status_code


def _check_email_task(_context: Dict[str, Any]) -> Dict[str, Any]:
    log.info("Mailbox check requested")
    try:
        service = _build_gmail_service()
    except Exception as exc:
        log.exception("Unable to initialise Gmail client")
        raise RuntimeError("Failed to initialise Gmail client.") from exc

    lookback_days = _determine_lookback_days()
    query = gmail_date_x_days_query(lookback_days)

    try:
        message_ids = list_message_ids(service, query)
    except Exception as exc:
        log.exception("Unable to list Gmail messages for query %s", query)
        raise RuntimeError("Failed to query Gmail.") from exc

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
        except Exception as exc:
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

    return summary


def _invoice_upload_task(context: Dict[str, Any]) -> Dict[str, Any]:
    files = context.get("files")
    if not isinstance(files, list) or len(files) == 0:
        raise ValueError("No invoice files provided.")

    processed: List[Dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        filename = str(entry.get("filename") or "")
        read_error = entry.get("read_error")
        if read_error:
            processed.append({
                "filename": filename,
                "status": "read_error",
                "order_number": None,
                "invoice_error": "Unable to read uploaded file.",
                "error": str(read_error),
            })
            continue

        raw_data = entry.get("data")
        if raw_data is None:
            processed.append({
                "filename": filename,
                "status": "read_error",
                "order_number": None,
                "invoice_error": "Unable to read uploaded file.",
            })
            continue

        try:
            if isinstance(raw_data, str):
                raw_bytes = raw_data.encode("utf-8")
            else:
                raw_bytes = bytes(raw_data)
        except Exception as exc:
            log.exception("Failed to normalise uploaded invoice %s", filename)
            processed.append({
                "filename": filename,
                "status": "processing_error",
                "error": str(exc),
            })
            continue

        storage = FileStorage(
            stream=io.BytesIO(raw_bytes),
            filename=filename,
            content_type=entry.get("content_type") or "application/octet-stream",
        )
        try:
            result = _ingest_invoice_file(storage)
        except Exception as exc:
            log.exception(
                "Failed to ingest uploaded invoice %s",
                filename,
            )
            result = {
                "filename": filename,
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

    return summary


def _analyze_invoice_html_task(context: Dict[str, Any]) -> Dict[str, Any]:
    invoice_uuid = str(context.get("invoice_uuid") or "").strip()
    html_chunk = context.get("html")

    if not invoice_uuid:
        raise ValueError("Invoice UUID is required.")

    if not isinstance(html_chunk, str) or not html_chunk.strip():
        raise ValueError("HTML content is required.")

    try:
        fragment_parent = lxml_html.fragment_fromstring(html_chunk, create_parent=True)
    except Exception as exc:
        log.exception("Failed to parse HTML fragment for invoice %s", invoice_uuid)
        raise RuntimeError("Provided HTML could not be parsed.") from exc

    try:
        report, _ = analyze_dom_report(html_chunk)
        condensed_summary = _condense_dom_report(report)
        new_summary_entries = json.loads(condensed_summary)
    except Exception as exc:
        log.exception("Failed to analyze HTML fragment for invoice %s", invoice_uuid)
        raise RuntimeError("Failed to analyze HTML.") from exc

    if not isinstance(new_summary_entries, list):
        new_summary_entries = []

    engine = get_engine()

    try:
        invoice_row = get_db_item_as_dict(engine, "invoices", invoice_uuid)
    except LookupError:
        raise ValueError("Invoice not found.")
    except ValueError:
        raise ValueError("Invalid invoice UUID.")
    except Exception as exc:
        log.exception("Failed to load invoice %s", invoice_uuid)
        raise RuntimeError("Failed to load invoice.") from exc

    existing_summary_raw = invoice_row.get("auto_summary")
    if isinstance(existing_summary_raw, str) and existing_summary_raw.strip():
        try:
            parsed = json.loads(existing_summary_raw)
            existing_summary_entries = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            existing_summary_entries = []
    else:
        existing_summary_entries = []

    combined_summary_entries = existing_summary_entries + new_summary_entries
    combined_summary_raw = json.dumps(combined_summary_entries, ensure_ascii=False)

    existing_html = invoice_row.get("html") or ""

    if not str(existing_html).strip():
        updated_html = html_chunk
    else:
        try:
            existing_root = lxml_html.fromstring(str(existing_html))
        except Exception as exc:
            log.exception("Failed to parse stored HTML for invoice %s", invoice_uuid)
            raise RuntimeError("Stored invoice HTML is invalid.") from exc

        if fragment_parent.text:
            if len(existing_root):
                last_child = existing_root[-1]
                last_child.tail = (last_child.tail or "") + fragment_parent.text
            else:
                existing_root.text = (existing_root.text or "") + fragment_parent.text

        for child in list(fragment_parent):
            existing_root.append(child)

        if fragment_parent.tail:
            if len(existing_root):
                last_child = existing_root[-1]
                last_child.tail = (last_child.tail or "") + fragment_parent.tail
            else:
                existing_root.text = (existing_root.text or "") + fragment_parent.tail

        updated_html = lxml_html.tostring(existing_root, encoding="unicode")

    update_payload = {
        "id": invoice_row.get("id") or invoice_uuid,
        "auto_summary": combined_summary_raw,
        "html": updated_html,
    }

    update_result = update_db_row_by_dict(
        engine, "invoices", invoice_uuid, update_payload, fuzzy=False
    )
    (
        status_code,
        is_error,
        reply_obj,
        row_data,
        primary_key,
        _message_text,
    ) = unwrap_db_result(update_result)

    if is_error:
        message = "Failed to update invoice."
        if isinstance(reply_obj, dict):
            message = (
                reply_obj.get("error")
                or reply_obj.get("message")
                or json.dumps(reply_obj, ensure_ascii=False)
            )
        else:
            message = str(reply_obj)
        raise RuntimeError(message or "Failed to update invoice.")

    try:
        updated_invoice = get_db_item_as_dict(engine, "invoices", invoice_uuid)
    except Exception:
        log.exception("Updated invoice %s saved but failed to reload", invoice_uuid)
        fallback_invoice = dict(row_data) if row_data else {"id": primary_key or invoice_uuid}
        fallback_invoice.setdefault("auto_summary", combined_summary_raw)
        fallback_invoice.setdefault("html", updated_html)
        updated_invoice = fallback_invoice

    return {"ok": True, "invoice": updated_invoice}
@bp.route("/checkemail", methods=["POST"])
@login_required
def check_email() -> Any:
    try:
        manager = get_job_manager(current_app)
        job_id = manager.start_job(_check_email_task, {})
    except Exception as exc:
        log.exception("Failed to enqueue mailbox check job")
        return jsonify({"ok": False, "error": str(exc)}), 503

    return jsonify({"job_id": job_id})


@bp.route("/invoiceupload", methods=["POST"])
@login_required
def invoice_upload() -> Any:
    files = request.files.getlist("files") or request.files.getlist("file")

    context_files: List[Dict[str, Any]] = []
    for storage in files:
        if not storage:
            continue
        filename = storage.filename or ""
        content_type = storage.content_type or "application/octet-stream"
        read_error: Optional[str] = None
        data_bytes: Optional[bytes] = None
        try:
            raw_data = storage.read()
            if isinstance(raw_data, bytes):
                data_bytes = raw_data
            elif isinstance(raw_data, str):
                data_bytes = raw_data.encode("utf-8")
            else:
                data_bytes = bytes(raw_data)
        except Exception as exc:
            read_error = str(exc)
            log.exception("Failed to read uploaded invoice %s", filename)
        finally:
            try:
                storage.stream.seek(0)
            except Exception:
                pass

        context_files.append(
            {
                "filename": filename,
                "content_type": content_type,
                "data": data_bytes,
                "read_error": read_error,
            }
        )

    try:
        manager = get_job_manager(current_app)
        job_id = manager.start_job(_invoice_upload_task, {"files": context_files})
    except Exception as exc:
        log.exception("Failed to enqueue invoice upload job")
        return jsonify({"error": str(exc)}), 503

    return jsonify({"job_id": job_id})


@bp.route("/analyzeinvoicehtml", methods=["POST"])
@login_required
def analyze_invoice_html() -> Any:
    payload = request.get_json(silent=True) or {}
    invoice_uuid = payload.get("uuid") or payload.get("invoice_uuid")
    html_chunk = payload.get("html")

    context = {
        "invoice_uuid": None if invoice_uuid is None else str(invoice_uuid),
        "html": html_chunk,
    }

    try:
        manager = get_job_manager(current_app)
        job_id = manager.start_job(_analyze_invoice_html_task, context)
    except Exception as exc:
        log.exception("Failed to enqueue invoice HTML analysis job")
        return jsonify({"ok": False, "error": str(exc)}), 503

    return jsonify({"job_id": job_id})

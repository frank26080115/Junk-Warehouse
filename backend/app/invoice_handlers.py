from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request, current_app
from sqlalchemy import text, bindparam
from werkzeug.datastructures import FileStorage

from automation.order_num_extract import extract_order_number, extract_order_number_and_url
from shop_handler import ShopHandler
from automation.html_invoice_helpers import parse_mhtml_from_string, sniff_format
from lxml import html as lxml_html
from app.db import get_db_item_as_dict, get_engine, update_db_row_by_dict, unwrap_db_result
from .user_login import login_required
from .job_manager import get_job_manager
from .helpers import normalize_pg_uuid
from .history import log_history

from backend.email_utils.gmail import GmailChecker
from backend.email_utils.imap import ImapChecker

bp = Blueprint("invoice_handlers", __name__, url_prefix="/api")

log = logging.getLogger(__name__)

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
    handler: Optional[ShopHandler] = None

    order_number, order_url = extract_order_number_and_url(html_body)
    if not order_number:
        order_number = extract_order_number(html_body)
        order_url = order_url or ""

    auto_summary = "[]"
    if html_body:
        try:
            handler = ShopHandler.ingest_html(html_body)
            auto_summary = handler.build_auto_summary()
            dom_report = handler.get_dom_report()
            dom_report_available = dom_report is not None
        except Exception:
            log.exception(
                "Failed to generate auto-summary using shop handler for uploaded invoice %s",
                filename,
            )
            handler = None
            auto_summary = "[]"

    if not order_number and handler is not None:
        handler_order_number = handler.get_order_number()
        if handler_order_number:
            order_number = handler_order_number.strip()

    if order_number:
        order_number = order_number.strip()

    invoice_id: Optional[str] = None
    invoice_error: Optional[str] = None
    invoice_status: Optional[int] = None

    if order_number:
        urls_value = order_url or ""
        now = datetime.now(timezone.utc)
        invoice_payload: Dict[str, Any] = {
            "date": now,
            "order_number": order_number,
            "shop_name": handler.get_shop_name() if handler else "",
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
            log_history(item_id_1=None, item_id_2=None, event="invoice ingest", meta=invoice_row)

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

    log_history(item_id_1=None, item_id_2=None, event="invoice " + ("insert" if target_uuid == "new" else "update"), meta=invoice_row)

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


def check_email_task(_context: Dict[str, Any]) -> Dict[str, Any]:
    """Coordinate mailbox polling across supported providers."""
    log.info("Mailbox check requested")
    # Gather Gmail updates first so any OAuth errors are reported promptly.
    gmail_summary = GmailChecker.check_email()
    # Poll the configured IMAP mailbox next; this may be skipped when unconfigured.
    imap_summary = ImapChecker.check_email()
    overall_ok = True
    for summary in (gmail_summary, imap_summary):
        if isinstance(summary, dict) and not summary.get("ok", True):
            overall_ok = False
    return {
        "ok": overall_ok,
        "gmail": gmail_summary,
        "imap": imap_summary,
    }

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
        handler = ShopHandler.ingest_html(html_chunk)
        condensed_summary = handler.build_auto_summary()
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
        job_id = manager.start_job(check_email_task, {})
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


@bp.route("/invoicesassociations", methods=["POST"])
@login_required
def bulk_link_invoices_api() -> Any:
    payload = request.get_json(silent=True) or {}
    table_name = str(payload.get("table") or "invoices").strip().lower()
    if table_name != "invoices":
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Only invoice associations are supported by this endpoint.",
                }
            ),
            400,
        )

    target_uuid = payload.get("target_uuid")
    if not target_uuid:
        return jsonify({"ok": False, "error": "Missing target UUID."}), 400

    try:
        normalized_target = normalize_pg_uuid(str(target_uuid))
    except Exception as exc:
        log.debug("bulk_link_invoices_api: invalid target UUID %r: %s", target_uuid, exc)
        return jsonify({"ok": False, "error": "Invalid target UUID."}), 400

    raw_ids = payload.get("pks")
    if not isinstance(raw_ids, list):
        return jsonify({"ok": False, "error": "pks must be a list."}), 400

    insert_sql = text(
        """
        INSERT INTO invoice_items (item_id, invoice_id)
        VALUES (:item_id, :invoice_id)
        ON CONFLICT DO NOTHING
        """
    )

    linked: List[str] = []
    with get_engine().begin() as conn:
        for candidate in raw_ids:
            try:
                normalized_invoice = normalize_pg_uuid(str(candidate))
            except Exception as exc:
                log.debug(
                    "bulk_link_invoices_api: skipping invalid invoice identifier %r: %s",
                    candidate,
                    exc,
                )
                continue

            result = conn.execute(
                insert_sql,
                {"item_id": normalized_target, "invoice_id": normalized_invoice},
            )
            if result.rowcount and result.rowcount > 0:
                linked.append(normalized_invoice)

    return jsonify({"ok": True, "linked": len(linked), "invoices": linked})


@bp.route("/invoicesassociations", methods=["DELETE"])
@login_required
def bulk_unlink_invoices_api() -> Any:
    payload = request.get_json(silent=True) or {}
    table_name = str(payload.get("table") or "invoices").strip().lower()
    if table_name != "invoices":
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Only invoice associations are supported by this endpoint.",
                }
            ),
            400,
        )

    target_uuid = payload.get("target_uuid")
    if not target_uuid:
        return jsonify({"ok": False, "error": "Missing target UUID."}), 400

    try:
        normalized_target = normalize_pg_uuid(str(target_uuid))
    except Exception as exc:
        log.debug("bulk_unlink_invoices_api: invalid target UUID %r: %s", target_uuid, exc)
        return jsonify({"ok": False, "error": "Invalid target UUID."}), 400

    raw_ids = payload.get("pks")
    if not isinstance(raw_ids, list):
        return jsonify({"ok": False, "error": "pks must be a list."}), 400

    normalized_invoices: List[str] = []
    for candidate in raw_ids:
        try:
            normalized_invoices.append(normalize_pg_uuid(str(candidate)))
        except Exception as exc:
            log.debug(
                "bulk_unlink_invoices_api: skipping invalid invoice identifier %r: %s",
                candidate,
                exc,
            )

    if not normalized_invoices:
        return jsonify({"ok": False, "error": "No valid invoice identifiers supplied."}), 400

    delete_sql = text(
        """
        DELETE FROM invoice_items
        WHERE item_id = :item_id
          AND invoice_id IN :invoice_ids
        """
    ).bindparams(bindparam("invoice_ids", expanding=True))

    with get_engine().begin() as conn:
        result = conn.execute(
            delete_sql,
            {"item_id": normalized_target, "invoice_ids": normalized_invoices},
        )

    return jsonify(
        {
            "ok": True,
            "removed": int(result.rowcount or 0),
            "invoices": normalized_invoices,
        }
    )


@bp.route("/invoicesbulkdelete", methods=["POST"])
@login_required
def bulk_delete_invoices_api() -> Any:
    payload = request.get_json(silent=True) or {}
    table_name = str(payload.get("table") or "invoices").strip().lower()
    if table_name != "invoices":
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Only invoices can be deleted through this endpoint.",
                }
            ),
            400,
        )

    raw_ids = payload.get("pks")
    if not isinstance(raw_ids, list):
        return jsonify({"ok": False, "error": "pks must be a list."}), 400

    normalized_invoices: List[str] = []
    for candidate in raw_ids:
        try:
            normalized_invoices.append(normalize_pg_uuid(str(candidate)))
        except Exception as exc:
            log.debug(
                "bulk_delete_invoices_api: skipping invalid invoice identifier %r: %s",
                candidate,
                exc,
            )

    if not normalized_invoices:
        return jsonify({"ok": False, "error": "No valid invoice identifiers supplied."}), 400

    delete_sql = text(
        """
        UPDATE invoices
        SET is_deleted = TRUE
        WHERE id IN :invoice_ids
        """
    ).bindparams(bindparam("invoice_ids", expanding=True))

    with get_engine().begin() as conn:
        result = conn.execute(delete_sql, {"invoice_ids": normalized_invoices})

    return jsonify(
        {
            "ok": True,
            "deleted": int(result.rowcount or 0),
            "invoices": normalized_invoices,
        }
    )

from __future__ import annotations

from typing import Any, Dict, List

from flask import Blueprint, jsonify, request
from werkzeug.datastructures import FileStorage

from .user_login import login_required

bp = Blueprint("invoice_handlers", __name__, url_prefix="/api")


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
    """Kick off background processing to pull invoices from the mailbox.

    TODO: Use backend/automation/gmail_proc.py to connect to Gmail, download messages, and attachments.
    TODO: Run backend/automation/order_num_extract.py routines to enrich invoice metadata.
    TODO: Upsert invoice rows plus email state into the tables defined in backend/schemas/schema.sql.
    """
    return jsonify(
        {
            "ok": True,
            "message": "Email check accepted. TODO: wire up mailbox processing pipeline.",
        }
    )


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

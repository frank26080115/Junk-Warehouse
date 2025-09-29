# backend/email/email_helper.py
"""Shared helpers for mailbox polling implementations."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import text

from automation.order_num_extract import extract_order_number, extract_order_number_and_url
from shop_handler import ShopHandler
from app.db import get_engine, update_db_row_by_dict, unwrap_db_result

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRETS_PATH = REPO_ROOT / "config" / "secrets.json"

log = logging.getLogger(__name__)


class EmailChecker:
    """Simple base class used by concrete mailbox polling helpers."""

    @staticmethod
    def check_email() -> Dict[str, Any]:
        """Subclasses must provide a polling implementation."""
        raise NotImplementedError("Subclasses must implement check_email().")

    @staticmethod
    def secrets_path() -> Path:
        """Return the expected location of config/secrets.json."""
        return SECRETS_PATH

    @staticmethod
    def load_secrets() -> Dict[str, Any]:
        """Load secrets.json when available, returning an empty mapping on failure."""
        path = EmailChecker.secrets_path()
        if not path.exists():
            log.info("No secrets.json found at %s; skipping credential-dependent checks.", path)
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log.exception("Unable to parse secrets file at %s; treating as empty.", path)
            return {}
        if not isinstance(data, dict):
            log.warning("Expected %s to contain a JSON object; ignoring malformed content.", path)
            return {}
        return data

    @staticmethod
    def build_summary(
        provider: str,
        lookback_days: int,
        query: Optional[str],
        checked: int,
        new_messages: int,
        processed: Optional[list],
        *,
        ok: bool = True,
        skipped: bool = False,
        reason: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a consistent summary payload for API responses."""
        summary = {
            "provider": provider,
            "ok": ok,
            "skipped": skipped,
            "reason": reason,
            "error": error,
            "queried_days": lookback_days,
            "query": query,
            "checked": checked,
            "new_messages": new_messages,
            "processed": processed or [],
        }
        return summary

    @staticmethod
    def determine_lookback_days(table_name: str) -> int:
        """Determine how many days of history should be polled for a mailbox."""
        engine = get_engine()
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(f"SELECT date_seen FROM {table_name} ORDER BY date_seen DESC LIMIT 1")
                ).first()
        except Exception:
            log.exception("Failed to query %s for last seen date; defaulting to 7 days", table_name)
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

    @staticmethod
    def parse_email_date(header_value: Optional[str]) -> datetime:
        """Parse an email Date header into a timezone-aware datetime."""
        if not header_value:
            return datetime.now(timezone.utc)
        from email.utils import parsedate_to_datetime

        try:
            parsed = parsedate_to_datetime(header_value)
        except (TypeError, ValueError):
            parsed = None
        if parsed is None:
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def ingest_invoice_from_email(
        provider_label: str,
        message_id: Optional[str],
        subject: str,
        email_date: datetime,
        html_body: str,
        text_body: str,
        primary_url: Optional[str],
        secondary_url: Optional[str],
    ) -> Dict[str, Any]:
        """Create or update an invoice row from parsed email content."""
        order_number: Optional[str] = None
        derived_order_url: Optional[str] = None
        if subject:
            order_number = extract_order_number(subject)
        if not order_number and html_body:
            order_number, derived_order_url = extract_order_number_and_url(html_body)
        if not order_number and text_body:
            order_number = extract_order_number(text_body)
        if secondary_url is None and derived_order_url:
            secondary_url = derived_order_url
        if order_number:
            order_number = order_number.strip()
        handler: Optional[ShopHandler] = None
        auto_summary = "[]"
        if html_body:
            try:
                handler = ShopHandler.ingest_html(html_body)
                auto_summary = handler.build_auto_summary()
            except Exception:
                log.exception(
                    "Failed to build auto-summary for %s message %s",
                    provider_label,
                    message_id,
                )
                handler = None
                auto_summary = "[]"
        if not order_number and handler is not None:
            handler_order = handler.get_order_number()
            if handler_order:
                order_number = handler_order.strip()
        invoice_id = None
        invoice_error: Optional[str] = None
        if order_number:
            url_parts = []
            if primary_url:
                url_parts.append(primary_url)
            if secondary_url:
                url_parts.append(secondary_url)
            urls_value = ";".join(part for part in url_parts if part)
            engine = get_engine()
            invoice_payload: Dict[str, Any] = {
                "date": email_date,
                "order_number": order_number,
                "shop_name": handler.get_shop_name() if handler else "",
                "urls": urls_value,
                "subject": subject,
                "html": html_body or text_body,
                "notes": "",
                "has_been_processed": False,
                "auto_summary": auto_summary,
                "snooze": datetime.now(timezone.utc),
                "is_deleted": False,
            }
            try:
                update_result = update_db_row_by_dict(
                    engine,
                    "invoices",
                    "new",
                    invoice_payload,
                    fuzzy=False,
                )
                (
                    _status,
                    failed,
                    _reply,
                    invoice_row,
                    invoice_pk,
                    message,
                ) = unwrap_db_result(update_result)
            except Exception as exc:
                log.exception(
                    "Failed to insert invoice for %s message %s",
                    provider_label,
                    message_id,
                )
                invoice_error = str(exc)
            else:
                if failed:
                    invoice_error = message if isinstance(message, str) else str(message)
                    log.error(
                        "Failed to insert invoice for %s message %s: %s",
                        provider_label,
                        message_id,
                        invoice_error,
                    )
                else:
                    invoice_id = invoice_pk or (
                        invoice_row.get("id") if isinstance(invoice_row, dict) else None
                    )
        status = "invoice_created" if invoice_id else (
            "invoice_failed" if invoice_error else "no_order_number"
        )
        return {
            "order_number": order_number,
            "invoice_id": invoice_id,
            "invoice_error": invoice_error,
            "status": status,
        }

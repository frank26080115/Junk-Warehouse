# backend/email/imap.py
"""Generic IMAP mailbox polling implementation."""

from __future__ import annotations

import imaplib
import logging
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text

from app.db import get_engine, update_db_row_by_dict, unwrap_db_result

from .email_helper import EmailChecker

log = logging.getLogger(__name__)


class ImapChecker(EmailChecker):
    """Poll a traditional IMAP inbox for recent order confirmations."""

    _CONFIG_KEY = "imap_mail"

    @staticmethod
    def _load_settings() -> Optional[Dict[str, Any]]:
        """Load IMAP connection details from secrets.json."""
        secrets = EmailChecker.load_secrets()
        config = secrets.get(ImapChecker._CONFIG_KEY)
        if not isinstance(config, dict):
            return None
        required_keys = ("host", "username", "password")
        for key in required_keys:
            value = config.get(key)
            if not isinstance(value, str) or not value.strip():
                log.warning("IMAP configuration missing required key %s", key)
                return None
        return config

    @staticmethod
    def is_configured() -> bool:
        """Indicate whether IMAP credentials are available."""
        return ImapChecker._load_settings() is not None

    @staticmethod
    def _bool_setting(value: Any, default: bool = True) -> bool:
        """Convert loosely-typed configuration values into booleans."""
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text_value = str(value).strip().lower()
        if text_value in {"1", "true", "yes", "on"}:
            return True
        if text_value in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _connect(settings: Dict[str, Any]) -> imaplib.IMAP4:
        """Establish an IMAP connection using the supplied settings."""
        host = settings.get("host")
        port = settings.get("port")
        use_ssl = ImapChecker._bool_setting(settings.get("use_ssl"), True)
        if isinstance(port, str) and port.strip():
            try:
                port_value = int(port)
            except ValueError:
                port_value = 993 if use_ssl else 143
        elif isinstance(port, (int, float)):
            port_value = int(port)
        else:
            port_value = 993 if use_ssl else 143
        if use_ssl:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port_value)
        else:
            client = imaplib.IMAP4(host, port_value)
        client.login(settings.get("username"), settings.get("password"))
        mailbox = settings.get("mailbox") or "INBOX"
        client.select(mailbox)
        return client

    @staticmethod
    def _fetch_seen_uids() -> Sequence[str]:
        """Fetch IMAP UIDs that were already processed."""
        engine = get_engine()
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT email_uuid FROM imail_seen"))
                seen: List[str] = []
                for row in result:
                    value = row[0]
                    if isinstance(value, memoryview):
                        value = value.tobytes()
                    if isinstance(value, bytes):
                        seen.append(value.decode("utf-8", errors="ignore"))
                    elif value is not None:
                        seen.append(str(value))
                return seen
        except Exception:
            log.exception("Failed to load imail_seen entries; treating as empty set")
            return []

    @staticmethod
    def _decode_header_value(raw_value: Optional[str]) -> str:
        """Decode MIME-encoded header values into readable text."""
        if not raw_value:
            return ""
        try:
            decoded = str(make_header(decode_header(raw_value)))
            return decoded
        except Exception:
            log.exception("Failed to decode header value %r", raw_value)
            return str(raw_value)

    @staticmethod
    def _extract_bodies(msg) -> Tuple[str, str]:
        """Extract text and HTML parts from an email message."""
        html_body = ""
        text_body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.is_multipart():
                    continue
                disposition = part.get("Content-Disposition", "")
                if disposition and "attachment" in disposition.lower():
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                try:
                    decoded = payload.decode(charset, errors="replace")
                except Exception:
                    decoded = payload.decode("utf-8", errors="replace")
                content_type = part.get_content_type()
                if content_type == "text/html" and not html_body:
                    html_body = decoded
                elif content_type == "text/plain" and not text_body:
                    text_body = decoded
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                try:
                    decoded = payload.decode(charset, errors="replace")
                except Exception:
                    decoded = payload.decode("utf-8", errors="replace")
                if msg.get_content_type() == "text/html":
                    html_body = decoded
                else:
                    text_body = decoded
        return html_body, text_body

    @staticmethod
    def _handle_imap_message(uid: str, msg_bytes: bytes) -> Dict[str, Any]:
        """Process a raw IMAP message payload."""
        msg = message_from_bytes(msg_bytes)
        subject = ImapChecker._decode_header_value(msg.get("Subject"))
        message_id = ImapChecker._decode_header_value(msg.get("Message-ID")) or uid
        html_body, text_body = ImapChecker._extract_bodies(msg)
        email_date = EmailChecker.parse_email_date(msg.get("Date"))
        ingestion = EmailChecker.ingest_invoice_from_email(
            "imap",
            message_id,
            subject,
            email_date,
            html_body,
            text_body,
            None,
            None,
        )
        payload: Dict[str, Any] = {
            "email_uuid": uid.encode("utf-8"),
            "date_seen": email_date,
        }
        invoice_id = ingestion.get("invoice_id")
        if invoice_id is not None:
            payload["invoice_id"] = invoice_id
        engine = get_engine()
        (
            imap_status,
            imap_failed,
            _imap_reply,
            _imap_row,
            _imap_pk,
            imap_message,
        ) = unwrap_db_result(
            update_db_row_by_dict(engine, "imail_seen", "new", payload, fuzzy=False)
        )
        if imap_failed:
            log.error("Failed to insert imail_seen row for UID %s: %s", uid, imap_message)
        return {
            "uid": uid,
            "message_id": message_id,
            "order_number": ingestion.get("order_number"),
            "invoice_id": str(invoice_id) if invoice_id is not None else None,
            "email_date": email_date.isoformat(),
            "invoice_error": ingestion.get("invoice_error"),
            "imap_status": imap_status,
            "status": ingestion.get("status"),
        }

    @staticmethod
    def check_email() -> Dict[str, Any]:
        """Poll the configured IMAP mailbox for new emails."""
        settings = ImapChecker._load_settings()
        if not settings:
            return EmailChecker.build_summary(
                "imap",
                0,
                None,
                0,
                0,
                [],
                skipped=True,
                reason="IMAP credentials are not configured.",
            )
        lookback_days = EmailChecker.determine_lookback_days("imail_seen")
        since_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        query = f"(SINCE {since_date.strftime('%d-%b-%Y')})"
        try:
            client = ImapChecker._connect(settings)
        except Exception as exc:
            log.exception("Unable to initialise IMAP client")
            return EmailChecker.build_summary(
                "imap",
                lookback_days,
                query,
                0,
                0,
                [],
                ok=False,
                error=str(exc),
            )
        try:
            status, data = client.uid("search", None, query)
            if status != "OK":
                raise RuntimeError(f"IMAP search failed with status {status}")
            raw_ids = data[0].split() if data and data[0] else []
            uids = [uid.decode("utf-8", errors="ignore") for uid in raw_ids]
        except Exception as exc:
            log.exception("Unable to list IMAP messages for query %s", query)
            try:
                client.logout()
            except Exception:
                log.debug("IMAP logout raised an exception after search failure", exc_info=True)
            return EmailChecker.build_summary(
                "imap",
                lookback_days,
                query,
                0,
                0,
                [],
                ok=False,
                error=str(exc),
            )
        seen_uids = set(ImapChecker._fetch_seen_uids())
        new_uids = [uid for uid in uids if uid not in seen_uids]
        processed: List[Dict[str, Any]] = []
        for uid in new_uids:
            try:
                status, data = client.uid("fetch", uid, "(RFC822)")
                if status != "OK" or not data:
                    raise RuntimeError(f"IMAP fetch for UID {uid} failed with status {status}")
                parts = [part[1] for part in data if isinstance(part, tuple) and part[1]]
                msg_bytes = b"".join(parts)
                if not msg_bytes:
                    raise RuntimeError(f"No RFC822 payload returned for UID {uid}")
            except Exception as exc:
                log.exception("Failed to fetch IMAP message %s", uid)
                processed.append(
                    {
                        "uid": uid,
                        "status": "fetch_error",
                        "error": str(exc),
                    }
                )
                continue
            try:
                result = ImapChecker._handle_imap_message(uid, msg_bytes)
                processed.append(result)
            except Exception as exc:
                log.exception("Failed to process IMAP message %s", uid)
                processed.append(
                    {
                        "uid": uid,
                        "status": "processing_error",
                        "error": str(exc),
                    }
                )
        try:
            client.logout()
        except Exception:
            log.debug("IMAP logout raised an exception", exc_info=True)
        return EmailChecker.build_summary(
            "imap",
            lookback_days,
            query,
            len(uids),
            len(new_uids),
            processed,
        )

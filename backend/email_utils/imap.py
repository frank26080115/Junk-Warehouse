# backend/email/imap.py
"""Generic IMAP mailbox polling implementation."""

from __future__ import annotations

import imaplib
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure repository imports function when executing the file directly.
from pathlib import Path

_CURRENT_FILE = Path(__file__).resolve()
_PROJECT_ROOT = _CURRENT_FILE.parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import text

from app.db import get_engine, update_db_row_by_dict, unwrap_db_result
from app.helpers import bytea_to_hex_str, hex_str_to_bytea

from .email_helper import EmailChecker

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


class ImapChecker(EmailChecker):
    """Poll a traditional IMAP inbox for recent order confirmations."""

    _CONFIG_KEY = "imap_mail"

    @staticmethod
    def _load_settings() -> Optional[Dict[str, Any]]:
        """Load IMAP connection details from secrets.json."""
        log.debug("Loading IMAP configuration from secrets store.")
        secrets = EmailChecker.load_secrets()
        config = secrets.get(ImapChecker._CONFIG_KEY)
        if not isinstance(config, dict):
            log.debug("IMAP configuration missing or not a dictionary: %s", type(config))
            return None
        required_keys = ("host", "username", "password")
        for key in required_keys:
            value = config.get(key)
            if not isinstance(value, str) or not value.strip():
                log.warning("IMAP configuration missing required key %s", key)
                return None
        log.debug("IMAP configuration successfully loaded for host %s", config.get("host"))
        return config

    @staticmethod
    def is_configured() -> bool:
        """Indicate whether IMAP credentials are available."""
        settings = ImapChecker._load_settings()
        configured = settings is not None
        log.debug("IMAP configuration check result: %s", configured)
        return configured

    @staticmethod
    def _bool_setting(value: Any, default: bool = True) -> bool:
        """Convert loosely-typed configuration values into booleans."""
        log.debug("Converting configuration value %r to boolean with default %s", value, default)
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
        log.debug(
            "Preparing IMAP connection to host=%s port=%s use_ssl=%s", host, port, use_ssl
        )
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
        log.debug("Connected to IMAP mailbox %s on host %s", mailbox, host)
        return client

    @staticmethod
    def _uid_to_bytes(uid: Optional[str]) -> bytes:
        """Normalize a raw IMAP UID into the canonical byte representation stored in the database."""
        if not uid:
            return b""
        return hex_str_to_bytea(uid)

    @staticmethod
    def _has_seen_uid(uid_bytes: bytes) -> bool:
        """Return True when the UID already exists in imail_seen."""
        if not uid_bytes:
            return False
        engine = get_engine()
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT 1 FROM imail_seen WHERE email_uuid = :email_uuid LIMIT 1"),
                    {"email_uuid": uid_bytes},
                ).first()
        except Exception:
            log.exception(
                "Failed to check imail_seen for UID %s; assuming the message is new.",
                bytea_to_hex_str(uid_bytes),
            )
            return False
        already_seen = row is not None
        log.debug(
            "IMAP UID %s already processed=%s",
            bytea_to_hex_str(uid_bytes),
            already_seen,
        )
        return already_seen

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
        log.debug("Extracting bodies from IMAP message; multipart=%s", msg.is_multipart())
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
        log.debug(
            "Extracted IMAP body lengths: text=%d html=%d",
            len(text_body),
            len(html_body),
        )
        return html_body, text_body

    @staticmethod
    def _handle_imap_message(
        uid: str,
        msg_bytes: bytes,
        precomputed_uid_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Process a raw IMAP message payload."""
        log.debug("Handling IMAP message with UID %s", uid)
        msg = message_from_bytes(msg_bytes)
        subject = ImapChecker._decode_header_value(msg.get("Subject"))
        message_id = ImapChecker._decode_header_value(msg.get("Message-ID")) or uid
        from_header = ImapChecker._decode_header_value(msg.get("From"))
        sender_email = parseaddr(from_header)[1] or None
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
            sender_email,
        )
        uid_bytes = (
            ImapChecker._uid_to_bytes(uid)
            if precomputed_uid_bytes is None
            else precomputed_uid_bytes
        )
        if not uid_bytes:
            uid_bytes = b""
        payload: Dict[str, Any] = {
            "email_uuid": uid_bytes,
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
        else:
            log.debug("Inserted imail_seen row for UID %s with status %s", uid, imap_status)
        log.debug(
            "Finished handling IMAP UID %s with invoice id %s", uid, invoice_id
        )
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
        log.debug("Starting IMAP email check routine.")
        settings = ImapChecker._load_settings()
        if not settings:
            log.debug("IMAP not configured; returning skipped summary.")
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
        log.debug(
            "IMAP lookback days=%d resulting in SINCE date %s", lookback_days, since_date
        )
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
            log.debug(
                "IMAP search returned %d raw ids for query %s", len(uids), query
            )
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
        # Track per-run processing state to avoid redundant work and to build an informative summary.
        processed: List[Dict[str, Any]] = []
        new_uids: List[str] = []
        previously_seen_uids: Set[str] = set()
        processed_uid_bytes: Set[bytes] = set()
        seen_raw_uids: Set[str] = set()
        for uid in uids:
            if uid in seen_raw_uids:
                log.debug(
                    "Skipping duplicate raw IMAP UID %s encountered during listing.",
                    uid,
                )
                continue
            seen_raw_uids.add(uid)
            uid_bytes: Optional[bytes] = None
            canonical_uid: str = ""
            try:
                uid_bytes = ImapChecker._uid_to_bytes(uid)
                canonical_uid = bytea_to_hex_str(uid_bytes) if uid_bytes else ""
            except Exception:
                log.exception(
                    "Failed to normalize IMAP UID %s before processing; continuing without database deduplication.",
                    uid,
                )
                uid_bytes = None
            already_processed = False
            if uid_bytes:
                if uid_bytes in processed_uid_bytes:
                    log.debug(
                        "Skipping IMAP UID %s because it already appeared earlier in this run.",
                        uid,
                    )
                    already_processed = True
                elif ImapChecker._has_seen_uid(uid_bytes):
                    log.debug(
                        "Database indicates IMAP UID %s (canonical %s) was already processed; skipping.",
                        uid,
                        canonical_uid,
                    )
                    processed_uid_bytes.add(uid_bytes)
                    if canonical_uid:
                        previously_seen_uids.add(canonical_uid)
                    already_processed = True
            if already_processed:
                continue
            if uid_bytes:
                processed_uid_bytes.add(uid_bytes)
            new_uids.append(uid)
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
                result = ImapChecker._handle_imap_message(uid, msg_bytes, uid_bytes)
                processed.append(result)
                log.debug(
                    "Successfully processed IMAP UID %s with status %s",
                    uid,
                    result.get("status"),
                )
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
        log.debug(
            "IMAP processing completed. Checked=%d, newly processed=%d, results=%d",
            len(uids),
            len(new_uids),
            len(processed),
        )
        log.debug("Found %d previously seen IMAP UIDs", len(previously_seen_uids))
        return EmailChecker.build_summary(
            "imap",
            lookback_days,
            query,
            len(uids),
            len(new_uids),
            processed,
        )


def _configure_cli_logging(level: int = logging.DEBUG) -> None:
    """Configure logging for command line execution with detailed debug output."""
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
    else:
        root_logger.setLevel(level)
    log.debug("CLI logging configured at level %s", logging.getLevelName(level))


def main() -> int:
    """Run the IMAP email check routine and print a JSON summary."""
    _configure_cli_logging()
    log.debug("Invoking ImapChecker.check_email from __main__ entry point.")
    summary = ImapChecker.check_email()
    print(json.dumps(summary, indent=2, default=str))
    log.debug("IMAP email check completed with ok=%s", summary.get("ok"))
    return 0 if summary.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())

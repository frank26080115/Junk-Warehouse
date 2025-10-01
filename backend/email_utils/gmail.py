# backend/email/gmail.py
"""Gmail-specific mailbox polling implementation."""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text

from backend.app.config_loader import get_private_dir_path
from app.db import get_engine, update_db_row_by_dict, unwrap_db_result
from app.helpers import normalize_pg_uuid

from .email_helper import EmailChecker

log = logging.getLogger(__name__)


class GmailChecker(EmailChecker):
    """Poll Gmail for recent order confirmations and create invoices."""

    @staticmethod
    def _gmail_token_path() -> Path:
        """Resolve the Gmail OAuth token cache location."""
        private_dir = get_private_dir_path()
        if private_dir is not None:
            return Path(private_dir) / "gmail_token.json"
        return EmailChecker.secrets_path().with_name("gmail_token.json")

    @staticmethod
    def _load_gmail_token() -> Optional[Dict[str, Any]]:
        """Load cached Gmail OAuth details from disk or secrets.json."""
        token_path = GmailChecker._gmail_token_path()
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
        secrets = EmailChecker.load_secrets()
        token = secrets.get("gmail_api_token")
        if isinstance(token, dict):
            return token
        return None

    @staticmethod
    def is_configured() -> bool:
        """Return True when Gmail credentials are present."""
        try:
            token_path = GmailChecker._gmail_token_path()
            if token_path.exists():
                return True
            secrets = EmailChecker.load_secrets()
            token = secrets.get("gmail_api_token")
            return isinstance(token, dict) and bool(token)
        except Exception:
            log.exception("Error while checking Gmail configuration; assuming Gmail is unavailable.")
            return False

    @staticmethod
    def _build_gmail_service() -> Any:
        """Initialise the Gmail API service client."""
        token_info = GmailChecker._load_gmail_token()
        if not token_info:
            raise RuntimeError("Gmail credentials are not configured.")
        token_path = GmailChecker._gmail_token_path()
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - dependency provided in runtime env
            raise RuntimeError("Google API client libraries are required to poll Gmail") from exc
        scopes = token_info.get("scopes") or ["https://www.googleapis.com/auth/gmail.readonly"]
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

    @staticmethod
    def _fetch_seen_ids() -> Sequence[str]:
        """Retrieve Gmail message identifiers that were already processed."""
        engine = get_engine()
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT email_uuid FROM gmail_seen"))
                return [str(row[0]) for row in result if row[0] is not None]
        except Exception:
            log.exception("Failed to load gmail_seen entries; treating as empty set")
            return []

    @staticmethod
    def _normalize_gmail_id(message_id: Optional[str]) -> Optional[str]:
        """Convert Gmail message identifiers into canonical UUID format when possible."""
        if not message_id:
            return message_id
        cleaned = message_id.strip()
        hex_candidate = cleaned.replace("-", "")
        hex_chars = set("0123456789abcdefABCDEF")
        if 16 <= len(hex_candidate) <= 32 and set(hex_candidate).issubset(hex_chars):
            try:
                padded = hex_candidate.rjust(32, "0")
                return normalize_pg_uuid(padded)
            except Exception:
                log.debug("Message id %s looked hex-like but failed UUID normalization", message_id)
        return cleaned

    @staticmethod
    def gmail_date_x_days_query(days: int) -> str:
        """Build a Gmail search query that limits the lookback window."""
        today_local = datetime.now().date()
        after_date = (today_local - timedelta(days=days)).strftime("%Y/%m/%d")
        query_parts: List[str] = []
        # We scope the query to the inbox for consistency with previous automation behavior.
        query_parts.append("in:inbox")
        query_parts.append(f"newer_than:{days}d")
        query_parts.append(f"after:{after_date}")
        return " ".join(query_parts)

    @staticmethod
    def list_message_ids(service: Any, query: str, max_page: int = 10) -> List[str]:
        """List Gmail message identifiers for the provided query."""
        message_ids: List[str] = []
        request = service.users().messages().list(userId="me", q=query, maxResults=100)
        page_count = 0
        while request is not None and page_count < max_page:
            response = request.execute()
            message_ids.extend([
                message.get("id")
                for message in response.get("messages", [])
                if message.get("id")
            ])
            request = service.users().messages().list_next(
                previous_request=request,
                previous_response=response,
            )
            page_count += 1
        return message_ids

    @staticmethod
    def get_full_message(service: Any, message_id: str) -> Dict[str, Any]:
        """Fetch the full Gmail message resource for downstream processing."""
        return service.users().messages().get(userId="me", id=message_id, format="full").execute()

    @staticmethod
    def _decode_part_body(part: Dict[str, Any]) -> str:
        """Decode a MIME part body using the Gmail base64 encoding."""
        data = part.get("body", {}).get("data")
        if not data:
            return ""
        try:
            raw_bytes = base64.urlsafe_b64decode(data.encode("utf-8"))
        except Exception:
            log.debug("Failed to decode MIME part data; returning an empty string.")
            return ""
        try:
            return raw_bytes.decode("utf-8", errors="replace")
        except Exception:
            return raw_bytes.decode("latin-1", errors="replace")

    @staticmethod
    def _extract_text_content(payload: Dict[str, Any]) -> Dict[str, str]:
        """Extract plain text and HTML content from the Gmail payload structure."""
        if not payload:
            return {"text": "", "html": ""}
        mime_type = payload.get("mimeType", "")
        if mime_type.startswith("text/"):
            body = GmailChecker._decode_part_body(payload)
            return {
                "text": body if mime_type == "text/plain" else "",
                "html": body if mime_type == "text/html" else "",
            }
        text_content = ""
        html_content = ""
        for part in payload.get("parts", []) or []:
            part_type = part.get("mimeType", "")
            if part_type == "text/plain" and not text_content:
                text_content = GmailChecker._decode_part_body(part)
            elif part_type == "text/html" and not html_content:
                html_content = GmailChecker._decode_part_body(part)
            elif part.get("parts"):
                nested = GmailChecker._extract_text_content(part)
                if not text_content:
                    text_content = nested.get("text", "")
                if not html_content:
                    html_content = nested.get("html", "")
        return {"text": text_content, "html": html_content}


    @staticmethod
    def _handle_gmail_message(msg: Dict[str, Any]) -> Dict[str, Any]:
        """Process a Gmail API message and create or update invoice rows."""
        headers = {
            h.get("name", "").lower(): h.get("value", "")
            for h in msg.get("payload", {}).get("headers", [])
        }
        subject = headers.get("subject", "")
        message_id = msg.get("id") or ""
        normalized_id = GmailChecker._normalize_gmail_id(message_id)
        content = GmailChecker._extract_text_content(msg.get("payload", {}))
        html_body = content.get("html") or ""
        text_body = content.get("text") or ""
        email_date = EmailChecker.parse_email_date(headers.get("date"))
        gmail_link = f"https://mail.google.com/mail/u/0/#all/{message_id}" if message_id else None
        ingestion = EmailChecker.ingest_invoice_from_email(
            "gmail",
            message_id,
            subject,
            email_date,
            html_body,
            text_body,
            gmail_link,
            None,
        )
        gmail_payload: Dict[str, Any] = {
            "email_uuid": normalized_id or message_id,
            "date_seen": email_date,
        }
        if ingestion.get("invoice_id"):
            gmail_payload["invoice_id"] = ingestion["invoice_id"]
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
            log.error(
                "Failed to insert gmail_seen row for message %s: %s",
                message_id,
                gmail_message,
            )
        invoice_id = ingestion.get("invoice_id")
        return {
            "message_id": message_id,
            "normalized_id": normalized_id,
            "order_number": ingestion.get("order_number"),
            "invoice_id": str(invoice_id) if invoice_id is not None else None,
            "email_date": email_date.isoformat(),
            "invoice_error": ingestion.get("invoice_error"),
            "gmail_status": gmail_status,
            "status": ingestion.get("status"),
        }

    @staticmethod
    def check_email() -> Dict[str, Any]:
        """Poll Gmail for new messages and build a processing summary."""
        if not GmailChecker.is_configured():
            return EmailChecker.build_summary(
                "gmail",
                0,
                None,
                0,
                0,
                [],
                skipped=True,
                reason="Gmail credentials are not configured.",
            )
        lookback_days = EmailChecker.determine_lookback_days("gmail_seen")
        query = GmailChecker.gmail_date_x_days_query(lookback_days)
        try:
            service = GmailChecker._build_gmail_service()
        except Exception as exc:
            log.exception("Unable to initialise Gmail client")
            return EmailChecker.build_summary(
                "gmail",
                lookback_days,
                query,
                0,
                0,
                [],
                ok=False,
                error=str(exc),
            )
        try:
            message_ids = GmailChecker.list_message_ids(service, query)
        except Exception as exc:
            log.exception("Unable to list Gmail messages for query %s", query)
            return EmailChecker.build_summary(
                "gmail",
                lookback_days,
                query,
                0,
                0,
                [],
                ok=False,
                error=str(exc),
            )
        seen_ids = list(GmailChecker._fetch_seen_ids())
        seen_normalized = {
            GmailChecker._normalize_gmail_id(value) for value in seen_ids if value
        }
        new_ids: List[str] = []
        for mid in message_ids:
            normalized = GmailChecker._normalize_gmail_id(mid)
            if mid in seen_ids or (normalized and normalized in seen_normalized):
                continue
            new_ids.append(mid)
        processed: List[Dict[str, Any]] = []
        for mid in new_ids:
            try:
                msg = GmailChecker.get_full_message(service, mid)
            except Exception as exc:
                log.exception("Failed to fetch Gmail message %s", mid)
                processed.append(
                    {
                        "message_id": mid,
                        "status": "fetch_error",
                        "error": str(exc),
                    }
                )
                continue
            try:
                result = GmailChecker._handle_gmail_message(msg)
                processed.append(result)
            except Exception as exc:
                log.exception("Failed to process Gmail message %s", mid)
                processed.append(
                    {
                        "message_id": mid,
                        "status": "processing_error",
                        "error": str(exc),
                    }
                )
        return EmailChecker.build_summary(
            "gmail",
            lookback_days,
            query,
            len(message_ids),
            len(new_ids),
            processed,
        )

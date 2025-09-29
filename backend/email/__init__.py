# backend/email/__init__.py
"""Helper classes for polling external mailboxes."""

from .gmail import GmailChecker
from .imap import ImapChecker

__all__ = ["GmailChecker", "ImapChecker"]

import re
from email import policy
from email.parser import Parser
from lxml import html as lxml_html
from email import policy
from email.parser import Parser

HEADER_LINE_RE = re.compile(r"^[!-9;-~]+:\s?.+$")  # permissive "Field-Name: value"
HTML_TAG_RE    = re.compile(r"<\s*html[\s>]", re.IGNORECASE)
DOCTYPE_RE     = re.compile(r"<!doctype\s+html", re.IGNORECASE)

def _peek_head_body(text: str, head_max_bytes: int = 64_000):
    """Split headers (before first blank line) and body, scanning only the first chunk for speed."""
    sample = text[:head_max_bytes]
    # Normalize newlines for header/body split
    # Try CRLF first (common in MHT), fall back to LF
    if "\r\n\r\n" in sample:
        head, _, _ = sample.partition("\r\n\r\n")
    else:
        head, _, _ = sample.partition("\n\n")
    return head, sample

def looks_like_mhtml(text: str) -> bool:
    """
    Heuristics:
      1) A header block with multiple well-formed header lines.
      2) Presence of MIME-Version or a multipart Content-Type (esp. multipart/related or multipart/mixed).
      3) Optional boundary marker hints.
      4) Parsable by email.parser as multipart with at least one text/html part.
    """
    head, sample = _peek_head_body(text)

    # Count "Field: value" lines at top (before first blank line)
    header_lines = [ln for ln in head.splitlines() if HEADER_LINE_RE.match(ln)]
    many_headers = len(header_lines) >= 3

    # Strong MIME signals in the head
    mime_version = re.search(r"^MIME-Version:\s*\d+\.\d+", head, re.IGNORECASE | re.MULTILINE) is not None
    ctype_match  = re.search(r"^Content-Type:\s*([^;\r\n]+)", head, re.IGNORECASE | re.MULTILINE)
    ctype = ctype_match.group(1).lower().strip() if ctype_match else ""

    multipartish = ctype.startswith("multipart/")
    related_or_mixed = ctype in ("multipart/related", "multipart/mixed", "multipart/alternative")

    # Quick boundary hint
    bnd = None
    bnd_m = re.search(r'boundary="?([^"\r\n;]+)"?', head, re.IGNORECASE)
    if bnd_m:
        bnd = bnd_m.group(1)
    boundary_present_in_body = bool(bnd and (("--" + bnd) in sample))

    # Early accept if headers look like MIME + multipart structure
    if many_headers and (mime_version or multipartish) and (related_or_mixed or boundary_present_in_body):
        return True

    # As a fallback, try lightweight email parse: “multipart” with a text/html child screams MHTML.
    try:
        msg = Parser(policy=policy.default).parsestr(text)
        if msg.is_multipart():
            for p in msg.walk():
                if p.get_content_type() == "text/html":
                    return True
    except Exception:
        pass

    return False

def looks_like_html(text: str) -> bool:
    """
    Heuristics for raw HTML:
      - Starts with <!doctype html> or <html ...>
      - Or the first non-whitespace '<' tag is an HTML tag (cheap check)
    """
    # Quick explicit checks
    if DOCTYPE_RE.search(text[:2048]) or HTML_TAG_RE.search(text[:8192]):
        return True

    # Cheap tag-first test: if the first '<' opens something that looks like an HTML tag
    first_lt = text.find("<")
    if 0 <= first_lt < 4096:
        snippet = text[first_lt:first_lt + 256].lower()
        # Common openers that suggest HTML
        if any(snippet.startswith(x) for x in ("<html", "<head", "<!doctype", "<body", "<meta", "<title")):
            return True
    return False

def sniff_format(text: str) -> str:
    """
    Returns "mhtml" or "html".
    Preference order:
      - If it clearly looks like MHTML, call it MHTML.
      - Else if it looks like HTML, call it HTML.
      - Else: last resort — try MIME parse; if multipart w/ html → MHTML, otherwise HTML.
    """
    if looks_like_mhtml(text):
        return "mhtml"
    if looks_like_html(text):
        return "html"

    # Last resort: try to parse as MIME and see if it contains a text/html part
    try:
        msg = Parser(policy=policy.default).parsestr(text)
        if msg.is_multipart():
            for p in msg.walk():
                if p.get_content_type() == "text/html":
                    return "mhtml"
        # Otherwise treat it as HTML-ish (e.g., plain HTML missing <html> wrapper)
        return "html"
    except Exception:
        # If even MIME parse fails, assume HTML (safer for downstream)
        return "html"

def parse_mhtml_from_string(mht_text: str):
    msg = Parser(policy=policy.default).parsestr(mht_text)
    html_text = None
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            html_text = payload.decode(charset, errors="replace")
            break
    if not html_text:
        # crude fallback: slice at first <html
        i = mht_text.lower().find("<html")
        if i != -1:
            html_text = mht_text[i:]
    if not html_text:
        raise ValueError("No HTML part found in MHTML content.")
    return lxml_html.fromstring(html_text)

def parse_unknown_html_or_mhtml(text: str):
    fmt = sniff_format(text)
    if fmt == "mhtml":
        return parse_mhtml_from_string(text), "mhtml"
    else:
        return lxml_html.fromstring(text), "html"

import re

def normalize_pg_uuid(s: str) -> str:
    """
    Normalize an input string into a PostgreSQL UUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).

    Steps:
    1) Strip ALL non-alphanumeric characters.
    2) Ensure the remaining length is exactly 32.
    3) Reject any letters beyond 'F' (i.e., not valid hex).
    4) Return lowercased UUID in 8-4-4-4-12 format.

    Raises:
        ValueError: If length != 32 after cleaning, or if letters beyond 'F' are present.
    """
    if not isinstance(s, str):
        raise TypeError("normalize_pg_uuid expects a string input.")

    # 1) Remove everything that's not 0-9 or A-Z/a-z
    cleaned = re.sub(r'[^0-9A-Za-z]+', '', s)

    # 2) Enforce length
    if len(cleaned) != 32:
        raise ValueError(
            f"Normalized UUID must have exactly 32 hex characters; got {len(cleaned)}."
        )

    # 3) Detect letters beyond 'F' explicitly (invalid for hexadecimal)
    if re.search(r'[G-Zg-z]', cleaned):
        raise ValueError("Invalid UUID: contains letters beyond 'F' (non-hex characters).")

    # 4) Format as canonical UUID (lowercase)
    cleaned = cleaned.lower()
    return f"{cleaned[0:8]}-{cleaned[8:12]}-{cleaned[12:16]}-{cleaned[16:20]}-{cleaned[20:32]}"

import re
import html as _html
import unicodedata
from typing import Union, Any

def sanitize_html_for_pg(
    value: Union[str, bytes, Any],
    *,
    keep_html: bool = True,
    as_literal: bool = False,
    max_length: int | None = None
) -> str:
    """
    Sanitize possibly-HTML input (str/bytes/BeautifulSoup-like) for PostgreSQL inserts.

    Recommended usage: pass the returned string as a parameter to your SQL
    (e.g., with psycopg's `cursor.execute("INSERT ... VALUES (%s)", [clean])`).

    Args:
        value: The input; str, bytes, or an HTML-ish object (e.g., BeautifulSoup Tag).
        keep_html: If True, keep HTML but normalize & unescape entities; if False, strip tags.
        as_literal: If True, return a single-quoted SQL literal with proper escaping.
                    If False (default), return the plain sanitized text suitable for parameters.
        max_length: If provided, truncate the result to this many characters.

    Returns:
        A sanitized string (or a safely escaped SQL literal if `as_literal=True`).

    Notes:
        - Removes NULs and C0 control chars except TAB (\\t) and NEWLINE (\\n).
        - Normalizes to Unicode NFKC.
        - Unescapes HTML entities (&amp; → &), then re-escapes minimal if `keep_html=True`.
        - Collapses excessive whitespace.
        - If `keep_html=False`, strips tags with a conservative regex.

    Security:
        Prefer parameterized queries. Only use `as_literal=True` when absolutely necessary.
    """
    # 1) Coerce to text --------------------------------------------------------
    if isinstance(value, (bytes, bytearray, memoryview)):
        text = bytes(value).decode("utf-8", errors="replace")
    else:
        # BeautifulSoup tags or objects: str(value) yields the HTML string
        text = str(value) if not isinstance(value, str) else value

    # 2) Normalize Unicode (NFKC) ---------------------------------------------
    text = unicodedata.normalize("NFKC", text)

    # 3) Drop NULs and disallowed control chars (keep \t and \n) --------------
    # Keep TAB(0x09) and LF(0x0A); remove others in C0 + DEL
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)

    # 4) Unescape HTML entities (so we can consistently process content) ------
    text = _html.unescape(text)

    # 5) Strip tags if requested ----------------------------------------------
    if not keep_html:
        # Conservative tag strip (handles simple HTML). For complex HTML,
        # use an HTML parser upstream and pass us .get_text().
        text = re.sub(r"<[^>]+>", "", text)

    # 6) Whitespace cleanup ----------------------------------------------------
    # Normalize CRLF → LF
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse horizontal whitespace runs to a single space, preserve newlines
    # (do tabs first so they become spaces)
    text = text.replace("\t", " ")
    # Collapse >1 spaces
    text = re.sub(r"[ ]{2,}", " ", text)
    # Limit blank lines to at most 2 in a row
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    # 7) Optional length clamp -------------------------------------------------
    if max_length is not None and max_length >= 0:
        text = text[:max_length]

    # 8) Output mode: parameter value vs. SQL literal -------------------------
    if not as_literal:
        # Return plain sanitized text for parameterized inserts (preferred).
        return text
    else:
        # Return a single-quoted SQL literal with proper escaping for Postgres.
        # Standard-conforming strings are on by default; double single quotes.
        return "'" + text.replace("'", "''") + "'"

import base64
from typing import Union, Tuple

def html_to_base64_datauri(
    html: Union[str, bytes, bytearray, memoryview],
    *,
    charset: str = "utf-8"
) -> Tuple[str, str]:
    """
    Convert HTML (string or bytes-like) into:
      1) a Base64-encoded string (ASCII-safe for JSON transport)
      2) a data: URI you can drop into <iframe src="..."> or open in a browser.

    Returns:
        (base64_str, data_uri)

    Usage:
        html_bytes = base64.b64decode(b64)      # -> bytes
        html_text = html_bytes.decode("utf-8")  # -> str

    """
    if isinstance(html, (bytes, bytearray, memoryview)):
        raw = bytes(html)
    else:
        raw = str(html).encode(charset, errors="strict")

    b64 = base64.b64encode(raw).decode("ascii")
    data_uri = f"data:text/html;charset={charset};base64,{b64}"
    return b64, data_uri


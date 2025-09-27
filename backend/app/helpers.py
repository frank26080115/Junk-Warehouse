from __future__ import annotations

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

try:
    from flask import jsonify as _jsonify  # type: ignore

    def flask_return_wrap(payload: dict, code: int):
        return _jsonify(payload), code
except Exception:  # pragma: no cover
    def flask_return_wrap(payload: dict, code: int):
        return payload, code

import re

def fuzzy_norm_key(s: str) -> str:
    """lowercase and remove non-alphanumerics so 'User-Name' ~ 'username'."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def fuzzy_levenshtein_at_most(a: str, b: str, limit: int = 2) -> int:
    """
    Levenshtein distance with an early-exit 'limit'.
    Returns a distance <= limit, or limit+1 if it exceeds the limit.
    """
    la, lb = len(a), len(b)
    if abs(la - lb) > limit:
        return limit + 1
    # DP row
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        min_row = cur[0]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            v = min(
                cur[j - 1] + 1,   # insertion
                prev[j] + 1,      # deletion
                prev[j - 1] + cost,  # substitution
            )
            cur.append(v)
            if v < min_row:
                min_row = v
        if min_row > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def fuzzy_apply_fuzzy_keys(data: dict[str, Any], columns: set[str], table_name: str, limit: int = 2) -> dict[str, Any]:
    import logging
    log = logging.getLogger(__name__)

    """
    For each key in data, find best column match by edit distance (<= limit).
    Rename key if it doesn't cause a duplicate and hasn’t already been claimed.
    Log all mismatches at debug level.
    """
    if not data:
        return data

    col_norm = {c: fuzzy_norm_key(c) for c in columns}
    claimed: set[str] = set()
    out: dict[str, Any] = {}

    for k, v in data.items():
        if k in columns:
            # Exact match; keep and mark claimed (so fuzzies don't steal it)
            out[k] = v
            claimed.add(k)
            continue

        nk = fuzzy_norm_key(k)
        best_col: Optional[str] = None
        best_dist = limit + 1

        for col, ncol in col_norm.items():
            if col in claimed:
                continue
            d = fuzzy_levenshtein_at_most(nk, ncol, limit=limit)
            if d < best_dist:
                best_dist = d
                best_col = col

        if best_col is not None and best_dist <= limit:
            if best_col in out:
                log.debug("fuzzy: '%s' -> '%s' (dist=%d) SKIPPED (would duplicate key)", k, best_col, best_dist)
                out[k] = v
            else:
                log.debug("fuzzy: '%s' -> '%s' (dist=%d) on table '%s'", k, best_col, best_dist, table_name)
                out[best_col] = v
                claimed.add(best_col)
        else:
            log.debug("fuzzy: no acceptable match for key '%s' on table '%s'", k, table_name)
            out[k] = v

    return out


from datetime import datetime, date, timezone, timedelta
from decimal import Decimal
from typing import Any, Optional, Union

try:
    # Python 3.9+ stdlib timezones
    from zoneinfo import ZoneInfo  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

try:
    # Nice-to-have parser (RFC2822, many formats)
    from dateutil import parser as dateutil_parser  # type: ignore
except Exception:  # pragma: no cover
    dateutil_parser = None  # type: ignore


def _tz_from_name(name: str):
    if name.upper() == "UTC":
        return timezone.utc
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    # Fallback: UTC if unknown tz name
    return timezone.utc


def _ensure_aware(dt: datetime, default_tz: str) -> datetime:
    """Make a datetime timezone-aware (attach default_tz if naive)."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=_tz_from_name(default_tz))
    return dt


def _from_epoch_numeric(x: Union[int, float, Decimal]) -> datetime:
    """Interpret numeric epochs in seconds / ms / µs / ns."""
    # Use absolute value for scale detection
    val = float(x)
    aval = abs(val)
    # thresholds roughly: s ~ 1e9..1e10 (2001..2286), ms ~ 1e12, µs ~ 1e15, ns ~ 1e18
    if aval >= 1e18:
        # nanoseconds
        seconds = val / 1e9
    elif aval >= 1e15:
        # microseconds
        seconds = val / 1e6
    elif aval >= 1e11:
        # milliseconds
        seconds = val / 1e3
    else:
        # seconds (handles fractional seconds too)
        seconds = val
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def to_timestamptz(
    value: Any,
    *,
    return_datetime: bool = False,
    default_tz: str = "UTC",
) -> Optional[Union[str, datetime]]:
    """
    Coerce many input types into a Postgres timestamptz-ready value.

    - If return_datetime=False (default): returns ISO 8601 string in UTC, e.g. "2025-09-10T12:34:56.789012Z".
    - If return_datetime=True: returns a timezone-aware datetime in UTC (good for SQLAlchemy/psycopg).

    Accepted inputs:
      * datetime (aware or naive)  -> attach default_tz if naive
      * date                       -> midnight in default_tz
      * int/float/Decimal          -> Unix epoch (auto-detect s/ms/µs/ns)
      * str:
          - "now", "utcnow" -> current time in UTC
          - "today"         -> midnight today in default_tz
          - "yesterday"/"tomorrow"
          - ISO 8601 strings (supports trailing 'Z')
          - RFC2822/etc. if python-dateutil is available
      * None or ""                 -> None (NULL)

    On failure, raises ValueError.
    """
    if value is None:
        return None

    # Empty strings -> NULL
    if isinstance(value, str) and value.strip() == "":
        return None

    dt_utc: Optional[datetime] = None

    # datetime / date
    if isinstance(value, datetime):
        dt_utc = _ensure_aware(value, default_tz).astimezone(timezone.utc)
    elif isinstance(value, date):
        dtn = datetime(value.year, value.month, value.day)
        dt_utc = _ensure_aware(dtn, default_tz).astimezone(timezone.utc)

    # numeric epochs
    elif isinstance(value, (int, float, Decimal)):
        dt_utc = _from_epoch_numeric(value)

    # strings
    elif isinstance(value, str):
        s = value.strip()

        # quick keywords
        lower = s.lower()
        if lower in ("now", "utcnow"):
            dt_utc = datetime.now(tz=timezone.utc)
        elif lower in ("today",):
            # today at 00:00 in default tz -> UTC
            local_midnight = datetime.now(_tz_from_name(default_tz)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            dt_utc = local_midnight.astimezone(timezone.utc)
        elif lower in ("yesterday",):
            local_midnight = datetime.now(_tz_from_name(default_tz)).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=1)
            dt_utc = local_midnight.astimezone(timezone.utc)
        elif lower in ("tomorrow",):
            local_midnight = datetime.now(_tz_from_name(default_tz)).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
            dt_utc = local_midnight.astimezone(timezone.utc)
        else:
            # Try dateutil (broad formats)
            if dateutil_parser is not None:
                try:
                    parsed = dateutil_parser.parse(s)  # type: ignore[attr-defined]
                    dt_utc = _ensure_aware(parsed, default_tz).astimezone(timezone.utc)
                except Exception:
                    dt_utc = None

            # Fallback: ISO 8601 via fromisoformat (supports "+00:00"; handle 'Z' manually)
            if dt_utc is None:
                try:
                    iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
                    parsed = datetime.fromisoformat(iso)
                    dt_utc = _ensure_aware(parsed, default_tz).astimezone(timezone.utc)
                except Exception:
                    # Last resort: maybe it's a numeric epoch in a string
                    try:
                        num = float(s)
                        dt_utc = _from_epoch_numeric(num)
                    except Exception:
                        pass

    # Unknown type?
    if dt_utc is None:
        raise ValueError(f"Cannot interpret value as timestamptz: {value!r}")

    if return_datetime:
        return dt_utc

    # Return an ISO string with 'Z' (Postgres accepts this fine)
    iso = dt_utc.isoformat()
    if iso.endswith("+00:00"):
        iso = iso[:-6] + "Z"
    return iso

import difflib

def fuzzy_word_list_match(words, user_input):
    """
    Given a list of properly spelled words and a user input string,
    return the closest match and its index in the list.

    # Example usage:
    dictionary = ["banana", "orange", "apple", "grapefruit"]
    user_word = "applle"

    match, idx = fuzzy_match(dictionary, user_word)
    print(match, idx)  # → apple 2

    """
    # Get best matches from difflib
    matches = difflib.get_close_matches(user_input, words, n=1, cutoff=0.6)
    if not matches:
        return None, -1  # no reasonable match found

    best_match = matches[0]
    index = words.index(best_match)
    return best_match, index

def parse_tagged_text_to_dict(text: str, required_key: str = "name", def_req_val: str = "(no name)", acceptable_keys: list[str] | None = None) -> dict[str, str]:
    """
    Parse a multiline string into a dict where sections start with lines beginning with '#'.
      - A line starting with '#' defines a new key: everything after '#' (stripped) is the key.
      - The value for that key is all subsequent lines up to (but not including) the next '#' line.
      - If the '#'-line contains a ':' then the text immediately following the ':' becomes the first value line.
      - Keys and values are stripped of leading/trailing whitespace (values preserve inner newlines).
      - If acceptable_keys is provided, each parsed key is fuzzy-corrected to the closest entry
        in that list using fuzzy_word_list_match; if no reasonable match is found, the original key is used.
      - Case is preserved (case-sensitive behavior).

    Example input:
        # Title
         My Document
        # Body
          hello world

    Returns:
        {"Title": "My Document", "Body": "hello world"}
    """

    result: dict[str, str] = {}
    lines = text.splitlines()

    current_key: str | None = None
    current_buf: list[str] = []

    def _commit():
        nonlocal current_key, current_buf
        if current_key is None:
            current_buf = []
            return
        key = current_key.strip()
        if acceptable_keys:
            match, _ = fuzzy_word_list_match(acceptable_keys, key)
            if match:
                key = match  # fuzzy-correct the key if we got a reasonable match
        value = chr(10).join(current_buf).strip()  # Use a literal LF to prevent accidental CRLF artifacts in stored text
        result[key] = value
        current_key, current_buf = None, []

    for raw in lines:
        # Preserve original line content for values; only use stripped to detect markers
        stripped = raw.lstrip()  # we only care that the line *starts* with '#', ignoring leading spaces
        if stripped.startswith("#"):
            # New section starts: commit previous
            _commit()
            after_hash = stripped[1:]
            inline_fragment: str | None = None
            if ':' in after_hash:
                # Respect inline values such as "# Title: My Value" by splitting at the first ':'
                key_fragment, inline_fragment = after_hash.split(':', 1)
            else:
                key_fragment = after_hash
            current_key = key_fragment.strip()
            current_buf = []
            if inline_fragment is not None:
                # Inline values begin immediately after the ':' and should not carry leading spaces
                inline_text = inline_fragment.lstrip()
                current_buf.append(inline_text)
        else:
            # part of current value (even if it's empty or whitespace)
            if current_key is not None:
                current_buf.append(raw)

    # commit any trailing section
    _commit()

    if required_key not in result:
        # Determine the fallback value for the required key in a very explicit manner so the
        # calling code can remain predictable. When the default required value references another
        # key using angle brackets (for example '<url>'), the referenced key is copied instead of
        # the literal text. This allows invoice-specific templates to reuse data points without
        # inventing brand new keys.
        fallback_value = def_req_val
        if isinstance(def_req_val, str) and len(def_req_val) >= 3 and def_req_val.startswith('<') and def_req_val.endswith('>'):
            referenced_key = def_req_val[1:-1].strip()
            if referenced_key and referenced_key in result:
                fallback_value = result[referenced_key]
        result[required_key] = fallback_value

    return result

def dict_to_tagged_text(
    d: dict[str, str],
    inline_threshold: int = 30,
    key_order: list[str] | None = None,
) -> str:
    """
    Convert a dict back into the tagged text format that parse_tagged_text_to_dict parses.

    Each key becomes a line starting with '#', followed by its value lines.
    Values are written exactly as they are (multiline preserved).
    Leading/trailing whitespace is stripped for keys but values are preserved.
    Keys are emitted in dict iteration order.
    If a value is a single trimmed line shorter than `inline_threshold`, the output uses "# Key: value" form.

    Example:
        {"Title": "My Document", "Body": "hello\nworld"}

    →
        # Title
        My Document
        # Body
        hello
        world
    """
    parts: list[str] = []

    # Determine which keys we will iterate over and in what order.
    # When a key_order hint is provided, we honor that explicit ordering first
    # and then append any remaining dictionary keys in their original order.
    if key_order is not None:
        ordered_keys: list[str] = []
        seen_keys: set[str] = set()

        for desired_key in key_order:
            if desired_key in d and desired_key not in seen_keys:
                ordered_keys.append(desired_key)
                seen_keys.add(desired_key)

        for fallback_key in d.keys():
            if fallback_key not in seen_keys:
                ordered_keys.append(fallback_key)
                seen_keys.add(fallback_key)
    else:
        ordered_keys = list(d.keys())

    for key in ordered_keys:
        value = d.get(key, '')
        safe_key = key.strip()
        value_text = '' if value is None else str(value)
        trimmed_value = value_text.strip()
        has_newline = chr(10) in trimmed_value  # Detect embedded newlines without rewriting literal escape sequences
        should_inline = not has_newline and len(trimmed_value) < inline_threshold

        if should_inline:
            # Inline short, single-line values so the parser can recover them without extra blank lines
            if trimmed_value == '':
                parts.append(f"# {safe_key}:")
            else:
                parts.append(f"# {safe_key}: {trimmed_value}")
            continue

        parts.append(f"# {safe_key}")
        if trimmed_value != '':
            # Preserve the original value text (including intentional spaces or newlines)
            parts.append(value_text)
    return chr(10).join(parts)  # Emit consistent LF separators so the parser can reliably split sections


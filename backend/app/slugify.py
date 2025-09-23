import re
from typing import Iterable, List, Optional

# You can extend this later
DEFAULT_STOPWORDS = {"a", "an", "the", "this", "that", "these", "those"}

# Remove possessive suffixes "'s" or "’s" at word boundaries (e.g., "bob's" -> "bob")
_POSSESSIVE_RE = re.compile(r"['’]s\b", re.IGNORECASE)


def slugify(title: str, short_id: int, stopwords: Optional[Iterable[str]] = None, charlimit: int = 40) -> str:
    """
    Build a URL slug from a free-text title and a 32-bit integer short_id.

    Rules:
      - lowercase everything
      - whitespace -> '-' (single)
      - original '-' from input -> '--' (double)
      - remove possessive "'s"/"’s" (keep the root word)
      - remove stopwords ('a', 'an', 'the', ...), but NOT 'in', 'of', etc.
      - title portion max 40 chars without cutting a word
      - never end with a dangling '-' or '--'
      - always append "-{short_id:08x}"
    """
    if stopwords is None:
        stopwords = DEFAULT_STOPWORDS

    # 0) normalize case + strip possessives
    s = (title or "").lower()
    s = _POSSESSIVE_RE.sub("", s)

    # 1) tokenize into [word | SPACE | HYPH]; ignore other punctuation
    tokens: List[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c.isspace():
            while i < len(s) and s[i].isspace():
                i += 1
            tokens.append("SPACE")
            continue
        if c == "-":
            while i < len(s) and s[i] == "-":
                i += 1
            tokens.append("HYPH")
            continue
        if c.isalnum():
            j = i + 1
            while j < len(s) and s[j].isalnum():
                j += 1
            tokens.append(s[i:j])  # word
            i = j
            continue
        i += 1  # skip any other punctuation

    # 2) remove stopwords (words only)
    filtered: List[str] = []
    for t in tokens:
        if t not in ("SPACE", "HYPH"):
            if t not in stopwords:
                filtered.append(t)
        else:
            filtered.append(t)

    # 3) build segments with mapping rules:
    #    SPACE -> "-" ; HYPH -> "--" ; collapse multiple separators naturally
    segments: List[str] = []
    pending_sep: Optional[str] = None

    def push_word(w: str):
        nonlocal pending_sep, segments
        if not w:
            return
        if segments and pending_sep:
            segments.append(pending_sep)
        segments.append(w)
        pending_sep = None

    for t in filtered:
        if t == "SPACE":
            pending_sep = "-"     # whitespace becomes single hyphen
        elif t == "HYPH":
            pending_sep = "--"    # original hyphen becomes double hyphen
        else:
            push_word(t)

    # 4) join with ≤40 char soft limit (don’t cut words) and strip trailing seps
    title_portion = _join_with_soft_limit(segments, limit=charlimit).rstrip("-")

    # 5) append short_id as 8 lowercase hex
    sid = f"{(short_id & 0xFFFFFFFF):08x}"
    return f"{title_portion}-{sid}" if title_portion else f"-{sid}"


def _join_with_soft_limit(segments: List[str], limit: int = 40) -> str:
    """
    Join [word, sep, word, ...] but stop before exceeding limit.
    Never cut a word; separators count toward the limit.
    Allow the very first word even if it alone exceeds the limit.
    """
    out = ""
    if not segments:
        return out

    # First item (should be a word if any words exist)
    i = 0
    while i < len(segments) and segments[i] in ("-", "--"):
        i += 1  # skip leading separators, if any
    if i >= len(segments):
        return out

    first = segments[i]
    out = first
    i += 1

    while i < len(segments):
        part = segments[i]
        if len(out) + len(part) <= limit:
            out += part
            i += 1
        else:
            # If it's a separator that doesn't fit, skip it and check next,
            # but if it's a word, we stop (no mid-word truncation).
            if part in ("-", "--"):
                i += 1
                continue
            break
    return out

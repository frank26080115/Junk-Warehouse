#!/usr/bin/env python3
"""
order_extractor.py

Usage:
  python order_extractor.py path/to/file.html

Output (JSON on stdout):
  {"order_number": "SO-9843A", "url": "https://example.com/orders/view?id=SO-9843A"}
  or:
  {"order_number": "123-4567890-1234567", "url": null}
  or:
  {"order_number": null, "url": null}
"""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

# --- Order/invoice keyword coverage ------------------------------------------
KEYWORD_PATTERNS = [
    r"order", r"order\s*id", r"order\s*number",
    r"invoice", r"invoice\s*id", r"invoice\s*number",
    r"sales\s*order", r"salesorder",
    r"purchase\s*order", r"\bpo\b", r"\bso\b",
    r"transaction", r"confirmation", r"receipt",
    r"reference", r"ref(?:erence)?\s*(?:no|num|number)?"
]
KEYWORD_RE = re.compile(r"(?i)\b(?:" + "|".join(KEYWORD_PATTERNS) + r")\b")

# Strict token (general case): 6–40 chars, alnum with internal - _
#STRICT_TOKEN_RE = r"[A-Z0-9](?:[A-Z0-9\-_]{4,38})[A-Z0-9]"
STRICT_TOKEN_RE = r"[0-9](?:[0-9\-_]{4,38})[0-9]"

# Short IDs for strongly-cued forms like "Order No. 1135" or "Invoice num: A12B3"
# 3–12 chars, allow hyphenated groups
#SHORT_ID_RE = r"[A-Z0-9]{3,12}(?:-[A-Z0-9]{2,12}){0,3}"
SHORT_ID_RE = r"[0-9]{3,12}(?:-[0-9]{2,12}){0,3}"

# Cues immediately before a short ID:
# - "#"
# - "No", "No.", "no", PLUS typographic variants: "№" (U+2116), "Nº" (U+00BA), "N°" (U+00B0)
# - "num", "num.", "number", "id", "id."
SHORT_CUE_RE = (
    r"(?:"
    r"[\#\u2116]"              # # or №
    r"|n[oº°]\.?"              # No, No., Nº, N°  (case-insensitive)
    r"|num(?:ber)?\.?"         # num, number, num., number.
    r"|id\.?"                  # id, id.
    r")\b"
)

# Words we want to avoid drifting into after the keyword
AVOID_WORDS = r"(?:date|placed|total|status|shipment|shipped|tracking)"

TOKEN_RE = r"[0-9](?:[0-9\-_]{2,38})[0-9]"
TOKEN = re.compile(r"(?i)" + TOKEN_RE)

KEYWORD_THEN_ID = re.compile(
    r"(?i)(" + "|".join(KEYWORD_PATTERNS) + r")"
    r"(?:(?!\b(?:date|placed|total|status|shipment|shipped)\b).){0,60}?"
    r"([#:\-\s]*)(" + TOKEN_RE + r")"
)

ORDER_PARAM_CANDIDATES = {
    "order", "orderid", "order_id", "ordernumber", "order_number",
    "invoice", "invoiceid", "invoice_id", "invoicenumber", "invoice_number",
    "ref", "reference", "transaction", "confirmation", "receipt", "po", "ponumber", "po_number",
    "salesorder", "sales_order", "so", "id"
}

# 1) Keyword → small tempered gap → STRICT token
KEYWORD_THEN_STRICT_ID = re.compile(
    rf"(?i)(?P<kw>{'|'.join(KEYWORD_PATTERNS)})"
    rf"(?:(?!\b{AVOID_WORDS}\b).){{0,60}}?"
    rf"[#:\-\s]*"
    rf"(?P<id>{STRICT_TOKEN_RE})"
)

# 2) Keyword → small tempered gap → cue (No/num/#/№/...) → SHORT id
KEYWORD_THEN_SHORT_ID = re.compile(
    rf"(?i)(?P<kw>order|invoice|sales\s*order|salesorder|purchase\s*order|\bpo\b|\bso\b)"
    rf"(?:(?!\b{AVOID_WORDS}\b).){{0,60}}?"
    rf"(?:[\s:\-]*{SHORT_CUE_RE}[\s:\-]*)"
    rf"(?P<id>{SHORT_ID_RE})"
)

# 3) Relaxed: keyword → tiny gap → bare SHORT id (use sparingly; keep gap tiny)
RELAXED_KEYWORD_SHORT = re.compile(
    rf"(?i)(?P<kw>order|invoice|\bpo\b|\bso\b)"
    rf"[\s:\-]{{0,10}}"
    rf"(?P<id>{SHORT_ID_RE})"
)

# ------------------------ Helpers ---------------------------------------------

def clean_soup(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()

def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def extract_token_near_keywords(text: str):
    """
    Prefer long/strict IDs; if not found, accept short IDs when strongly cued by
    'No', 'No.', 'num', 'num.', '#', '№', 'Nº', 'N°', or 'id'. As a last resort,
    allow bare short IDs very close to the keyword.
    """
    if not text:
        return None

    # 1) strict (long) IDs
    m = KEYWORD_THEN_STRICT_ID.search(text)
    if m:
        return m.group("id")

    # 2) short IDs with explicit cue (No/num/#/№/id)
    m = KEYWORD_THEN_SHORT_ID.search(text)
    if m:
        return m.group("id")

    # 3) relaxed short (tiny gap, no cue)
    m = RELAXED_KEYWORD_SHORT.search(text)
    if m:
        return m.group("id")

    return None

def extract_token_near_keywords_old(text: str):
    m = KEYWORD_THEN_ID.search(text)
    if m:
        return m.group(3)
    return None

def token_from_url(url: str):
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    # query params
    try:
        q = parse_qs(parsed.query)
        for k, vals in q.items():
            if k.lower() in ORDER_PARAM_CANDIDATES:
                for v in vals:
                    v = norm_text(unquote(v))
                    if TOKEN.fullmatch(v):
                        return v
                    m = TOKEN.search(v)
                    if m:
                        return m.group(0)
    except Exception:
        pass

    # path segments next to order-ish words
    path_segs = [seg for seg in parsed.path.split("/") if seg]
    for i, seg in enumerate(path_segs):
        seg_l = seg.lower()
        if any(kw in seg_l for kw in ("order", "invoice", "salesorder", "so", "po", "reference", "transaction", "receipt", "confirmation")):
            m = TOKEN.search(seg)
            if m:
                return m.group(0)
            if i + 1 < len(path_segs):
                nxt = norm_text(unquote(path_segs[i + 1]))
                m2 = TOKEN.search(nxt)
                if m2:
                    return m2.group(0)

    if KEYWORD_RE.search(url):
        m = TOKEN.search(url)
        if m:
            return m.group(0)
    return None

def iter_links(soup: BeautifulSoup):
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        text = norm_text(a.get_text(separator=" ", strip=True))
        yield href, text, a

def link_keyword_score(href: str, text: str) -> int:
    # Score links that look relevant; higher is better.
    score = 0
    if KEYWORD_RE.search(text):
        score += 3
    if KEYWORD_RE.search(href or ""):
        score += 2
    # URLs with obvious “order-like” params also get a bump
    try:
        p = urlparse(href or "")
        q = parse_qs(p.query)
        if any(k.lower() in ORDER_PARAM_CANDIDATES for k in q.keys()):
            score += 2
    except Exception:
        pass
    return score

def best_global_keyword_link(soup: BeautifulSoup):
    candidates = []
    for href, text, a in iter_links(soup):
        s = link_keyword_score(href, text)
        if s > 0:
            candidates.append((s, href, text, a))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (-t[0], -len(t[1] or "")))
    return candidates[0][3]  # return Tag

# ------------------------ Proximity search ------------------------------------

def links_within(tag: Tag):
    return tag.find_all("a")

def neighbor_links(element: Tag, max_siblings: int = 4):
    # Look a few siblings up/down for nearby links
    results = []

    # forward siblings
    nxt = element
    for _ in range(max_siblings):
        nxt = nxt.next_sibling
        if nxt is None:
            break
        if isinstance(nxt, NavigableString):
            continue
        if isinstance(nxt, Tag):
            results.extend(nxt.find_all("a"))
            if results:
                break

    # backward siblings
    prv = element
    for _ in range(max_siblings):
        prv = prv.previous_sibling
        if prv is None:
            break
        if isinstance(prv, NavigableString):
            continue
        if isinstance(prv, Tag):
            results.extend(prv.find_all("a"))
            if results:
                break

    return results

def choose_best_link(links):
    if not links:
        return None
    scored = []
    for a in links:
        href = a.get("href") or ""
        text = norm_text(a.get_text(separator=" ", strip=True))
        scored.append((link_keyword_score(href, text), a))
    scored.sort(key=lambda x: (-x[0], -(len(x[1].get("href") or ""))))
    return scored[0][1] if scored else None

# ------------------------ Extraction passes -----------------------------------

def pass_one_links(soup: BeautifulSoup):
    """
    Pass 1: If a link's anchor text mentions keywords, try extracting the ID
    from the anchor text, else from the URL. If successful, return (id, url).
    """
    candidates = []
    for href, text, a in iter_links(soup):
        if not KEYWORD_RE.search(text):
            continue
        token = extract_token_near_keywords(text)
        if token:
            candidates.append((token, href, "anchor-text", text))
            continue
        token = token_from_url(href)
        if token:
            candidates.append((token, href, "url", text))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: (x[2] != "anchor-text", -len(x[0])))
    best = candidates[0]
    return best[0], best[1]

def pass_two_text_with_nearest_link(soup: BeautifulSoup):
    """
    Pass 2: Find order token in visible text (any element). If found,
    try to pair with the nearest relevant link:
      - links inside the same element
      - else links inside parent (up to 2 levels)
      - else nearby sibling elements
      - else best global keyword link
    Returns (order_number, url_or_None)
    """
    # Search element-by-element for better locality
    text_elements = soup.find_all(string=True)
    for node in text_elements:
        if not isinstance(node, NavigableString):
            continue
        text = norm_text(str(node))
        if not text:
            continue
        token = extract_token_near_keywords(text)
        if not token:
            continue

        # Found a token in this text node; locate a nearby link
        owner = node.parent if isinstance(node, NavigableString) else None
        chosen = None

        # 1) links inside same element
        if isinstance(owner, Tag):
            chosen = choose_best_link(links_within(owner))

        # 2) parent (up to 2 levels)
        if not chosen and isinstance(owner, Tag):
            par = owner.parent
            for _ in range(2):
                if par and isinstance(par, Tag):
                    cand = choose_best_link(links_within(par))
                    if cand:
                        chosen = cand
                        break
                    par = par.parent

        # 3) neighbor siblings
        if not chosen and isinstance(owner, Tag):
            chosen = choose_best_link(neighbor_links(owner, max_siblings=4))

        # 4) global fallback
        if not chosen:
            chosen = best_global_keyword_link(soup)

        url = chosen.get("href") if (chosen is not None) else None
        return token, url

    return None, None

# ------------------------ Orchestrator ----------------------------------------

def extract_order_number_and_url(html_bytes: bytes):
    soup = None
    for parser in ("lxml", "html5lib", "html.parser"):
        try:
            soup = BeautifulSoup(html_bytes, parser)
            break
        except Exception:
            continue
    if soup is None:
        return None, None

    clean_soup(soup)

    # Pass 1: anchor-first logic
    order_num, url = pass_one_links(soup)
    if order_num:
        return order_num, url

    # Pass 2: text-first + nearest link pairing
    order_num, url = pass_two_text_with_nearest_link(soup)
    if order_num:
        return order_num, url

    # Nothing found
    return None, None

def extract_order_number(text: str):
    if not KEYWORD_RE.search(text):
        return None
    token = extract_token_near_keywords(text)
    if token:
        return token
    return None

# ------------------------ CLI --------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Extract order/invoice number and a related URL from HTML.")
    ap.add_argument("html_file", type=Path, help="Path to HTML file")
    args = ap.parse_args()

    if not args.html_file.exists():
        print(json.dumps({"order_number": None, "url": None}))
        return

    html_bytes = args.html_file.read_bytes()
    order_number, url = extract_order_number_and_url(html_bytes)
    print(json.dumps({"order_number": order_number, "url": url}))

if __name__ == "__main__":
    main()

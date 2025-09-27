from __future__ import annotations

import json
import logging
import re
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple, Type

from lxml import etree
from lxml import html as lxml_html

from automation.html_dom_finder import analyze as analyze_dom_report, sanitize_dom
from automation.order_num_extract import extract_order_number

from app.helpers import dict_to_tagged_text

log = logging.getLogger(__name__)


class ShopHandler:
    """Base class for extracting store specific information from invoices."""

    POSSIBLE_NAMES: ClassVar[Sequence[str]] = tuple()
    ORDER_NUMBER_REGEX: ClassVar[Optional[re.Pattern[str]]] = None

    def __init__(self, raw_html: str, sanitized_root: etree._Element, sanitized_html: str) -> None:
        self.raw_html = raw_html
        self.sanitized_root = sanitized_root
        self.sanitized_html = sanitized_html
        self._sanitized_text_cache: Optional[str] = None
        self._last_dom_report: Optional[Dict[str, Any]] = None

    @classmethod
    def ingest_html(cls, raw_html: str) -> "ShopHandler":
        if not isinstance(raw_html, str) or not raw_html.strip():
            raise ValueError("HTML content must be a non-empty string.")

        try:
            root = lxml_html.fromstring(raw_html)
        except Exception as exc:
            log.debug("Falling back to HTML fragment parsing during ingestion: %s", exc)
            root = lxml_html.fragment_fromstring(raw_html, create_parent=True)

        sanitized_root = sanitize_dom(root)
        sanitized_html = lxml_html.tostring(sanitized_root, encoding="unicode")
        normalized_text = sanitized_html.lower()

        specific_handlers = cls._get_specific_handlers()
        name_counts: Dict[Type[ShopHandler], int] = {}
        for handler_cls in specific_handlers:
            name_counts[handler_cls] = handler_cls._count_name_hits(normalized_text)

        best_handler: Type[ShopHandler] = GenericShopHandler
        if name_counts:
            sorted_counts: List[Tuple[Type[ShopHandler], int]] = sorted(
                name_counts.items(), key=lambda item: item[1], reverse=True
            )
            top_cls, top_hits = sorted_counts[0]
            other_hits = [count for _, count in sorted_counts[1:]]
            if top_hits > 0 and all(top_hits > value for value in other_hits):
                best_handler = top_cls

        return best_handler(raw_html, sanitized_root, sanitized_html)

    @classmethod
    def _get_specific_handlers(cls) -> Sequence[Type["ShopHandler"]]:
        from .amazon_handler import AmazonHandler
        from .digikey_handler import DigiKeyHandler
        from .mcmastercarr_handler import McMasterCarrHandler

        return (AmazonHandler, DigiKeyHandler, McMasterCarrHandler)

    @classmethod
    def _count_name_hits(cls, haystack: str) -> int:
        if not cls.POSSIBLE_NAMES:
            return 0

        total = 0
        for candidate in cls.POSSIBLE_NAMES:
            token = candidate.strip().lower()
            if not token:
                continue
            total += haystack.count(token)
        return total

    @classmethod
    def get_shop_name(cls) -> str:
        if not cls.POSSIBLE_NAMES:
            return ""
        return cls.POSSIBLE_NAMES[0]

    def as_specific_handler(self) -> "ShopHandler":
        return self

    def get_order_number(self) -> Optional[str]:
        pattern = self.ORDER_NUMBER_REGEX
        if pattern is None:
            return None
        match = pattern.search(self._get_sanitized_text())
        if match:
            return match.group(1) if match.groups() else match.group(0)
        return None

    def guess_items(self) -> List[Dict[str, str]]:
        # TODO: Implement store specific item extraction when structure is known.
        return []

    def build_auto_summary(self) -> str:
        entries: List[Dict[str, str]] = []
        shop_name = self.get_shop_name().strip()
        items = self.guess_items()

        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue

            working: Dict[str, str] = {}
            image_token: Optional[str] = None

            for raw_key, raw_value in item.items():
                key_text = str(raw_key or "").strip()
                if not key_text:
                    continue
                value_text = "" if raw_value is None else str(raw_value)
                normalized_value = value_text.replace("\r\n", "\n").replace("\r", "\n").strip()
                if key_text.lower() == "image":
                    if normalized_value:
                        image_token = normalized_value
                    continue
                if not normalized_value:
                    continue
                if key_text.lower() == "text" and "notes" not in working:
                    working["notes"] = normalized_value
                    continue
                working[key_text] = normalized_value

            if shop_name:
                working.setdefault("shop", shop_name)

            name_candidates = [
                working.get("name"),
                working.get("title"),
                working.get("notes"),
                working.get("description"),
                working.get("url"),
            ]
            chosen_name = ""
            for candidate in name_candidates:
                if isinstance(candidate, str) and candidate.strip():
                    chosen_name = candidate.strip()
                    break

            normalized_name = chosen_name.replace("\r\n", "\n").replace("\r", "\n") if chosen_name else ""
            if "\n" in normalized_name:
                normalized_name = normalized_name.split("\n", 1)[0].strip()
            normalized_name = normalized_name.strip()
            if normalized_name.lower() == "(no name)" or not normalized_name:
                fallback_seed = working.get("url") or f"{shop_name or 'Item'} {index}"
                normalized_fallback = str(fallback_seed).replace("\r\n", "\n").replace("\r", "\n")
                normalized_name = normalized_fallback.split("\n", 1)[0].strip() or f"Item {index}"

            working["name"] = normalized_name

            if "title" in working and working["title"] == working["name"]:
                working.pop("title", None)

            tagged_text = dict_to_tagged_text(
                working,
                key_order=[
                    "name",
                    "description",
                    "remarks",
                    "quantity",
                    "metatext",
                    "product_code",
                    "url",
                    "source",
                ],
            )

            entry: Dict[str, str] = {"text": tagged_text}
            if image_token:
                entry["image"] = image_token

            entries.append(entry)

        return json.dumps(entries, ensure_ascii=False)

    def _get_sanitized_text(self) -> str:
        if self._sanitized_text_cache is None:
            self._sanitized_text_cache = lxml_html.tostring(
                self.sanitized_root, encoding="unicode", method="text"
            )
        return self._sanitized_text_cache

    def get_dom_report(self) -> Optional[Dict[str, Any]]:
        return self._last_dom_report


class GenericShopHandler(ShopHandler):
    ORDER_NUMBER_REGEX = None

    def get_order_number(self) -> Optional[str]:
        try:
            return extract_order_number(self.raw_html)
        except Exception:
            log.exception("Generic order number extraction failed")
            return None

    def guess_items(self) -> List[Dict[str, str]]:
        try:
            report, _ = analyze_dom_report(self.raw_html)
        except Exception:
            log.exception("Failed to analyze DOM for generic handler")
            return []

        self._last_dom_report = report if isinstance(report, dict) else None
        summary: List[Dict[str, str]] = []
        candidates = report.get("top_candidates") if isinstance(report, dict) else None
        if not isinstance(candidates, list):
            candidates = []

        for item in candidates:
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or "").strip()
            preview_text = (item.get("preview_text") or "").strip()
            anchor_text = (item.get("anchor_text") or "").strip()

            chosen_text = ""
            if url:
                chosen_text = anchor_text if len(anchor_text) >= 12 else preview_text
                if len(chosen_text) < 12:
                    chosen_text = ""
            else:
                if len(preview_text) >= 12:
                    chosen_text = preview_text

            if not chosen_text and not url:
                continue

            name_source = chosen_text or url
            sanitized_name = name_source.replace("\r\n", "\n").replace("\r", "\n").split("\n", 1)[0].strip()
            if sanitized_name.lower() == "(no name)" or not sanitized_name:
                sanitized_name = url.split("\n", 1)[0].strip() if url else ""
            if not sanitized_name:
                sanitized_name = "Auto summary item"

            entry: Dict[str, str] = {"name": sanitized_name}
            if url:
                entry["url"] = url
            if chosen_text:
                entry["notes"] = chosen_text

            summary.append(entry)

        return summary

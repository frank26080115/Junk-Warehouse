from __future__ import annotations

import argparse
import importlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple, Type

from lxml import etree
from lxml import html as lxml_html

# Compute canonical project paths so the automation helpers can always be located,
# even when an IDE launches this module with a different working directory.
current_file = Path(__file__).resolve()
backend_root = current_file.parent.parent
project_root = backend_root.parent

def _ensure_module_search_paths() -> None:
    """Ensure automation helpers remain importable in IDE and CLI contexts."""
    for candidate in (backend_root, project_root):
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)

_ensure_module_search_paths()

from automation.html_dom_finder import analyze as analyze_dom_report, sanitize_dom
from automation.html_invoice_helpers import parse_unknown_html_or_mhtml
from automation.order_num_extract import extract_order_number

from app.helpers import (
    DOM_WHITESPACE_NORMALIZATION_PATTERN,
    clean_dom_text_fragment,
    dict_to_tagged_text,
)
from app.search import find_code_matched_items

log = logging.getLogger(__name__)


# Collapse any run of whitespace into a single regular space so repeated spacing never leaks through.
WHITESPACE_NORMALIZATION_PATTERN = DOM_WHITESPACE_NORMALIZATION_PATTERN


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

        # Use an HTML parser configured for extremely large documents so that gigantic
        # invoices never trigger lxml's security limits during ingestion.
        parser = lxml_html.HTMLParser(huge_tree=True, recover=True)
        try:
            root = lxml_html.fromstring(raw_html, parser=parser)
        except Exception as exc:
            log.debug("Falling back to HTML fragment parsing during ingestion: %s", exc)
            root = lxml_html.fragment_fromstring(raw_html, create_parent=True, parser=parser)

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
        from amazon_handler import AmazonHandler
        from digikey_handler import DigiKeyHandler
        from mcmastercarr_handler import McMasterCarrHandler

        handler_specs: Sequence[Tuple[str, str]] = (
            ("amazon_handler", "AmazonHandler"),
            ("digikey_handler", "DigiKeyHandler"),
            ("mcmastercarr_handler", "McMasterCarrHandler"),
        )

        handler_package = cls._determine_handler_package()
        resolved_handlers: List[Type[ShopHandler]] = []

        for module_name, class_name in handler_specs:
            module_path = f"{handler_package}.{module_name}"
            module = importlib.import_module(module_path)
            handler_type = getattr(module, class_name)
            resolved_handlers.append(handler_type)

        return tuple(resolved_handlers)

    @staticmethod
    def _determine_handler_package() -> str:
        """Determine the package path used when loading store specific handlers."""

        # When the module is imported as part of the backend package, __package__ is set
        # accordingly (for example, "backend.shop_handler"). When the module is executed
        # directly for offline testing, __package__ will be empty, so we fall back to the
        # explicit package name. This dual-path approach keeps imports working in both
        # deployment and standalone execution contexts.
        if __package__:
            return __package__
        return "backend.shop_handler"

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
                working.setdefault("source", shop_name)

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

            def _split_semicolon_values(raw_value: Optional[str]) -> List[str]:
                """Break semicolon-delimited fields into trimmed tokens."""

                if not isinstance(raw_value, str):
                    return []

                parts = [segment.strip() for segment in raw_value.split(";")]
                return [segment for segment in parts if segment]

            product_code_candidates = _split_semicolon_values(working.get("product_code"))
            url_candidates = _split_semicolon_values(working.get("url"))

            matches: List[str] = []
            if product_code_candidates or url_candidates:
                try:
                    # Reuse the search helper so potential duplicates are discovered consistently.
                    matches = find_code_matched_items(
                        product_codes=product_code_candidates,
                        urls=url_candidates,
                    )
                except Exception:
                    # Avoid failing auto-summary generation if the duplicate lookup encounters an error.
                    log.exception("Unable to evaluate potential code/url duplicates during auto summary generation.")
                    matches = []

            if matches:
                duplicate_count = len(matches)
                plural_suffix = "s" if duplicate_count != 1 else ""
                working["matchesbycode"] = (
                    f"{duplicate_count} possible duplicate{plural_suffix} by code or URL"
                )

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
                    "matchesbycode",
                ],
            )

            entry: Dict[str, str] = {"text": tagged_text}
            if image_token:
                entry["image"] = image_token

            entries.append(entry)

        return json.dumps(entries, ensure_ascii=False)

    def _get_sanitized_text(self) -> str:
        if self._sanitized_text_cache is None:
            text_segments: List[str] = []
            # Gather visible text from every node so downstream searches see the same
            # spacing that a human would perceive between words separated by tags.
            for fragment in self.sanitized_root.itertext():
                if not fragment:
                    continue

                # Remove invisible formatting characters and normalize internal spacing
                # within the fragment before we consider adding it to the aggregate list.
                cleaned_fragment = clean_dom_text_fragment(fragment)
                cleaned_fragment = WHITESPACE_NORMALIZATION_PATTERN.sub(" ", cleaned_fragment)
                cleaned_fragment = cleaned_fragment.strip()

                if cleaned_fragment:
                    text_segments.append(cleaned_fragment)

            # Join the fragments with single spaces so words that came from different
            # elements or lines remain separated, then collapse any accidental doubles.
            joined_text = " ".join(text_segments)
            normalized_text = WHITESPACE_NORMALIZATION_PATTERN.sub(" ", joined_text).strip()
            self._sanitized_text_cache = normalized_text
        return self._sanitized_text_cache

    def get_dom_report(self) -> Optional[Dict[str, Any]]:
        return self._last_dom_report


class GenericShopHandler(ShopHandler):
    ORDER_NUMBER_REGEX = None
    TEXT_LENGTH_LIMIT = 12

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
                chosen_text = anchor_text if len(anchor_text) >= TEXT_LENGTH_LIMIT else preview_text
                if len(chosen_text) < TEXT_LENGTH_LIMIT:
                    chosen_text = ""
            else:
                if len(preview_text) >= TEXT_LENGTH_LIMIT:
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

            summary.append(entry)

        return summary


def _load_invoice_html(file_path: Path) -> str:
    """Return HTML text from the provided path, converting MIME formats when needed."""

    # Read using UTF-8 with replacement so that the test helper does not crash on
    # odd encodings. This is only meant for manual testing, not production use.
    try:
        raw_text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError(f"Unable to read file '{file_path}'.") from exc

    extension = file_path.suffix.lower()
    mime_like_extensions = {".eml", ".mhtml", ".mht"}
    if extension in mime_like_extensions:
        # These files usually include MIME headers before the HTML. Reuse the helper so
        # that the parsing logic stays in one place.
        parsed_root, detected_format = parse_unknown_html_or_mhtml(raw_text)
        logging.getLogger(__name__).debug(
            "Converted MIME-flavoured input using detected format '%s'.", detected_format
        )
        return lxml_html.tostring(parsed_root, encoding="unicode")

    # Plain HTML (or unknown) is returned verbatim for ingestion by the handler.
    return raw_text


def main() -> None:
    """Simple command-line helper for manually exercising the shop handler."""

    parser = argparse.ArgumentParser(
        description=(
            "Inspect an invoice-like document and print detection details."
        )
    )
    parser.add_argument(
        "input_path",
        help=(
            "Path to an HTML, EML, or MHTML file. MIME-based files will be converted "
            "to HTML automatically."
        ),
    )
    parser.add_argument(
        "--show-html",
        action="store_true",
        help="Display the normalized HTML text (useful for troubleshooting).",
    )

    args = parser.parse_args()

    file_path = Path(args.input_path).expanduser().resolve()
    if not file_path.is_file():
        parser.error(f"File not found: {file_path}")

    raw_html = _load_invoice_html(file_path)

    handler = ShopHandler.ingest_html(raw_html)
    specific_handler = handler.as_specific_handler()

    print(f"Detected handler: {specific_handler.__class__.__name__}")

    order_number = specific_handler.get_order_number()
    if order_number:
        print(f"Order number: {order_number}")
    else:
        print("Order number: <not found>")

    try:
        auto_summary = specific_handler.build_auto_summary()
    except Exception as exc:
        auto_summary = "<summary generation failed>"
        logging.getLogger(__name__).exception("Auto summary generation failed during manual test.")
        print(f"Auto summary error: {exc}")

    print("Auto summary JSON:")
    print(auto_summary)

    if args.show_html:
        print("\nNormalized HTML snippet:")
        snippet = raw_html[:2000]
        print(snippet)


if __name__ == "__main__":
    main()

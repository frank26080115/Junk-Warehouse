from __future__ import annotations

import re
from typing import Dict, List, Optional
import logging

from lxml import html as lxml_html

from shop_handler import ShopHandler
from automation.web_get import fetch_with_requests, fetch_with_playwright
from automation.ai_helpers import AiInstance

log = logging.getLogger(__name__)

class AmazonHandler(ShopHandler):
    """Handler for Amazon order invoices."""

    POSSIBLE_NAMES = (
        "Amazon",
    )
    ORDER_NUMBER_REGEX = re.compile(r"(?i)order\s*[#:]*\s*(\d{3}-\d{7}-\d{7})")
    PRICE_REGEX = re.compile(r"\$\s*[0-9][0-9,]*\.?[0-9]{0,2}")
    QUANTITY_REGEX = re.compile(r"(?i)quantity\s*:\s*([0-9][0-9,]*)")
    TOTAL_ROW_REGEX = re.compile(r"(?i)^total\s+\$\s?[0-9][0-9,]*\.[0-9]{2}$")
    ASIN_IN_URL_REGEX = re.compile(
        r"/dp/([A-Z0-9]{10})(?:[/?]|$)",
        re.IGNORECASE,
    )  # Amazon Standard Identification Numbers (ASINs) are 10 characters.
    REQUEST_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    REQUEST_TIMEOUT = 15

    def guess_items(self) -> List[Dict[str, str]]:
        """Run the available extraction strategies and return the richest result set."""
        candidates_from_invoice_tables = self._guess_items_strategy_one()
        candidates_from_anchor_scan = self._guess_items_strategy_two()

        # Compare the candidate lists so that the more complete option is used.
        best_candidates = candidates_from_invoice_tables
        if len(candidates_from_anchor_scan) > len(candidates_from_invoice_tables):
            best_candidates = candidates_from_anchor_scan

        if not best_candidates:
            return []

        # Perform the network lookups only after a winning strategy has been selected.
        enriched_items: List[Dict[str, str]] = []
        seen_urls: set[str] = set()

        for candidate in best_candidates:
            base_url = candidate.get("url", "").strip()
            base_name = candidate.get("name", "").strip()
            if not base_url or not base_name:
                continue

            final_url, prod_name, description, product_code = self._fetch_remote_details(
                base_url,
                base_name,
            )

            final_name = prod_name
            ai = AiInstance("offline")

            try:
                ai_name = ai.query([prod_name], "You will be given the product name of an item available on Amazon, it will have some useless information that can be removed, reply with a concise name for the object without any SEO info or quantity information.")
                final_name = ai_name or prod_name
            except Exception as ex:
                log.error(f"AI exception when summarizing Amazon product name: {ex!r}")

            item: Dict[str, str] = {
                "name": final_name,
                "url": final_url,
                "source": self.POSSIBLE_NAMES[0],
            }

            fallback_product_code = candidate.get("product_code")
            if product_code is None and fallback_product_code:
                product_code = fallback_product_code
            if product_code:
                item["product_code"] = product_code

            #price_value = candidate.get("price", "")
            #if price_value:
            #    item["price"] = price_value

            #quantity_value = candidate.get("quantity", "")
            #if quantity_value:
            #    item["quantity"] = quantity_value

            description_value = description or candidate.get("description", "")
            if description_value:
                try:
                    ai_desc = ai.query([final_name, description_value], "You will be given the product name and description of an item available on Amazon. Summarize it down to a short paragraph about what the item is and what it is used for.")
                    description_value = ai_desc or description_value
                except Exception as ex:
                    log.error(f"AI exception when summarizing Amazon product name: {ex!r}")
                item["description"] = description_value

            url_key = item.get("url", "")
            if url_key and url_key not in seen_urls:
                enriched_items.append(item)
                seen_urls.add(url_key)

        return enriched_items

    def _guess_items_strategy_one(self) -> List[Dict[str, str]]:
        """Original table-driven strategy updated to honor the strict invoice end marker."""
        candidates: List[Dict[str, str]] = []
        seen_urls: set[str] = set()

        # Identify the terminating table row whose text matches the strict invoice total pattern and contains no
        # nested tables. Everything that follows this row in document order is considered
        # advertising noise and must be ignored.
        cutoff_row: Optional[lxml_html.HtmlElement] = None
        for potential_row in self.sanitized_root.xpath(".//tr"):
            if self._is_total_row(potential_row):
                cutoff_row = potential_row
                break

        # Collect anchors in document order stopping when the cutoff row is reached. If the
        # terminating row is absent we conservatively examine every anchor in the document.
        relevant_anchors: List[lxml_html.HtmlElement] = []
        if cutoff_row is None:
            relevant_anchors = [anchor for anchor in self.sanitized_root.xpath(".//a")]
        else:
            for node in self.sanitized_root.iter():
                if node is cutoff_row:
                    break
                if isinstance(getattr(node, "tag", None), str) and node.tag.lower() == "a":
                    relevant_anchors.append(node)

        for anchor in relevant_anchors:
            # Ascend until we reach the containing table cell so that we can inspect the layout
            # context surrounding the product link.
            td_element: Optional[lxml_html.HtmlElement] = None
            ancestor = anchor
            while ancestor is not None:
                ancestor = ancestor.getparent()
                if ancestor is None:
                    break
                if isinstance(getattr(ancestor, "tag", None), str) and ancestor.tag.lower() == "td":
                    td_element = ancestor
                    break

            if td_element is None:
                continue

            # Continue walking upward until we locate the parent table element.
            table_element: Optional[lxml_html.HtmlElement] = None
            ancestor = td_element
            while ancestor is not None:
                ancestor = ancestor.getparent()
                if ancestor is None:
                    break
                if isinstance(getattr(ancestor, "tag", None), str) and ancestor.tag.lower() == "table":
                    table_element = ancestor
                    break

            if table_element is None:
                continue

            # Use consistent document depth measurements so the product cell and any candidate quantity
            # cell must sit at the same structural level within the invoice table.
            # Measure the table cell depth relative to the document root so we can compare it against
            # any quantity cells found within the same table structure.
            td_depth_from_root = self._depth_from_root(td_element)

            matching_quantity_cell_found = False
            for quantity_cell in table_element.xpath(".//td[not(descendant::table)]"):
                quantity_text = self._normalize_whitespace(quantity_cell.text_content())
                if not quantity_text or not quantity_text.lower().startswith("quantity"):
                    continue

                if self._depth_from_root(quantity_cell) == td_depth_from_root:
                    matching_quantity_cell_found = True
                    break

            if not matching_quantity_cell_found:
                continue

            candidate = self._extract_candidate_from_cell(td_element)

            if candidate is None:
                # As a fallback, build a minimal candidate directly from the anchor. This keeps the
                # extraction resilient even if pricing or quantity information is not embedded
                # inside the same cell as the product link.
                anchor_name = self._normalize_whitespace(anchor.text_content())
                anchor_href = (anchor.get("href") or "").strip()
                if not anchor_name or not anchor_href:
                    continue

                candidate = {
                    "name": anchor_name,
                    "url": anchor_href,
                }

                dp_match = self.ASIN_IN_URL_REGEX.search(anchor_href)
                if dp_match:
                    candidate["product_code"] = dp_match.group(1).upper()

                # Attempt to locate pricing and quantity details by inspecting the nearest table row.
                row_element: Optional[lxml_html.HtmlElement] = td_element
                while row_element is not None and (
                    not isinstance(getattr(row_element, "tag", None), str)
                    or row_element.tag.lower() != "tr"
                ):
                    row_element = row_element.getparent()

                if row_element is not None:
                    row_text = self._normalize_whitespace(row_element.text_content())
                    price_match = self.PRICE_REGEX.search(row_text)
                    if price_match:
                        candidate["price"] = price_match.group(0).replace(" ", "")

                    quantity_match = self.QUANTITY_REGEX.search(row_text)
                    if quantity_match:
                        candidate["quantity"] = quantity_match.group(1).replace(",", "")

            url_key = candidate.get("url", "")
            if not url_key or url_key in seen_urls:
                continue

            candidates.append(candidate)
            seen_urls.add(url_key)

        return candidates

    def _guess_items_strategy_two(self) -> List[Dict[str, str]]:
        """Secondary strategy scanning every anchor for Amazon product detail links."""
        candidates: List[Dict[str, str]] = []
        seen_urls: set[str] = set()

        for anchor in self.sanitized_root.xpath(".//a"):
            href = (anchor.get("href") or "").strip()
            text = self._normalize_whitespace(anchor.text_content())
            if not href or not text:
                continue

            dp_match = self.ASIN_IN_URL_REGEX.search(href)
            if dp_match is None:
                continue

            if "amazon.com" not in href.lower():
                # Convert relative or regional links into canonical amazon.com URLs.
                normalized_path = href.lstrip("/")
                if normalized_path:
                    href = f"https://amazon.com/{normalized_path}"
                else:
                    href = "https://amazon.com/"

            product_code = dp_match.group(1).upper()  # Normalize ASIN to uppercase for consistency.

            if href in seen_urls:
                continue

            candidate: Dict[str, str] = {
                "name": text,
                "url": href,
                "product_code": product_code,
            }

            candidates.append(candidate)
            seen_urls.add(href)

        return candidates

    def _extract_candidate_from_cell(
        self,
        cell: lxml_html.HtmlElement,
    ) -> Optional[Dict[str, str]]:
        anchor = self._find_amazon_anchor(cell)
        if anchor is None:
            return None

        text_content = self._normalize_whitespace(cell.text_content())
        if "quantity:" not in text_content.lower():
            return None

        price_match = self.PRICE_REGEX.search(text_content)
        if price_match is None:
            return None

        quantity_match = self.QUANTITY_REGEX.search(text_content)
        quantity_text = quantity_match.group(1).replace(",", "") if quantity_match else ""

        base_name = self._normalize_whitespace(anchor.text_content())
        base_url = (anchor.get("href") or "").strip()
        if not base_name or not base_url:
            return None

        candidate: Dict[str, str] = {
            "name": base_name,
            "url": base_url,
            "price": price_match.group(0).replace(" ", ""),
        }

        if quantity_text:
            candidate["quantity"] = quantity_text

        return candidate

    def _find_amazon_anchor(self, cell: lxml_html.HtmlElement) -> Optional[lxml_html.HtmlElement]:
        for anchor in cell.xpath(".//a"):
            href = (anchor.get("href") or "").strip()
            text = self._normalize_whitespace(anchor.text_content())
            if not href or not text:
                continue
            if "amazon.com" not in href.lower():
                continue
            return anchor
        return None

    def _fetch_remote_details(
        self,
        url: str,
        fallback_name: str,
    ) -> tuple[str, str, str, Optional[str]]:
        final_url = url
        final_name = fallback_name
        description = ""
        product_code: Optional[str] = None

        if not url:
            return final_url, final_name, description, product_code

        try:
            # Use the shared automation helper so that HTTP behaviour stays consistent across handlers.
            html_content, _text_content, resolved_url = fetch_with_requests(url, timeout=self.REQUEST_TIMEOUT)
        except Exception:
            return final_url, final_name, description, product_code

        final_url = resolved_url or final_url

        # Parse the product page with a huge-tree capable parser to handle very large
        # Amazon listings that sometimes include massive embedded tables or scripts.
        parser = lxml_html.HTMLParser(huge_tree=True)
        try:
            remote_root = lxml_html.fromstring(html_content, parser=parser)
        except Exception:
            return final_url, final_name, description, product_code

        title_element = remote_root.xpath('.//span[@id="productTitle"]')
        if title_element:
            updated_name = self._normalize_whitespace(title_element[0].text_content())
            if updated_name:
                final_name = updated_name

        feature_sections = remote_root.xpath('.//div[@id="feature-bullets"]')
        if feature_sections:
            bullet_lines: List[str] = []
            for list_item in feature_sections[0].xpath('.//li'):
                bullet_text = self._normalize_whitespace(list_item.text_content())
                if bullet_text:
                    bullet_lines.append(f"- {bullet_text}")

            if bullet_lines:
                description = "\r\n".join(bullet_lines)

        if 'amazon.com' not in final_url.lower():
            normalized_path = final_url.lstrip('/')
            if normalized_path:
                final_url = f"https://amazon.com/{normalized_path}"
            else:
                final_url = 'https://amazon.com/'

        dp_match = self.ASIN_IN_URL_REGEX.search(final_url)
        if dp_match:
            product_code = dp_match.group(1).upper()  # Normalize ASIN to uppercase for consistency.

        return final_url, final_name, description, product_code

    def _is_total_row(self, row: lxml_html.HtmlElement) -> bool:
        if row is None or not isinstance(getattr(row, "tag", None), str):
            return False
        if row.tag.lower() != "tr":
            return False

        normalized_text = self._normalize_whitespace(row.text_content())
        if not self.TOTAL_ROW_REGEX.fullmatch(normalized_text):
            return False

        # Ensure the terminating row does not contain additional nested tables. The business logic
        # treats everything beyond this point as advertisement content that must be ignored.
        if row.xpath(".//table"):
            return False

        return True

    def _depth_from_root(self, element: lxml_html.HtmlElement) -> int:
        """Return how many ancestors separate an element from the document root."""
        depth = 0
        current = element
        while current is not None and current.getparent() is not None:
            depth += 1
            current = current.getparent()
        return depth

    def _normalize_whitespace(self, value: Optional[str]) -> str:
        if not value:
            return ''
        return re.sub(r'\s+', ' ', value).strip()

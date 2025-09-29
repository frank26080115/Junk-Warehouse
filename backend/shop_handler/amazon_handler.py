from __future__ import annotations

import re
from typing import Dict, List, Optional

from lxml import html as lxml_html

from shop_handler import ShopHandler
from automation.web_get import fetch_with_requests


class AmazonHandler(ShopHandler):
    """Handler for Amazon order invoices."""

    POSSIBLE_NAMES = (
        "Amazon",
    )
    ORDER_NUMBER_REGEX = re.compile(r"(?i)order\s*[#:]*\s*(\d{3}-\d{7}-\d{7})")
    TOTAL_ROW_REGEX = re.compile(r"(?i)\btotal\b")
    PRICE_REGEX = re.compile(r"\$\s*[0-9][0-9,]*\.?[0-9]{0,2}")
    QUANTITY_REGEX = re.compile(r"(?i)quantity\s*:\s*([0-9][0-9,]*)")
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
        session = requests.Session()
        enriched_items: List[Dict[str, str]] = []
        seen_urls: set[str] = set()

        for candidate in best_candidates:
            base_url = candidate.get("url", "").strip()
            base_name = candidate.get("name", "").strip()
            if not base_url or not base_name:
                continue

            final_url, final_name, description, product_code = self._fetch_remote_details(
                session,
                base_url,
                base_name,
            )

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

            price_value = candidate.get("price", "")
            if price_value:
                item["price"] = price_value

            quantity_value = candidate.get("quantity", "")
            if quantity_value:
                item["quantity"] = quantity_value

            description_value = description or candidate.get("description", "")
            if description_value:
                item["description"] = description_value

            url_key = item.get("url", "")
            if url_key and url_key not in seen_urls:
                enriched_items.append(item)
                seen_urls.add(url_key)

        return enriched_items

    def _guess_items_strategy_one(self) -> List[Dict[str, str]]:
        """Original table-driven strategy that walks invoice rows until totals appear."""
        candidates: List[Dict[str, str]] = []
        seen_urls: set[str] = set()
        reached_total_row = False

        for row in self.sanitized_root.xpath(".//tr"):
            if reached_total_row:
                break

            row_text = self._normalize_whitespace(row.text_content())
            if row_text and self._is_total_row(row_text):
                reached_total_row = True
                break

            for cell in row.xpath(".//td"):
                candidate = self._extract_candidate_from_cell(cell)
                if candidate is None:
                    continue

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
            remote_root = lxml_html.fromstring(html_content)
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

    def _is_total_row(self, text: str) -> bool:
        if not text:
            return False
        if not self.TOTAL_ROW_REGEX.search(text):
            return False
        return bool(self.PRICE_REGEX.search(text))

    def _normalize_whitespace(self, value: Optional[str]) -> str:
        if not value:
            return ''
        return re.sub(r'\s+', ' ', value).strip()

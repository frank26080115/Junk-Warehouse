from __future__ import annotations

import re
from typing import Dict, List, Optional

import requests
from lxml import html as lxml_html

from .shop_handler import ShopHandler


class AmazonHandler(ShopHandler):
    """Handler for Amazon order invoices."""

    POSSIBLE_NAMES = (
        "Amazon",
    )
    ORDER_NUMBER_REGEX = re.compile(r"(?i)order\s*[#:]*\s*(\d{3}-\d{7}-\d{7})")
    TOTAL_ROW_REGEX = re.compile(r"(?i)\btotal\b")
    PRICE_REGEX = re.compile(r"\$\s*[0-9][0-9,]*\.?[0-9]{0,2}")
    QUANTITY_REGEX = re.compile(r"(?i)quantity\s*:\s*([0-9][0-9,]*)")
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
        items: List[Dict[str, str]] = []
        seen_urls: set[str] = set()
        session = requests.Session()

        reached_total_row = False

        # Walk every table row in order so that we can stop once the "Total" row is
        # encountered. Amazon invoices often contain long marketing blocks after the
        # totals section and we want to completely ignore those distractions.
        for row in self.sanitized_root.xpath('.//tr'):
            if reached_total_row:
                break

            row_text = self._normalize_whitespace(row.text_content())
            if row_text and self._is_total_row(row_text):
                reached_total_row = True
                break

            # Examine every table cell because the invoice mixes product details and
            # promotional content within the same table structure.
            for cell in row.xpath('.//td'):
                item = self._extract_item_from_cell(session, cell)
                if item is None:
                    continue

                url_key = item.get('url', '')
                if not url_key or url_key in seen_urls:
                    continue

                items.append(item)
                seen_urls.add(url_key)

        return items

    def _extract_item_from_cell(
        self,
        session: requests.Session,
        cell: lxml_html.HtmlElement,
    ) -> Optional[Dict[str, str]]:
        anchor = self._find_amazon_anchor(cell)
        if anchor is None:
            return None

        text_content = self._normalize_whitespace(cell.text_content())
        if 'quantity:' not in text_content.lower():
            return None

        price_match = self.PRICE_REGEX.search(text_content)
        if price_match is None:
            return None

        quantity_match = self.QUANTITY_REGEX.search(text_content)
        quantity_text = quantity_match.group(1).replace(',', '') if quantity_match else ''

        base_name = self._normalize_whitespace(anchor.text_content())
        base_url = (anchor.get('href') or '').strip()
        if not base_name or not base_url:
            return None

        final_url, final_name, description = self._fetch_remote_details(session, base_url, base_name)

        item: Dict[str, str] = {
            'name': final_name,
            'url': final_url,
            'source': self.POSSIBLE_NAMES[0],
        }

        if price_match:
            item['price'] = price_match.group(0).replace(' ', '')
        if quantity_text:
            item['quantity'] = quantity_text
        if description:
            item['description'] = description

        return item

    def _find_amazon_anchor(self, cell: lxml_html.HtmlElement) -> Optional[lxml_html.HtmlElement]:
        for anchor in cell.xpath('.//a'):
            href = (anchor.get('href') or '').strip()
            text = self._normalize_whitespace(anchor.text_content())
            if not href or not text:
                continue
            if 'amazon.com' not in href.lower():
                continue
            return anchor
        return None

    def _fetch_remote_details(
        self,
        session: requests.Session,
        url: str,
        fallback_name: str,
    ) -> tuple[str, str, str]:
        final_url = url
        final_name = fallback_name
        description = ''

        try:
            response = session.get(
                url,
                headers=self.REQUEST_HEADERS,
                allow_redirects=True,
                timeout=self.REQUEST_TIMEOUT,
            )
        except Exception:
            return final_url, final_name, description

        if not response.ok:
            return response.url or final_url, final_name, description

        final_url = response.url or final_url

        try:
            remote_root = lxml_html.fromstring(response.text)
        except Exception:
            return final_url, final_name, description

        title_element = remote_root.xpath('.//span[@id="productTitle"]')
        if title_element:
            updated_name = self._normalize_whitespace(title_element[0].text_content())
            if updated_name:
                final_name = updated_name
                # TODO: Use backend.automation.ai_helpers to shorten verbose Amazon product titles into concise names.

        feature_sections = remote_root.xpath('.//div[@id="feature-bullets"]')
        if feature_sections:
            bullet_lines: List[str] = []
            for list_item in feature_sections[0].xpath('.//li'):
                bullet_text = self._normalize_whitespace(list_item.text_content())
                if bullet_text:
                    bullet_lines.append(f"- {bullet_text}")

            if bullet_lines:
                description = "\r\n".join(bullet_lines)
                # TODO: Use backend.automation.ai_helpers to summarize the feature bullets into a concise paragraph without marketing fluff.

        return final_url, final_name, description

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

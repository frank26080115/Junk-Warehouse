from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import requests
from lxml import html as lxml_html

from .shop_handler import ShopHandler


class DigiKeyHandler(ShopHandler):
    """Handler for Digi-Key order invoices."""

    POSSIBLE_NAMES = (
        "Digi-Key",
        "Digi Key",
        "DigiKey",
    )
    ORDER_NUMBER_REGEX = re.compile(r"(?i)order\s*[#:]*\s*(\d{7,10})")
    PRODUCT_CODES_REGEX = re.compile(
        r"(?is)digikey\s*part\s*number\W*([A-Za-z0-9][A-Za-z0-9\-._/ ]{0,80})\W*manufacturer\s*part\s*number\W*([A-Za-z0-9][A-Za-z0-9\-._/ ]{0,80})"
    )
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
        """Attempt to extract item information from Digi-Key invoices."""
        items: List[Dict[str, str]] = []
        seen_codes: set[str] = set()
        session = requests.Session()

        # Iterate over every table cell so we can inspect deeply nested structures.
        for cell in self.sanitized_root.xpath('.//td'):
            anchor = self._locate_anchor(cell)
            if anchor is None:
                continue

            combined_text = cell.text_content() or ""
            match = self.PRODUCT_CODES_REGEX.search(combined_text)
            if not match:
                continue

            digikey_code = self._clean_code(match.group(1))
            manufacturer_code = self._clean_code(match.group(2))
            if not digikey_code or not manufacturer_code:
                continue

            product_identifier = f"{digikey_code};{manufacturer_code}"
            if product_identifier in seen_codes:
                continue

            product_name = self._normalize_whitespace(anchor.text_content())
            href = (anchor.get('href') or '').strip()
            if not product_name or not href:
                continue

            final_url, description = self._retrieve_remote_details(session, href)

            item: Dict[str, str] = {
                'name': product_name,
                'url': final_url or href,
                'product_code': product_identifier,
                'source': self.POSSIBLE_NAMES[0],
            }

            if description:
                item['description'] = description

            items.append(item)
            seen_codes.add(product_identifier)

        return items

    def _locate_anchor(self, cell: lxml_html.HtmlElement) -> Optional[lxml_html.HtmlElement]:
        """Find the first meaningful hyperlink inside the provided cell."""
        for anchor in cell.xpath('.//a'):
            text = self._normalize_whitespace(anchor.text_content())
            href = (anchor.get('href') or '').strip()
            if text and href:
                return anchor
        return None

    def _clean_code(self, raw_code: Optional[str]) -> str:
        """Normalize product codes by trimming and collapsing whitespace."""
        if not raw_code:
            return ''
        return self._normalize_whitespace(raw_code)

    def _normalize_whitespace(self, value: Optional[str]) -> str:
        """Collapse any amount of whitespace into single spaces for readability."""
        if not value:
            return ''
        return re.sub(r'\s+', ' ', value).strip()

    def _retrieve_remote_details(self, session: requests.Session, url: str) -> Tuple[str, str]:
        """Follow redirects to the real product page and harvest its description."""
        if not url:
            return url, ''

        try:
            response = session.get(
                url,
                headers=self.REQUEST_HEADERS,
                allow_redirects=True,
                timeout=self.REQUEST_TIMEOUT,
            )
        except Exception:
            return url, ''

        if not response.ok:
            return self._strip_query(response.url or url), ''

        final_url = self._strip_query(response.url or url)
        description = self._extract_description_from_page(response.text)
        return final_url, description

    def _strip_query(self, target_url: str) -> str:
        """Remove query parameters and fragments so the URL is stable."""
        if not target_url:
            return ''
        parts = urlsplit(target_url)
        cleaned = parts._replace(query='', fragment='')
        return urlunsplit(cleaned)

    def _extract_description_from_page(self, html_text: str) -> str:
        """Search for the "Detailed Description" row on the product page."""
        if not html_text:
            return ''

        try:
            root = lxml_html.fromstring(html_text)
        except Exception:
            return ''

        # Examine every table row and look for a cell mentioning the label we need.
        for row in root.xpath('.//tr'):
            cells = row.xpath('./th | ./td')
            if not cells:
                continue

            for index, cell in enumerate(cells):
                label = self._normalize_whitespace(cell.text_content())
                if 'detailed description' not in label.lower():
                    continue

                # Try to find the neighbouring cell that carries the actual description.
                for neighbour in cells[index + 1:]:
                    description = self._normalize_whitespace(neighbour.text_content())
                    if description:
                        return description

        return ''

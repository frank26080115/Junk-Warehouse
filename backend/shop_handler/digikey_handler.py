from __future__ import annotations

import logging

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from lxml import html as lxml_html

from .digikey_api import (
    DigiKeyAPIError,
    DigiKeyConfigurationError,
    get_all_items,
    get_item_details,
)
from shop_handler import ShopHandler
from automation.web_get import fetch_with_playwright

log = logging.getLogger(__name__)

class DigiKeyHandler(ShopHandler):
    """Handler for Digi-Key order invoices."""

    def has_already_been_handled(self, shop_name: str, order_number: str) -> bool:
        """Digi-Key invoices reuse the shared human-processing lookup without modification."""
        return super().has_already_been_handled(shop_name, order_number)

    POSSIBLE_NAMES = (
        "Digi-Key",
        "Digi Key",
        "DigiKey",
    )
    ORDER_NUMBER_REGEX = re.compile(r"(?i)\s*number\s*[A-Za-z]*\s*[#:]*\s*(\d{7,12})")
    PRODUCT_CODES_REGEX = re.compile(
        r"(?is)digikey\s*part\s*number\W*([A-Za-z0-9][A-Za-z0-9\-._/ ]{0,80})\W*manufacturer\s*part\s*number\W*([A-Za-z0-9][A-Za-z0-9\-._/ ]{0,80})"
    )
    REQUEST_TIMEOUT = 15

    def get_order_number(self) -> Optional[str]:
        pattern = self.ORDER_NUMBER_REGEX
        if pattern is None:
            return None
        match = pattern.search(self._get_sanitized_text())
        if match:
            return match.group(1) if match.groups() else match.group(0)
        return None

    def guess_items(self) -> List[Dict[str, str]]:
        """Attempt all Digi-Key strategies in priority order."""

        # Strategy 1: Contact the Digi-Key API when credentials are configured.
        api_items = self._guess_items_via_api()
        if api_items:
            return api_items

        # Strategy 2: Parse structured links in the email style invoice.
        email_items = self._guess_items_from_email()
        if email_items:
            return email_items

        # Strategy 3: Analyse the web invoice layout when the email provides no hints.
        return self._guess_items_from_web_invoice()

    def _guess_items_via_api(self) -> List[Dict[str, str]]:
        """Use the Digi-Key REST API to retrieve detailed line item data."""

        order_number = self.get_order_number()
        if not order_number:
            # Without an order number the API cannot be queried.
            return []

        try:
            product_numbers = get_all_items(order_number)
        except DigiKeyConfigurationError as config_error:
            log.info(
                "Digi-Key API configuration is incomplete, skipping API strategy: %s",
                config_error,
            )
            return []
        except DigiKeyAPIError as api_error:
            log.info(
                "Digi-Key API rejected the sales order lookup for %s: %s",
                order_number,
                api_error,
            )
            return []

        if not product_numbers:
            return []

        detailed_items: List[Dict[str, str]] = []
        seen_codes: set[str] = set()

        for product_number in product_numbers:
            normalized_number = self._clean_code(product_number)
            if not normalized_number or normalized_number in seen_codes:
                continue

            try:
                details = get_item_details(normalized_number)
            except DigiKeyAPIError as detail_error:
                log.info(
                    "Digi-Key API could not return details for %s: %s",
                    normalized_number,
                    detail_error,
                )
                continue

            if not isinstance(details, dict):
                continue

            product_name = self._normalize_whitespace(details.get('name'))
            product_description = self._normalize_whitespace(details.get('description'))
            product_url = (details.get('url') or '').strip()
            product_code = self._clean_code(details.get('product_code')) or normalized_number

            item: Dict[str, str] = {
                'name': product_name or normalized_number,
                'url': product_url,
                'product_code': product_code,
                'source': self.POSSIBLE_NAMES[0],
            }

            if product_description:
                item['description'] = product_description

            image_url = (details.get('img_url') or '').strip()
            if image_url:
                item['img_url'] = image_url

            detailed_items.append(item)
            seen_codes.add(normalized_number)

        return detailed_items

    def _guess_items_from_email(self) -> List[Dict[str, str]]:
        """Parse structured hyperlinks embedded in the email style invoice."""
        items: List[Dict[str, str]] = []
        seen_codes: set[str] = set()

        # Visit every hyperlink so we can verify where it leads and how it is presented.
        for anchor in self.sanitized_root.xpath('.//a'):
            href = (anchor.get('href') or '').strip()
            if not href:
                continue

            if not self._is_digikey_link(href):
                # Ignore links that do not clearly target Digi-Key product pages.
                continue

            if anchor.xpath('.//img'):
                # The caller asked for plain text anchors only, so images disqualify the candidate.
                continue

            product_name = self._normalize_whitespace(anchor.text_content())
            if not product_name:
                continue

            table = self._find_enclosing_table(anchor)
            if table is None:
                continue

            table_summary = self._normalize_whitespace(table.text_content())
            if not table_summary:
                continue

            match = self.PRODUCT_CODES_REGEX.search(table_summary)
            if not match:
                continue

            digikey_code = self._clean_code(match.group(1))
            manufacturer_code = self._clean_code(match.group(2))
            if not digikey_code or not manufacturer_code:
                continue

            product_identifier = f"{digikey_code};{manufacturer_code}"
            if product_identifier in seen_codes:
                continue

            final_url, description = self._retrieve_remote_details(href)

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

    def _guess_items_from_web_invoice(self) -> List[Dict[str, str]]:
        """Fallback parser that scans Digi-Key's product detail cells."""

        # Collect candidate cells that hold structured product details in MudBlazor tables.
        detail_cells = self.sanitized_root.xpath(
            ".//td[@data-label='Product Details' and contains(concat(' ', normalize-space(@class), ' '), ' product-details-cell ')]"
        )

        preliminary_items: List[Dict[str, str]] = []
        seen_codes: set[str] = set()

        for cell in detail_cells:
            # Each cell contains a dedicated container with multiple <p> blocks of metadata.
            containers = cell.xpath(
                ".//div[contains(concat(' ', normalize-space(@class), ' '), ' products-details ')]"
            )
            if not containers:
                continue

            container = containers[0]
            paragraphs = container.xpath('./p')
            if not paragraphs:
                continue

            anchor_index: Optional[int] = None
            anchor_element: Optional[lxml_html.HtmlElement] = None

            for index, paragraph in enumerate(paragraphs):
                anchors = paragraph.xpath('.//a')
                if not anchors:
                    continue

                hyperlink = anchors[0]
                hyperlink_text = self._normalize_whitespace(hyperlink.text_content())
                href = (hyperlink.get('href') or '').strip()
                if not hyperlink_text or not href:
                    continue

                anchor_index = index
                anchor_element = hyperlink
                break

            if anchor_index is None or anchor_element is None:
                continue

            if anchor_index + 2 >= len(paragraphs):
                continue

            digikey_code = self._clean_code(anchor_element.text_content())
            manufacturer_code = self._clean_code(paragraphs[anchor_index + 1].text_content())
            product_name = self._normalize_whitespace(paragraphs[anchor_index + 2].text_content())
            href = (anchor_element.get('href') or '').strip()

            if not digikey_code or not manufacturer_code or not product_name or not href:
                continue

            product_identifier = f"{digikey_code};{manufacturer_code}"
            if product_identifier in seen_codes:
                continue

            preliminary_items.append(
                {
                    'name': product_name,
                    'url': href,
                    'product_code': product_identifier,
                }
            )
            seen_codes.add(product_identifier)

        final_items: List[Dict[str, str]] = []

        for entry in preliminary_items:
            # Reuse the remote scraping routine so the behaviour matches the primary path.
            final_url, description = self._retrieve_remote_details(entry['url'])

            item: Dict[str, str] = {
                'name': entry['name'],
                'url': final_url or entry['url'],
                'product_code': entry['product_code'],
                'source': self.POSSIBLE_NAMES[0],
            }

            if description:
                item['description'] = description

            final_items.append(item)

        return final_items

    def _find_enclosing_table(self, element: lxml_html.HtmlElement) -> Optional[lxml_html.HtmlElement]:
        """Walk up the tree until we locate the nearest table that wraps the element."""
        current: Optional[lxml_html.HtmlElement] = element
        while current is not None:
            if current.tag and current.tag.lower() == 'table':
                return current
            current = current.getparent()
        return None

    def _is_digikey_link(self, href: str) -> bool:
        """Validate that the hyperlink clearly targets a Digi-Key domain."""
        if not href:
            return False

        candidate = href.strip()

        # Normalise schemeless URLs so that urlsplit can inspect the host name reliably.
        if candidate.startswith('//'):
            candidate = f'https:{candidate}'
        elif candidate.startswith('www.'):
            candidate = f'https://{candidate}'

        try:
            parts = urlsplit(candidate)
        except Exception:
            return False

        host = parts.netloc.lower()
        if not host:
            return False

        return host.endswith('digikey.com')

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

    def _retrieve_remote_details(self, url: str) -> Tuple[str, str]:
        """Follow redirects to the real product page and harvest its description."""
        if not url:
            return url, ''

        try:
            # Leverage the automation helper to obtain consistent HTTP handling and content parsing.
            html_content, _text_content, resolved_url = fetch_with_playwright(url)
        except Exception as ex:
            log.error(f"Digi-Key part remote fetch exception: {ex!r}")
            return url, ''

        final_url = self._strip_query(resolved_url or url)
        description = self._extract_description_from_page(html_content)
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

        # Configure the HTML parser with huge_tree support so exceptionally large
        # DigiKey product pages can be processed without triggering parser guards.
        parser = lxml_html.HTMLParser(huge_tree=True)
        try:
            root = lxml_html.fromstring(html_text, parser=parser)
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

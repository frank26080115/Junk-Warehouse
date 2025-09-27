from __future__ import annotations

import re
from typing import Dict, List

from .shop_handler import ShopHandler


class McMasterCarrHandler(ShopHandler):
    """Handler for McMaster-Carr order invoices."""

    POSSIBLE_NAMES = (
        "McMaster-Carr",
        "McMaster",
    )
    ORDER_NUMBER_REGEX = re.compile(r"(?i)order\s*[#:]*\s*(\d{4,6}[A-Z]{3,20})")

    def guess_items(self) -> List[Dict[str, str]]:
        """Attempt to extract item details from McMaster-Carr order tables."""
        # Gather every table in the sanitized DOM. The structure we care about should
        # have a <tbody> that contains the item rows.
        candidate_tables = list(self.sanitized_root.xpath('.//table'))

        target_table = None
        for table in candidate_tables:
            # Only inspect direct <tbody> children to avoid wandering into nested tables.
            bodies = table.xpath('./tbody') or []
            if not bodies:
                continue

            table_is_valid = True
            has_any_valid_row = False

            for body in bodies:
                for row in body.xpath('./tr'):
                    cells = row.xpath('./td')
                    if len(cells) < 3:
                        table_is_valid = False
                        break

                    hyperlink = None
                    for anchor in cells[1].xpath('.//a'):
                        anchor_text = anchor.text_content().strip()
                        if anchor_text:
                            hyperlink = anchor
                            break

                    if hyperlink is None:
                        table_is_valid = False
                        break

                    product_code = hyperlink.text_content().strip()
                    href = (hyperlink.get('href') or '').strip()
                    if not product_code or not href or product_code not in href:
                        table_is_valid = False
                        break

                    description_text = cells[2].text_content().strip()
                    if not description_text:
                        table_is_valid = False
                        break

                    has_any_valid_row = True

                if not table_is_valid:
                    break

            if table_is_valid and has_any_valid_row:
                target_table = table
                break

        if target_table is None:
            # The table format did not materialize, so fall back to a div-based parser.
            return self._guess_items_2()

        # Build the final list of dictionaries containing the structured data.
        items: List[Dict[str, str]] = []

        for body in target_table.xpath('./tbody'):
            for row in body.xpath('./tr'):
                cells = row.xpath('./td')
                if len(cells) < 3:
                    continue

                hyperlink = None
                for anchor in cells[1].xpath('.//a'):
                    anchor_text = anchor.text_content().strip()
                    if anchor_text:
                        hyperlink = anchor
                        break

                if hyperlink is None:
                    continue

                product_code = hyperlink.text_content().strip()
                href = (hyperlink.get('href') or '').strip()
                if not product_code or not href or product_code not in href:
                    continue

                description_text = cells[2].text_content().strip()
                if not description_text:
                    continue

                name = description_text.strip()
                description = ''
                if ',' in description_text:
                    first_part, remaining = description_text.split(',', 1)
                    name = first_part.strip()
                    description = remaining.strip()

                item: Dict[str, str] = {
                    'name': name,
                    'description': description,
                    'product_code': product_code,
                    'url': href,
                    'source': self.POSSIBLE_NAMES[0],
                }

                items.append(item)

        return items

    def _guess_items_2(self) -> List[Dict[str, str]]:
        """Fallback strategy that inspects div-based product listings."""
        # Collect every div that matches the expected class used for product rows.
        fallback_rows = self.sanitized_root.xpath(".//div[contains(concat(' ', normalize-space(@class), ' '), ' dtl-row-info ')]")

        items: List[Dict[str, str]] = []

        for row in fallback_rows:
            # Find the first hyperlink that looks like a product link so we can harvest details.
            hyperlink = None
            for candidate_anchor in row.xpath('.//a'):
                href = (candidate_anchor.get('href') or '').strip()
                if href:
                    hyperlink = candidate_anchor
                    break

            if hyperlink is None:
                continue

            href = (hyperlink.get('href') or '').strip()
            if not href:
                continue

            product_code = ''
            product_code_nodes = hyperlink.xpath('./p')
            if product_code_nodes:
                product_code = product_code_nodes[0].text_content().strip()

            if not product_code or product_code not in href:
                continue

            # Flatten the descriptive hyperlink text into a human-readable sentence.
            anchor_text_raw = hyperlink.text_content()
            anchor_text_clean = re.sub(r"\s+", " ", anchor_text_raw).strip()
            if product_code:
                anchor_text_clean = anchor_text_clean.replace(product_code, '').strip()
            anchor_text_clean = re.sub(r"\s+", " ", anchor_text_clean).strip()

            if not anchor_text_clean:
                continue

            name = anchor_text_clean
            description = ''
            if ',' in anchor_text_clean:
                first_part, remaining = anchor_text_clean.split(',', 1)
                name = first_part.strip()
                description = remaining.strip()

            item: Dict[str, str] = {
                'name': name,
                'description': description,
                'product_code': product_code,
                'url': href,
                'source': self.POSSIBLE_NAMES[0],
            }

            items.append(item)

        return items

from __future__ import annotations

import re
from typing import Dict, List

from shop_handler import ShopHandler


class McMasterCarrHandler(ShopHandler):
    """Handler for McMaster-Carr order invoices."""

    POSSIBLE_NAMES = (
        "McMaster-Carr",
        "McMaster",
    )
    ORDER_NUMBER_REGEX = re.compile(r"(?i)order\s*[#:]*\s*(\d{4,6}[A-Z]{3,20})")
    PRODUCT_CODE_REGEX = re.compile(r"^[A-Z0-9]+$")

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
            # Walk every hyperlink in the row so we can pinpoint the one that truly
            # represents a product. Some rows carry additional links such as images or
            # ancillary resources, so the first <a> tag may not be the correct match.
            hyperlink = None
            href = ''
            product_code = ''
            anchor_text_collapsed = ''

            for candidate_anchor in row.xpath('.//a'):
                candidate_href = (candidate_anchor.get('href') or '').strip()
                if not candidate_href:
                    continue

                # Examine every <p> inside the anchor, because the product code might be
                # rendered in any of them. We only accept strings that match the expected
                # "capital letters and digits" pattern so we avoid misidentifying other
                # text snippets as a code.
                candidate_product_code = ''
                for node in candidate_anchor.xpath('./p'):
                    node_text = node.text_content().strip()
                    if node_text and self.PRODUCT_CODE_REGEX.fullmatch(node_text):
                        candidate_product_code = node_text
                        break

                if not candidate_product_code or candidate_product_code not in candidate_href:
                    continue

                candidate_anchor_text = candidate_anchor.text_content()
                candidate_anchor_text_collapsed = re.sub(r"\s+", " ", candidate_anchor_text).strip()
                if not candidate_anchor_text_collapsed or candidate_product_code not in candidate_anchor_text_collapsed:
                    continue

                hyperlink = candidate_anchor
                href = candidate_href
                product_code = candidate_product_code
                anchor_text_collapsed = candidate_anchor_text_collapsed
                break

            if hyperlink is None:
                continue

            # Remove the product code from the collapsed text so we can craft a
            # human-friendly description while keeping the surrounding narrative intact.
            anchor_text_clean = anchor_text_collapsed.replace(product_code, '', 1).strip()
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

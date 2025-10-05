from __future__ import annotations

import re
from typing import Dict, List
from urllib.parse import urljoin

from lxml import html as lxml_html

from shop_handler import ShopHandler
from automation.web_get import fetch_with_playwright


class McMasterCarrHandler(ShopHandler):
    """Handler for McMaster-Carr order invoices."""

    def has_already_been_handled(self, shop_name: str, order_number: str) -> bool:
        """McMaster-Carr invoices reuse the shared human-processing lookup without modification."""
        return super().has_already_been_handled(shop_name, order_number)

    POSSIBLE_NAMES = (
        "McMaster-Carr",
        "McMaster",
    )
    ORDER_NUMBER_REGEX = re.compile(r"(\d{4,6}[A-Z]{3,20})")
    PRODUCT_CODE_REGEX = re.compile(r"^[A-Z0-9]+$")

    @staticmethod
    def _contains_digit(value: str) -> bool:
        """Return True when ``value`` includes any numeric digit."""
        return any(character.isdigit() for character in value)

    def get_order_number(self) -> Optional[str]:
        pattern = self.ORDER_NUMBER_REGEX
        if pattern is None:
            return None
        match = pattern.search(self._get_sanitized_text())
        if match:
            return match.group(1) if match.groups() else match.group(0)
        return None

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

                    description_text = cells[1].text_content().strip()
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

                description_text = cells[1].text_content().strip()
                if not description_text:
                    continue

                name = description_text.strip()
                description = ''
                if ',' in description_text:
                    first_part, remaining = description_text.split(',', 1)
                    name = first_part.strip()
                    description = remaining.strip()
                    # Preserve the digits from the trailing narrative when the main name lacks them.
                    if description and self._contains_digit(description) and not self._contains_digit(name):
                        # Retain the full text as the visible name so part numbers stay front-and-center.
                        name = description_text.strip()

                item: Dict[str, str] = {
                    'name': name,
                    'description': description,
                    'product_code': product_code,
                    'url': href,
                    'source': self.POSSIBLE_NAMES[0],
                }

                items.append(item)

        # Enrich the harvested rows with authoritative metadata gathered from
        # the corresponding product detail pages.
        return [
            self._apply_remote_details(candidate)
            for candidate in items
        ]

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
                # Apply the same digit-preserving logic for the fallback parser.
                if description and self._contains_digit(description) and not self._contains_digit(name):
                    # Promote the entire phrase to the name so identifiers are never hidden in the description alone.
                    name = anchor_text_clean.strip()

            item: Dict[str, str] = {
                'name': name,
                'description': description,
                'product_code': product_code,
                'url': href,
                'source': self.POSSIBLE_NAMES[0],
            }

            items.append(item)

        # Apply the same enrichment used by the table-driven parser so the
        # calling code receives consistent results regardless of which path
        # successfully interpreted the invoice.
        return [
            self._apply_remote_details(candidate)
            for candidate in items
        ]

    def _apply_remote_details(self, item: Dict[str, str]) -> Dict[str, str]:
        """Return a copy of ``item`` augmented with remote product details."""
        product_code = item.get('product_code', '').strip()
        base_url = item.get('url', '').strip()

        final_url, name, description, image_url = self._fetch_remote_details(
            product_code,
            base_url,
        )

        enriched = dict(item)
        if final_url:
            enriched['url'] = final_url
        if name:
            enriched['name'] = name
        if description:
            enriched['description'] = description
        if image_url:
            enriched['img_url'] = image_url

        return enriched

    def _fetch_remote_details(
        self,
        product_code: str,
        product_url: str,
    ) -> tuple[str, str, str, str]:
        """Look up the McMaster-Carr product page and extract key metadata."""
        # Prefer the explicit hyperlink from the invoice. When it is missing,
        # fall back to McMaster-Carr's predictable URL structure based on the
        # product code itself.
        candidate_url = product_url.strip()
        if not candidate_url and product_code:
            candidate_url = f"https://www.mcmaster.com/{product_code.strip()}/"

        if not candidate_url:
            return '', '', '', ''

        try:
            html_content, _text_content, resolved_url = fetch_with_playwright(candidate_url)
        except Exception:
            return candidate_url, '', '', ''

        final_url = resolved_url or candidate_url

        if not html_content:
            return final_url, '', '', ''

        parser = lxml_html.HTMLParser(huge_tree=True)
        try:
            remote_root = lxml_html.fromstring(html_content, parser=parser)
        except Exception:
            return final_url, '', '', ''

        def _select_text(nodes: List[lxml_html.HtmlElement]) -> str:
            for node in nodes:
                text_value = (node.text_content() or '').strip()
                if text_value:
                    return text_value
            return ''

        def _match_xpath(tag: str, token: str) -> List[lxml_html.HtmlElement]:
            lowercase_token = token.lower()
            expression = (
                f".//{tag}[contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
                f"'{lowercase_token}')]"
            )
            return remote_root.xpath(expression)

        name = _select_text(_match_xpath('h1', 'productdetailheaderprimary'))
        description = _select_text(_match_xpath('h3', 'productdetailheadersecondary'))
        if description and self._contains_digit(description) and not self._contains_digit(name):
            # Mirror the invoice parsing heuristic so part numbers within the remote description remain visible in the name.
            if name:
                name = f"{name}, {description}"
            else:
                name = description

        image_url = ''
        image_containers = _match_xpath('div', 'imagecontainer')
        if image_containers:
            first_container = image_containers[0]
            image_nodes = first_container.xpath('.//img')
            if image_nodes:
                raw_src = (image_nodes[0].get('src') or '').strip()
                if raw_src:
                    image_url = urljoin('https://www.mcmaster.com/', raw_src)

        return final_url, name, description, image_url

from __future__ import annotations

import re
from typing import Optional

from .shop_handler import GenericShopHandler


class EbayHandler(GenericShopHandler):
    """Specialized handler for eBay invoices that reuses the generic workflow."""

    # The store name is intentionally captured in lowercase so comparisons remain predictable,
    # even when upstream callers provide differently capitalized variants.
    POSSIBLE_NAMES = ("ebay",)

    # eBay transaction confirmation links expose the identifying value as a query parameter.
    # The verbose regular expression makes it clear that we only accept digits so accidental
    # matches on unrelated query parameters are avoided.
    ORDER_NUMBER_REGEX = re.compile(r"transactionId=(?P<transaction_id>\d+)", re.IGNORECASE)

    def get_order_number(self) -> Optional[str]:
        """Extract the transaction identifier directly from embedded hyperlinks."""

        pattern = self.ORDER_NUMBER_REGEX
        if pattern is None:
            # The base implementation treats a missing pattern as an unsupported feature, so we
            # mirror that logic and return early to avoid unexpected attribute errors.
            return None

        # Use the original HTML instead of the sanitized snapshot to ensure query parameters remain
        # intact. Sanitization can reorder or escape characters, so working with the raw input keeps
        # the extraction precise and easy to reason about.
        match = pattern.search(self.raw_html)
        if match:
            transaction_id = match.group("transaction_id") or match.group(0)
            return transaction_id

        # Fall back to the generic strategy so that any additional heuristics implemented there
        # continue to help with unexpected invoice formats.
        return super().get_order_number()

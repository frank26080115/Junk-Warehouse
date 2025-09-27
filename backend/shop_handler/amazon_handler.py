from __future__ import annotations

import re
from typing import Dict, List

from .shop_handler import ShopHandler


class AmazonHandler(ShopHandler):
    """Handler for Amazon order invoices."""

    POSSIBLE_NAMES = (
        "Amazon",
    )
    ORDER_NUMBER_REGEX = re.compile(r"(?i)order\s*[#:]*\s*(\d{3}-\d{7}-\d{7})")

    def guess_items(self) -> List[Dict[str, str]]:
        # TODO: Implement Amazon specific extraction logic when the DOM structure is understood.
        return []

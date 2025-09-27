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
    ORDER_NUMBER_REGEX = re.compile(r"(?i)order\s*[#:]*\s*(\d{4}[A-Z]{5})")

    def guess_items(self) -> List[Dict[str, str]]:
        # TODO: Implement McMaster-Carr specific extraction logic when the DOM structure is understood.
        return []

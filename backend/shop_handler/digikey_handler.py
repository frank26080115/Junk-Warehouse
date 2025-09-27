from __future__ import annotations

import re
from typing import Dict, List

from .shop_handler import ShopHandler


class DigiKeyHandler(ShopHandler):
    """Handler for Digi-Key order invoices."""

    POSSIBLE_NAMES = (
        "Digi-Key",
        "Digi Key",
        "DigiKey",
    )
    ORDER_NUMBER_REGEX = re.compile(r"(?i)order\s*[#:]*\s*(\d{7,10})")

    def guess_items(self) -> List[Dict[str, str]]:
        # TODO: Implement Digi-Key specific extraction logic when the DOM structure is understood.
        return []

"""Helpers for selecting specialized shop handlers."""

from .shop_handler import ShopHandler, GenericShopHandler
from .amazon_handler import AmazonHandler
from .digikey_handler import DigiKeyHandler
from .mcmastercarr_handler import McMasterCarrHandler

__all__ = [
    "ShopHandler",
    "GenericShopHandler",
    "AmazonHandler",
    "DigiKeyHandler",
    "McMasterCarrHandler",
]

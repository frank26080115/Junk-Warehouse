from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from backend.app import config_loader

__all__ = [
    "get_all_items",
    "get_item_details",
]

log = logging.getLogger(__name__)

# Resolve the repository's secrets file in the same manner as the rest of the
# backend modules.  The Digi-Key credentials are optional, therefore the helper
# functions below perform extensive validation and raise a descriptive error
# when configuration is incomplete.
_SECRETS_PATH = Path(config_loader.CONFIG_DIR) / "secrets.json"
_CREDENTIALS_KEY = "digikey_api"

# Digi-Key exposes an OAuth 2.0 token service.  The values below reflect the
# documented endpoints for the production environment.  They can be overridden
# by tests if needed by monkeypatching the module level constants.
_TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
_SALES_ORDER_URL_TEMPLATE = (
    "https://api.digikey.com/services/orderdetails/v3/salesorders/{sales_order_id}"
)
_PART_DETAILS_URL_TEMPLATE = (
    "https://api.digikey.com/services/partsearch/v2/partdetails/{part_number}"
)
_DEFAULT_SCOPES: tuple[str, ...] = (
    "orderdetails",
    "salesorder",
    "productinfo",
)

# Maintain a single requests.Session so TCP connections can be reused across
# multiple invocations and to centralise timeout handling.
_SESSION = requests.Session()

# Cache credential material between calls.  The secrets file is small and rarely
# changes during runtime, so caching avoids repeated disk reads.
_CACHED_CREDENTIALS: Optional[Dict[str, str]] = None

# Cache the most recent OAuth token so we only contact the token endpoint when
# it expires.  The expires_at value stores the UNIX timestamp of the moment the
# token should be considered stale.
_TOKEN_CACHE: Dict[str, Any] = {
    "access_token": None,
    "expires_at": 0.0,
}


class DigiKeyConfigurationError(RuntimeError):
    """Raised when Digi-Key credentials are missing or invalid."""


class DigiKeyAPIError(RuntimeError):
    """Raised when Digi-Key reports a transport or payload issue."""


def _load_credentials() -> Dict[str, str]:
    """Load Digi-Key OAuth credentials from config/secrets.json."""

    global _CACHED_CREDENTIALS

    if _CACHED_CREDENTIALS is not None:
        return _CACHED_CREDENTIALS

    if not _SECRETS_PATH.exists():
        raise DigiKeyConfigurationError(
            f"Expected Digi-Key credentials at {_SECRETS_PATH}, but the file was not found."
        )

    try:
        raw_text = _SECRETS_PATH.read_text(encoding="utf-8")
        secrets: Dict[str, Any] = json.loads(raw_text)
    except Exception as exc:  # pragma: no cover - defensive logging
        log.exception("Unable to load Digi-Key credentials from %s", _SECRETS_PATH)
        raise DigiKeyConfigurationError("Unable to parse Digi-Key credentials file.") from exc

    section = secrets.get(_CREDENTIALS_KEY)
    if not isinstance(section, dict):
        raise DigiKeyConfigurationError(
            f"The secrets.json file does not contain the '{_CREDENTIALS_KEY}' section."
        )

    required_keys = ("customer_id", "client_id", "client_secret")
    credentials: Dict[str, str] = {}

    for key in required_keys:
        value = section.get(key)
        if not isinstance(value, str) or not value.strip():
            raise DigiKeyConfigurationError(
                f"The Digi-Key credential '{key}' is missing or empty in secrets.json."
            )
        credentials[key] = value.strip()

    _CACHED_CREDENTIALS = credentials
    return credentials


def _obtain_access_token() -> str:
    """Request an OAuth access token from Digi-Key when the cache is stale."""

    now = time.time()
    cached_token = _TOKEN_CACHE.get("access_token")
    expires_at = float(_TOKEN_CACHE.get("expires_at") or 0.0)

    # Refresh the token one minute before it expires to avoid race conditions
    # when multiple workers start using the module simultaneously.
    if cached_token and now < (expires_at - 60):
        return cached_token

    credentials = _load_credentials()
    data = {
        "client_id": credentials["client_id"],
        "client_secret": credentials["client_secret"],
        "grant_type": "client_credentials",
        "scope": " ".join(_DEFAULT_SCOPES),
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        response = _SESSION.post(_TOKEN_URL, data=data, headers=headers, timeout=30)
    except requests.RequestException as exc:  # pragma: no cover - network failure
        log.exception("Failed to contact Digi-Key token endpoint at %s", _TOKEN_URL)
        raise DigiKeyAPIError("Unable to reach the Digi-Key token endpoint.") from exc

    if response.status_code >= 400:
        log.error(
            "Digi-Key token endpoint returned %s: %s",
            response.status_code,
            response.text,
        )
        raise DigiKeyAPIError("Digi-Key rejected the OAuth credential request.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise DigiKeyAPIError("Digi-Key token response was not valid JSON.") from exc

    token = payload.get("access_token")
    expires_in = payload.get("expires_in", 0)
    if not isinstance(token, str) or not token:
        raise DigiKeyAPIError("Digi-Key token response did not include an access_token field.")

    expires_in_seconds = float(expires_in) if isinstance(expires_in, (int, float, str)) else 0.0
    if expires_in_seconds <= 0:
        # Default to a conservative ten minute lifetime if Digi-Key omits the expiry.
        expires_in_seconds = 600.0

    _TOKEN_CACHE["access_token"] = token
    _TOKEN_CACHE["expires_at"] = now + expires_in_seconds
    return token


def _build_headers() -> Dict[str, str]:
    """Construct HTTP headers required for Digi-Key API calls."""

    credentials = _load_credentials()
    token = _obtain_access_token()

    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "X-DIGIKEY-Customer-Id": credentials["customer_id"],
        "X-DIGIKEY-Client-Id": credentials["client_id"],
    }


def _perform_get(url: str) -> Dict[str, Any]:
    """Issue an authenticated GET request and return the decoded JSON payload."""

    headers = _build_headers()

    try:
        response = _SESSION.get(url, headers=headers, timeout=30)
    except requests.RequestException as exc:  # pragma: no cover - network failure
        log.exception("Network problem while calling Digi-Key API at %s", url)
        raise DigiKeyAPIError("Unable to reach the Digi-Key API endpoint.") from exc

    if response.status_code == 404:
        raise DigiKeyAPIError(f"Digi-Key returned 404 for {url}. Please verify the identifier.")

    if response.status_code >= 400:
        log.error("Digi-Key API error %s: %s", response.status_code, response.text)
        raise DigiKeyAPIError("Digi-Key API reported an error. See logs for details.")

    if not response.content:
        return {}

    try:
        return response.json()
    except ValueError as exc:
        raise DigiKeyAPIError("Digi-Key API response was not valid JSON.") from exc


def get_all_items(salesOrderId: str) -> List[str]:
    """Return Digi-Key product numbers for every line item in a sales order."""

    if not salesOrderId:
        return []

    url = _SALES_ORDER_URL_TEMPLATE.format(sales_order_id=salesOrderId)
    payload = _perform_get(url)

    # Digi-Key's schema groups line items in a couple of differently cased keys
    # depending on the API version.  Search through the known variants while
    # keeping the code intentionally verbose for clarity.
    candidate_keys = [
        "SalesOrderLines",
        "salesOrderLines",
        "LineItems",
        "lineItems",
    ]

    line_items: List[Dict[str, Any]] = []
    for key in candidate_keys:
        raw_value = payload.get(key)
        if isinstance(raw_value, list):
            line_items = [entry for entry in raw_value if isinstance(entry, dict)]
            if line_items:
                break

    product_numbers: List[str] = []
    for entry in line_items:
        # Prefer DigiKeyProductNumber but fall back to DigiKeyPartNumber to cover
        # variations across the API family.
        raw_number = entry.get("DigiKeyProductNumber") or entry.get("DigiKeyPartNumber")
        if isinstance(raw_number, str):
            cleaned = raw_number.strip()
            if cleaned and cleaned not in product_numbers:
                product_numbers.append(cleaned)

    return product_numbers


def get_item_details(digikey_product_number: str) -> Dict[str, str]:
    """Retrieve descriptive metadata for a Digi-Key product number."""

    if not digikey_product_number:
        return {
            "name": "",
            "description": "",
            "url": "",
            "product_code": "",
        }

    url = _PART_DETAILS_URL_TEMPLATE.format(part_number=digikey_product_number)
    payload = _perform_get(url)

    product_description = payload.get("ProductDescription")
    detailed_description = payload.get("DetailedDescription")
    product_url = payload.get("ProductUrl")

    digi_part = payload.get("DigiKeyProductNumber") or digikey_product_number
    manufacturer_part = payload.get("ManufacturerProductNumber")

    product_code_parts = []
    if isinstance(digi_part, str) and digi_part.strip():
        product_code_parts.append(digi_part.strip())
    if isinstance(manufacturer_part, str) and manufacturer_part.strip():
        product_code_parts.append(manufacturer_part.strip())

    product_code = ";".join(product_code_parts)

    return {
        "name": product_description.strip() if isinstance(product_description, str) else "",
        "description": detailed_description.strip() if isinstance(detailed_description, str) else "",
        "url": product_url.strip() if isinstance(product_url, str) else "",
        "product_code": product_code,
    }

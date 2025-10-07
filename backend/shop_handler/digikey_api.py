from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

import requests

from sqlalchemy import text

# When this module is executed as a standalone script we need to ensure the
# repository root is available on sys.path so that the "backend" package can
# be imported successfully.  The production server configures PYTHONPATH for
# us, but a direct command-line invocation does not.
def _ensure_repo_root_on_path() -> None:
    """Add the repository root directory to sys.path when missing."""
    script_path = Path(__file__).resolve()

    repo_root = None
    for candidate in script_path.parents:
        potential_backend = candidate / "backend"
        if potential_backend.exists():
            repo_root = candidate
            break

    if repo_root is None:
        repo_root = script_path.parent

    resolved_root = str(repo_root)
    if resolved_root not in sys.path:
        sys.path.insert(0, resolved_root)

_ensure_repo_root_on_path()

from backend.app import config_loader
from backend.app.db import session_scope

__all__ = [
    "get_all_items",
    "get_item_details",
    "has_seen_digikey_invoice",
    "set_digikey_invoice_seen",
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


def _split_category_path(raw_value: Any) -> List[str]:
    """Return a list of category name segments extracted from diverse inputs."""

    if isinstance(raw_value, str):
        cleaned = raw_value.strip()
        if not cleaned:
            return []

        # Digi-Key payloads are not entirely consistent about delimiters.  The
        # expression below accepts characters that commonly appear in the
        # hierarchy strings and then trims any resulting whitespace.
        segments = [segment.strip() for segment in re.split(r"\s*[>/\\|]+\s*", cleaned) if segment.strip()]
        return segments

    if isinstance(raw_value, dict):
        # Attempt to locate a pre-built breadcrumb string before recursively
        # descending into parent containers.
        breadcrumb_keys = (
            "CategoryPath",
            "CategoryPathName",
            "CategoryBreadcrumb",
            "Breadcrumb",
            "Breadcrumbs",
        )
        for key in breadcrumb_keys:
            if key in raw_value:
                segments = _split_category_path(raw_value.get(key))
                if segments:
                    return segments

        # Some responses expose the hierarchy as a list of ancestor entries.
        ancestor_keys = ("Ancestors", "Parents", "ParentCategories")
        ancestor_segments: List[str] = []
        for key in ancestor_keys:
            raw_ancestors = raw_value.get(key)
            if isinstance(raw_ancestors, list):
                for ancestor in raw_ancestors:
                    segments = _split_category_path(ancestor)
                    if segments:
                        ancestor_segments.extend(segments)
                if ancestor_segments:
                    break

        # Finally, capture the local category name to append to the ancestor
        # chain.  This is intentionally verbose to keep the decision making
        # obvious and easy to adjust when Digi-Key changes their payloads.
        name_keys = ("CategoryName", "Category", "Name", "Description")
        local_name: Optional[str] = None
        for key in name_keys:
            candidate = raw_value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                local_name = candidate.strip()
                break

        combined_segments = ancestor_segments[:]
        if local_name:
            combined_segments.append(local_name)

        return combined_segments

    if isinstance(raw_value, (list, tuple, set)):
        for entry in raw_value:
            segments = _split_category_path(entry)
            if segments:
                return segments

    return []


def _extract_category_path(payload: Dict[str, Any]) -> Optional[str]:
    """Compile a human-readable product category path when data is available."""

    candidate_keys = (
        "ProductCategories",
        "DefaultCategory",
        "PrimaryCategory",
        "Categories",
        "Category",
        "ProductCategory",
    )

    for key in candidate_keys:
        if key not in payload:
            continue
        segments = _split_category_path(payload.get(key))
        if segments:
            # Remove duplicates while preserving order so the breadcrumb remains
            # meaningful even if Digi-Key repeats ancestor names.
            seen: set[str] = set()
            ordered: List[str] = []
            for segment in segments:
                if segment not in seen:
                    seen.add(segment)
                    ordered.append(segment)
            if ordered:
                return " > ".join(ordered)

    return None


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

    def _clean_image_url(candidate: Any) -> Optional[str]:
        """Return a normalised URL string when the candidate looks valid."""

        if isinstance(candidate, str):
            stripped = candidate.strip()
            return stripped or None
        return None

    def _extract_image_url(source: Any) -> Optional[str]:
        """Walk the Digi-Key payload and return the first plausible image URL."""

        prioritized_keys = (
            "Url",
            "URL",
            "ImageUrl",
            "ImageURL",
            "LargeImageUrl",
            "MediumImageUrl",
            "SmallImageUrl",
            "PrimaryImageUrl",
            "PrimaryImageURL",
            "NormalizedUrl",
            "PhotoUrl",
        )

        if isinstance(source, dict):
            for key in prioritized_keys:
                if key in source:
                    cleaned = _clean_image_url(source.get(key))
                    if cleaned:
                        return cleaned
            for value in source.values():
                nested = _extract_image_url(value)
                if nested:
                    return nested
            return None

        if isinstance(source, (list, tuple, set)):
            for entry in source:
                nested = _extract_image_url(entry)
                if nested:
                    return nested
            return None

        return _clean_image_url(source)

    potential_sources: List[Any] = []
    for key in ("PrimaryPhoto", "PrimaryImage", "PrimaryProductImage"):
        value = payload.get(key)
        if value is not None:
            potential_sources.append(value)

    for key in ("ProductImages", "Media", "Images", "AlternatePhotos"):
        value = payload.get(key)
        if value is not None:
            potential_sources.append(value)

    image_url: Optional[str] = None
    for source in potential_sources:
        image_url = _extract_image_url(source)
        if image_url:
            break

    result: Dict[str, str] = {
        "name": product_description.strip() if isinstance(product_description, str) else "",
        "description": detailed_description.strip() if isinstance(detailed_description, str) else "",
        "url": product_url.strip() if isinstance(product_url, str) else "",
        "product_code": product_code,
    }

    if image_url:
        result["img_url"] = image_url

    category_path = _extract_category_path(payload)
    if category_path:
        # Present the category on a new line so it reads naturally underneath
        # the detailed description without losing the original content.
        descriptor = f"Product Category: {category_path}"
        if result["description"]:
            result["description"] = f"{result['description']}\r\n{descriptor}"
        else:
            result["description"] = descriptor

    return result


def _encode_salesorder(salesorder: int) -> bytes:
    """Convert a sales order identifier into the stored byte representation."""

    if not isinstance(salesorder, int):
        raise ValueError("salesorder must be provided as an integer value")

    # The digikey_seen table stores the identifier as a BYTEA column containing the ASCII digits.
    digits = str(salesorder).strip()
    if not digits:
        raise ValueError("salesorder cannot be an empty value")

    return digits.encode("utf-8")


def _coerce_invoice_uuid(invoice_id: Optional[Union[UUID, str]]) -> Optional[UUID]:
    """Normalise optional invoice identifiers into UUID objects or None."""

    if invoice_id is None:
        return None

    if isinstance(invoice_id, UUID):
        return invoice_id

    candidate = str(invoice_id).strip()
    if not candidate:
        return None

    try:
        return UUID(candidate)
    except Exception as exc:  # pragma: no cover - defensive validation
        raise ValueError(f"invoice_id {invoice_id!r} is not a valid UUID") from exc

# ---------------------------------------------------------------------------
# Command-line helper utilities
# ---------------------------------------------------------------------------


def _non_empty_string(value: str) -> str:
    """Return a stripped string and raise an argparse error when empty."""
    cleaned_value = value.strip()
    if not cleaned_value:
        raise argparse.ArgumentTypeError(
            "The provided value cannot be empty or whitespace."
        )
    return cleaned_value


def _parse_cli_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Build the command-line parser for the local Digi-Key API smoke tests."""
    parser = argparse.ArgumentParser(
        description=(
            "Query the Digi-Key API using the existing helper functions. "
            "Provide either a sales order number to list all products or a "
            "part number to inspect the metadata returned by Digi-Key."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--sales-order",
        dest="sales_order",
        metavar="ORDER_ID",
        type=_non_empty_string,
        help="Fetch every product number contained in the specified Digi-Key sales order.",
    )
    group.add_argument(
        "--part-number",
        dest="part_number",
        metavar="PART_NUMBER",
        type=_non_empty_string,
        help="Fetch descriptive metadata for the provided Digi-Key product number.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point used when exercising this module directly from the command line."""
    args = _parse_cli_arguments(argv)

    try:
        if args.sales_order is not None:
            items = get_all_items(args.sales_order)
            print(
                f"Sales order {args.sales_order} contains {len(items)} unique Digi-Key product number(s):"
            )
            for product_number in items:
                print(f"- {product_number}")
        else:
            details = get_item_details(args.part_number)
            print(f"Details for Digi-Key product '{args.part_number}':")
            print(json.dumps(details, indent=2, sort_keys=True))
    except DigiKeyConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except DigiKeyAPIError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive CLI execution path
        log.exception("Unexpected failure while executing the Digi-Key CLI helper.")
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

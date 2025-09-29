"""Utilities for retrieving web content using either Playwright or the requests library."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from typing import Tuple
from urllib.parse import urlsplit

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def fetch_with_playwright(url: str, loop_count: int = 0, loop_timeout_ms: int = 1000) -> Tuple[str, str, str]:
    """Retrieve a web page using Playwright with optional scrolling loops."""
    # Use Playwright to launch a Chromium instance in headless mode so that we can render the page.
    # The context manager ensures that the browser is torn down cleanly even if an error is raised.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        # A single new page is sufficient for this utility function; it will follow redirects automatically.
        page = browser.new_page()
        try:
            # Navigate to the requested URL and wait for the initial DOM content to be ready.
            page.goto(url, wait_until="domcontentloaded")
            # The caller can request additional passes that progressively scroll and wait for extra network quietness.
            for _ in range(max(loop_count, 0)):
                try:
                    # Scroll to the bottom of the page so that lazy-loaded content has a chance to appear.
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                except PlaywrightTimeoutError:
                    # Scrolling should normally succeed immediately, but if it does not we simply continue.
                    pass
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=max(loop_timeout_ms, 0))
                except PlaywrightTimeoutError:
                    # The load state might already be satisfied; in that case we ignore the timeout.
                    pass
                try:
                    page.wait_for_load_state("networkidle", timeout=max(loop_timeout_ms, 0))
                except PlaywrightTimeoutError:
                    # Some pages never reach a fully idle network state; do not treat this as fatal.
                    pass
            # Capture the rendered HTML and the human-readable text content.
            html_content = page.content()
            text_content = page.evaluate("document.body.innerText")
            current_url = page.url
            return html_content, text_content, current_url
        finally:
            # Explicitly close the browser so that resources are released promptly.
            browser.close()


class _TextExtractor(HTMLParser):
    """Lightweight HTML parser that collects the textual content of the <body> element."""

    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._chunks = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "body":
            self._capture = True

    def handle_endtag(self, tag):
        if tag.lower() == "body":
            self._capture = False

    def handle_data(self, data):
        if self._capture and data.strip():
            self._chunks.append(data.strip())

    def get_text(self) -> str:
        return "\n".join(self._chunks)


def fetch_with_requests(url: str, *, timeout: int = 30) -> Tuple[str, str, str]:
    """Retrieve a web page using the requests library, respecting redirects."""
    # Compute a referer header that corresponds to the domain of the requested URL.
    split_url = urlsplit(url)
    referer = f"{split_url.scheme}://{split_url.netloc}" if split_url.scheme and split_url.netloc else None
    headers = {
        # Present a user agent string that resembles a contemporary desktop Chrome browser.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    }
    if referer:
        headers["Referer"] = referer
    # Perform the GET request, allowing requests to handle redirect resolution automatically.
    response = requests.get(url, headers=headers, allow_redirects=True, timeout=timeout)
    response.raise_for_status()
    html_content = response.text
    parser = _TextExtractor()
    parser.feed(html_content)
    text_content = parser.get_text()
    current_url = response.url
    return html_content, text_content, current_url


def _inject_redirect_url(html_content: str, redirected_url: str) -> str:
    """Insert the resolved URL immediately after the first ``>`` character in the HTML source."""
    # When the HTML is empty or the resolved URL is unavailable, return the original content unchanged.
    if not html_content or not redirected_url:
        return html_content

    first_delimiter_index = html_content.find(">")
    # If a ``>`` symbol does not exist, append the URL at the end so that the caller still sees it.
    if first_delimiter_index == -1:
        return f"{html_content}\n<!-- Redirected URL: {redirected_url} -->"

    insertion = f"\n<!-- Redirected URL: {redirected_url} -->"
    return f"{html_content[: first_delimiter_index + 1]}{insertion}{html_content[first_delimiter_index + 1:]}"


def main() -> None:
    """Command line entry point that exercises the retrieval helpers using argparse."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a URL using either the requests library or Playwright and display the HTML "
            "with the resolved URL noted."
        )
    )
    parser.add_argument(
        "url",
        help=(
            "The HTTP or HTTPS URL that should be retrieved. Redirects are followed automatically and "
            "the final resolved URL is embedded in the displayed HTML."
        ),
    )
    parser.add_argument(
        "--method",
        choices=("requests", "playwright"),
        default="requests",
        help=(
            "Select which retrieval strategy to use. The default leverages the requests library; "
            "choose 'playwright' when a headless browser is required."
        ),
    )
    args = parser.parse_args()

    if args.method == "requests":
        html_content, _, final_url = fetch_with_requests(args.url)
    else:
        html_content, _, final_url = fetch_with_playwright(args.url)

    # Present the HTML content with a clear annotation that records the resolved URL.
    annotated_html = _inject_redirect_url(html_content, final_url)
    print(annotated_html)


if __name__ == "__main__":
    main()

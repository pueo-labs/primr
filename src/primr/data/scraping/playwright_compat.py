"""Compatibility policy for browser tiers that use Playwright's sync API."""

from __future__ import annotations

import os
import sys

from .models import Attempt, ErrorType, ScrapeResult

SYNC_BROWSER_UNAVAILABLE_REASON = (
    "Playwright and Patchright sync browser tiers require Python 3.11 through 3.15; "
    "Python 3.15 also requires greenlet 3.5.6 or newer. Update Primr's dependencies "
    "before using browser-backed collection."
)

PLAYWRIGHT_BROWSERS_MISSING = "Playwright browsers not installed. Run: playwright install chromium"


def sync_browser_runtime_supported(version: tuple[int, int] | None = None) -> bool:
    """Return whether Playwright's greenlet-backed sync API is safe to start."""

    runtime = version if version is not None else (sys.version_info.major, sys.version_info.minor)
    if not (3, 11) <= runtime <= (3, 15):
        return False
    if runtime < (3, 15):
        return True
    try:
        import greenlet

        return tuple(int(part) for part in greenlet.__version__.split(".")) >= (3, 5, 6)
    except (ImportError, ValueError):
        return False


def chromium_launch_status() -> str | None:
    """Return None when Chromium launches, otherwise one user-facing error.

    Doctor and preflight both use this sentence for a missing browser. The
    native driver can abort the process, so doctor runs the same function in a
    short-lived child and reads the exit status.
    """

    try:
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
        finally:
            playwright.stop()
    except Exception as exc:
        message = str(exc)
        lowered = message.lower()
        if "executable doesn't exist" in lowered or "playwright install" in lowered:
            return PLAYWRIGHT_BROWSERS_MISSING
        return f"Playwright check failed: {message}"
    return None


def chromium_readiness_probe() -> str:
    """Return source for a child that exits 0 when Chromium launches.

    Exit 2 means the browser bundle is missing. Any other nonzero status is a
    probe failure, including a native abort that never reaches this source.
    """

    return (
        "from primr.data.scraping.playwright_compat import chromium_launch_status\n"
        "status = chromium_launch_status()\n"
        "if status is None:\n"
        "    raise SystemExit(0)\n"
        "print(status)\n"
        "missing = status.startswith('Playwright browsers not installed')\n"
        "raise SystemExit(2 if missing else 1)\n"
    )


def resolve_browser_headless(headless: bool | None) -> bool:
    """Allow CLI or environment policy to force headed browser recovery."""

    if os.getenv("PRIMR_BROWSER_HEADED", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return True if headless is None else headless


def sync_browser_unavailable_result(url: str, tier: str) -> ScrapeResult:
    """Return a typed failure before an unsafe sync browser runtime can start."""

    return ScrapeResult(
        url=url,
        success=False,
        error_type=ErrorType.NETWORK_ERROR,
        error=SYNC_BROWSER_UNAVAILABLE_REASON,
        tier=tier,
        elapsed_ms=0,
        attempts=[
            Attempt(
                tier=tier,
                success=False,
                error=SYNC_BROWSER_UNAVAILABLE_REASON,
                error_type=ErrorType.NETWORK_ERROR,
                elapsed_ms=0,
            )
        ],
    )


__all__ = [
    "PLAYWRIGHT_BROWSERS_MISSING",
    "SYNC_BROWSER_UNAVAILABLE_REASON",
    "chromium_launch_status",
    "chromium_readiness_probe",
    "resolve_browser_headless",
    "sync_browser_runtime_supported",
    "sync_browser_unavailable_result",
]

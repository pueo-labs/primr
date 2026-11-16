"""Tests for sync browser compatibility boundaries."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from primr.data.scraping import stealth_browser
from primr.data.scraping.playwright_compat import (
    PLAYWRIGHT_BROWSERS_MISSING,
    SYNC_BROWSER_UNAVAILABLE_REASON,
    chromium_launch_status,
    chromium_readiness_probe,
    sync_browser_runtime_supported,
)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ((3, 10), False),
        ((3, 11), True),
        ((3, 14), True),
        ((3, 15), True),
        ((3, 16), False),
    ],
)
def test_sync_browser_runtime_boundary(version, expected, monkeypatch):
    monkeypatch.setitem(sys.modules, "greenlet", SimpleNamespace(__version__="3.5.6"))
    assert sync_browser_runtime_supported(version) is expected


@pytest.mark.parametrize("installed", ["3.5.1", "3.5.5", "3.5.6rc1"])
def test_python_315_rejects_unverified_greenlet_builds(installed, monkeypatch):
    monkeypatch.setitem(sys.modules, "greenlet", SimpleNamespace(__version__=installed))
    assert not sync_browser_runtime_supported((3, 15))


def test_missing_browser_uses_one_sentence():
    playwright = MagicMock()
    playwright.chromium.launch.side_effect = RuntimeError(
        "BrowserType.launch: Executable doesn't exist at /tmp/chrome"
    )
    sync_playwright = MagicMock(return_value=playwright)
    sync_playwright.return_value.start.return_value = playwright

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setitem(
            sys.modules,
            "playwright.sync_api",
            SimpleNamespace(sync_playwright=sync_playwright),
        )
        assert chromium_launch_status() == PLAYWRIGHT_BROWSERS_MISSING

    assert "playwright install chromium" in PLAYWRIGHT_BROWSERS_MISSING
    probe = chromium_readiness_probe()
    assert "chromium_launch_status" in probe
    assert "SystemExit(2 if missing else 1)" in probe


def test_chromium_launch_reports_other_failures():
    playwright = MagicMock()
    playwright.chromium.launch.side_effect = RuntimeError("driver closed")
    sync_playwright = MagicMock()
    sync_playwright.return_value.start.return_value = playwright

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setitem(
            sys.modules,
            "playwright.sync_api",
            SimpleNamespace(sync_playwright=sync_playwright),
        )
        status = chromium_launch_status()

    assert status == "Playwright check failed: driver closed"


def test_unavailable_reason_is_actionable():
    assert "Python 3.15" in SYNC_BROWSER_UNAVAILABLE_REASON
    assert "Playwright" in SYNC_BROWSER_UNAVAILABLE_REASON
    assert "greenlet 3.5.6" in SYNC_BROWSER_UNAVAILABLE_REASON


def test_patchright_fails_closed_before_dependency_probe(monkeypatch):
    monkeypatch.setattr(stealth_browser, "sync_browser_runtime_supported", lambda: False)
    dependency_probe = MagicMock()
    monkeypatch.setattr(stealth_browser, "_patchright_available", dependency_probe)

    result = stealth_browser.scrape_with_patchright("https://example.com")

    assert result.success is False
    assert result.tier == "patchright"
    assert result.error == SYNC_BROWSER_UNAVAILABLE_REASON
    assert len(result.attempts) == 1
    dependency_probe.assert_not_called()

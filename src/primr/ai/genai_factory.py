"""Default HTTP options for google-genai clients.

The SDK ships with no default HTTP timeout. A live run hung for 3.5 hours
in ``ssl.read`` when the Gemini endpoint went quiet mid-response. The stack
was ``genai._api_client._request`` to the HTTP client to ``ssl.read``, with
no deadline. Every ``genai.Client(...)`` construction passes
``http_options=default_genai_http_options()`` so each request has a finite
timeout (default 5 minutes). That is long enough for a legitimate
``generate_content`` call and short enough for the provider retry loops to
regain control. Override it with ``PRIMR_GEMINI_HTTP_TIMEOUT_MS``.

SDK 3 defaults the Gemini Developer API to ``v1``. Deep research calls the
Agents API, which is still ``v1beta`` only, so these options also set
``api_version`` to ``v1beta``. The timeout stays an integer millisecond
count. Primr does not pass HTTP-client objects into ``HttpOptions``.

Call sites keep constructing through their own module's ``genai.Client``
reference so the existing test seam, patching ``<module>.genai.Client``,
keeps working. This module only supplies the options object.
``google.genai`` is imported lazily so importing this module never pulls
the SDK at startup.
"""

from __future__ import annotations

import os
from typing import Any

from primr.utils.logging_config import get_logger

logger = get_logger("ai.genai_factory")

# 5 minutes per HTTP request. The SDK value is in milliseconds.
DEFAULT_GENAI_HTTP_TIMEOUT_MS = 300_000

# SDK 3 defaults the Developer API to v1. The Agents API used by deep
# research is v1beta-only, so the shared client options keep that route.
GENAI_API_VERSION = "v1beta"


def accepts_sampling_parameters(model: str) -> bool:
    """Whether ``model`` still accepts explicit sampling controls.

    Google removed ``temperature``, ``top_p``, and ``top_k`` beginning with
    Gemini 3.6 Flash and Gemini 3.5 Flash-Lite. Gemini 3.7 and later reject
    them. Older production models retain their existing request shape.
    """
    normalized = model.lower()
    unsupported_prefixes = (
        "gemini-4-",
        "gemini-nano-banana-",
        "gemini-3.8-",
        "gemini-3.5-flash-lite",
        "gemini-3.6-",
        "gemini-3.7-",
    )
    return not normalized.startswith(unsupported_prefixes)


def supported_thinking_levels(model: str) -> tuple[str, ...]:
    """Return the published thinking-level contract for a Gemini model."""
    normalized = model.lower()
    if normalized.startswith("gemini-nano-banana-2.1"):
        return ("minimal", "medium", "high")
    all_levels = ("minimal", "low", "medium", "high")
    if normalized.startswith(
        (
            "gemini-3.8-",
            "gemini-3.6-",
            "gemini-3.5-",
            "gemini-3.1-flash-lite",
            "gemini-3-flash",
        )
    ):
        return all_levels
    if normalized.startswith("gemini-3-pro"):
        return ("low", "high")
    if normalized.startswith(("gemini-4-", "gemini-3.7-", "gemini-3.1-pro", "gemini-2.5-")):
        return ("low", "medium", "high")
    return ("low", "high")


def get_genai_http_timeout_ms() -> int:
    """Resolve the genai HTTP timeout (ms), env-overridable, always finite."""
    raw = os.environ.get("PRIMR_GEMINI_HTTP_TIMEOUT_MS", "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
            logger.warning(
                "PRIMR_GEMINI_HTTP_TIMEOUT_MS must be positive, got %r — using default %d",
                raw,
                DEFAULT_GENAI_HTTP_TIMEOUT_MS,
            )
        except ValueError:
            logger.warning(
                "PRIMR_GEMINI_HTTP_TIMEOUT_MS is not an integer: %r — using default %d",
                raw,
                DEFAULT_GENAI_HTTP_TIMEOUT_MS,
            )
    return DEFAULT_GENAI_HTTP_TIMEOUT_MS


def default_genai_http_options() -> Any:
    """HttpOptions with a finite timeout and the v1beta Agents API route."""
    from google.genai import types as genai_types

    return genai_types.HttpOptions(
        timeout=get_genai_http_timeout_ms(),
        api_version=GENAI_API_VERSION,
    )

"""Hash-verified pip bootstrap for the source-checkout setup wizard.

The application certificate bundle and pip's vendored trust store are separate.
Source: https://discuss.python.org/t/109576
"""

from __future__ import annotations

import re
import subprocess
import sys
from importlib import metadata

# Official Python incident notice: hash verification also protects bootstrap
# downloads made by an older pip with an affected vendored CA bundle.
PATCHED_PIP_URL = (
    "https://files.pythonhosted.org/packages/07/25/"
    "ee8c38ae72d66b844ce8eb9711ba905dba21ca1a8a0c960057c41cd23337/"
    "pip-26.3-py3-none-any.whl"
    "#sha256=f3b60b20ffaadd4415ed7957212a38ac63fa4c5fa375bd0f4a797777940fad13"
)


def ensure_patched_installer() -> None:
    """Repair pip before any index download; failure stops the setup flow."""
    try:
        version = metadata.version("pip")
    except metadata.PackageNotFoundError:
        version = "0"
    parsed = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", version)
    if parsed and tuple(int(part or 0) for part in parsed.groups()) >= (26, 2, 2):
        return
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--no-cache-dir", PATCHED_PIP_URL],
        check=True,
    )

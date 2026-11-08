"""
Documentation links, status headers, and project-neutral wording.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_documentation_is_consistent():
    result = subprocess.run(
        [sys.executable, "tools/check_docs.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "docs: OK" in result.stdout

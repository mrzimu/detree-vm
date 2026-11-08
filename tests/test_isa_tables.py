"""
The opcode table and its Markdown mirror are consistent.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_isa_tables_are_consistent():
    result = subprocess.run(
        [sys.executable, "tools/gen_isa.py", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "isa: OK" in result.stdout

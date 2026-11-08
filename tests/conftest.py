"""
Test configuration: run against the source tree without an install.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(params=["python", "cpp", "rust"])
def machine_factory(request):
    """
    Build a machine from module bytes, once per available backend.

    A compiled backend is skipped when its library is not present; an installed
    library with the wrong identity or ABI is an error, not a skip.
    """
    name = request.param
    if name == "python":
        from detree.vm import PythonMachine

        return PythonMachine

    from detree.abi import capi
    from detree.spec.errors import BackendUnavailable

    try:
        library = capi.load(name)
    except BackendUnavailable as error:
        pytest.skip(f"{name} is not available: {error}")
    return library.machine

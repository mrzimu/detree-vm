"""
The package imports cleanly and the core layers stay dependency-light.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ("detree.spec", "detree.ir", "detree.opt", "detree.lower", "detree.vm")
FORBIDDEN = ("numpy", "uproot", "awkward")


def test_package_imports():
    import detree

    assert isinstance(detree.__version__, str)
    assert detree.__version__
    assert isinstance(detree.PUBLIC_API, tuple)
    assert {"Machine", "compile", "include_dir"} <= set(detree.PUBLIC_API)
    assert "Program" not in detree.PUBLIC_API


def test_core_layers_do_not_import_arrays_or_hosts():
    script = (
        "import sys\n"
        "import " + ", ".join(CORE) + "\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in " + repr(FORBIDDEN) + ")\n"
        "print('\\n'.join(bad))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.strip() == "", result.stdout


def test_backends_module_imports():
    import detree.backends

    rows = detree.backends.available()
    names = {name for name, _, _ in rows}
    assert {"python", "cpp", "rust"} <= names

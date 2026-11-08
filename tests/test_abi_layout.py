"""
The ctypes structures must match the C header byte for byte.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
from pathlib import Path

import pytest

from detree.abi import capi

ROOT = Path(__file__).resolve().parent.parent

STRUCTS = {
    "detree_error": (
        capi._DetreeError,
        [
            "status",
            "reserved",
            "ip",
            "cursor",
            "message",
            "message_length",
            "function",
            "function_length",
        ],
    ),
    "detree_output": (
        capi._DetreeOutput,
        ["data", "length", "itemsize", "owner"],
    ),
    "detree_state": (
        capi._DetreeState,
        [
            "name",
            "name_length",
            "is_name",
            "reserved",
            "value",
            "text",
            "text_length",
        ],
    ),
}


def parse(lines):
    """
    Turn the C probe's `name sizeof=N field=O ...` lines into dictionaries.
    """
    rows = {}
    for line in lines.splitlines():
        parts = line.split()
        if not parts:
            continue
        values = {}
        for item in parts[1:]:
            key, _, value = item.partition("=")
            values[key] = int(value)
        rows[parts[0]] = values
    return rows


def test_c_structs_match_ctypes(tmp_path):
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("no C compiler is available")

    executable = tmp_path / "abi_layout"
    subprocess.run(
        [
            compiler,
            "-std=c99",
            "-I",
            str(ROOT / "kernel" / "include"),
            str(ROOT / "tools" / "abi_layout.c"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    probe = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
    rows = parse(probe.stdout)

    for name, (structure, fields) in STRUCTS.items():
        assert rows[name]["sizeof"] == ctypes.sizeof(structure), name
        for field in fields:
            assert rows[name][field] == getattr(structure, field).offset, (
                name,
                field,
            )

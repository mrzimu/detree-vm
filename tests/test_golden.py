"""
Replay the golden corpus through every available backend.

The corpus is written by `tools/golden.py` from the Python reference; this
test only reads it. A stale corpus fails `test_corpus_is_fresh` before any
backend is compared.
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import pytest

from detree.abi.capi import STATUS_TO_EXCEPTION

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "data"


class _Cursor:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.pos = 0

    def take(self, size: int) -> bytes:
        chunk = self.raw[self.pos : self.pos + size]
        assert len(chunk) == size
        self.pos += size
        return chunk

    def unpack(self, fmt: str):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))

    def text(self) -> str:
        (size,) = self.unpack("<H")
        return self.take(size).decode("utf-8")


def load(path: Path) -> dict:
    cursor = _Cursor(path.read_bytes())
    assert cursor.take(4) == b"DTGL"
    (version,) = cursor.unpack("<B")
    assert version == 1

    (module_len,) = cursor.unpack("<I")
    module = cursor.take(module_len)
    (data_len,) = cursor.unpack("<I")
    data = cursor.take(data_len)
    (n_offsets,) = cursor.unpack("<I")
    offsets = tuple(cursor.unpack(f"<{n_offsets}q")) if n_offsets else ()
    (cursor_offset,) = cursor.unpack("<Q")
    status, _reserved, ip = cursor.unpack("<B3sq")

    (n_buffers,) = cursor.unpack("<I")
    buffers = []
    for _ in range(n_buffers):
        name = cursor.text()
        dtype = cursor.text()
        count, size = cursor.unpack("<QQ")
        buffers.append((name, dtype, count, cursor.take(size)))

    (n_states,) = cursor.unpack("<I")
    states = []
    for _ in range(n_states):
        name = cursor.text()
        is_name, value = cursor.unpack("<BQ")
        states.append((name, bool(is_name), value, cursor.text()))

    assert cursor.pos == len(cursor.raw)
    return {
        "module": module,
        "data": data,
        "offsets": offsets,
        "cursor_offset": cursor_offset,
        "status": status,
        "ip": ip,
        "buffers": buffers,
        "states": states,
    }


CASES = sorted(CORPUS.glob("*.dtg"))


def test_corpus_is_fresh():
    result = subprocess.run(
        [sys.executable, "tools/golden.py", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("path", CASES, ids=lambda path: path.stem)
def test_backend_replays_case(machine_factory, path):
    case = load(path)
    machine = machine_factory(case["module"])

    exception = STATUS_TO_EXCEPTION[case["status"]]
    if exception is None:
        outputs = machine.run(case["data"], case["offsets"], case["cursor_offset"])
    else:
        with pytest.raises(exception) as info:
            machine.run(case["data"], case["offsets"], case["cursor_offset"])
        if case["ip"] >= 0:
            assert info.value.ip == case["ip"]
        outputs = {}

    for name, dtype, count, payload in case["buffers"]:
        array = outputs[name]
        assert array.dtype.name == dtype
        assert array.size == count
        assert array.tobytes() == payload

    values = machine.state_values()
    for name, is_name, raw, text in case["states"]:
        if is_name:
            assert values[name] == (text if text else None)
        else:
            assert values[name] == raw

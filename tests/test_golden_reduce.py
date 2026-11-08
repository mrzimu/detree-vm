"""
The corpus loader and the divergence reducer in `tools/golden.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from module_builder import Builder, ins

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "data"

sys.path.insert(0, str(ROOT / "tools"))

import golden  # noqa: E402

from detree.spec.isa import Op, Typecode  # noqa: E402


def read(name: str) -> golden.RecordedCase:
    return golden.read_case((CORPUS / f"{name}.dtg").read_bytes(), name=name)


def test_read_case_round_trips_a_corpus_file():
    raw = (CORPUS / "bulk.dtg").read_bytes()
    case = golden.read_case(raw, name="bulk")

    assert case.offsets
    assert case.data
    # `encode` reruns the reference machine, so an identical file proves the
    # loader recovered every input the corpus was written from.
    assert golden.encode(case) == raw


def test_read_case_rejects_a_foreign_file():
    with pytest.raises(ValueError, match="bad magic"):
        golden.read_case(b"NOPE" + b"\x00" * 32)


def two_function_module():
    builder = Builder()
    out = builder.buffer("out", "int32")
    builder.function(
        "read",
        [
            ins(Op.SREAD, rd=0, imm1=Typecode.INT32),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    builder.function("unused", [ins(Op.RET, ra=0)])
    return builder.build()


def test_unreachable_functions_are_pruned():
    module = two_function_module()
    assert golden.reachable_functions(module) == {0}

    pruned = golden.prune_functions(module)
    assert pruned is not None
    assert [func.name for func in pruned.funcs] == ["read"]
    assert pruned.entry == 0
    assert pruned.code == module.code[: module.funcs[0].code_len]


def test_a_module_with_no_dead_functions_is_unchanged():
    module = two_function_module()
    one = golden.Module(
        entry=module.entry,
        regs_max=module.regs_max,
        buffers=module.buffers,
        states=module.states,
        strings=module.strings,
        layouts=module.layouts,
        funcs=module.funcs[:1],
        code=module.code[: module.funcs[0].code_len],
        isa_major=module.isa_major,
        isa_minor=module.isa_minor,
        magic=module.magic,
    )
    assert golden.prune_functions(one) is None


def test_reduce_refuses_a_case_that_does_not_diverge():
    case = read("bulk")
    with pytest.raises(SystemExit, match="nothing to reduce"):
        golden.reduce_case(case, "python")

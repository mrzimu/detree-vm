"""
Mutated module bytes must fail cleanly, never crash the process.

A module is arbitrary input in practice: a compiler produces it, a cache may
have written it, a file may have been truncated. Conformance §4 makes a
process crash a conformance failure, so this test feeds truncations and random
byte flips to the decoder and to every built machine and accepts only the
error taxonomy as an outcome.
"""

from __future__ import annotations

import random

import pytest
from module_builder import Builder, ins

from detree.abi import capi
from detree.abi.libs import library_path
from detree.spec.errors import DetreeError
from detree.spec.isa import Op, Typecode
from detree.spec.module import Module

# Enough cases to reach the count, operand, and jump decoders, few enough to
# stay in the ordinary test budget.
MUTATIONS = 400


def valid_module() -> bytes:
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
    return builder.build().to_bytes()


def mutations(raw: bytes, rng: random.Random, count: int):
    """
    Yield truncations and byte-flipped copies of *raw*.
    """
    for _ in range(count):
        blob = bytearray(raw)
        for _ in range(rng.randrange(1, 5)):
            blob[rng.randrange(len(blob))] = rng.randrange(256)
        if rng.random() < 0.2:
            blob = blob[: rng.randrange(len(blob) + 1)]
        yield bytes(blob)


def test_the_decoder_survives_mutated_modules():
    raw = valid_module()
    rng = random.Random(0xD37EE)
    decoded = 0
    for blob in mutations(raw, rng, MUTATIONS):
        try:
            Module.from_bytes(blob)
        except DetreeError:
            continue
        decoded += 1
    assert decoded < MUTATIONS  # the fuzzer does reach the rejection paths


@pytest.mark.parametrize("name", ["python", "cpp", "rust"])
def test_every_machine_survives_mutated_modules(name):
    if name != "python" and library_path(name) is None:
        pytest.skip(f"{name} is not built")

    raw = valid_module()
    rng = random.Random(0x5EED + len(name))
    for blob in mutations(raw, rng, MUTATIONS // 2):
        try:
            if name == "python":
                from detree.vm import PythonMachine

                PythonMachine(blob)
            else:
                capi.build(blob, name)
        except DetreeError:
            continue

"""
Randomized differential testing of the compiled machines.

The generator stays inside the validated IR subset and never divides by zero,
so a mismatch is a real semantic divergence rather than an expected error.
"""

from __future__ import annotations

import random

import pytest

import detree
from detree.backends import registry
from detree.ir import Binary, Const, Local, ProgramBuilder, Unary
from detree.spec.errors import BackendUnavailable
from detree.spec.isa import Op

BINARY_OPS = (
    Op.ADD,
    Op.SUB,
    Op.MUL,
    Op.DIV_S,
    Op.DIV_U,
    Op.MOD_S,
    Op.MOD_U,
    Op.SHL,
    Op.SHR_S,
    Op.SHR_U,
    Op.AND,
    Op.OR,
    Op.XOR,
)
UNARY_OPS = (Op.NEG, Op.NOT, Op.NOT_B)
DIVISORS = (1, 2, 3, 5, 7, 1024)


def random_expr(rng, depth=3):
    if depth == 0 or rng.random() < 0.25:
        return Const(rng.randint(-(2**31), 2**31))
    if rng.random() < 0.2:
        return Unary(rng.choice(UNARY_OPS), random_expr(rng, depth - 1))
    op = rng.choice(BINARY_OPS)
    left = random_expr(rng, depth - 1)
    if op in (Op.DIV_S, Op.DIV_U, Op.MOD_S, Op.MOD_U):
        right = Const(rng.choice(DIVISORS))
    else:
        right = random_expr(rng, depth - 1)
    return Binary(op, left, right)


def build_program(seed: int):
    rng = random.Random(seed)
    builder = ProgramBuilder()
    out = builder.buffer("out", "int64")
    func = builder.function("read")
    for _ in range(12):
        func.assign(func.local("x"), random_expr(rng))
        func.append(out, Local("x"))
    func.ret()
    return builder.build()


def available_machines():
    machines = ["python"]
    for name in ("cpp", "rust"):
        try:
            registry.get(name)
        except BackendUnavailable:
            continue
        machines.append(name)
    return machines


@pytest.mark.parametrize("seed", range(20))
def test_random_arithmetic_matches_the_reference(seed):
    from detree.lower import lower

    program = build_program(seed)
    # Half the seeds exercise the raw machine; half go through the optimizer so
    # both the arithmetic and the folded constants are compared.
    raw = detree.compile(program) if seed % 2 else lower(program)
    machines = available_machines()
    reference = None
    for name in machines:
        with detree.Machine(raw, backend=name) as machine:
            outputs = machine.run(b"", [0, 0])["out"].tolist()
        if reference is None:
            reference = outputs
        else:
            assert outputs == reference, f"{name} diverged on seed {seed}"

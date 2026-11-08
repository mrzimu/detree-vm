"""
Instruction emission.

Resolves the labels of a `linearize.LinearFunction` and emits the
20-byte canonical instruction form from `docs/spec/isa.md`.
"""

from __future__ import annotations

from dataclasses import replace

from ..ir.errors import IRValidationError
from ..spec.isa import JUMP_OPS, Op
from ..spec.module import Ins
from .linearize import Label, LinearFunction, PIns


def encode_function(linear: LinearFunction) -> list[Ins]:
    """
    Resolve labels and return the function's `Ins` records.

    Args:
        linear: the linearized function, with labels still symbolic.

    Returns:
        One `Ins` per pseudo-instruction, in emission order.

    Raises:
        IRValidationError: a label is missing or resolves past the function.
    """
    positions = {}
    index = 0
    for item in linear.code:
        if isinstance(item, Label):
            positions[item.id] = index
        else:
            index += 1

    instructions = []
    for item in linear.code:
        if isinstance(item, Label):
            continue
        if not isinstance(item, PIns):
            raise IRValidationError(f"{linear.name}: unknown pseudo-instruction")
        imm1 = item.imm1
        if item.label is not None:
            if item.label not in positions:
                raise IRValidationError(f"{linear.name}: unknown label {item.label}")
            imm1 = positions[item.label]
            if not 0 <= imm1 < index:
                raise IRValidationError(
                    f"{linear.name}: label {item.label} resolves past the function"
                )
        instructions.append(Ins(item.op, item.rd, item.ra, item.rb, imm1, item.imm2))
    return _thread_jumps(instructions)


def _thread_jumps(instructions: list[Ins]) -> list[Ins]:
    """
    A jump to a jump becomes a jump to the final target.

    Threading happens after label resolution, so a chain of empty blocks
    collapses to one branch without another linearization pass.
    """
    threaded = list(instructions)
    for index, ins in enumerate(threaded):
        if ins.op not in JUMP_OPS:
            continue
        target = ins.imm1
        seen = set()
        while 0 <= target < len(threaded) and threaded[target].op == Op.JMP:
            if target in seen:
                break
            seen.add(target)
            target = threaded[target].imm1
        if target != ins.imm1 and 0 <= target < len(threaded):
            threaded[index] = replace(ins, imm1=target)
    return threaded


__all__ = ["encode_function"]

"""
IR to module bytes.

The pipeline is defined in `docs/spec/ir.md` §Lowering: verify, desugar,
linearize, allocate registers, emit instructions, and validate the result. It is
deterministic: declaration order is the only source of ids.
"""

from __future__ import annotations

from ..ir import nodes
from ..ir.errors import IRValidationError
from ..ir.verify import verify_program
from ..spec.errors import DetreeError
from ..spec.module import (
    BufferDecl,
    FuncDecl,
    Layout,
    LayoutField,
    Module,
    StateDecl,
)
from ..spec.validate import validate_module
from .codegen import encode_function
from .linearize import linearize_function
from .regalloc import finalize_registers


class StringArena:
    """
    The module's string table, seeded with the IR's strings and constants.

    Ids follow first-use order, which keeps lowering deterministic.
    """

    def __init__(self, program: nodes.Program) -> None:
        self.values = []
        self.index = {}
        self.constants = [constant.text for constant in program.constants]
        for text in program.strings:
            self.intern(text)
        for text in self.constants:
            self.intern(text)

    def intern(self, text: str) -> int:
        """
        Return the id of *text*, adding it when it is new.

        Args:
            text: the string to intern.

        Returns:
            The arena id.
        """
        try:
            return self.index[text]
        except KeyError:
            ident = len(self.values)
            self.values.append(text)
            self.index[text] = ident
            return ident

    def constant(self, index: int) -> int:
        """
        The string id a `Constant` node refers to.

        Args:
            index: the constant id from the IR.

        Returns:
            The arena id of that constant's text.

        Raises:
            IRValidationError: the constant does not exist.
        """
        try:
            return self.index[self.constants[index]]
        except (IndexError, KeyError):
            raise IRValidationError(f"constant {index} does not exist") from None

    def message(self, msg: int | str | nodes.Constant) -> int:
        """
        The string id of an assert or throw message.

        Args:
            msg: an arena id, a `Constant` node, or literal text.

        Returns:
            The arena id of the message.

        Raises:
            IRValidationError: *msg* is none of the accepted forms.
        """
        if isinstance(msg, int):
            if not 0 <= msg < len(self.values):
                raise IRValidationError(f"message string {msg} does not exist")
            return msg
        if isinstance(msg, nodes.Constant):
            return self.intern(msg.text)
        if isinstance(msg, str):
            return self.intern(msg)
        raise IRValidationError(f"a message is a string, not {type(msg).__name__}")


def lower_module(program: nodes.Program) -> Module:
    """
    Verify *program* and lower it to a validated module object.

    Args:
        program: the IR program to lower.

    Returns:
        A module that already passed `detree.spec.validate`.

    Raises:
        IRValidationError: the program is not lowerable.
        ModuleValidationError: the emitted module is not executable.
    """
    verify_program(program)
    arena = StringArena(program)

    buffers = [
        BufferDecl(
            name=buffer.name,
            dtype=buffer.dtype,
            kind=buffer.kind,
            init=tuple(buffer.init),
        )
        for buffer in program.buffers
    ]
    states = [
        StateDecl(
            name=state.name,
            init=state.init,
            is_string=state.type == "name",
        )
        for state in program.states
    ]
    layouts = [
        Layout(
            id=layout.id,
            fields=tuple(
                LayoutField(
                    kind=field.kind,
                    typecode=field.typecode,
                    count=field.count,
                    buffer_id=field.buffer_id,
                )
                for field in layout.fields
            ),
        )
        for layout in program.layouts
    ]

    funcs = []
    code = []
    regs_max = 1
    for index, func in enumerate(program.functions):
        linear = linearize_function(program, func, arena)
        reg_count = finalize_registers(linear)
        instructions = encode_function(linear)
        funcs.append(
            FuncDecl(
                id=index,
                name=func.name,
                arity=func.arity,
                reg_count=reg_count,
                code_off=len(code),
                code_len=len(instructions),
            )
        )
        code.extend(instructions)
        regs_max = max(regs_max, reg_count)

    module = Module(
        entry=program.entry,
        regs_max=regs_max,
        buffers=buffers,
        states=states,
        strings=list(arena.values),
        layouts=layouts,
        funcs=funcs,
        code=code,
    )
    try:
        validate_module(module)
    except DetreeError as error:
        raise IRValidationError(f"lowering produced an invalid module: {error}") from error
    return module


def lower(program: nodes.Program) -> bytes:
    """
    Lower a verified IR program to canonical module bytes.

    This is what `detree.compile` calls after the optimizer.

    Args:
        program: the IR program to lower.

    Returns:
        The module bytes of `docs/spec/module.md`.
    """
    return lower_module(program).to_bytes()


__all__ = ["StringArena", "lower", "lower_module"]

"""
Static validation of a decoded module.

The checks mirror `docs/spec/module.md` §Validation. Validation is static: it runs
once per module, before any basket byte is read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import ModuleValidationError
from .isa import (
    BUFFER_DTYPES,
    BUFFER_OPS,
    ISA_MAJOR,
    ISA_MINOR,
    JUMP_OPS,
    MAGIC,
    MESSAGE_OPS,
    SCALAR_READ_OPS,
    STATE_OPS,
    BufferKind,
    FieldKind,
    Op,
    Typecode,
)

if TYPE_CHECKING:
    from .module import Module

# Instructions whose `rd` is an operand.
_RD = frozenset(
    {
        Op.CONST,
        Op.CONST_F,
        Op.MOV,
        Op.ZEXT32,
        Op.ZEXT16,
        Op.ZEXT8,
        Op.SEXT32,
        Op.SEXT16,
        Op.SEXT8,
        Op.ADD,
        Op.SUB,
        Op.MUL,
        Op.DIV_S,
        Op.DIV_U,
        Op.MOD_S,
        Op.MOD_U,
        Op.NEG,
        Op.SHL,
        Op.SHR_S,
        Op.SHR_U,
        Op.AND,
        Op.OR,
        Op.XOR,
        Op.NOT,
        Op.NOT_B,
        Op.CMP_EQ,
        Op.CMP_NE,
        Op.CMP_LT_S,
        Op.CMP_LE_S,
        Op.CMP_GT_S,
        Op.CMP_GE_S,
        Op.CMP_LT_U,
        Op.CMP_LE_U,
        Op.CMP_GT_U,
        Op.CMP_GE_U,
        Op.CALL,
        Op.SREAD,
        Op.SREAD_FNB,
        Op.SREAD_VERSION,
        Op.SREAD_CSTR,
        Op.CURSOR,
        Op.ENTRIES,
        Op.INIT_OFFSET,
        Op.DATA_SIZE,
        Op.OFFSET_AT,
        Op.OFFSET_SEEK,
        Op.EVENT_END,
        Op.BUF_LAST,
        Op.BUF_LEN,
        Op.STATE_GET,
        Op.REF_HAS,
        Op.REF_GET_OBJ,
        Op.REF_GET_CLASS,
        Op.REF_COUNT,
        Op.STR_CONST,
        Op.STR_EQ,
        Op.STR_LOAD,
    }
)

# Instructions whose `ra` is an operand.
_RA = frozenset(
    {
        Op.MOV,
        Op.ZEXT32,
        Op.ZEXT16,
        Op.ZEXT8,
        Op.SEXT32,
        Op.SEXT16,
        Op.SEXT8,
        Op.NEG,
        Op.NOT,
        Op.NOT_B,
        Op.JZ,
        Op.JNZ,
        Op.BR_EQ,
        Op.BR_NE,
        Op.BR_LT_S,
        Op.BR_LE_S,
        Op.BR_GT_S,
        Op.BR_GE_S,
        Op.BR_LT_U,
        Op.BR_LE_U,
        Op.BR_GT_U,
        Op.BR_GE_U,
        Op.CALL,
        Op.RET,
        Op.SKIP_R,
        Op.SET_CURSOR,
        Op.OFFSET_AT,
        Op.OFFSET_SEEK,
        Op.READ_BULK,
        Op.READ_STRIDED,
        Op.READ_FIELDS,
        Op.BUF_APPEND,
        Op.BUF_APPEND_OFFSET,
        Op.BUF_RESERVE,
        Op.STATE_SET,
        Op.REF_HAS,
        Op.REF_GET_OBJ,
        Op.REF_GET_CLASS,
        Op.REF_SET_OBJ,
        Op.REF_SET_CLASS,
        Op.STR_EQ,
        Op.STR_STORE,
        Op.ASSERT,
        Op.ASSERT_CURSOR_EQ,
        Op.ASSERT_STR_EQ,
        Op.DEBUG_PRINT,
    }
)

# Instructions whose `rb` is an operand.
_RB = frozenset(
    {
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
        Op.CMP_EQ,
        Op.CMP_NE,
        Op.CMP_LT_S,
        Op.CMP_LE_S,
        Op.CMP_GT_S,
        Op.CMP_GE_S,
        Op.CMP_LT_U,
        Op.CMP_LE_U,
        Op.CMP_GT_U,
        Op.CMP_GE_U,
        Op.BR_EQ,
        Op.BR_NE,
        Op.BR_LT_S,
        Op.BR_LE_S,
        Op.BR_GT_S,
        Op.BR_GE_S,
        Op.BR_LT_U,
        Op.BR_LE_U,
        Op.BR_GT_U,
        Op.BR_GE_U,
        Op.READ_STRIDED,
        Op.REF_SET_OBJ,
        Op.REF_SET_CLASS,
        Op.STR_EQ,
        Op.ASSERT_STR_EQ,
    }
)


def validate_module(module: Module) -> None:
    """
    Raise `ModuleValidationError` if *module* is not executable.

    Args:
        module: the decoded module to check.

    Raises:
        ModuleValidationError: the module breaks a rule of `docs/spec/module.md`.
    """
    if module.magic != MAGIC:
        raise ModuleValidationError(f"bad magic {module.magic!r}")
    if module.isa_major != ISA_MAJOR:
        raise ModuleValidationError(f"module isa_major {module.isa_major} != vm {ISA_MAJOR}")
    if module.isa_minor > ISA_MINOR:
        raise ModuleValidationError(
            f"module isa_minor {module.isa_minor} is newer than vm {ISA_MINOR}"
        )
    if not module.funcs:
        raise ModuleValidationError("module has no functions")
    if not 0 <= module.entry < len(module.funcs):
        raise ModuleValidationError(f"entry function {module.entry} does not exist")

    _validate_funcs(module)
    _validate_buffers(module)
    _validate_layouts(module)
    _validate_code(module)


def _validate_funcs(module: Module) -> None:
    spans = []
    for index, func in enumerate(module.funcs):
        if func.id != index:
            raise ModuleValidationError(f"function {index} has id {func.id}")
        if func.arity not in (0, 1):
            raise ModuleValidationError(f"{func.name}: arity {func.arity}")
        if func.arity == 1 and func.reg_count == 0:
            raise ModuleValidationError(
                f"{func.name}: arity 1 needs a register for the argument"
            )
        if func.code_len == 0:
            raise ModuleValidationError(f"{func.name}: empty body")
        if func.code_off < 0 or func.end > len(module.code):
            raise ModuleValidationError(f"{func.name}: code range out of bounds")
        if module.regs_max < func.reg_count:
            raise ModuleValidationError(
                f"regs_max {module.regs_max} below {func.name} reg_count {func.reg_count}"
            )
        spans.append((func.code_off, func.end, func.name))

    spans.sort()
    for (_start, end, name), (next_start, _, next_name) in zip(spans, spans[1:]):
        if end > next_start:
            raise ModuleValidationError(f"{name} and {next_name} have overlapping code slices")


def _validate_buffers(module: Module) -> None:
    names = set()
    for decl in module.buffers:
        if decl.name in names:
            raise ModuleValidationError(f"duplicate buffer name {decl.name!r}")
        names.add(decl.name)
        if decl.dtype not in BUFFER_DTYPES:
            raise ModuleValidationError(f"buffer {decl.name}: dtype {decl.dtype!r}")
        if decl.kind not in (BufferKind.DATA, BufferKind.OFFSETS, BufferKind.COUNT):
            raise ModuleValidationError(f"buffer {decl.name}: kind {decl.kind}")
        if decl.kind in (BufferKind.OFFSETS, BufferKind.COUNT) and decl.dtype != "int64":
            raise ModuleValidationError(
                f"buffer {decl.name}: {decl.kind.name} buffers must be int64"
            )


def _validate_layouts(module: Module) -> None:
    for layout in module.layouts:
        for field in layout.fields:
            where = f"layout {layout.id}"
            if field.kind == FieldKind.TOBJECT_SKIP:
                raise ModuleValidationError(f"{where}: TOBJECT_SKIP unsupported")
            if field.kind not in (
                FieldKind.SCALAR,
                FieldKind.SKIP,
                FieldKind.ARRAY_FIXED,
            ):
                raise ModuleValidationError(f"{where}: field kind {field.kind}")
            if field.count < 0:
                raise ModuleValidationError(f"{where}: negative field count")
            if field.kind != FieldKind.SKIP:
                if not 0 <= field.buffer_id < len(module.buffers):
                    raise ModuleValidationError(
                        f"{where}: buffer {field.buffer_id} does not exist"
                    )
                _check_typecode(field.typecode, where)


def _validate_code(module: Module) -> None:
    n_buffers = len(module.buffers)
    n_states = len(module.states)
    n_layouts = len(module.layouts)
    n_strings = len(module.strings)

    for func in module.funcs:
        code = module.code[func.code_off : func.end]
        for offset, ins in enumerate(code):
            where = f"{func.name}#{offset}"
            if ins.op not in Op._value2member_map_:
                raise ModuleValidationError(f"{where}: unknown opcode {int(ins.op):#x}")

            if ins.op in _RD and ins.rd >= func.reg_count:
                raise ModuleValidationError(
                    f"{where}: register {ins.rd} >= reg_count {func.reg_count}"
                )
            if ins.op in _RA and ins.ra >= func.reg_count:
                raise ModuleValidationError(
                    f"{where}: register {ins.ra} >= reg_count {func.reg_count}"
                )
            if ins.op in _RB and ins.rb >= func.reg_count:
                raise ModuleValidationError(
                    f"{where}: register {ins.rb} >= reg_count {func.reg_count}"
                )

            if ins.op in JUMP_OPS and not 0 <= ins.imm1 < len(code):
                raise ModuleValidationError(
                    f"{where}: jump target {ins.imm1} outside the function"
                )

            if ins.op == Op.SKIP and ins.imm1 < 0:
                raise ModuleValidationError(f"{where}: negative skip {ins.imm1}")

            if ins.op == Op.CALL and not 0 <= ins.imm1 < len(module.funcs):
                raise ModuleValidationError(f"{where}: function {ins.imm1} does not exist")

            if ins.op in BUFFER_OPS and not 0 <= ins.imm1 < n_buffers:
                raise ModuleValidationError(f"{where}: buffer {ins.imm1} does not exist")

            if ins.op == Op.BUF_APPEND_LEN:
                for operand in (ins.imm1, ins.imm2):
                    if not 0 <= operand < n_buffers:
                        raise ModuleValidationError(
                            f"{where}: buffer {operand} does not exist"
                        )

            if ins.op in (Op.READ_BULK, Op.READ_STRIDED):
                if not 0 <= ins.imm1 < n_buffers:
                    raise ModuleValidationError(f"{where}: buffer {ins.imm1} does not exist")
                _check_typecode(ins.imm2, where)

            if ins.op in SCALAR_READ_OPS:
                _check_typecode(ins.imm2 if ins.op == Op.READ_APPEND else ins.imm1, where)

            if ins.op in STATE_OPS and not 0 <= ins.imm1 < n_states:
                raise ModuleValidationError(f"{where}: state {ins.imm1} does not exist")

            if ins.op == Op.STR_CONST and not 0 <= ins.imm1 < n_strings:
                raise ModuleValidationError(f"{where}: string {ins.imm1} does not exist")

            if ins.op == Op.READ_FIELDS and not 0 <= ins.imm1 < n_layouts:
                raise ModuleValidationError(f"{where}: layout {ins.imm1} does not exist")

            if ins.op in MESSAGE_OPS and not 0 <= ins.imm1 < n_strings:
                raise ModuleValidationError(f"{where}: message {ins.imm1} does not exist")


def _check_typecode(typecode: object, where: str) -> None:
    if typecode not in Typecode._value2member_map_:
        raise ModuleValidationError(f"{where}: unknown typecode {typecode}")


__all__ = ["ModuleValidationError", "validate_module"]

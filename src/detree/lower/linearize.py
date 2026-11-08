"""
Lower structured control flow to a linear pseudo-instruction stream.

Registers are assigned with a stack discipline: parameters and locals keep a
fixed virtual register, temporaries are taken from a small free list and
released after their consumer. The result is a straight-line program with
symbolic labels; `_codegen` resolves the labels and `regalloc`
checks the register bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..ir import nodes
from ..ir.errors import IRValidationError
from ..ir.verify import op_of
from ..spec.isa import Op

if TYPE_CHECKING:
    from . import StringArena

MAX_REGISTERS = 255


@dataclass
class PIns:
    """
    One pseudo-instruction; a jump stores its target in `label`.

    Labels are resolved to instruction indices by `encode_function`.
    """

    op: Op
    rd: int = 0
    ra: int = 0
    rb: int = 0
    imm1: int = 0
    imm2: int = 0
    label: int | None = None


@dataclass
class Label:
    """
    A branch target that emits no instruction.

    Its position in `LinearFunction.code` is what a jump resolves to.
    """

    id: int


@dataclass
class LinearFunction:
    """
    One function lowered to pseudo-instructions.

    Attributes:
        name: The function name.
        arity: The number of parameters.
        code: The pseudo-instructions, with jumps carrying label ids.
        reg_count: The number of registers the function needs.
    """

    name: str
    arity: int
    code: list
    reg_count: int


def _signed64(bits: int) -> int:
    bits &= (1 << 64) - 1
    return bits - (1 << 64) if bits & (1 << 63) else bits


def _collect_locals(func: nodes.Function) -> list[str]:
    names = list(func.params) + list(func.locals)
    seen = set(names)
    for stmt in func.body:
        collected = []
        _collect_stmt_locals(stmt, collected)
        for name in collected:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _collect_stmt_locals(stmt: nodes.Stmt, out: list[str]) -> None:
    if isinstance(stmt, nodes.Assign) and isinstance(stmt.target, nodes.Local):
        out.append(stmt.target.name)
    elif isinstance(stmt, nodes.CallStmt) and stmt.target is not None:
        out.append(stmt.target.name)
    elif isinstance(stmt, nodes.ForRange):
        out.append(stmt.var)
        for inner in stmt.body:
            _collect_stmt_locals(inner, out)
    elif isinstance(stmt, nodes.If):
        for inner in stmt.then_body:
            _collect_stmt_locals(inner, out)
        for inner in stmt.else_body:
            _collect_stmt_locals(inner, out)
    elif isinstance(stmt, nodes.While):
        for inner in stmt.body:
            _collect_stmt_locals(inner, out)


class Linearizer:
    """
    Emit one function's pseudo-instructions.

    Locals keep one register each; temporaries come from a free list, so a
    function's register count stays bounded by its expression depth.
    """

    def __init__(
        self,
        program: nodes.Program,
        func: nodes.Function,
        arena: StringArena,
    ) -> None:
        self.program = program
        self.func = func
        self.arena = arena
        self.code = []
        self.local_regs = {}
        self.local_names = _collect_locals(func)
        for index, name in enumerate(self.local_names):
            self.local_regs[name] = index
        self.local_set = set(self.local_regs.values())
        self.next_reg = len(self.local_names)
        self.free_temps = []
        self.next_label = 0

    # -- registers --------------------------------------------------------
    def alloc_temp(self) -> int:
        """
        Take a temporary register.

        Returns:
            A released register when one is free, otherwise a new one.

        Raises:
            IRValidationError: the function would need too many registers.
        """
        if self.free_temps:
            return self.free_temps.pop()
        register = self.next_reg
        self.next_reg += 1
        if self.next_reg > MAX_REGISTERS:
            raise IRValidationError(
                f"{self.func.name}: more than {MAX_REGISTERS} registers are needed"
            )
        return register

    def release(self, register: int) -> None:
        """
        Return a temporary register to the free list.

        Args:
            register: the register to release; a local is ignored.
        """
        if register not in self.local_set and register not in self.free_temps:
            self.free_temps.append(register)

    def local(self, name: str) -> int:
        """
        The register that holds the local *name*.

        Args:
            name: the local's name.

        Returns:
            The register index.

        Raises:
            IRValidationError: the local is not declared.
        """
        try:
            return self.local_regs[name]
        except KeyError:
            raise IRValidationError(
                f"{self.func.name}: local {name!r} is not declared"
            ) from None

    # -- emission ---------------------------------------------------------
    def label(self) -> int:
        """
        Allocate a branch-target id.

        Returns:
            A label id unique within the function.
        """
        value = self.next_label
        self.next_label += 1
        return value

    def emit(
        self,
        op: Op,
        rd: int = 0,
        ra: int = 0,
        rb: int = 0,
        imm1: int = 0,
        imm2: int = 0,
        label: int | None = None,
    ) -> None:
        """
        Append one pseudo-instruction.

        Args:
            op: the opcode to emit.
            rd: the destination register.
            ra: the first source register.
            rb: the second source register.
            imm1: the first immediate.
            imm2: the second immediate.
            label: the branch target, for a jump.
        """
        self.code.append(PIns(op, rd, ra, rb, imm1, imm2, label))

    def emit_jump(self, op: Op, label: int, ra: int = 0) -> None:
        """
        Append a pseudo-instruction that branches to *label*.

        Args:
            op: the jump opcode.
            label: the target label id.
            ra: the register the branch tests, when it tests one.
        """
        self.code.append(PIns(op, ra=ra, label=label))

    # The branch that jumps when the comparison is false, one instruction for
    # the compare-and-jump pair a plain `CMP_*` plus `JZ` would need.
    _FALSE_BRANCH = {
        Op.CMP_EQ: Op.BR_NE,
        Op.CMP_NE: Op.BR_EQ,
        Op.CMP_LT_S: Op.BR_GE_S,
        Op.CMP_LE_S: Op.BR_GT_S,
        Op.CMP_GT_S: Op.BR_LE_S,
        Op.CMP_GE_S: Op.BR_LT_S,
        Op.CMP_LT_U: Op.BR_GE_U,
        Op.CMP_LE_U: Op.BR_GT_U,
        Op.CMP_GT_U: Op.BR_LE_U,
        Op.CMP_GE_U: Op.BR_LT_U,
    }

    def cond_branch(self, cond: nodes.Expr, label: int) -> bool:
        """
        Jump to *label* when *cond* is false.

        Args:
            cond: the condition to test.
            label: the target label id.

        Returns:
            `True` when a fused compare-and-branch was emitted.
        """
        if not isinstance(cond, nodes.Compare):
            return False
        branch = self._FALSE_BRANCH.get(op_of(cond.op))
        if branch is None:
            return False
        left = self.expr(cond.a)
        right = self.expr(cond.b)
        self.emit(branch, ra=left, rb=right, label=label)
        self.release(left)
        self.release(right)
        return True

    # -- expressions ------------------------------------------------------
    def expr(self, node: nodes.Expr, dest: int | None = None) -> int:
        """
        Emit code for *node* and return the register holding its value.

        Args:
            node: the expression to lower.
            dest: the register to write, or `None` to take a temporary.

        Returns:
            The register that holds the result.
        """
        if node is None:
            raise IRValidationError(f"{self.func.name}: missing expression")
        if dest is None:
            dest = self.alloc_temp()

        if isinstance(node, nodes.Const):
            self.emit(Op.CONST, rd=dest, imm1=_signed64(node.value))
            return dest
        if isinstance(node, nodes.ConstFloat):
            self.emit(Op.CONST_F, rd=dest, imm1=_signed64(node.bits))
            return dest
        if isinstance(node, nodes.Local):
            self.emit(Op.MOV, rd=dest, ra=self.local(node.name))
            return dest
        if isinstance(node, nodes.StateGet):
            state = self.program.states[node.slot]
            op = Op.STR_LOAD if state.type == "name" else Op.STATE_GET
            self.emit(op, rd=dest, imm1=node.slot)
            return dest
        if isinstance(node, nodes.BufferLast):
            self.emit(Op.BUF_LAST, rd=dest, imm1=node.buf)
            return dest
        if isinstance(node, nodes.BufferLen):
            self.emit(Op.BUF_LEN, rd=dest, imm1=node.buf)
            return dest
        if isinstance(node, nodes.Cursor):
            self.emit(Op.CURSOR, rd=dest)
            return dest
        if isinstance(node, nodes.DataSize):
            self.emit(Op.DATA_SIZE, rd=dest)
            return dest
        if isinstance(node, nodes.Entries):
            self.emit(Op.ENTRIES, rd=dest)
            return dest
        if isinstance(node, nodes.InitOffset):
            self.emit(Op.INIT_OFFSET, rd=dest)
            return dest
        if isinstance(node, nodes.OffsetAt):
            index = self.expr(node.index)
            self.emit(Op.OFFSET_AT, rd=dest, ra=index)
            self.release(index)
            return dest
        if isinstance(node, nodes.OffsetSeek):
            pos = self.expr(node.pos)
            self.emit(Op.OFFSET_SEEK, rd=dest, ra=pos)
            self.release(pos)
            return dest
        if isinstance(node, nodes.EventEnd):
            self.emit(Op.EVENT_END, rd=dest)
            return dest
        if isinstance(node, nodes.StreamRead):
            self.emit(Op.SREAD, rd=dest, imm1=int(node.typecode))
            return dest
        if isinstance(node, nodes.StreamReadFNB):
            self.emit(Op.SREAD_FNB, rd=dest)
            return dest
        if isinstance(node, nodes.StreamReadVersion):
            self.emit(Op.SREAD_VERSION, rd=dest)
            return dest
        if isinstance(node, nodes.StreamReadCString):
            self.emit(Op.SREAD_CSTR, rd=dest)
            return dest
        if isinstance(node, nodes.Unary):
            value = self.expr(node.x)
            self.emit(op_of(node.op), rd=dest, ra=value)
            self.release(value)
            return dest
        if isinstance(node, nodes.Binary):
            left = self.expr(node.a)
            right = self.expr(node.b)
            self.emit(op_of(node.op), rd=dest, ra=left, rb=right)
            self.release(left)
            self.release(right)
            return dest
        if isinstance(node, nodes.Compare):
            left = self.expr(node.a)
            right = self.expr(node.b)
            self.emit(op_of(node.op), rd=dest, ra=left, rb=right)
            self.release(left)
            self.release(right)
            return dest
        if isinstance(node, nodes.Cast):
            value = self.expr(node.x)
            prefix = "ZEXT" if node.kind == "zext" else "SEXT"
            self.emit(Op[f"{prefix}{node.width}"], rd=dest, ra=value)
            self.release(value)
            return dest
        if isinstance(node, nodes.RefHas):
            tag = self.expr(node.tag)
            self.emit(Op.REF_HAS, rd=dest, ra=tag)
            self.release(tag)
            return dest
        if isinstance(node, nodes.RefGetObj):
            tag = self.expr(node.tag)
            self.emit(Op.REF_GET_OBJ, rd=dest, ra=tag)
            self.release(tag)
            return dest
        if isinstance(node, nodes.RefGetClass):
            tag = self.expr(node.tag)
            self.emit(Op.REF_GET_CLASS, rd=dest, ra=tag)
            self.release(tag)
            return dest
        if isinstance(node, nodes.RefCount):
            self.emit(Op.REF_COUNT, rd=dest)
            return dest
        if isinstance(node, nodes.StrConst):
            self.emit(Op.STR_CONST, rd=dest, imm1=self.arena.constant(node.index))
            return dest
        if isinstance(node, nodes.StrEq):
            left = self.expr(node.a)
            right = self.expr(node.b)
            self.emit(Op.STR_EQ, rd=dest, ra=left, rb=right)
            self.release(left)
            self.release(right)
            return dest
        if isinstance(node, nodes.Call):
            arg = 0 if node.arg is None else self.expr(node.arg)
            self.emit(Op.CALL, rd=dest, ra=arg, imm1=node.func)
            if node.arg is not None:
                self.release(arg)
            return dest
        raise IRValidationError(
            f"{self.func.name}: cannot lower expression {type(node).__name__}"
        )

    # -- statements -------------------------------------------------------
    def body(self, body: list[nodes.Stmt]) -> None:
        """
        Emit every statement of *body*.

        Args:
            body: the statements to lower, in order.
        """
        for stmt in body:
            self.stmt(stmt)

    def stmt(self, node: nodes.Stmt) -> None:
        """
        Emit one statement.

        Args:
            node: the statement to lower.
        """
        if isinstance(node, (nodes.Comment, nodes.Nop)):
            return
        if isinstance(node, nodes.Assign):
            if isinstance(node.target, nodes.Local):
                self.expr(node.value, dest=self.local(node.target.name))
                return
            if isinstance(node.target, nodes.StateSlot):
                value = self.expr(node.value)
                state = self.program.states[node.target.index]
                op = Op.STR_STORE if state.type == "name" else Op.STATE_SET
                self.emit(op, ra=value, imm1=node.target.index)
                self.release(value)
                return
            raise IRValidationError(f"{self.func.name}: bad assignment target")
        if isinstance(node, nodes.BufferAppend):
            value = self.expr(node.value)
            self.emit(Op.BUF_APPEND, ra=value, imm1=node.buf)
            self.release(value)
            return
        if isinstance(node, nodes.BufferAppendConst):
            self.emit(Op.BUF_APPEND_CONST, imm1=node.buf, imm2=_signed64(node.value))
            return
        if isinstance(node, nodes.BufferAppendOffset):
            value = self.expr(node.value)
            self.emit(Op.BUF_APPEND_OFFSET, ra=value, imm1=node.buf)
            self.release(value)
            return
        if isinstance(node, nodes.BufferAppendLen):
            self.emit(Op.BUF_APPEND_LEN, imm1=node.dst, imm2=node.src)
            return
        if isinstance(node, nodes.BufferReserve):
            count = self.expr(node.count)
            self.emit(Op.BUF_RESERVE, ra=count, imm1=node.buf)
            self.release(count)
            return
        if isinstance(node, nodes.ReadBulk):
            count = self.expr(node.count)
            self.emit(Op.READ_BULK, ra=count, imm1=node.buf, imm2=int(node.typecode))
            self.release(count)
            return
        if isinstance(node, nodes.ReadBulkUntil):
            end = self.expr(node.end)
            self.emit(Op.READ_BULK_UNTIL, ra=end, imm1=node.buf, imm2=int(node.typecode))
            self.release(end)
            return
        if isinstance(node, nodes.ReadString):
            self.emit(
                Op.READ_STRING,
                rb=node.mode,
                imm1=node.chars,
                imm2=node.offsets,
            )
            return
        if isinstance(node, nodes.ReadStrided):
            count = self.expr(node.count)
            stride = self.expr(node.stride)
            self.emit(
                Op.READ_STRIDED,
                ra=count,
                rb=stride,
                imm1=node.buf,
                imm2=int(node.typecode),
            )
            self.release(count)
            self.release(stride)
            return
        if isinstance(node, nodes.ReadFields):
            count = self.expr(node.count)
            self.emit(Op.READ_FIELDS, ra=count, imm1=node.layout)
            self.release(count)
            return
        if isinstance(node, nodes.ReadAppend):
            self.emit(Op.READ_APPEND, imm1=node.buf, imm2=int(node.typecode))
            return
        if isinstance(node, nodes.Skip):
            self.emit(Op.SKIP, imm1=node.bytes)
            return
        if isinstance(node, nodes.SkipR):
            value = self.expr(node.value)
            self.emit(Op.SKIP_R, ra=value)
            self.release(value)
            return
        if isinstance(node, nodes.SkipFNB):
            self.emit(Op.SKIP_FNB)
            return
        if isinstance(node, nodes.SkipVersion):
            self.emit(Op.SKIP_VERSION)
            return
        if isinstance(node, nodes.SkipTObject):
            self.emit(Op.SKIP_TOBJECT)
            return
        if isinstance(node, nodes.SkipObjHdr):
            self.emit(Op.SKIP_OBJHDR)
            return
        if isinstance(node, nodes.SkipCStr):
            self.emit(Op.SKIP_CSTR)
            return
        if isinstance(node, nodes.SetCursor):
            value = self.expr(node.value)
            self.emit(Op.SET_CURSOR, ra=value)
            self.release(value)
            return
        if isinstance(node, nodes.CallStmt):
            if node.target is None:
                # A discarded call still needs somewhere to put the result.
                # Register 0 is the callee's argument slot, so using it would
                # clobber a parameter the caller still needs (notably the
                # end position of a read_until loop).
                target = self.alloc_temp()
                release_target = True
            else:
                target = self.local(node.target.name)
                release_target = False
            borrowed = isinstance(node.arg, nodes.Local)
            arg = self.local(node.arg.name) if borrowed else 0
            if node.arg is not None and not borrowed:
                arg = self.expr(node.arg)
            self.emit(Op.CALL, rd=target, ra=arg, imm1=node.func)
            if node.arg is not None and not borrowed:
                self.release(arg)
            if release_target:
                self.release(target)
            return
        if isinstance(node, nodes.If):
            else_label = self.label()
            end_label = self.label()
            if not self.cond_branch(node.cond, else_label):
                cond = self.expr(node.cond)
                self.emit_jump(Op.JZ, else_label, ra=cond)
                self.release(cond)
            self.body(node.then_body)
            self.emit_jump(Op.JMP, end_label)
            self.code.append(Label(else_label))
            self.body(node.else_body)
            self.code.append(Label(end_label))
            return
        if isinstance(node, nodes.While):
            start = self.label()
            end = self.label()
            self.code.append(Label(start))
            if not self.cond_branch(node.cond, end):
                cond = self.expr(node.cond)
                self.emit_jump(Op.JZ, end, ra=cond)
                self.release(cond)
            self.body(node.body)
            self.emit_jump(Op.JMP, start)
            self.code.append(Label(end))
            return
        if isinstance(node, nodes.ForRange):
            var = self.local(node.var)
            self.expr(node.start, dest=var)
            stop = self.expr(node.stop)
            head = self.label()
            end = self.label()
            self.code.append(Label(head))
            # Jump out when the loop variable has reached the stop value:
            # `var < stop` is false exactly when `var >= stop` for a rising loop.
            self.emit(
                Op.BR_GE_S if node.step > 0 else Op.BR_LE_S,
                ra=var,
                rb=stop,
                label=end,
            )
            self.body(node.body)
            step = self.alloc_temp()
            self.emit(Op.CONST, rd=step, imm1=_signed64(node.step))
            self.emit(Op.ADD, rd=var, ra=var, rb=step)
            self.release(step)
            self.emit_jump(Op.JMP, head)
            self.code.append(Label(end))
            self.release(stop)
            return
        if isinstance(node, nodes.Assert):
            cond = self.expr(node.cond)
            self.emit(Op.ASSERT, ra=cond, imm1=self.arena.message(node.msg))
            self.release(cond)
            return
        if isinstance(node, nodes.AssertCursorEq):
            value = self.expr(node.value)
            self.emit(Op.ASSERT_CURSOR_EQ, ra=value, imm1=self.arena.message(node.msg))
            self.release(value)
            return
        if isinstance(node, nodes.AssertStrEq):
            left = self.expr(node.a)
            right = self.expr(node.b)
            self.emit(
                Op.ASSERT_STR_EQ,
                ra=left,
                rb=right,
                imm1=self.arena.message(node.msg),
            )
            self.release(left)
            self.release(right)
            return
        if isinstance(node, nodes.Throw):
            self.emit(Op.THROW, imm1=self.arena.message(node.msg))
            return
        if isinstance(node, nodes.RefSetObj):
            tag = self.expr(node.tag)
            value = self.expr(node.value)
            self.emit(Op.REF_SET_OBJ, ra=tag, rb=value)
            self.release(tag)
            self.release(value)
            return
        if isinstance(node, nodes.RefSetClass):
            tag = self.expr(node.tag)
            value = self.expr(node.value)
            self.emit(Op.REF_SET_CLASS, ra=tag, rb=value)
            self.release(tag)
            self.release(value)
            return
        if isinstance(node, nodes.Return):
            if isinstance(node.value, nodes.Local):
                self.emit(Op.RET, ra=self.local(node.value.name))
                return
            value = 0 if node.value is None else self.expr(node.value)
            self.emit(Op.RET, ra=value)
            if node.value is not None:
                self.release(value)
            return
        raise IRValidationError(
            f"{self.func.name}: cannot lower statement {type(node).__name__}"
        )

    def finish(self) -> LinearFunction:
        """
        Close the function with a return and report its register count.

        Returns:
            The linearized function, ready for label resolution.
        """
        if not self.code or not (
            isinstance(self.code[-1], PIns) and self.code[-1].op in (Op.RET, Op.THROW)
        ):
            self.emit(Op.RET, ra=0)
        return LinearFunction(
            name=self.func.name,
            arity=self.func.arity,
            code=self.code,
            reg_count=max(self.next_reg, 1),
        )


def linearize_function(
    program: nodes.Program,
    func: nodes.Function,
    arena: StringArena,
) -> LinearFunction:
    """
    Lower one function to pseudo-instructions with virtual registers.

    Args:
        program: the program that owns *func*.
        func: the function to lower.
        arena: the string table shared by the module.

    Returns:
        The linearized function.
    """
    lowerer = Linearizer(program, func, arena)
    lowerer.body(func.body)
    return lowerer.finish()


__all__ = ["Label", "LinearFunction", "Linearizer", "PIns", "linearize_function"]

"""
IR node definitions.

The node set is defined in `docs/spec/ir.md`: program and function containers, the
buffer/state/string/layout declarations, typed expressions, and structured
statements (`If`, `While`, `ForRange`). Nodes are plain dataclasses; the
tree is never serialized. A pass receives a program and returns a program, so
the compiler never mutates a caller's tree in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..spec.isa import BufferKind, FieldKind, Op, Typecode

# The IR contract version. Breaking changes bump the major component.
IR_VERSION = "0.1"

# Value types. `bool` is a subtype of `i64`; `str` is an arena id.
I64 = "i64"
F32 = "f32"
F64 = "f64"
BOOL = "bool"
STR = "str"
VOID = "void"
TYPES = frozenset({I64, F32, F64, BOOL, STR, VOID})


class Expr:
    """
    Base class for expressions; used for `isinstance` checks.

    A node carries its operands as attributes, so passes walk it with
    `dataclasses.fields`.
    """


class Stmt:
    """
    Base class for statements.

    A statement may contain expressions and, for structured forms, nested
    bodies of further statements.
    """


class Target:
    """
    Base class for assignment targets.

    Only a `Local` and a `StateSlot` can appear on the left of `Assign`.
    """


# -- program declarations ---------------------------------------------------


@dataclass
class Buffer:
    """
    An output buffer declaration.

    `init` restores the declared content at every reset.
    """

    name: str
    dtype: str
    kind: BufferKind = BufferKind.DATA
    init: tuple = ()


@dataclass
class State:
    """
    A scalar that survives across events.

    `type` is `i64` for a value slot, `name` for a string slot.
    """

    name: str
    init: int = 0
    type: str = "i64"


@dataclass
class LayoutField:
    """
    One field of a flat `READ_FIELDS` layout.

    `count` is 1 for a scalar; `buffer_id` is set only for a fixed array.
    """

    kind: FieldKind
    typecode: Typecode
    count: int = 1
    buffer_id: int = -1


@dataclass
class Layout:
    """
    A flat sequence of scalar, skip, and fixed-array fields.

    Field order is the order they appear in the stream.
    """

    id: int
    fields: tuple = ()


@dataclass
class Constant:
    """
    A string constant; `StrConst(k)` indexes the program's constants.

    Lowering interns `text` into the module string arena.
    """

    text: str


@dataclass
class Function:
    """
    One function: a name, its parameters, and a structured body.

    `params` names the arguments; `locals` grows as the body is built.
    """

    name: str
    arity: int = 0
    params: tuple = ()
    body: list = field(default_factory=list)
    result: str = VOID
    locals: list = field(default_factory=list)


@dataclass
class Program:
    """
    A complete compiler-side program.

    Ids follow declaration order; `entry` indexes `functions`.
    """

    entry: int = 0
    functions: list = field(default_factory=list)
    buffers: list = field(default_factory=list)
    states: list = field(default_factory=list)
    strings: list = field(default_factory=list)
    layouts: list = field(default_factory=list)
    constants: list = field(default_factory=list)


# -- expressions -------------------------------------------------------------


@dataclass
class Const(Expr):
    """
    A 64-bit integer constant.

    Values are two's-complement; the verifier types the node as `i64`.
    """

    value: int = 0


@dataclass
class ConstFloat(Expr):
    """
    A floating-point constant, stored as its IEEE-754 bit pattern.

    `width` is 32 or 64 and decides between `f32` and `f64`.
    """

    bits: int = 0
    width: int = 64


@dataclass
class Local(Expr, Target):
    """
    A function-local variable, usable as an expression and a target.

    The verifier types it from the first assignment it sees.
    """

    name: str = ""


@dataclass
class StateSlot(Target):
    """
    A state slot as the target of an assignment.

    `index` refers to `Program.states`.
    """

    index: int = 0


@dataclass
class StateGet(Expr):
    """
    The current value of a state slot.

    `slot` refers to `Program.states`.
    """

    slot: int = 0


@dataclass
class BufferLast(Expr):
    """
    The last element of a buffer.

    `buf` refers to `Program.buffers`; an empty buffer reads as zero.
    """

    buf: int = 0


@dataclass
class BufferLen(Expr):
    """
    The number of elements currently in a buffer.

    `buf` refers to `Program.buffers`.
    """

    buf: int = 0


@dataclass
class Cursor(Expr):
    """
    The current stream cursor as a byte offset.

    The value is read from the machine's cursor register.
    """


@dataclass
class DataSize(Expr):
    """
    The size of the basket in bytes.

    It is fixed for the whole run, including every event.
    """


@dataclass
class Entries(Expr):
    """
    The number of entries in the basket.

    It is one less than the number of entry offsets.
    """


@dataclass
class InitOffset(Expr):
    """
    The cursor offset the run started at.

    It is the key length the caller passed to `run`.
    """


@dataclass
class OffsetAt(Expr):
    """
    The entry offset stored at `index`.

    `index` is an integer expression; an out-of-range index fails the run.
    """

    index: Expr = None


@dataclass
class OffsetSeek(Expr):
    """
    Seek to the entry offset at `index` and yield the new position.

    The expression form exists so the compiler can fold the seek into a jump.
    """

    pos: Expr = None


@dataclass
class EventEnd(Expr):
    """
    The byte offset where the current event ends.

    `ReadBulkUntil` uses it as its bound.
    """


@dataclass
class StreamRead(Expr):
    """
    Read one big-endian scalar of the given typecode.

    The cursor advances by the typecode's width.
    """

    typecode: Typecode = Typecode.UINT8


@dataclass
class StreamReadFNB(Expr):
    """
    Read a length field that carries the `kByteCountMask`.

    A length without the mask is an `InvalidByteCount` error.
    """


@dataclass
class StreamReadVersion(Expr):
    """
    Read the int16 stream version.

    The result is the version value, not the cursor.
    """


@dataclass
class StreamReadCString(Expr):
    """
    Read a NUL-terminated string as a name-slot id.

    The bytes are interned in the machine's string arena.
    """


@dataclass
class Unary(Expr):
    """
    A unary operation, currently only `neg`.

    `op` is an `Op` member; the verifier checks that it is a unary one.
    """

    op: object = Op.NEG
    x: Expr = None


@dataclass
class Binary(Expr):
    """
    A binary arithmetic operation.

    `op` is an `Op` member; `a` and `b` must be integers.
    """

    op: object = Op.ADD
    a: Expr = None
    b: Expr = None


@dataclass
class Compare(Expr):
    """
    A comparison that yields a boolean.

    The result type is `bool`, which widens to `i64` where an integer is
    required.
    """

    op: object = Op.CMP_EQ
    a: Expr = None
    b: Expr = None


@dataclass
class Cast(Expr):
    """
    A narrowing or widening cast (`zext` or `sext`).

    `width` is the target width in bits.
    """

    kind: str = "zext"
    width: int = 32
    x: Expr = None


@dataclass
class RefHas(Expr):
    """
    Whether a reference tag is present.

    The result is a boolean.
    """

    tag: Expr = None


@dataclass
class RefGetObj(Expr):
    """
    The object id behind a reference tag.

    A missing tag is reported as `-1`, never as a failure.
    """

    tag: Expr = None


@dataclass
class RefGetClass(Expr):
    """
    The class-name id behind a reference tag.

    A missing tag is reported as `-1`, never as a failure.
    """

    tag: Expr = None


@dataclass
class RefCount(Expr):
    """
    The number of interned references in the current run.

    The count resets with the machine, not with each event.
    """


@dataclass
class StrConst(Expr):
    """
    A reference to a string constant declared by the program.

    `index` refers to `Program.constants`.
    """

    index: int = 0


@dataclass
class StrEq(Expr):
    """
    String equality.

    Both sides must be string-valued; the result is a boolean.
    """

    a: Expr = None
    b: Expr = None


@dataclass
class Call(Expr):
    """
    A call to another function that returns a value.

    `func` refers to `Program.functions`; a void callee uses `CallStmt`.
    """

    func: int = 0
    arg: Expr = None


# -- statements --------------------------------------------------------------


@dataclass
class Assign(Stmt):
    """
    Assign an expression to a local or a state slot.

    The target decides which slot is written; the value must be compatible
    with its type.
    """

    target: Target = None
    value: Expr = None


@dataclass
class BufferAppend(Stmt):
    """
    Append one computed value to a buffer.

    `buf` refers to `Program.buffers`.
    """

    buf: int = 0
    value: Expr = None


@dataclass
class BufferAppendConst(Stmt):
    """
    Append one constant to a buffer without materializing it in a register.

    Lowering emits `BUF_APPEND_CONST` for this node.
    """

    buf: int = 0
    value: int = 0


@dataclass
class BufferAppendOffset(Stmt):
    """
    Append a computed value to an offsets buffer.

    Keeping the offset append separate lets the optimizer fuse it with the
    read that produced the value.
    """

    buf: int = 0
    value: Expr = None


@dataclass
class BufferAppendLen(Stmt):
    """
    Append the length of one buffer to another, as an offset.

    `src` is read first, so the append records the region that just ended.
    """

    dst: int = 0
    src: int = 0


@dataclass
class BufferReserve(Stmt):
    """
    Reserve capacity in a buffer; a hint with no output effect.

    A machine may ignore it; it never changes the result.
    """

    buf: int = 0
    count: Expr = None


@dataclass
class ReadBulk(Stmt):
    """
    Read `count` values of one typecode into a buffer.

    Values are converted to the buffer's dtype, exactly as `StreamRead` plus
    `BufferAppend` would.
    """

    buf: int = 0
    typecode: Typecode = Typecode.UINT8
    count: Expr = None


@dataclass
class ReadBulkUntil(Stmt):
    """
    Read values until the event end, rejecting a partial element.

    A trailing byte string that is not a whole element is a
    `ReadLengthMismatch`.
    """

    buf: int = 0
    typecode: Typecode = Typecode.UINT8
    end: Expr = None


@dataclass
class ReadString(Stmt):
    """
    A length-prefixed byte string: bytes into `chars`, end into `offsets`.

    `mode` 0 appends `len(chars)` after the bytes, mode 1 appends
    `offsets[-1] + size`; a program that keeps the size before the characters
    uses the second form.
    """

    chars: int = 0
    offsets: int = 0
    mode: int = 0


@dataclass
class ReadStrided(Stmt):
    """
    Read `count` values with a fixed byte stride between them.

    The cursor advances by `stride` per value, not by the typecode width.
    """

    buf: int = 0
    typecode: Typecode = Typecode.UINT8
    count: Expr = None
    stride: Expr = None


@dataclass
class ReadFields(Stmt):
    """
    Read one layout `count` times.

    `layout` refers to `Program.layouts`.
    """

    layout: int = 0
    count: Expr = None


@dataclass
class ReadAppend(Stmt):
    """
    Read one value and append it in a single dispatch.

    Lowering emits `READ_APPEND`, which saves a register and a dispatch.
    """

    buf: int = 0
    typecode: Typecode = Typecode.UINT8


@dataclass
class Skip(Stmt):
    """
    Skip a fixed number of bytes.

    The cursor advances without producing a value.
    """

    bytes: int = 0


@dataclass
class SkipR(Stmt):
    """
    Skip the number of bytes held in a register.

    A negative count fails the run.
    """

    value: Expr = None


@dataclass
class SkipFNB(Stmt):
    """
    Skip a length-prefixed byte region (`fNBytes`).

    The length must carry `kByteCountMask`.
    """


@dataclass
class SkipVersion(Stmt):
    """
    Skip the int16 stream version.

    It pairs with `StreamReadVersion` when the version is not needed.
    """


@dataclass
class SkipTObject(Stmt):
    """
    Skip a `TObject` header.

    The version and byte-count fields are validated before the skip.
    """


@dataclass
class SkipObjHdr(Stmt):
    """
    Skip an object header.

    A new-class tag is rejected: this node skips a reference, not a definition.
    """


@dataclass
class SkipCStr(Stmt):
    """
    Skip a NUL-terminated string.

    The cursor stops just past the terminator.
    """


@dataclass
class SetCursor(Stmt):
    """
    Set the stream cursor.

    The value must lie inside the current event.
    """

    value: Expr = None


@dataclass
class CallStmt(Stmt):
    """
    Call another function, optionally keeping its result in a local.

    A `None` target discards the result of a value-returning callee.
    """

    target: Local = None
    func: int = 0
    arg: Expr = None


@dataclass
class If(Stmt):
    """
    A two-way branch.

    `cond` must be an integer or boolean expression.
    """

    cond: Expr = None
    then_body: list = field(default_factory=list)
    else_body: list = field(default_factory=list)


@dataclass
class While(Stmt):
    """
    A conditional loop.

    `cond` is re-evaluated before every iteration.
    """

    cond: Expr = None
    body: list = field(default_factory=list)


@dataclass
class ForRange(Stmt):
    """
    A counted loop over `range(start, stop, step)`.

    `step` is a literal integer; the loop variable lives in `Function.locals`.
    """

    var: str = ""
    start: Expr = None
    stop: Expr = None
    step: int = 1
    body: list = field(default_factory=list)


@dataclass
class Assert(Stmt):
    """
    Fail the run when `cond` is false.

    `msg` is literal text, an arena id, or a `Constant` node.
    """

    cond: Expr = None
    msg: object = ""


@dataclass
class AssertCursorEq(Stmt):
    """
    Fail the run when the cursor is not at `value`.

    It is the check that a variable-length region consumed what it declared.
    """

    value: Expr = None
    msg: object = ""


@dataclass
class AssertStrEq(Stmt):
    """
    Fail the run when two strings differ.

    Both sides are string-valued expressions.
    """

    a: Expr = None
    b: Expr = None
    msg: object = ""


@dataclass
class Throw(Stmt):
    """
    Fail the run unconditionally with a message.

    Lowering emits `THROW`; the statement terminates its block.
    """

    msg: object = ""


@dataclass
class RefSetObj(Stmt):
    """
    Bind an object id to a reference tag.

    The tag is an integer expression; later reads resolve it.
    """

    tag: Expr = None
    value: Expr = None


@dataclass
class RefSetClass(Stmt):
    """
    Bind a class-name id to a reference tag.

    The tag is an integer expression; later reads resolve it.
    """

    tag: Expr = None
    value: Expr = None


@dataclass
class Return(Stmt):
    """
    Return from the current function.

    A void function returns `None`; the verifier checks the declared result
    type.
    """

    value: Expr = None


@dataclass
class Comment(Stmt):
    """
    A source-level comment; lowering drops it.

    It survives optimization so a dump still shows the compiler's intent.
    """

    text: str = ""


@dataclass
class Nop(Stmt):
    """
    An explicit no-op.

    Passes keep it; lowering drops it.
    """


__all__ = [
    "BOOL",
    "Assign",
    "Binary",
    "Buffer",
    "BufferAppend",
    "BufferAppendConst",
    "BufferAppendLen",
    "BufferAppendOffset",
    "BufferLast",
    "BufferLen",
    "BufferReserve",
    "Call",
    "CallStmt",
    "Cast",
    "Comment",
    "Compare",
    "Const",
    "ConstFloat",
    "Constant",
    "Cursor",
    "DataSize",
    "Entries",
    "EventEnd",
    "F32",
    "F64",
    "ForRange",
    "Function",
    "I64",
    "IR_VERSION",
    "If",
    "InitOffset",
    "Layout",
    "LayoutField",
    "Local",
    "Nop",
    "OffsetAt",
    "OffsetSeek",
    "Program",
    "ReadAppend",
    "ReadBulk",
    "ReadBulkUntil",
    "ReadFields",
    "ReadStrided",
    "ReadString",
    "RefCount",
    "RefGetClass",
    "RefGetObj",
    "RefHas",
    "RefSetClass",
    "RefSetObj",
    "Return",
    "STR",
    "SetCursor",
    "Skip",
    "SkipCStr",
    "SkipFNB",
    "SkipObjHdr",
    "SkipR",
    "SkipTObject",
    "SkipVersion",
    "State",
    "StateGet",
    "StateSlot",
    "StreamRead",
    "StreamReadCString",
    "StreamReadFNB",
    "StreamReadVersion",
    "StrConst",
    "StrEq",
    "TYPES",
    "Throw",
    "Unary",
    "VOID",
    "While",
]

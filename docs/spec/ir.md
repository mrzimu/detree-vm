# IR, Optimizer, and Lowering

**Status: draft, IR v0.2.**

The IR is a **compiler-side contract**. A compiler builds a typed tree; the optimizer rewrites it; the lowering pass turns it into a module. Machines never see the IR.

The IR is deliberately a Python object tree rather than a serialized format:

- the compiler is a Python program that already walks a source AST;
- the optimizer wants structured control flow, not flat jumps;
- the tree is easy to inspect, snapshot, and test.

## 1. Pipeline

```text
   compiler
      │  builds
      ▼
  detree.ir.Program  ──►  detree.opt  ──►  detree.lower  ──►  module bytes
      │                                      │
      └── verify ────────────────────────────┘
```

A program that verifies MUST lower to a module that passes `detree.spec.validate`. The compiler never emits a module the machine would reject.

## 2. Program structure

| Entity | Fields |
|---|---|
| `Program` | `entry`, `functions`, `buffers`, `states`, `strings`, `layouts`, `constants` |
| `Function` | `name`, `arity`, `params`, `body`, `result` |
| `Buffer` | `name`, `dtype`, `kind`, `init` |
| `State` | `name`, `init`, `type` (`i64` or `name`) |
| `Layout` | `id`, `fields` (flat `READ_FIELDS` layout) |
| `Constant` | `text` (a string in the arena) |

Function ids, buffer ids, state ids, layout ids, and string ids are assigned by declaration order. The entry function is id 0. Ids MUST be deterministic; a `set` iteration or a `hash()` value MUST NOT influence them.

## 3. Types

| Type | Meaning |
|---|---|
| `i64` | a 64-bit integer slot; all integers are this type |
| `f32` | an IEEE-754 single-precision bit pattern |
| `f64` | an IEEE-754 double-precision bit pattern |
| `bool` | `0` or `1`, a subtype of `i64` |
| `str` | a string-arena id |
| `void` | no value |

The verifier checks that a value is used where its type is valid. Floats are pass-through only: `f32` and `f64` may be copied, stored in a buffer, or returned from a stream read, but they MUST NOT appear in arithmetic or comparison nodes.

## 4. Expressions

| Node | Type | Meaning |
|---|---|---|
| `Const(value)` | `i64` | a signed 64-bit constant |
| `ConstFloat(bits, width)` | `f32`/`f64` | an IEEE-754 bit pattern |
| `Local(name)` | declared type | a function-local value |
| `StateGet(slot)` | `i64`/`str` | read a state slot |
| `BufferLast(buf)` | buffer dtype | last element of a buffer |
| `BufferLen(buf)` | `i64` | element count |
| `Cursor()` | `i64` | current stream cursor |
| `DataSize()` | `i64` | size of the current event's data |
| `Entries()` | `i64` | number of events in the basket |
| `InitOffset()` | `i64` | initial cursor position |
| `OffsetAt(index)` | `i64` | `entry_offsets[index]` |
| `OffsetSeek(pos)` | `i64` | first entry offset after `pos` |
| `EventEnd()` | `i64` | `entry_offsets[event + 1]` |
| `StreamRead(typecode)` | `i64`/`f32`/`f64` | read one scalar |
| `StreamReadFNB()` | `i64` | read a masked `fNBytes` length |
| `StreamReadVersion()` | `i64` | read the int16 `fVersion` |
| `StreamReadCString()` | `str` | read and intern a C string |
| `Unary(op, x)` | `i64`/`bool` | `NEG`, `NOT`, `NOT_B` |
| `Binary(op, a, b)` | `i64` | integer arithmetic and bitwise ops |
| `Compare(op, a, b)` | `bool` | comparison |
| `Cast(kind, width, x)` | `i64` | zero- or sign-extend |
| `RefHas(tag)` | `bool` | reference-table membership |
| `RefGetObj(tag)` | `i64` | object index of a reference |
| `RefGetClass(tag)` | `str` | class-name id, or `-1` |
| `RefCount()` | `i64` | number of references |
| `StrConst(k)` | `str` | interned constant id |
| `StrEq(a, b)` | `bool` | string equality |
| `Call(func, arg)` | function result | call a function with one argument |

`Unary`, `Binary`, and `Compare` use the same operator names as the ISA: `NEG`, `NOT`, `NOT_B`, `ADD`, `SUB`, `MUL`, `DIV_S`, `DIV_U`, `MOD_S`, `MOD_U`, `SHL`, `SHR_S`, `SHR_U`, `AND`, `OR`, `XOR`, `CMP_EQ`, `CMP_NE`, `CMP_LT_S`, `CMP_LE_S`, `CMP_GT_S`, `CMP_GE_S`, `CMP_LT_U`, `CMP_LE_U`, `CMP_GT_U`, `CMP_GE_U`.

## 5. Statements

| Node | Meaning |
|---|---|
| `Assign(target, expr)` | assign to a local or a state slot |
| `BufferAppend(buf, expr)` | append a value |
| `BufferAppendConst(buf, value)` | append a constant |
| `BufferAppendOffset(buf, expr)` | append `buf[-1] + expr` |
| `BufferAppendLen(dst, src)` | append `len(src)` |
| `BufferReserve(buf, count)` | capacity hint |
| `ReadBulk(buf, typecode, count)` | bulk read |
| `ReadBulkUntil(buf, typecode, end)` | bulk read up to an end offset |
| `ReadStrided(buf, typecode, count, stride)` | strided bulk read |
| `ReadFields(layout, count)` | flat layout read |
| `ReadAppend(buf, typecode)` | read one and append |
| `Skip(bytes)` / `SkipR(expr)` | advance the cursor |
| `SkipFNB` / `SkipVersion` / `SkipTObject` / `SkipObjHdr` / `SkipCStr` | domain skips |
| `FNBegin()` / `FNEnd(msg)` | open and close a byte-counted region |
| `SetCursor(expr)` | set the cursor |
| `CallStmt(target, func, arg)` | call and assign the result |
| `If(cond, then_body, else_body)` | structured branch |
| `While(cond, body)` | structured loop |
| `ForRange(var, start, stop, step, body)` | counted loop |
| `Assert(cond, msg)` / `AssertCursorEq(expr, msg)` / `AssertStrEq(a, b, msg)` | checks |
| `Throw(msg)` | unconditional error |
| `Return(expr)` | return from the function |
| `Comment(text)` / `Nop()` | debug only |

`If`, `While`, and `ForRange` are the only control-flow constructs. There is no `goto`, `break`, or `continue` at IR level; a frontend that needs one writes it with a boolean local and a structured condition. `CallStmt` is sugar for `Assign(target, Call(...))`.

## 6. Verification rules

The verifier MUST reject a program that violates any of these rules:

1. `entry` names a function with arity 0.
2. Function names are unique; ids are contiguous and assigned by declaration order.
3. Every local is assigned before it is read on every path.
4. Every `Call` target exists and its arity matches the argument count.
5. Every buffer, state, layout, string, and message reference exists.
6. A `BufferAppend` type is compatible with the buffer's dtype: integers may be truncated or wrapped; a float buffer accepts a float expression or a float bit pattern; a `str` buffer does not exist.
7. `BufferAppendOffset` targets an `OFFSETS` buffer; `BufferAppendLen` targets an `OFFSETS` buffer.
8. A float expression appears only in a copy, a buffer append, a stream read, or a return. It MUST NOT appear in `Unary`, `Binary`, or `Compare`.
9. `Return` matches the function's result type.
10. A function has at most 255 registers after allocation.
11. A `While` condition and an `If` condition are boolean or integer expressions; no implicit float conversion exists.

## 7. Determinism

- Declaration order determines every id.
- No `set` iteration, `dict` hash order, `hash(obj)`, or process-specific value may influence ids or emitted bytes.
- Two structurally identical programs MUST produce identical modules. A test lowers the same program twice and compares `to_bytes()`.

## 8. Textual dump

`detree.ir.dump(program)` returns a stable, indented text form for tests and debugging. It is not a wire format. Example:

```text
program entry=decode
buffer data dtype=float64 kind=DATA
buffer offsets dtype=int64 kind=OFFSETS init=[0]
function decode arity=0 regs=4
  sread r0 tc=uint32
  buf_append_offset offsets r0
  read_bulk data tc=float64 count=r0
  ret r0
```

A dump change is allowed when it makes a test clearer; the dump is not part of the conformance contract.

## 9. Optimizer passes

Every pass states what it looks for, what it rewrites to, when it must not fire, and why it is worth having. The compiler runs a pass to a fixed point only when the pass declares itself monotone.

<!-- gen:opt-table -->
| Pass | Level | Finds | Rewrites to | Must not fire when | Value |
|---|---|---|---|---|---|
| `const_fold` | 1 | an arithmetic, unary, or comparison node whose operands are constants | the same node folded to one constant | a division by zero, or the signed minimum divided by -1, would be hidden | high |
| `const_branch` | 1 | an If or While whose condition is a constant | the arm the constant selects, or nothing when no arm can run | the condition is not a literal constant, or an arm has an effect the other does not | high |
| `tobject_fusion` | 2 | a skipped object header: a version, an unused id, a bits word that only decides whether two more bytes follow | SkipTObject, which reads the same bytes in one instruction | the id or the bits word is read anywhere else, or the conditional skip is not exactly two bytes | high |
| `dead_code` | 1 | an assignment whose local is never read | the statement list without that assignment | the node is a read, skip, buffer, state, reference, or string mutation | high |
| `condition_locals` | 2 | a local assigned once and read once, as the condition of a later If or While in the same body | the condition with the assigned value; the assignment is dropped | the value has a side effect, the local is assigned or read more than once, or a loop condition would re-evaluate a value that is not loop-stable | high |
| `literal_append` | 1 | BufferAppend whose value is a constant | BufferAppendConst, which needs no register | the constant is not representable in the buffer's dtype | small |
| `read_append_fusion` | 1 | BufferAppend of a StreamRead | ReadAppend, one instruction per scalars | the read value is used anywhere else | high |
| `bulk_fusion` | 2 | a unit-stride counted loop whose body is one fixed-size read, directly or through a call | one ReadBulk with the loop count as an operand | the body has another effect, the stride is not one, or the bound reads the loop variable | high |
| `bulk_until_fusion` | 2 | a loop that reads one fixed-size element and stops when the cursor reaches an end position | one ReadBulkUntil, which reads the rest of the entry in one dispatch | the condition is not `cursor < end`, the body has more than one effect, or the element size varies | high |
| `strided_fusion` | 2 | a flat run of reads of the same element separated by one constant gap | one ReadStrided with the element count as an operand | the gap is zero or not constant, the reads differ in buffer or typecode, or the run does not end on a read | high |
| `len_append_fusion` | 1 | an offsets buffer appended with the length of another buffer | BufferAppendLen, one instruction instead of a load and an append | the destination is not an OFFSETS buffer | medium |
| `offset_fusion` | 1 | an offsets buffer appended with the last element plus a delta | BufferAppendOffset | the buffer is not an OFFSETS buffer, or the addend is the buffer itself | high |
| `string_fusion` | 2 | a string length prefix, its bytes, and the offset it extends, in one body | one ReadString, which reads the prefix and the bytes in one dispatch | the length local is read or written elsewhere, the buffers are not a byte buffer and an offsets buffer, or the escape guard is missing | high |
| `skip_fusion` | 1 | adjacent Skip and SkipVersion statements, or a zero-byte Skip | one Skip covering both | a jump targets the second statement | medium |
| `dead_read_to_skip` | 1 | a StreamRead assigned to a local nothing reads | a Skip of the value's width | the read is fNBytes, a version, or a string, whose validation must still run | medium |
| `range_unroll` | 2 | a ForRange with constant bounds and a small body | the body repeated, with the loop variable folded to each value | the body is large, the count is large, the body writes the loop variable, or the variable is read after the loop | small |
| `dead_function` | 2 | a function no call reaches from the entry point | the program without it, with function ids renumbered | every function is reachable | high |
| `inline_small` | 2 | a call to a straight-line callee with one call site in its caller | the callee's body at the call site | the callee has more than one call site in the same function, calls anything, is recursive, or is longer than six statements | high |
| `jump_thread` | 2 | a jump whose target is another jump | a jump to the final target | the jump would cross a function boundary | small |
| `unreachable` | 1 | a statement after a Return or Throw in the same body | the body up to and including the terminator | a label still targets the dropped region | small |
| `layout_infer` | 2 | a run of at least two flat field steps, standalone or as the whole body of a counted loop | a ReadFields over an inferred layout, with the loop count as the object count | a field is conditional or variable-width, a bulk count is not constant, the run holds no value field, or the loop variable is read later | medium |
| `reserve` | 2 | a counted loop or a bulk, strided, or layout read that grows one buffer | a BufferReserve before the loop or the read | the count is not known before the read, or more than one buffer grows | small |
<!-- /gen:opt-table -->

The pass manager exposes `DETREE_OPT=0` to disable optimization and `DETREE_DUMP=ir` to print the program before and after each pass. A pass that moves neither instruction count nor benchmark time is a candidate for removal.

## 10. Lowering

1. **Verify** the IR.
2. **Desugar** `ForRange`, `CallStmt`, and `Assign` to the canonical node set.
3. **Linearize** structured control flow into basic blocks and labels; resolve labels to absolute instruction indices.
4. **Allocate registers**: one register per live local, reusing ranges where a linear scan proves they do not overlap. The result must fit in 255 registers.
5. **Emit** instructions using the tables in `spec/isa.toml`.
6. **Peephole** on the bytecode where a rewrite is cheaper after lowering: adjacent skips, jump threading, unreachable code.
7. **Validate** the resulting module; a failure is a compiler bug.

The lowering MUST be deterministic: the same IR produces the same bytes in every process and on every platform.

## 11. Versioning

`detree.ir.IR_VERSION` is `"0.2"`. The IR is a public contract for compilers. Additive node kinds and optional fields bump the minor version, and a change that adds nodes for new opcodes bumps it together with `isa_minor`; a breaking change bumps the major version and documents the migration in the changelog.

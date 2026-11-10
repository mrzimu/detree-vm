# Instruction Set

**Status: draft, ISA v0.2.** The opcode numbers in this document are generated from [isa.toml](../../spec/isa.toml); the prose is normative.

## 1. Design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Machine model | register machine | easy to validate, easy to fuse into superinstructions |
| Module granularity | one module per decoding program | sub-programs are static and configuration folds away |
| Control flow | flat jumps (`JMP`/`JZ`/`JNZ`/`BR_*`) | simple machine; the frontend lowers structured loops |
| Value model | 64-bit slots | sizes, counts, tags, and indices fit below `2^63` |
| Integer semantics | 64-bit two's complement, wrapping | identical in the Python package and the Rust machine |
| Boolean results | `0` / `1` | comparisons and `bool(...)` produce `0`/`1` |
| Floating point | pass-through only | floats are copied, never computed on |
| `READ_FIELDS` | flat layouts only | nested structures use `CALL` and fine-grained opcodes |
| Strings | interned ids | a register carries a `u32` id, never the bytes |
| Buffer checks | always on | a stream violation raises, never crashes the process |
| Byte-count frames | a stack in the machine | a counted object costs two instructions, and the machine can check the frame when the function returns |

## 2. Instruction encoding

Every instruction is 20 bytes, little-endian:

```cpp
struct Ins {
    uint8_t op;   // opcode
    uint8_t rd;   // destination register
    uint8_t ra;   // source register A
    uint8_t rb;   // source register B
    int64_t imm1; // immediate, buffer, slot, function, layout, message, typecode
    int64_t imm2; // jump target, second immediate, or stride
};
```

Operand notation:

| Symbol | Meaning |
|---|---|
| `rd`, `ra`, `rb` | register numbers (0–255) |
| `IMM` | immediate value in `imm1` |
| `LBL` | absolute instruction index in `imm1` |
| `BUF` | buffer id in `imm1` |
| `SLOT` | state slot id in `imm1` |
| `FUNC` | function id in `imm1` |
| `LAYOUT` | layout id in `imm1` |
| `MSG` | string id in `imm1` |
| `CONST` | string constant id in `imm1` |
| `TC` | typecode in `imm1` |

The immediates are 64-bit because a register is: a module carries masks such as `0x80000000` and the sentinel `-1`, and a 32-bit field cannot hold both without changing the value's meaning after widening.

## 3. Control and constants (0x00–0x0C)

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `HALT` | 0x00 | — | Stop execution and return the buffers. |
| `NOP` | 0x01 | — | No operation. |
| `LINE` | 0x02 | `IMM` | Record a source line (debug only). |
| `DEBUG_PRINT` | 0x03 | `MSG`, `ra` | Print a debug message and a value (disabled by default). |
| `CONST` | 0x04 | `rd`, `IMM` | `rd = IMM` (signed 64-bit). |
| `CONST_F` | 0x05 | `rd`, `IMM` | `rd = IEEE-754 bits` (pass-through only). |
| `MOV` | 0x06 | `rd`, `ra` | `rd = ra`. |
| `ZEXT32` | 0x07 | `rd`, `ra` | Zero-extend the low 32 bits. |
| `ZEXT16` | 0x08 | `rd`, `ra` | Zero-extend the low 16 bits. |
| `ZEXT8` | 0x09 | `rd`, `ra` | Zero-extend the low 8 bits. |
| `SEXT32` | 0x0A | `rd`, `ra` | Sign-extend the low 32 bits. |
| `SEXT16` | 0x0B | `rd`, `ra` | Sign-extend the low 16 bits. |
| `SEXT8` | 0x0C | `rd`, `ra` | Sign-extend the low 8 bits. |

## 4. Integer arithmetic (0x10–0x1F)

All operations wrap modulo `2^64`. Division and remainder truncate toward zero. Division or remainder by zero raises `DetreeError`.

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `ADD` | 0x10 | `rd`, `ra`, `rb` | `rd = ra + rb` |
| `SUB` | 0x11 | `rd`, `ra`, `rb` | `rd = ra - rb` |
| `MUL` | 0x12 | `rd`, `ra`, `rb` | `rd = ra * rb` |
| `DIV_S` | 0x13 | `rd`, `ra`, `rb` | signed division |
| `DIV_U` | 0x14 | `rd`, `ra`, `rb` | unsigned division |
| `MOD_S` | 0x15 | `rd`, `ra`, `rb` | signed remainder |
| `MOD_U` | 0x16 | `rd`, `ra`, `rb` | unsigned remainder |
| `NEG` | 0x17 | `rd`, `ra` | `rd = -ra` |
| `SHL` | 0x18 | `rd`, `ra`, `rb` | shift left; shift amount masked to 63 |
| `SHR_S` | 0x19 | `rd`, `ra`, `rb` | arithmetic shift right; amount masked to 63 |
| `SHR_U` | 0x1A | `rd`, `ra`, `rb` | logical shift right; amount masked to 63 |
| `AND` | 0x1B | `rd`, `ra`, `rb` | bitwise AND |
| `OR` | 0x1C | `rd`, `ra`, `rb` | bitwise OR |
| `XOR` | 0x1D | `rd`, `ra`, `rb` | bitwise XOR |
| `NOT` | 0x1E | `rd`, `ra` | bitwise complement |
| `NOT_B` | 0x1F | `rd`, `ra` | logical not, result `0`/`1` |

There are no floating-point arithmetic opcodes. A float typecode places its IEEE-754 bit pattern in the register, and a buffer append reinterprets it.

## 5. Comparison (0x20–0x29)

Every result is `0` or `1`.

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `CMP_EQ` | 0x20 | `rd`, `ra`, `rb` | `ra == rb` |
| `CMP_NE` | 0x21 | `rd`, `ra`, `rb` | `ra != rb` |
| `CMP_LT_S` | 0x22 | `rd`, `ra`, `rb` | signed `ra < rb` |
| `CMP_LE_S` | 0x23 | `rd`, `ra`, `rb` | signed `ra <= rb` |
| `CMP_GT_S` | 0x24 | `rd`, `ra`, `rb` | signed `ra > rb` |
| `CMP_GE_S` | 0x25 | `rd`, `ra`, `rb` | signed `ra >= rb` |
| `CMP_LT_U` | 0x26 | `rd`, `ra`, `rb` | unsigned `ra < rb` |
| `CMP_LE_U` | 0x27 | `rd`, `ra`, `rb` | unsigned `ra <= rb` |
| `CMP_GT_U` | 0x28 | `rd`, `ra`, `rb` | unsigned `ra > rb` |
| `CMP_GE_U` | 0x29 | `rd`, `ra`, `rb` | unsigned `ra >= rb` |

## 6. Control flow (0x30–0x3E)

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `JMP` | 0x30 | `LBL` | Unconditional jump. |
| `JZ` | 0x31 | `ra`, `LBL` | Jump if `ra == 0`. |
| `JNZ` | 0x32 | `ra`, `LBL` | Jump if `ra != 0`. |
| `BR_EQ` | 0x33 | `ra`, `rb`, `LBL` | Jump if `ra == rb`. |
| `BR_NE` | 0x34 | `ra`, `rb`, `LBL` | Jump if `ra != rb`. |
| `BR_LT_S` | 0x35 | `ra`, `rb`, `LBL` | Jump on signed less-than. |
| `BR_LE_S` | 0x36 | `ra`, `rb`, `LBL` | Jump on signed less-or-equal. |
| `BR_GT_S` | 0x37 | `ra`, `rb`, `LBL` | Jump on signed greater-than. |
| `BR_GE_S` | 0x38 | `ra`, `rb`, `LBL` | Jump on signed greater-or-equal. |
| `BR_LT_U` | 0x39 | `ra`, `rb`, `LBL` | Jump on unsigned less-than. |
| `BR_LE_U` | 0x3A | `ra`, `rb`, `LBL` | Jump on unsigned less-or-equal. |
| `BR_GT_U` | 0x3B | `ra`, `rb`, `LBL` | Jump on unsigned greater-than. |
| `BR_GE_U` | 0x3C | `ra`, `rb`, `LBL` | Jump on unsigned greater-or-equal. |
| `CALL` | 0x3D | `rd`, `FUNC`, `ra` | Call `FUNC` with `ra`; result in `rd`. |
| `RET` | 0x3E | `ra` | Return `ra` to the caller. |

A `CALL` opens a new register window. The callee's `reg_count` registers are private; the argument is copied into the callee's register 0 and the result is copied back to the caller's `rd`.

## 7. Stream access (0x40–0x4F)

Every access is bounds-checked against the current event's data. A violation raises `StreamUnderflow`.

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `SREAD` | 0x40 | `rd`, `TC` | Read one big-endian scalar of type `TC`. |
| `SREAD_FNB` | 0x41 | `rd` | Read an `fNBytes` field: validate the byte-count mask, return the masked value. |
| `SREAD_VERSION` | 0x42 | `rd` | Read the int16 `fVersion`. |
| `SREAD_CSTR` | 0x43 | `rd` | Read a NUL-terminated string, intern it, return its arena id. |
| `SKIP` | 0x44 | `IMM` | Advance the cursor by a constant. |
| `SKIP_R` | 0x45 | `ra` | Advance the cursor by a register value. |
| `SKIP_FNB` | 0x46 | — | Skip an `fNBytes` field, validating its mask. |
| `SKIP_VERSION` | 0x47 | — | Skip the int16 `fVersion`. |
| `SKIP_TOBJECT` | 0x48 | — | Skip a `TObject` header and its optional references. |
| `SKIP_OBJHDR` | 0x49 | — | Skip an object header: `fNBytes`, tag, and class name when new. |
| `SKIP_CSTR` | 0x4A | — | Skip a NUL-terminated string. |
| `CURSOR` | 0x4B | `rd` | `rd = cursor`. |
| `SET_CURSOR` | 0x4C | `ra` | `cursor = ra` (bounds-checked). |
| `ENTRIES` | 0x4D | `rd` | Number of entries in the basket. |
| `INIT_OFFSET` | 0x4E | `rd` | Initial cursor position of the stream (the key length). |
| `DATA_SIZE` | 0x4F | `rd` | Size of the current event's data. |

`SREAD_FNB` validates that the `kByteCountMask` (`0x40000000`) is set and returns the value with the mask cleared. A missing mask raises `InvalidByteCount`. `SKIP_OBJHDR` reads `fNBytes`; when the tag is `kNewClassTag` (`0xFFFFFFFF`) it also skips a NUL-terminated class name.

## 8. Byte-count frames (0x50–0x51)

A ROOT object framed by its byte count declares how many bytes follow it. These two opcodes keep the end position in the machine, so a decoder does not carry it in a register and the machine can check the frame when the function returns.

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `FNB_BEGIN` | 0x50 | — | Read a masked `fNBytes`, validating the mask, and push `cursor + nbytes`. |
| `FNB_END` | 0x51 | `MSG` | Pop the stack; fail unless `cursor` equals the popped value. |

`FNB_BEGIN` is the framed form of `SREAD_FNB`: it consumes the same four bytes and validates the same mask, and it also records where the counted object ends. A machine keeps one stack of end positions:

- `run` starts with an empty stack, and every event starts empty too;
- `FNB_BEGIN` past the call-depth limit (256) raises `StackOverflow`;
- `FNB_END` with an empty stack raises `DetreeError`;
- `FNB_END` with the cursor elsewhere raises `AssertionFailed`, with the module's `MSG` followed by `(expected N, got M)`;
- a return that would leave a frame open raises `DetreeError`: the machine records the stack depth at `CALL` and requires that depth back at `RET`. A function that opens a frame and returns before closing it is a decoder bug, not bad data.

The stack is machine state, not module state: two runs never share it, and `reset()` drops it. A module that never emits `FNB_BEGIN` is unaffected, because its depth is always zero.

## 9. Event offsets (0x60–0x62)

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `OFFSET_AT` | 0x60 | `rd`, `ra` | `rd = entry_offsets[ra]`. |
| `OFFSET_SEEK` | 0x61 | `rd`, `ra` | Smallest entry offset strictly greater than `ra`. |
| `EVENT_END` | 0x62 | `rd` | `rd = entry_offsets[event + 1]`. |

`OFFSET_SEEK` is the primitive that finds the end of a jagged array without a linear scan in the decoder.

## 10. Coarse-grained reads (0x70–0x75)

These are the performance tier. A machine SHOULD implement them as native loops, not as a dispatch per element.

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `READ_BULK` | 0x70 | `BUF`, `TC`, `ra` | Read `ra` consecutive elements into `BUF`. |
| `READ_STRIDED` | 0x71 | `BUF`, `TC`, `ra`, `rb` | Read `ra` elements spaced by `rb` bytes into `BUF`. |
| `READ_FIELDS` | 0x72 | `LAYOUT`, `ra` | Read `ra` flat objects of a layout into their buffers. |
| `READ_APPEND` | 0x73 | `BUF`, `TC` | Read one element and append it to `BUF`. |
| `READ_BULK_UNTIL` | 0x74 | `BUF`, `TC`, `ra` | Read elements until the cursor reaches `ra`, exactly. |
| `READ_STRING` | 0x75 | `BUF`, `BUF`, `rb` | Read a length-prefixed string and extend its offsets. |

`READ_STRING` reads the length prefix of a byte string -- one byte, or a four-byte count when that byte is `255` -- appends the bytes to the first buffer, and appends the end position to the second, an offsets buffer. Its `rb` operand selects the form of that offset: `0` appends `len(bytes)` after the append (a header-less string), `1` appends `offsets[-1] + size` (a string that keeps its size first).

`READ_BULK_UNTIL` is the counted form of a "read this element until the entry ends" loop: it reads `(ra - cursor) / sizeof(TC)` elements and raises `ReadLengthMismatch` when the remaining span is not a whole number of them, so a malformed basket fails where the loop would have overrun. It is the primitive an optimizer turns a fixed-size `while cursor < end` loop into.

`READ_BULK` and `READ_APPEND` convert the stream value to the buffer dtype. An integer buffer wraps modulo its width; a float buffer receives the IEEE-754 bit pattern of a float typecode, or a real value when the typecode is integral.

## 11. Buffer access (0x80–0x86)

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `BUF_APPEND` | 0x80 | `BUF`, `ra` | `BUF.append(ra)`. |
| `BUF_APPEND_CONST` | 0x81 | `BUF`, `IMM` | Append a constant. |
| `BUF_APPEND_OFFSET` | 0x82 | `BUF`, `ra` | `BUF.append(BUF[-1] + ra)`. |
| `BUF_APPEND_LEN` | 0x83 | `BUF`, `BUF` | `dst.append(len(src))`. |
| `BUF_LAST` | 0x84 | `rd`, `BUF` | `rd = BUF[-1]`. |
| `BUF_LEN` | 0x85 | `rd`, `BUF` | `rd = len(BUF)`. |
| `BUF_RESERVE` | 0x86 | `BUF`, `ra` | Reserve capacity for `ra` more elements. |

`BUF_APPEND_OFFSET` requires an `OFFSETS` buffer with at least one element. `BUF_LAST` on an empty buffer raises `DetreeError`. `BUF_RESERVE` has no effect on the output; it is a capacity hint.

## 12. Program state (0x90–0x92)

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `STATE_GET` | 0x90 | `rd`, `SLOT` | `rd = states[SLOT]`. |
| `STATE_SET` | 0x91 | `SLOT`, `ra` | `states[SLOT] = ra`. |
| `STATE_IADD` | 0x92 | `SLOT`, `IMM` | `states[SLOT] += IMM` (wrapping). |

## 13. Object references (0xA0–0xA5)

The reference table maps a `u32` tag to an object index and an optional class name. It is the primitive behind ROOT object graphs.

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `REF_HAS` | 0xA0 | `rd`, `ra` | `rd = (tag ra in refs)`. |
| `REF_GET_OBJ` | 0xA1 | `rd`, `ra` | Object index of a reference; missing tag raises. |
| `REF_GET_CLASS` | 0xA2 | `rd`, `ra` | Class-name id, or `-1`. |
| `REF_SET_OBJ` | 0xA3 | `ra`, `rb` | `refs[ra].object = rb`. |
| `REF_SET_CLASS` | 0xA4 | `ra`, `rb` | `refs[ra].class = rb`. |
| `REF_COUNT` | 0xA5 | `rd` | Number of entries. |

## 14. Strings (0xB0–0xB3)

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `STR_CONST` | 0xB0 | `rd`, `CONST` | Load an interned constant string id. |
| `STR_EQ` | 0xB1 | `rd`, `ra`, `rb` | String equality, result `0`/`1`. |
| `STR_STORE` | 0xB2 | `SLOT`, `ra` | Store a string id in a name slot. |
| `STR_LOAD` | 0xB3 | `rd`, `SLOT` | Load a string id from a name slot. |

The arena is per run and seeded with the module's constants in order. `-1` means "no string"; it is not a valid index.

## 15. Verification and errors (0xC0–0xC3)

| Opcode | Hex | Operands | Semantics |
|---|---|---|---|
| `ASSERT` | 0xC0 | `ra`, `MSG` | Fail unless `ra != 0`. |
| `ASSERT_CURSOR_EQ` | 0xC1 | `ra`, `MSG` | Fail unless `cursor == ra`. |
| `ASSERT_STR_EQ` | 0xC2 | `ra`, `rb`, `MSG` | Fail unless two string ids are equal. |
| `THROW` | 0xC3 | `MSG` | Raise an unconditional decoder error. |

## 16. Worked examples

### 16.1 A scalar decoder

Read one value per event and append it to `data`:

```text
  SREAD       r0, TC
  BUF_APPEND  data, r0
  RET         r0
```

### 16.2 A sequence decoder

Read a count, append the next offset, then read the elements in bulk:

```text
  SREAD              r0, UINT32
  BUF_APPEND_OFFSET  offsets, r0
  READ_BULK          data, TC, r0
  RET                r0
```

### 16.3 A jagged array

Find the next entry offset and let a child decoder read until it:

```text
  CURSOR         r0
  OFFSET_SEEK    r1, r0
  CALL           r2, child.read_until, r1
  BUF_APPEND_OFFSET offsets, r2
  RET            r2
```

### 16.4 A `TObject` header

```text
  SKIP_VERSION
  SREAD        r0, UINT32      ; fUniqueID
  SREAD        r1, UINT32      ; fBits
  CONST        r2, 0x10         ; kIsReferenced
  AND          r3, r1, r2
  JZ           r3, L1
  SREAD        r4, UINT16      ; pidf
  BUF_APPEND   pidf, r4
L1:
  BUF_APPEND   unique_id, r0
  BUF_APPEND   bits, r1
  BUF_LEN      r5, pidf
  BUF_APPEND   pidf_offsets, r5
  RET          r0
```

### 16.5 A counted object

The object declares its size, so the machine holds the end position while the members are read:

```text
  FNB_BEGIN
  SKIP_VERSION
  CALL         r0, member.read, r0
  FNB_END      "the members did not consume the byte count"
  RET          r0
```

`FNB_END` fails where the old sequence would have compared a register: the machine knows the expected offset, so the message can name both it and the cursor.

## 17. Performance requirements

The machine's cost is dominated by dispatch frequency. A compiler SHOULD keep hot loops inside coarse opcodes:

| Pattern | Opcode | Target |
|---|---|---|
| scalar arrays, `vector<primitive>` | `READ_BULK` | ≤ 1.5 ns/element |
| arrays of flat structs | `READ_FIELDS` | ≤ 2 ns/element |
| offset bookkeeping | `BUF_APPEND_OFFSET` | one instruction per element |
| single-element hot path | `READ_APPEND` | ≤ 2 ns/element |
| loop control | fused `BR_*` | one dispatch per iteration |
| buffer growth | `BUF_RESERVE` | avoid geometric reallocation |

A decoder that executes three or more instructions per element will be several times slower than a native decoder graph.

## 18. Reserved opcodes

The following ranges are reserved and MUST NOT be emitted in v0.2:

`0x0D–0x0F`, `0x2A–0x2F`, `0x3F`, `0x52–0x5F`, `0x63–0x6F`, `0x76–0x7F`, `0x87–0x8F`, `0x93–0x9F`, `0xA6–0xAF`, `0xB4–0xBF`, `0xC4–0xFF`.

A module that contains an opcode outside the v0.2 table is invalid and MUST be rejected at validation with `DETREE_MODULE_ERROR`. A machine does not guess the width or meaning of an opcode it does not know.

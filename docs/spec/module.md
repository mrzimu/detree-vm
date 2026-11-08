# Module Format

**Status: draft, ISA v0.1.**

A **module** is the unit of compilation, caching, and distribution. It contains everything a machine needs to execute a decoding program: code, buffer declarations, state declarations, string constants, layouts, and the function table. A machine receives module **bytes** and nothing else.

The format is canonical and deterministic: two structurally identical programs MUST produce byte-identical modules. That is what makes deduplication by content hash possible and what makes the golden corpus meaningful.

## Primitive encodings

All integers are **little-endian**.

| Name | Encoding |
|---|---|
| `u8`, `u16`, `u32` | unsigned little-endian integer of that width |
| `i32`, `i64` | two's-complement little-endian integer |
| `text` | `u16` byte length followed by that many UTF-8 bytes |

`text` is bounded by `u16` (65 535 bytes). A name may be non-ASCII; it is still length-prefixed bytes, not a NUL-terminated string.

## Layout

```text
offset 0     magic         4 bytes, "DETR"
             isa_major     u16
             isa_minor     u16
             entry         u16      function id executed once per event
             regs_max      u16      upper bound used for validation

             n_buffers     u32
             n_states      u32
             n_strings     u32
             n_layouts     u32
             n_funcs       u32

buffers[]    text name              structural path, e.g. "root/items:offsets"
             text dtype             one of the buffer dtypes below
             u8   kind              0 DATA, 1 OFFSETS, 2 COUNT
             u32  n_init
             i64  init[n_init]      seeds the buffer before the first event

states[]     text name
             i64  init
             u8   flags             bit 0: a name slot (holds a string-arena id)

strings[]    text                    class names and message templates

layouts[]    u32  id
             u32  n_fields
             { u8 kind, u8 typecode, i32 count, i32 buffer_id }[n_fields]

funcs[]      u32  id
             text name
             u8   arity             0 for read(), 1 for the bulk entry points
             u16  reg_count
             u16  reserved          written as 0
             u32  code_off
             u32  code_len

code[]       u32  n_code
             { u8 op, u8 rd, u8 ra, u8 rb, i64 imm1, i64 imm2 }[n_code]
```

An instruction is 20 bytes. The opcode table in [isa.toml](../../spec/isa.toml) defines `op`; [Instruction Set](isa.md) defines the operands.

## Buffers

A buffer is a plain, growable, append-only array of one fixed-width element type. Its representation is not part of the format: a machine may use a typed `array.array`, a `std::vector<T>`, a Rust `Vec<T>`, or anything else, as long as the exposed bytes match the declared dtype in native byte order.

`dtype` is a portable name, not a platform type:

```text
bool
uint8 uint16 uint32 uint64
int8  int16  int32  int64
float32 float64
```

`kind` describes how the buffer is used, not how it is stored:

| Kind | Value | Meaning |
|---|---|---|
| `DATA` | 0 | element storage |
| `OFFSETS` | 1 | int64 offsets; initialised with a single `0` |
| `COUNT` | 2 | int64 counters |

An `OFFSETS` buffer MUST be declared with dtype `int64`. A `COUNT` buffer MUST be declared with dtype `int64`. A `DATA` buffer may use any dtype.

`init` seeds the buffer before the first event. An offsets buffer normally has `init = (0,)`, which makes `BUF_APPEND_OFFSET` a one-instruction update.

## States

A state slot is a 64-bit value that survives across events. `flags` bit 0 marks a **name slot**: the slot holds a string-arena id, and `-1` means "no string". Every other bit MUST be zero in v0.1.

A name slot is why the flag is in the format rather than in the frontend: a machine has to know how to decode the value it reports at the end of a run.

## Strings

`strings[]` holds class names and message templates. The arena is seeded with these constants in order, so `STR_CONST rd, k` is the constant at index `k`. `SREAD_CSTR` interns what it reads; `STR_EQ` is therefore an id comparison.

A string id is a `u32` in a register. `-1` means "no string" and MUST NOT be used as an index.

## Layouts

A layout is a flat sequence of fields for `READ_FIELDS`. `field.kind` is one of:

| Kind | Value | Meaning |
|---|---|---|
| `SCALAR` | 0 | read one scalar into `buffer_id` |
| `SKIP` | 1 | skip `count` bytes |
| `ARRAY_FIXED` | 2 | read `count` scalars into `buffer_id` |
| `TOBJECT_SKIP` | 3 | skip a `TObject` header |

A `buffer_id` of `-1` means "not a buffer". `SCALAR` and `ARRAY_FIXED` MUST reference an existing buffer. `TOBJECT_SKIP` is reserved in v0.1; executing a layout that contains it MUST raise `UnsupportedFeature`.

## Functions

A function has an arity of 0 or 1:

- arity 0: `read()` — called once per event by the entry function;
- arity 1: `read_many(count)`, `read_until(end_pos)`, and `read_many_memberwise(count)` — the bulk entry points.

`reg_count` is the size of the function's register window. A machine MUST reject an instruction that references a register at or above it.

A function's code is the slice `[code_off, code_off + code_len)`. The slice MUST be non-empty, MUST lie inside `code[]`, and MUST NOT overlap another function's slice. `reserved` MUST be written as zero and ignored on read.

## Identifiers and determinism

Ids are assigned by a deterministic traversal of the program: the entry function first, then its callees in declaration order, buffers and states in declaration order, strings in constant order. Ids MUST NOT be derived from `hash(obj)`, from a process-specific value, or from a branch name.

Two programs with the same structure MUST produce the same ids and therefore the same bytes. A module's `content_hash()` is the SHA-256 of `to_bytes()`.

## Validation

A machine MUST validate a module before executing it. At minimum:

1. `magic` equals `"DETR"`; `isa_major` equals the machine's major version.
2. `entry` names an existing function; every function id equals its index.
3. Every buffer name is unique; every dtype is known; every kind is valid.
4. Every state name is unique; only bit 0 of `flags` is set.
5. Every layout field references an existing buffer where required.
6. Every function has arity 0 or 1, a non-empty code slice, and a code slice inside `code[]`.
7. Every register operand is below the owning function's `reg_count`.
8. Every jump target is inside the owning function.
9. Every referenced buffer, state, function, layout, string, and message exists.
10. Every typecode is known.

A module that fails validation MUST be rejected with `DETREE_MODULE_ERROR` before a single basket byte is read. Validation is a static property; no basket can turn a valid module into an invalid one.

## Compatibility

- A machine MUST reject a module with a different `isa_major`.
- A machine SHOULD reject a module with a newer `isa_minor`; it cannot know the semantics of an opcode added after it was built.
- A new opcode bumps `isa_minor`. A change to an existing opcode's number, operand shape, or meaning bumps `isa_major`.
- Adding a buffer dtype, state flag, or layout field kind is a minor change if existing modules remain valid, and a major change otherwise.

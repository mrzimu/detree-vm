# Machine Model

**Status: draft, ISA v0.1.**

A **machine** is built once from a module's bytes and then reads basket after basket. It owns the per-basket event loop; bytecode never sees the loop.

## 1. Runtime state

```text
machine
├── regs[0..R)        64-bit slots, one window per call frame
├── frames            (function id, return ip, return register)
├── stream            data[], entry_offsets[], cursor, initial_cursor, event_end
├── buffers[]         {dtype, kind, storage, count}
├── states[]          64-bit scalar state per decoder instance
├── refs              map<u32, {object_index, class_name_id}>
└── strings           constant pool plus the per-run arena
```

A machine is not thread-safe. Registers, the cursor, the frames, the buffers, the states, the references, and the arena are instance state. One machine belongs to one thread.

## 2. Values

A register holds a 64-bit slot.

- Integer opcodes interpret it as signed or unsigned as the opcode says.
- Integer arithmetic wraps modulo `2^64`; division and remainder truncate toward zero.
- Comparisons produce `0` or `1`.
- A float typecode stores its IEEE-754 bit pattern in the low 32 or 64 bits. Floats are only moved: there is no floating-point arithmetic.
- A string is a `u32` arena id. `-1` means "no string".

The compiler knows the static type of every expression. The machine does not need to track it.

## 3. Buffers

A buffer is append-only during a run. It has a declared dtype, a kind, and an initial sequence.

- `DATA` holds elements.
- `OFFSETS` holds integer offsets and normally starts with a single `0`.

After a run, each buffer is exposed as a one-dimensional array of its declared dtype. The machine adopts a representation that can be viewed without copying where the language binding allows it; the Python binding uses NumPy views over the machine's storage.

A machine MUST allocate **fresh** buffer storage on `reset()`. A caller may still hold an array from the previous run; reusing storage would rewrite a finished basket's data.

## 4. States

State slots survive across events. A slot with the name-slot flag holds a string-arena id; at the end of a run it is decoded to a string or `None` (`-1`). Every other slot is a signed 64-bit integer.

States are reset to their declared initial values by `reset()`.

## 5. References

The reference table maps a `u32` tag to an object index and an optional class name id. It is shared by all functions of a module and reset per run.

## 6. Strings

The arena is seeded with the module's string constants in order, so constant index `k` is a stable id. `SREAD_CSTR` interns the bytes it reads. Ids are valid only for the current run; they MUST NOT be compared across runs.

## 7. Calling convention

Every function has arity 0 or 1.

- Arity 0: `read()`.
- Arity 1: `read_many(count)`, `read_until(end_pos)`, `read_many_memberwise(count)`.

`CALL rd, FUNC, ra` passes `ra` as the callee's register 0 and stores the callee's `RET ra` value in the caller's `rd`. A callee has its own register window; the machine MUST NOT let it address the caller's registers.

The call depth is bounded. Exceeding the bound raises `StackOverflow`. The recommended bound is 256 frames.

## 8. Event loop

The machine owns the loop; bytecode never sees it.

```text
cursor = initial_cursor
for event in range(entries):
    event_end = entry_offsets[event + 1]
    call the entry function
    if cursor != event_end:
        raise ReadLengthMismatch
```

`initial_cursor` is the cursor offset passed to `run` (the key length). The entry function is called once per event.

This shape keeps the caller/machine boundary at one call per basket and lets each event be checked independently. A machine MUST check the final cursor against the event boundary; a decoder that under-reads or over-reads is a bug in the module, not a silent success.

## 9. Machine reuse and reset

`run(data, offsets, cursor_offset)` starts with `reset()`. No buffer, state slot, reference, or interned string survives into the next basket.

`reset()` restores the state just after construction:

- buffers are empty and seeded with their declared `init`;
- states hold their declared initial values;
- the reference table is empty;
- the arena holds only the module's constants;
- the cursor is unset until `run`.

`reset()` allocates fresh buffer storage rather than clearing the old storage. The arrays a finished run handed out are still owned by the caller; reusing the storage would rewrite them.

`reset()` is public so a caller can inspect or discard a run it did not consume.

## 10. Input and output lifetime

- `data` and `offsets` are read-only for the duration of `run`. The machine MUST NOT retain them after `run` returns.
- Output arrays are valid until the caller drops them. The C ABI expresses this with an owner handle: a caller that needs an output beyond the next `run` acquires the owner and releases it when done.
- The Python binding keeps the owner alive through the array's base object.

## 11. Validation

A machine MUST decode and validate a module before executing it. The checks are listed in [Module Format](module.md) §Validation and [Instruction Set](isa.md) §17.

Validation is static: no basket can make a valid module invalid. A machine that cannot decode a module MUST fail before reading any basket byte.

## 12. Errors

All errors carry the instruction index, the cursor position (when known), and a message id. The Python classes and the C statuses have the same meaning.

| Error | Raised when |
|---|---|
| `ModuleError` | the module does not decode or does not validate |
| `StreamUnderflow` | a read or skip would leave the event's data |
| `InvalidByteCount` | an `fNBytes` field lacks `kByteCountMask` |
| `ReadLengthMismatch` | the cursor is not at the event boundary after `read` |
| `UnsupportedFeature` | a reserved opcode or layout field is executed |
| `AssertionFailed` | an `ASSERT*` or `THROW` opcode fails |
| `StackOverflow` | the call depth limit is exceeded |
| `DetreeError` | any other runtime failure (division by zero, empty buffer, ...) |

A runtime error MUST NOT crash the process. A machine that cannot report an error through its language binding MUST abort the run and leave the outputs in a consistent state.

## 13. Determinism

Given the same module bytes, the same basket bytes, the same offsets, and the same cursor offset, every conformant machine MUST produce byte-identical buffer contents and the same state values. Error identity (status and instruction index) MUST also match for the cases in the golden corpus.

The machine has no randomness, no wall-clock dependency, and no platform-dependent integer width. Byte order is the only platform property that affects the exposed buffer bytes, and it is fixed to little-endian by the corpus.

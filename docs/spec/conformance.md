# Conformance

**Status: draft, ISA v0.2.**

A machine is conformant when it replays every case in the golden corpus with the recorded status, instruction index, cursor, buffers, and states, and when its C ABI matches the declared layout. This document defines the levels, the corpus, and the checks a machine must pass.

## 1. Levels

| Level | Name | What it must pass |
|---|---|---|
| **L0** | Decoder | parse every module field exactly; reject every malformed module with `DETREE_MODULE_ERROR` |
| **L1** | Core machine | event loop, buffers, states, arithmetic, comparison, control flow, scalar stream ops |
| **L2** | Full machine | L1 plus strings, references, layouts, bulk reads, every opcode, the full error taxonomy |
| **L3** | Conformant | L2 plus byte-identical replay of the full corpus and the C ABI layout checks |

A backend declares its level in `detree.backends`. The default selection only considers backends at L3. A backend below L3 can be selected explicitly for testing.

## 2. Golden corpus

The corpus lives in `tests/data/*.dtg`. Each file contains one module, one basket, the entry offsets, the cursor offset, the expected status, and the expected outputs.

All integers are little-endian. `text` is a `u16` byte length followed by UTF-8 bytes.

```text
magic           4 bytes, "DTGL"
version         u8, currently 1
module_len      u32
module          module_len bytes
data_len        u32
data            data_len bytes
n_offsets       u32
offsets         i64[n_offsets]
cursor_offset   u64
expected_status u8
reserved        u8[3] = 0
expected_ip     i64   (-1 when not applicable)

n_buffers       u32
buffers[]       text name
                text dtype
                u64  count
                u64  byte_len
                bytes[byte_len]

n_states        u32
states[]        text name
                u8   is_name
                u64  value
                text text       (empty when the slot holds no string)
```

The corpus is committed with the format and is never written by a build under test. `tools/golden.py --check` fails when the committed files are stale.

Element bytes are stored in native byte order. The corpus is defined for little-endian platforms; `tools/golden.py` refuses to write on a big-endian platform.

## 3. Coverage

Every opcode family MUST appear in at least one case. The minimum set is:

| Family | Cases |
|---|---|
| constants and moves | `CONST`, `CONST_F`, `MOV`, `ZEXT*`, `SEXT*` |
| arithmetic | every operator, plus division by zero |
| comparison | signed, unsigned, equality |
| control flow | `JMP`, `JZ`, `JNZ`, every `BR_*`, `CALL`, `RET`, recursion depth |
| scalar stream | every typecode, `SREAD_FNB`, `SREAD_VERSION`, `SREAD_CSTR` |
| skips | `SKIP`, `SKIP_R`, `SKIP_FNB`, `SKIP_VERSION`, `SKIP_TOBJECT`, `SKIP_OBJHDR`, `SKIP_CSTR` |
| byte-count frames | `FNB_BEGIN`, `FNB_END`, a nested pair, a mismatch, an unmatched end, a return with a frame open |
| cursor | `CURSOR`, `SET_CURSOR`, `ENTRIES`, `INIT_OFFSET`, `DATA_SIZE` |
| offsets | `OFFSET_AT`, `OFFSET_SEEK`, `EVENT_END` |
| bulk | `READ_BULK`, `READ_STRIDED`, `READ_FIELDS`, `READ_APPEND` |
| buffers | every `BUF_*` opcode, including an empty `BUF_LAST` error |
| state | `STATE_GET`, `STATE_SET`, `STATE_IADD` |
| references | every `REF_*` opcode, including a missing tag |
| strings | `STR_CONST`, `STR_EQ`, `STR_STORE`, `STR_LOAD`, `-1` name slot |
| verification | `ASSERT`, `ASSERT_CURSOR_EQ`, `ASSERT_STR_EQ`, `THROW` |

The corpus SHOULD also include one real basket per supported TTree construct (primitive scalar, primitive array, variable-length sequence, jagged array, `TString`, object reference). These cases are the regression net for the decoder logic, not for the opcode coverage.

## 4. Negative cases

A conformant machine must reject or fail with the same status and instruction index as the reference:

- truncated module header, truncated table, trailing bytes;
- bad magic, unsupported major, newer minor;
- unknown opcode, register out of range, jump outside the function;
- buffer dtype unknown, layout field out of range, missing function id;
- stream underflow at a scalar read, a skip, and a bulk read;
- invalid `fNBytes` mask;
- read-length mismatch at the event boundary;
- assertion failure and unconditional throw;
- stack overflow.

A negative case that crashes the process instead of returning a status is a conformance failure, not a crash to be reported.

## 5. Differential testing

Beyond the fixed corpus, a development build may generate random modules within the validated subset and compare a machine against a development-only oracle: same buffers, same states, same status. The generator MUST NOT produce an invalid module for the negative run; invalid modules are a separate, hand-written corpus.

Differential testing is a development tool, not part of the conformance contract: L3 is defined by the corpus, the C ABI layout, and the error taxonomy. A divergence should be reduced to a minimal module before it becomes a regression case. The reducer is a test tool, not part of the public API.

## 6. C ABI layout

`tools/abi_layout.c` includes `detree.h` and prints the size and every field offset of `detree_error`, `detree_output`, and `detree_state`. The test suite compiles it, runs it, and compares the output with the `ctypes` structures in `detree.abi.capi`. A mismatch is a failure.

The same test loads the built machine and checks:

- `detree_c_api_version()` equals `DETREE_C_API_VERSION`;
- `detree_backend_name()` is a stable, non-empty string;
- `detree_isa_major()`/`detree_isa_minor()` match the module header;
- a machine can be created, run, and freed in a loop without leaking owners.

## 7. Porting checklist

A new backend is complete when every item is checked:

1. Module decoder: all fields, truncation, trailing bytes, determinism.
2. Validator: the ten checks in [Module Format](module.md) §Validation.
3. Event loop: entry offsets, `initial_cursor`, the event-boundary check, reset.
4. Registers and frames: windows, call depth, stack overflow.
5. Integer semantics: wrapping, signed/unsigned division, shifts, masks.
6. Stream: every typecode, `fNBytes`, `fVersion`, C strings, every skip.
7. Buffers: every dtype, every kind, append, last, length, reserve.
8. States, references, strings, layouts.
9. Errors: status, instruction index, cursor, message.
10. C ABI: every symbol, every struct layout, every lifetime rule.
11. Golden corpus: byte-identical outputs and states.
12. Differential fuzzing: no divergence.
13. Benchmark: report per-basket time.

## 8. Reporting

`tools/bench.py` reports the time per basket for the shipping machine, measured through two harnesses:

- **integration**: Python `ctypes` calls, one `run` per basket, machine built once — the shape a Python caller sees;
- **native**: a C driver calls `detree_read` directly — the machine's own cost, without the Python FFI.

A ratio from one harness MUST NOT be compared with a ratio from the other.

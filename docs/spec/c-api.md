# C ABI

**Status: draft, C ABI version 1.** The canonical header is [`kernel/include/detree.h`](../../kernel/include/detree.h). This document explains the contract; the header is the source of truth for signatures and structure layouts.

The ABI is deliberately small: a machine handle, one call per basket, one call to take the outputs, one to read the states, and a thread-local error record.

## 1. Rules

1. **Nothing throws.** Every fallible call returns a `detree_status`, a count, or `NULL`. The reason is read with `detree_last_error()`, which the next failing call on that thread overwrites.
2. **Borrowed pointers have a stated lifetime.** Buffer bytes, names, and dtype strings belong to the machine until its next `run`, `reset`, or `free`. A caller that needs them longer calls `detree_acquire_outputs()` and keeps the `detree_owner`.
3. **One call per basket on the hot path.** `detree_read()` runs the basket and takes every output in one crossing. Each crossing costs a GIL release and re-acquire in Python, so two crossings are measurably worse for small baskets.
4. **One machine per thread.** The machine's registers, cursor, buffers, and states are instance state. `detree_last_error()` is thread-local.

## 2. Version and identity

| Symbol | Meaning |
|---|---|
| `DETREE_C_API_VERSION` | bumped when a struct layout or a symbol meaning changes |
| `detree_c_api_version()` | the version this library was built with |
| `detree_backend_name()` | `"cpp"`, `"rust"`, or another stable name |
| `detree_backend_version()` | the implementation's own version string |
| `detree_isa_major()` / `detree_isa_minor()` | the ISA version the machine implements |
| `detree_capabilities()` | a bitmask of optional features |

Capability bits:

| Bit | Name | Meaning |
|---|---|---|
| 0 | `DETREE_CAP_STREAMS` | scalar stream reads, skips, cursor operations |
| 1 | `DETREE_CAP_BULK` | `READ_BULK`, `READ_STRIDED`, `READ_FIELDS`, `READ_APPEND` |
| 2 | `DETREE_CAP_STRINGS` | the string arena and `STR_*` opcodes |
| 3 | `DETREE_CAP_REFS` | the reference table and `REF_*` opcodes |
| 4 | `DETREE_CAP_LAYOUTS` | flat `READ_FIELDS` layouts |

A backend that implements every v0.1 opcode returns `DETREE_CAP_ALL`.

## 3. Types

```c
typedef struct detree_machine detree_machine;  /* opaque */
typedef struct detree_owner detree_owner;      /* retained buffer storage */

typedef enum detree_status {
    DETREE_OK = 0,
    DETREE_MODULE_ERROR = 1,
    DETREE_STREAM_UNDERFLOW = 2,
    DETREE_INVALID_BYTE_COUNT = 3,
    DETREE_READ_LENGTH_MISMATCH = 4,
    DETREE_UNSUPPORTED_FEATURE = 5,
    DETREE_ASSERTION_FAILED = 6,
    DETREE_STACK_OVERFLOW = 7,
    DETREE_ERROR = 8,
    DETREE_BAD_ARGUMENT = 9
} detree_status;
```

`detree_error` is the last failure on the calling thread:

```c
typedef struct detree_error {
    int32_t status;
    int32_t reserved;
    int64_t ip;          /* instruction index, or -1 */
    int64_t cursor;      /* stream offset, or -1 */
    const char* message; /* borrowed, UTF-8, not NUL-terminated */
    size_t message_length;
    const char* function; /* borrowed function name, or NULL */
    size_t function_length;
} detree_error;
```

`message` and `function` are borrowed until the next failing call on that thread.

`detree_output` is one buffer:

```c
typedef struct detree_output {
    const void* data;    /* borrowed element bytes, native byte order */
    size_t length;       /* element count */
    size_t itemsize;     /* bytes per element */
    detree_owner* owner; /* retained; release with detree_owner_release */
} detree_output;
```

`detree_state` is one state slot:

```c
typedef struct detree_state {
    const char* name;    /* borrowed, machine-owned */
    size_t name_length;
    int32_t is_name;     /* nonzero: a name slot, read `text` */
    uint32_t reserved;
    uint64_t value;      /* the 64-bit value (the arena id for a name slot) */
    const char* text;    /* borrowed string, or NULL */
    size_t text_length;
} detree_state;
```

## 4. Functions

### 4.1 Construction and lifecycle

```c
detree_machine* detree_machine_new(const uint8_t* module, size_t size);
void detree_machine_free(detree_machine* machine);
void detree_machine_reset(detree_machine* machine);
uint16_t detree_machine_isa_major(const detree_machine* machine);
uint16_t detree_machine_isa_minor(const detree_machine* machine);
```

`detree_machine_new` decodes and validates the module. It returns `NULL` on failure; `detree_last_error()` carries `DETREE_MODULE_ERROR`. The machine does not retain the module pointer; the bytes are copied or decoded before return.

### 4.2 Buffer introspection

```c
size_t detree_buffer_count(const detree_machine* machine);
const char* detree_buffer_name(const detree_machine* machine, size_t index,
                               size_t* length);
const char* detree_buffer_dtype(const detree_machine* machine, size_t index,
                                size_t* length);
```

The names and dtype strings are borrowed for the machine's lifetime. `index` must be below `detree_buffer_count`.

### 4.3 Running one basket

```c
detree_status detree_run(detree_machine* machine,
                         const uint8_t* data, size_t size,
                         const int64_t* offsets, size_t n_offsets,
                         size_t cursor_offset);

detree_status detree_read(detree_machine* machine,
                          const uint8_t* data, size_t size,
                          const int64_t* offsets, size_t n_offsets,
                          size_t cursor_offset,
                          detree_output* outputs, size_t output_capacity,
                          size_t* outputs_written);
```

`detree_run` executes every event and leaves the outputs to be acquired. `detree_read` is the hot path: it runs the basket and fills `outputs` in one call. Each written output owns a retained storage; the caller must release every one it received with `detree_owner_release`.

`offsets` is the cumulative entry-offset array, at least two elements. The machine reads `offsets[event]` and `offsets[event + 1]`. `cursor_offset` is the key length used for object references.

### 4.4 Outputs and states

```c
size_t detree_acquire_outputs(detree_machine* machine,
                              detree_output* out, size_t capacity);
void detree_owner_release(detree_owner* owner);

size_t detree_state_count(const detree_machine* machine);
const char* detree_state_name(const detree_machine* machine, size_t index,
                              size_t* length);
size_t detree_get_states(const detree_machine* machine,
                         detree_state* out, size_t capacity);
detree_error detree_last_error(void);
```

`detree_acquire_outputs` returns `min(capacity, detree_buffer_count)`. Each returned output retains its storage; `detree_owner_release(NULL)` is a no-op.

`detree_get_states` fills up to `capacity` slots and returns how many were written. A name slot reports `is_name = 1` and a borrowed `text`; an empty name slot reports `text = NULL`, `text_length = 0`.

## 5. Lifetime summary

| Object | Borrowed until |
|---|---|
| output `data` | the next `detree_run` / `detree_machine_reset` / `detree_machine_free` on the machine |
| output `data` after `detree_acquire_outputs` | the matching `detree_owner_release` |
| buffer names and dtypes | `detree_machine_free` |
| state names and text | the next `detree_run` / `detree_machine_reset` |
| error `message` and `function` | the next failing call on the thread |

## 6. Threading

A machine is not thread-safe. `detree_last_error()` is thread-local. Two threads that share one machine MUST serialize every call on it; two threads that read different branches SHOULD use different machines.

The compiled libraries do not depend on a Python ABI. They can be loaded from C, Rust, Python (`ctypes`), or any other FFI, and one build serves every interpreter.

## 7. Embedding sequence

A minimal C caller:

1. `detree_machine_new(module_bytes, module_size)`; check `NULL`.
2. `detree_buffer_count()` and `detree_buffer_name()` to learn the output names.
3. For each basket: `detree_read(machine, data, size, offsets, n_offsets, cursor_offset, outputs, capacity, &written)`.
4. Read the bytes of each output; copy them, or keep the owner until done.
5. `detree_owner_release(owner)` for every output that was written.
6. `detree_get_states()` to read the final state values.
7. `detree_machine_free(machine)`.

The Python binding performs exactly this sequence, with one difference: it adopts an output as a NumPy view whose base object releases the owner when the array is collected.

## 8. ABI checks

Every backend must pass a layout test that compares `sizeof` and `offsetof` of the C structures with the Python `ctypes` definitions. The test compiles `tools/abi_layout.c` against the header and compares its output with `detree.abi.capi`. A drift is a test failure, not a warning.

# Embedding

**Status: guide.**

Three ways to use a machine, all through the same module bytes and the same contracts. Install the release with `pip install detree-vm`; the wheel carries the Rust library and the C header.

## 1. From C

`python -m detree paths` prints the header directory and the library file installed by the wheel; add them to the compiler and linker search paths.

```c
#include "detree.h"

detree_machine* machine = detree_machine_new(module, module_len);
if (!machine) {
    detree_error error = detree_last_error();
    fprintf(stderr, "%.*s\n", (int) error.message_length, error.message);
    return 1;
}

size_t n_buffers = detree_buffer_count(machine);
detree_output outputs[8];
size_t written = 0;

detree_status status = detree_read(
    machine, data, data_len, offsets, n_offsets, cursor_offset,
    outputs, 8, &written);

if (status != DETREE_OK) {
    detree_error error = detree_last_error();
    fprintf(stderr, "run failed at ip %lld: %.*s\n",
            (long long) error.ip, (int) error.message_length, error.message);
} else {
    for (size_t i = 0; i < written; ++i) {
        consume(outputs[i].data, outputs[i].length * outputs[i].itemsize);
    }
}

for (size_t i = 0; i < written; ++i) detree_owner_release(outputs[i].owner);
detree_machine_free(machine);
```

Rules:

- check every `NULL` and every non-`OK` status;
- copy an output before the next `run`, or keep its owner;
- one machine per thread;
- `detree_last_error()` is thread-local and overwritten by the next failure.

## 2. From Python

```python
import detree

with detree.Machine(module_bytes) as machine:
    outputs = machine.run(data, offsets, cursor_offset=key_length)
for name, array in outputs.items():
    consume(name, array)
```

`detree.Machine` decodes and validates the module once and resets itself before every basket. The instance belongs to the caller: build one per thread, and the machine is the Rust library shipped in the wheel.

## 3. From Rust

The crate is an internal artifact and is not published on crates.io (ADR 0009). A Rust embedder builds the cdylib from the repository and calls the same C ABI:

```rust
extern "C" {
    fn detree_machine_new(module: *const u8, len: usize) -> *mut core::ffi::c_void;
    fn detree_read(/* ... */) -> u32;
}
```

The `#[repr(C)]` structs and the meaning of every symbol are defined by `kernel/include/detree.h`, which the release wheel installs as `detree/include/detree.h`; `detree.include_dir()` returns that directory.

## 4. The machine that runs

The wheel contains one machine, the Rust `libdetree_rust`, and `program.run` uses it by default. Embedding in a C or Rust program means linking that same library from the wheel.

## 5. Lifetime checklist

- The module bytes must outlive `machine_new` only.
- The basket bytes must outlive the `run`/`detree_read` call only.
- Output bytes are borrowed until the next `run`, unless an owner is retained.
- State text is borrowed until the next `run`.
- A machine is not thread-safe; build one per thread that decodes.

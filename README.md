# Detree-VM

<!-- intro:start -->
Detree-VM is a domain-specific virtual machine for deserializing TTree binary streams into arrays. Its core machine is implemented in Rust, with a Python layer that exposes the compiler side: the tree IR, the optimizer, and lowering to module bytes.

## Why a virtual machine

Decoding TTree trades flexibility against performance and shipping convenience. A pure-Python decoder is easy to extend but slow on complex layouts; a C++ extension is fast, but it must be compiled against one upstream build, so downstream packages end up pinning each other. A domain-specific virtual machine keeps most of the flexibility for a small performance cost: the decoding program is data, and neither side has to be compiled into the other.

An Awkward Forth virtual machine already addresses the same problem and performs well, but Forth cannot express ROOT's reference pointers, so some decoding paths remain out of reach. Detree-VM closes that gap: a dedicated virtual machine handles those pointers directly, and covers the rest of TTree deserialization wherever it needs specialized treatment.
<!-- intro:end -->

## AI-led development

Detree is developed primarily by AI agents under human review. The normative specification in `spec/` and the agent guide in `AGENTS.md` are the primary sources of truth; design notes and implementation notes explain them but do not override them.

## Install

```bash
pip install detree-vm
```

The wheel ships the Rust machine and the C header. `python -m detree backends` reports what is available.

## What's included

- **`detree.spec`** — the module codec, the validator, the error taxonomy, and the ISA tables generated from `spec/isa.toml`, so the compiler side and the Rust machine agree on every opcode.

- **`detree.ir`** — a typed tree IR and a deterministic `ProgramBuilder` for functions, buffers, states, and structured control flow.

- **`detree.opt`** — optimizer passes over that IR: constant folding, read and append fusion, bulk and skip fusion, inlining, dead-code removal, and loop lowering.

- **`detree.lower` and `detree.compile`** — linearization, register allocation, and bytecode emission; the same IR lowers to byte-identical module bytes.

- **Rust machine** — the virtual machine itself, in `kernel/rust/`: a plain cdylib with no Python dependency, loaded through the C ABI and shipped in the wheel.

- **`detree.abi` and `detree.backends`** — the `ctypes` binding to the C ABI, the backend registry and selection policy, and NumPy views over the machine's typed buffers. The shipped backend is Rust.

- **Command line** — `python -m detree backends` reports the available backend; `python -m detree paths` prints the compiled library and C header locations; `python -m detree disasm` prints a module's tables and instructions.

- **Packaged C ABI** — the wheel ships the Rust cdylib with `detree/include/detree.h` (available through `detree.include_dir()`), so a C, C++, or Rust program can drive the same machine.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the setup, the non-negotiable rules, and the checks to run before opening a merge request; [AGENTS.md](AGENTS.md) is the working agreement behind it.

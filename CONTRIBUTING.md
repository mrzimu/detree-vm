# Contributing

Detree-VM is developed by AI agents under human review. The contracts are in [`docs/spec/`](docs/spec/README.md) and the working agreement is in [`AGENTS.md`](AGENTS.md); this page is the human-oriented summary of how to change code without breaking either.

## Set up

```sh
uv venv --python 3.13
source .venv/bin/activate
uv pip install -e . --group dev
pre-commit install
```

The editable install builds the Rust cdylib. `uv.lock` is intentionally not committed.

| Task | Command |
|---|---|
| Python tests | `uv run pytest -v tests/` |
| One test file | `uv run pytest -v tests/test_machine.py` |
| Formatting and linters | `uv run pre-commit run --all-files` |
| Pre-commit without the Rust toolchain | `SKIP=rustfmt uv run pre-commit run --all-files` |
| Documentation checks | `uv run python tools/check_docs.py` |
| Documentation site | `uv pip install --group docs`, then `uv run sphinx-build -b html -c docs -d build/docs/doctrees -W docs build/docs/html` |
| Rust tests | `cargo test --manifest-path kernel/rust/Cargo.toml` |
| C++ comparison machine | `cmake -S kernel -B build/cpp -DDETREE_BUILD_TESTS=ON && cmake --build build/cpp -j4` |

## Where things live

| Path | What it is |
|---|---|
| `spec/` | `isa.toml` and `opt.toml`, the machine-readable tables |
| `src/detree/spec/` | the code form of those tables, the module codec, and the validator |
| `src/detree/ir/`, `src/detree/opt/`, `src/detree/lower/` | the compiler side: tree IR, optimizer passes, bytecode emission |
| `src/detree/vm/` | the Python reference machine, the conformance oracle (development only) |
| `src/detree/abi/`, `src/detree/backends/` | the `ctypes` binding and the backend registry |
| `kernel/include/detree.h` | the C ABI, exported identically by every compiled machine |
| `kernel/rust/` | the shipped machine |
| `kernel/cpp/` | the C++ comparison machine (development only) |
| `docs/` | the published documentation; `docs/spec/` is normative |

## Rules that are not negotiable

- **Generated files are written by their tools.** `src/detree/spec/opinfo.py` comes from `uv run python tools/gen_isa.py --write`; the pass table in `docs/spec/ir.md` comes from `uv run python tools/gen_opt.py`. Run the tool and commit its output.
- **One module format and one C ABI.** An opcode that changes the wire format touches `spec/isa.toml`, `docs/spec/isa.md`, the generated Python table, all three machines, and the golden corpus.
- **Determinism.** Two structurally identical programs must lower to byte-identical modules; never derive ids from `hash()`, branch names, or iteration order of a set.
- **Dependency direction is one-way.** `spec` imports nothing else; `ir` imports `spec`; `opt` and `lower` import `ir` and `spec`; `vm` imports `spec`; `abi` and `backends` may import `spec`, `vm`, and NumPy. Nothing imports upward.
- **Caller-neutral wording.** Code and documents describe compilers, programs, modules, and callers, never a specific consumer's object model.
- **Keep the engineering small.** This is a domain-specific machine, not a security boundary: do not add sandboxing or defensive layers the specification does not require.

## Style

- **Python**: Black at 95 columns and Ruff (`E`, `F`, `I`, `UP`, `B`). Public APIs carry type hints. Docstrings use Google sections with Markdown, and both the opening `"""` and the closing `"""` sit on their own line; a one-line `"""text"""` docstring is not allowed.
- **Rust**: `rustfmt`; `#![deny(unsafe_op_in_unsafe_fn)]`, with `unsafe` only in `kernel/rust/src/capi.rs`.
- **C++**: the repository `.clang-format`, C++17, `-Wall -Wextra`, hidden visibility; only the `detree_*` symbols are exported.
- **Markdown**: one line per paragraph; never hard-wrap. Every document under `docs/` carries an H1 and a `Status:` line, and every relative link resolves.

## Tests and review

- Every opcode family has a hand-built module test; do not rely on the compiler to cover an opcode it does not emit.
- The golden corpus is recorded by the Python reference machine and replayed byte-for-byte by every build; a module-format or compiler change re-records it in the same commit.
- A new ABI field needs a layout test against `tools/abi_layout.c`.
- A run with skipped backends is not a full conformance run: build the C++ machine and install with `DETREE_SHIP_BACKEND=both` before claiming the matrix passes.
- `uv run` re-syncs the editable install to the Rust-only default, so `[cpp]` cases skip afterwards; for the full matrix call `.venv/bin/python -m pytest` or `uv run --no-sync`.

## Commits and merge requests

- Use Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`), and let the subject name the layer, for example `feat(opt): fuse length appends`.
- Keep a change small and verifiable, and say which checks ran and which backends were unavailable locally.
- A change to `spec/isa.toml` or `docs/spec/module.md` names the ISA version bump in the description.

## Reporting a problem

Include the module hash (`Module.content_hash()`), the backend and ISA version (`python -m detree backends`), the basket size, and a disassembly (`python -m detree disasm module.dtm`). A recorded `.dtg` case from `tests/data/` is the ideal reproduction, and `uv run python tools/golden.py --reduce tests/data/<case>.dtg` shrinks a divergent one.

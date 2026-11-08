# Detree Agent Guide

**Status: working agreement for agents.** This file is maintained for AI agents and is not part of the published documentation; human-facing material lives in [README.md](README.md) and `docs/`. Keep this file operational, link to the documents instead of copying them, and state explicitly when a note exists only for development work.

## 1. What this project is

Detree-VM is a domain-specific bytecode virtual machine for TTree baskets. A compiler outside this repository emits a module; the machine decodes it and turns basket bytes into typed buffers. The module format, the instruction set, the machine model, the C ABI, and the IR are specified in [docs/spec/README.md](docs/spec/README.md).

The project ships one machine implementation: the Rust cdylib in `kernel/rust/`. The repository also carries two development-only builds: the Python reference machine in `src/detree/vm/`, used as the conformance oracle, and the C++ machine in `kernel/cpp/`, used for performance comparison. Neither is a supported deployment path. Public documents must present Rust as the implementation; development material may name the other two only when it states that they are development-only. [docs/design/architecture.md](docs/design/architecture.md) holds the layer model.

The project is led by AI agents under human review. Prefer small, verifiable changes over large rewrites, and run the checks before claiming success.

## 2. Ground rules

1. `docs/spec/*.md` is the normative prose and `spec/*.toml` is the machine-readable authority. Code follows the specification; when this file disagrees with it, the specification wins.
2. Never edit generated content by hand. `src/detree/spec/opinfo.py` comes from `tools/gen_isa.py --write`, and the pass table in `docs/spec/ir.md` comes from `tools/gen_opt.py`. Run the tool and commit its output.
3. One module format and one C ABI: [kernel/include/detree.h](kernel/include/detree.h) is the public header, exported identically by every compiled machine.
4. The core stays dependency-light: `detree.spec`, `detree.ir`, `detree.opt`, `detree.lower`, and `detree.vm` use the standard library only. NumPy appears only in `detree.abi` and `detree.backends`.
5. Stay caller-neutral. Code and documents describe compilers, programs, modules, and callers, never a specific consumer's object model or array library.
6. Keep the engineering small. This is a domain-specific virtual machine, not a security boundary: do not add sandboxing, hardening, or defensive layers the specification does not require. Module validation exists because it is part of the byte-format contract, not because modules are suspect input.

## 3. Environment and commands

Use `uv` and an editable install; `uv.lock` is intentionally not committed.

```sh
uv venv --python 3.13
source .venv/bin/activate
uv pip install -e . --group dev
pre-commit install
```

| Task | Command |
|---|---|
| Python tests | `uv run pytest -v tests/` |
| One test file | `uv run pytest -v tests/test_cli.py` |
| Formatting and linters | `uv run pre-commit run --all-files` |
| Pre-commit without Rust | `SKIP=rustfmt uv run pre-commit run --all-files` |
| C++ configure | `cmake -S kernel -B build/cpp -DDETREE_BUILD_TESTS=ON` |
| C++ build | `cmake --build build/cpp -j4` |
| C++ tests | `ctest --test-dir build/cpp --output-on-failure` |
| Rust tests | `cargo test --manifest-path kernel/rust/Cargo.toml` |
| Rust formatting | `cargo fmt --manifest-path kernel/rust/Cargo.toml --all -- --check` |
| Both machines installed | `DETREE_SHIP_BACKEND=both uv pip install -e . --group dev` |
| Full backend matrix | `DETREE_SHIP_BACKEND=both uv pip install -e . --group dev --reinstall-package detree-vm` then `.venv/bin/python -m pytest -q` |
| ISA consistency | `uv run python tools/gen_isa.py --check` |
| Optimizer consistency | `uv run python tools/gen_opt.py --check` |
| Documentation checks | `uv run python tools/check_docs.py` |
| Documentation site | `uv pip install --group docs` then `uv run sphinx-build -b html -c docs -d build/docs/doctrees -W docs build/docs/html` |
| Documentation deploy | run the `deploy-docs` workflow; `release.yml` also calls it after PyPI |
| Golden corpus | `uv run python tools/golden.py --check` |
| Reduce a divergent case | `uv run python tools/golden.py --reduce tests/data/<case>.dtg` |
| Wheel contract | `uv build` then `python3 tools/inspect_wheel.py dist/*.whl` |
| Command line | `python -m detree backends`, `paths`, or `disasm <module>` |

## 4. Architecture map

```text
kernel/include/detree.h      the C ABI (C99)
kernel/rust/                 the shipped machine (cdylib, no Python dependency)
kernel/cpp/                  the C++ comparison machine (development only)
spec/                        isa.toml and opt.toml, the machine-readable tables
src/detree/spec/             the Python code form of the tables and the validator
src/detree/ir/               compiler-side tree IR
src/detree/opt/              optimizer passes over the IR
src/detree/lower/            IR to module bytes
src/detree/vm/               the Python reference machine (development oracle)
src/detree/abi/              ctypes binding to the C ABI
src/detree/backends/         backend registry and selection policy
docs/                        the published documentation (MyST), docs/spec/ is normative
tools/                       generators, corpus tooling, and checks
tests/                       pytest suite and the golden corpus
```

Dependency direction is one-way: `spec` imports nothing else; `ir` imports `spec`; `opt` and `lower` import `ir` and `spec`; `vm` imports `spec`; `abi` and `backends` may import `spec`, `vm`, and NumPy. Do not import upward.

## 5. Documentation rules

Human documents are [README.md](README.md) and every file under `docs/`. Keep them short and information-dense: a table or a few sentences beats a long paragraph, and the same fact appears once. Agent-only material, including this file and any future `dev/` notes, must say at the top that it exists for agents.

Every Markdown paragraph is one line. Never hard-wrap a paragraph; lists, tables, headings, code blocks, and MyST directives keep one line per item or row.

Sphinx builds from `docs/` as the source directory. MyST Markdown is the default format; use `{eval-rst}` only for autodoc directives that have no MyST equivalent, and do not add `.rst` files unless MyST cannot express the construct.

Every document under `docs/` carries an H1 and a `**Status: ...**` line, and every relative link resolves. [tools/check_docs.py](tools/check_docs.py) enforces those rules plus its `FORBIDDEN` wording list, which is matched as a substring in every document the script scans; read the list in the script rather than repeating it here.

Public wording follows the Rust-only rule from section 1. When a development-only build must be mentioned, say so in the same sentence and keep it inside development material.

## 6. Language conventions

- **Python**: Black with 95-character lines and Ruff (`E`, `F`, `I`, `UP`, `B`). Type hints in public APIs. Docstrings are Google style with Markdown formatting: the opening `"""` is followed by a newline, the body uses the Google sections when they apply, and the closing `"""` sits on its own line. A one-line `"""text"""` docstring is not allowed. Comments explain *why*, not *what*.
- **Rust**: `rustfmt`, `#![deny(unsafe_op_in_unsafe_fn)]`, and `unsafe` only in `kernel/rust/src/capi.rs`. Rustdoc Markdown, with a one-sentence summary first.
- **C++**: the repository `.clang-format`, C++17, `-Wall -Wextra`, hidden visibility. Only the `detree_*` symbols are exported; Doxygen only at the ABI surface in the public header.
- **Determinism**: never derive ids from `hash(obj)`, branch names, or iteration order of a set. Two structurally identical programs must produce byte-identical modules.

## 7. Testing requirements

- Every opcode family has at least one hand-built module test; do not rely on the compiler to cover an opcode it does not emit.
- The golden corpus is written by the Python reference build, never by the build under test. A module-format or compiler change re-records it in the same commit.
- Every build replays the same corpus byte-for-byte, including expected errors and final states.
- A new ABI field needs a layout test: `tools/abi_layout.c` versus the ctypes structures.
- A green run with skipped backends is not a full conformance run. Check the skip summary, and say explicitly when a build is unavailable locally. The `conformance` job in `run-tests.yml` enforces this: it installs both machines, fails when `detree.abi.libs.library_path` cannot find `cpp` or `rust`, and calls `.venv/bin/python -m pytest` rather than `uv run pytest`, because `uv run` re-syncs the Rust-only default.

## 8. Adding an opcode

1. Add the entry to `spec/isa.toml` (number, group, operands, reads, writes, flags, summary).
2. Add the semantics, errors, and a table row to `docs/spec/isa.md`; the checker compares that table with the TOML but does not generate it.
3. Run `uv run python tools/gen_isa.py --write` and commit `src/detree/spec/opinfo.py`.
4. Implement the opcode in the Python reference machine and add a hand-built test.
5. Implement it in the C++ and Rust machines and add their instruction tests.
6. Add a golden case when the compiler emits the opcode.
7. Bump `ISA_MINOR`; bump `ISA_MAJOR` only for an incompatible change.

## 9. Adding a machine

1. Implement the decoder and validator first; pass conformance level L0.
2. Implement the instruction families in the order listed in `docs/spec/conformance.md` §3; pass L1 and then L2.
3. Export the C ABI from [docs/spec/c-api.md](docs/spec/c-api.md); pass the C ABI layout test.
4. Replay the full golden corpus byte-for-byte; pass L3.
5. Register the machine in `detree.backends.registry`. A machine that is not the shipped Rust one is development-only and must be described that way in its code and documentation.

## 10. Forbidden edits

- Do not edit generated files, `LICENSE`, or a committed `uv.lock`.
- Do not add a caller-specific dependency to the core packages, or a Python or C++ object to the machine ABI.
- Do not add security hardening, sandboxing, or defensive layers the specification does not require.
- Do not introduce `.rst` documentation where MyST Markdown works.

## 11. Pitfalls

- `uv run` re-syncs the editable install to the default `DETREE_SHIP_BACKEND`, which is Rust only. After that, `[cpp]` cases skip instead of failing. For the full matrix install with `DETREE_SHIP_BACKEND=both` and call `.venv/bin/python -m pytest`, or use `uv run --no-sync`.
- `tools/check_docs.py` scans this file too, so its wording and link rules apply to `AGENTS.md`.
- `sphinx-build -W` turns warnings into errors; a stale toctree entry fails the build.
- `tools/gen_isa.py --check` compares the derived tables in `docs/spec/isa.md`, the Python package, and both compiled machines against `spec/isa.toml`; changing one without the others fails the check.
- The documentation source directory is `docs/`; the specifications under `docs/spec/` link the TOML tables at `../../spec/`.
- Two conventions are not automated yet: the one-paragraph-per-line Markdown rule and the docstring shape rule. `tests/test_comment_style.py` checks the Google/Markdown dialect but not those shapes, so apply them by hand and flag a violation in review.

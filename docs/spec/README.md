# Detree Specification

**Status: draft, ISA v0.1.** This directory is the normative definition of the Detree machine. It is written for compiler authors and machine implementers, and it is independent of any particular downstream project.

The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are to be interpreted as described in [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## Documents

| Document | Defines |
|---|---|
| [Module Format](module.md) | the module byte format, identifiers, hashing, and compatibility |
| [Instruction Set](isa.md) | instruction encoding, opcodes, operands, and semantics |
| [Machine Model](machine.md) | the runtime model, calling convention, event loop, reset, validation, and errors |
| [C ABI](c-api.md) | the C ABI exported by compiled implementations |
| [IR, Optimizer, and Lowering](ir.md) | the compiler-side tree IR, optimizer pipeline, and lowering contract |
| [Conformance](conformance.md) | conformance levels, the golden corpus, and the porting checklist |

`isa.toml` is the single source of truth for opcode numbers. The tables in the Instruction Set page, the Python package, and the Rust machine are derived from it with `tools/gen_isa.py`. CI rejects a stale derivation.

## Versioning

Detree has three independent versions:

| Version | Where it lives | Rule |
|---|---|---|
| **ISA** | `isa_major`/`isa_minor` in every module | `0.x` is unstable; `1.0` freezes the module format and opcode numbers |
| **C ABI** | `DETREE_C_API_VERSION` in `kernel/include/detree.h` | increment on a struct-layout change or a changed symbol meaning |
| **IR** | `IR_VERSION` in `detree.ir` | a compiler-side contract; a breaking change bumps the major version |

The Python package version is independent of all three. A machine MUST reject a module whose `isa_major` it does not implement. It SHOULD reject a newer `isa_minor` because it cannot know the meaning of an opcode added after it was built; the conservative choice is the safe one.

During `0.x` the ISA version is frozen at **0.1** (ADR 0006): opcodes may be added to the 0.x line without a minor bump, so `isa_minor` is not a forward-compatibility signal yet. What keeps a machine honest is the decoder: an opcode it does not know is a module error. `tools/gen_isa.py --check` keeps the version and magic in `isa.toml`, the Python package tables, and the Rust machine identical, so the "same ISA version" claim is verified rather than asserted.

## Conformance levels

A machine implementation is described by the highest level it passes. The levels are cumulative.

| Level | Name | Requirement |
|---|---|---|
| **L0** | Decoder | parse every module field exactly; reject malformed modules with the module-error status |
| **L1** | Core machine | event loop, buffers, states, arithmetic, comparisons, control flow, and the scalar stream ops |
| **L2** | Full machine | strings, references, layouts, bulk reads, all opcodes, and the complete error taxonomy |
| **L3** | Conformant | L2 plus byte-identical replay of the full golden corpus and the C ABI layout checks |

The Rust machine in this repository is the implementation this specification describes. The conformance levels below are measured against the committed golden corpus.

## Scope

Detree is a **domain-specific** machine for TTree baskets. It is not a general-purpose VM.

In scope:

- a register machine with 64-bit slots and fixed-size instructions;
- big-endian scalar reads, `TObject` headers, `fNBytes` length masks, object references, and interned strings;
- typed append-only buffers whose contents are exposed after a run;
- one module per decoding program, with deterministic bytes.

Out of scope:

- opening ROOT files, interpreting `TTree` metadata, or building arrays;
- a project-specific object model or DSL;
- floating-point arithmetic (floats are moved, never computed on);
- native code generation or an on-disk cache.

## Glossary

| Term | Meaning |
|---|---|
| **basket** | one compressed-then-decompressed block of TTree entry data, plus its entry offsets |
| **module** | a self-contained program: code, buffers, states, strings, layouts, and functions |
| **machine** | one execution engine instance for one module, reused basket after basket |
| **backend** | a machine implementation and the loader that makes it available |
| **program** | module bytes plus the machines and caches built from them |
| **compiler** | a component outside this repository that builds IR or modules |
| **IR** | the compiler-side typed tree between the compiler and the optimizer |
| **event** | one entry inside a basket |
| **state slot** | a 64-bit value that survives across events |
| **name slot** | a state slot that holds a string-arena id, or `-1` for "none" |
| **arena** | the per-run string table; ids are stable only within a run |

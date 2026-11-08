# Detree-VM

**Status: draft, pre-release.**

<!-- intro:start -->
Detree-VM is a domain-specific virtual machine for deserializing TTree binary streams into arrays. Its core machine is implemented in Rust, with a Python layer that exposes the compiler side: the tree IR, the optimizer, and lowering to module bytes.

## Why a virtual machine

Decoding TTree trades flexibility against performance and shipping convenience. A pure-Python decoder is easy to extend but slow on complex layouts; a C++ extension is fast, but it must be compiled against one upstream build, so downstream packages end up pinning each other. A domain-specific virtual machine keeps most of the flexibility for a small performance cost: the decoding program is data, and neither side has to be compiled into the other.

An Awkward Forth virtual machine already addresses the same problem and performs well, but Forth cannot express ROOT's reference pointers, so some decoding paths remain out of reach. Detree-VM closes that gap: a dedicated virtual machine handles those pointers directly, and covers the rest of TTree deserialization wherever it needs specialized treatment.
<!-- intro:end -->

## Install

```sh
pip install detree-vm
```

The wheel ships one machine, the Rust `libdetree_rust`, loaded through the C ABI; the compiler side is pure Python and is packaged with it. `python -m detree backends` reports the backend this environment can run.

## First run

```sh
python -m detree backends
python -m detree paths
python -m detree disasm program.dtm
```

A module is produced by a compiler, which is not part of this repository. `detree.Machine` builds one machine from those bytes; the instance belongs to the caller, who decides how many to build and which thread runs each.

```python
import detree

module_bytes = ...  # written by a compiler
basket_bytes = ...  # one TTree basket
offsets = [...]  # entry offsets inside the basket

with detree.Machine(module_bytes) as machine:
    arrays = machine.run(basket_bytes, offsets)
    print(machine.state_values())
```

The result maps each declared buffer to a NumPy array, and `state_values()` is the state after the last run. `python -m detree backends` lists what this environment can run.

## Where to go next

Start with [the architecture note](design/architecture.md) for the three contract layers, read [the specification](spec/README.md) before implementing a machine, and use [the embedding guide](guides/embedding.md) to drive a machine from C, Python, or Rust.

:::{toctree}
:caption: Design
:hidden:

design/architecture
:::

:::{toctree}
:caption: Guides
:hidden:

guides/embedding
:::

:::{toctree}
:caption: Specification
:hidden:

spec/README
spec/module
spec/isa
spec/machine
spec/c-api
spec/ir
spec/conformance
:::

:::{toctree}
:caption: API reference
:hidden:

api/index
api/facade
api/spec
api/machine
api/compiler
:::

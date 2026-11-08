# Compiler side

**Status: API reference.** Generated from the docstrings by `sphinx.ext.autodoc`.

A compiler builds a `detree.ir.nodes.Program`, the optimizer rewrites it, and the lowering pass emits module bytes. The IR and the passes are described normatively in [`spec/ir.md`](../spec/ir.md); machines never see the IR.

```{eval-rst}
.. automodule:: detree.ir.builder
   :members:
```

```{eval-rst}
.. automodule:: detree.ir.verify
   :members: verify_program
```

## Optimizer

```{eval-rst}
.. automodule:: detree.opt.manager
   :members: optimize
```

## Lowering

```{eval-rst}
.. automodule:: detree.lower
   :members:
```

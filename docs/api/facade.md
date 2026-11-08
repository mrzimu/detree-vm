# Public API

**Status: API reference.** Generated from the docstrings by `sphinx.ext.autodoc`.

The surface has two calls: `detree.compile` turns an IR program into module bytes, and `detree.Machine` builds one machine from those bytes. A machine accepts bytes or a `detree.spec.module.Module`, and belongs to whoever built it: build one per thread that decodes.

```{eval-rst}
.. automodule:: detree
   :members: include_dir, PUBLIC_API
```

```{eval-rst}
.. automodule:: detree.machine
   :members:
```

## Command line

```{eval-rst}
.. automodule:: detree.cli
   :members: disassemble, main
```

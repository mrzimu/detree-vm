# Machines and backends

**Status: API reference.** Generated from the docstrings by `sphinx.ext.autodoc`.

A machine implements the `Machine` protocol: it consumes module bytes and returns typed buffers. A backend is a machine implementation plus the loader that makes it available; the registry is development material, and the public entry point is `detree.Machine`. Selection rules are in `detree.backends.policy`.

The Python machine documented on this page is a development-only reference build, kept as the conformance oracle. The shipped machine is the Rust library; it is loaded through the same `Machine` protocol.

```{eval-rst}
.. automodule:: detree.vm.python
   :members:
```

## C ABI binding

```{eval-rst}
.. automodule:: detree.abi.capi
   :members: CapiMachine, build, load
```

```{eval-rst}
.. automodule:: detree.abi.libs
   :members: include_dir, library_path, library_names, cache_clear
```

## Registry

```{eval-rst}
.. automodule:: detree.backends.base
   :members:
```

```{eval-rst}
.. automodule:: detree.backends.registry
   :members:
```

```{eval-rst}
.. automodule:: detree.backends.policy
   :members:
```

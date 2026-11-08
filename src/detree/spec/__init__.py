"""
The normative Python form of the Detree specification.

Nothing here may import NumPy, an external framework, or a compiled backend. The
submodules mirror the documents in `spec/`:

- `isa` — opcode, typecode, buffer-kind, and field-kind tables;
- `module` — canonical module encoder and decoder;
- `validate` — static validation;
- `errors` — the shared error taxonomy.
"""

from __future__ import annotations

__all__ = ["errors", "isa", "module", "validate"]

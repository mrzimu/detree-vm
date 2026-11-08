"""
Which backend the distribution ships and which one runs by default.

The release wheel guarantees exactly one machine: the Rust machine. `SHIPPED`
is a release constant, not a runtime preference, and `PREFERENCE` contains
only that entry (ADR 0008, ADR 0009).

There is deliberately no fallback. A wheel whose library is missing or cannot
be loaded raises instead of decoding with the Python reference, because a
silent fallback would hide a broken installation behind a much slower path and
make a performance problem look like a performance surprise.

`DETREE_BACKEND`, `detree.backends.registry.use`, and an explicit `backend=`
argument to `detree.Machine` still select any installed machine; that is how
tests and benchmarks reach the Python reference and the C++ comparison
backend. None of these are part of the public surface.
"""

from __future__ import annotations

# The backend the release wheel guarantees.
SHIPPED = "rust"

# The order used by `detree.backends.registry.default`. One entry: the
# shipped machine. The C++ machine is a development and CI artifact, and the
# Python machine is the oracle, not a distribution promise.
PREFERENCE = (SHIPPED,)

__all__ = ["PREFERENCE", "SHIPPED"]

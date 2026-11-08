"""
Backend registry and adapters.

`detree.Machine` talks to the `base.Machine` protocol. A backend is a machine
implementation plus the loader that makes it available. Selection and
capability rules are in `detree.backends.policy`.
"""

from __future__ import annotations

from . import policy
from .base import Backend, Machine
from .registry import available, default, get, shipped, use

__all__ = [
    "Backend",
    "Machine",
    "available",
    "default",
    "get",
    "policy",
    "shipped",
    "use",
]

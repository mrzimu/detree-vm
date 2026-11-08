#!/usr/bin/env python3
"""
Check that a built wheel is the release artifact the project promises.

ADR 0009 makes the wheel contract explicit: one compiled machine, the header
that declares its symbols, a `py3-none-<platform>` tag, and no trace of the
Python-ABI or maturin paths that were removed.

    python tools/inspect_wheel.py dist/detree_vm-*.whl

Exit code 0 means every wheel passed. A problem is printed to stderr with the
offending member name, so CI logs point at the artifact and not at a guess.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

# The only compiled library a release may contain. Cargo keeps the `lib`
# prefix on Unix and drops it on Windows; CMake keeps it for the C++ machine.
RUST_LIBRARY = re.compile(
    r"^detree/_libs/(lib)?rustdetree\.(so|dylib|dll)$",
)

# The C ABI header, installed next to the library for embedders.
HEADER = "detree/include/detree.h"

# The marker that tells type checkers the package is typed.
TYPED = "detree/py.typed"

# Members that must never appear: the C++ machine is development-only, and
# the Python-ABI paths (pyo3, maturin `_native`) no longer exist.
FORBIDDEN = (
    re.compile(r"^detree/_libs/(lib)?cppdetree\."),
    re.compile(r"^rustdetree/"),
    re.compile(r"\.pyd$"),
    re.compile(r"\.pyi$"),
)

# `py3-none-any` would mean no compiled machine; `cp3xx` would mean the wheel
# depends on the CPython ABI, which the `ctypes` binding exists to avoid.
TAG = re.compile(r"^detree_vm-[^-]+-py3-none-(?P<platform>.+)\.whl$")


def check(path: Path) -> list[str]:
    """
    Return the problems found in *path*; an empty list means it passed.
    """
    problems: list[str] = []

    match = TAG.match(path.name)
    if match is None:
        problems.append(f"{path.name}: expected a py3-none-<platform> wheel tag")
    elif match.group("platform") == "any":
        problems.append(f"{path.name}: a wheel with no platform has no machine")

    with zipfile.ZipFile(path) as wheel:
        members = wheel.infolist()
        names = [member.filename for member in members]

    libraries = [
        member.filename
        for member in members
        if member.filename.startswith("detree/_libs/") and not member.is_dir()
    ]
    rust = [name for name in libraries if RUST_LIBRARY.match(name)]
    if len(libraries) != 1 or len(rust) != 1:
        problems.append(
            f"{path.name}: expected exactly one detree/_libs/librustdetree, "
            f"found {sorted(libraries)}"
        )
    if HEADER not in names:
        problems.append(f"{path.name}: missing {HEADER}")
    if TYPED not in names:
        problems.append(f"{path.name}: missing {TYPED}")

    for name in names:
        for pattern in FORBIDDEN:
            if pattern.search(name):
                problems.append(f"{path.name}: forbidden member {name!r}")

    return problems


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    paths = [Path(argument) for argument in arguments]
    if not paths:
        print("usage: inspect_wheel.py <wheel> [<wheel> ...]", file=sys.stderr)
        return 2

    problems: list[str] = []
    for path in paths:
        if not path.is_file():
            problems.append(f"{path}: not a file")
            continue
        problems.extend(check(path))

    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    for path in paths:
        print(f"wheel: OK {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
`python -m detree`: a small command-line entry point.

Three commands: `backends` diagnoses a local build, `paths` prints the
compiled library and header locations for an embedder, and `disasm` prints a
module in human-readable form. The disassembler matters because module bytes are
the only contract between a frontend and a machine (ADR 0001): when a decoded
value is wrong, the first question is what the program actually says.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def disassemble(raw: bytes) -> str:
    """
    Return a readable listing of a module.

    The operand fields come from `detree.spec.opinfo`, which is generated
    from `spec/isa.toml`, so an instruction prints exactly the bytes it uses.
    """
    from .spec.module import Module
    from .spec.opinfo import OPERANDS

    module = Module.from_bytes(raw)
    names = {func.id: func.name for func in module.funcs}
    entry = names.get(module.entry, str(module.entry))
    lines = [
        f"module  isa={module.isa_major}.{module.isa_minor}  entry={entry}"
        f"  regs={module.regs_max}  sha256={module.content_hash()[:12]}"
    ]

    if module.buffers:
        lines.append("buffers")
        for index, buffer in enumerate(module.buffers):
            lines.append(
                f"  [{index}] {buffer.name}  {buffer.dtype}  {buffer.kind.name.lower()}"
                + (f"  init={list(buffer.init)}" if buffer.init else "")
            )

    if module.states:
        lines.append("states")
        for index, state in enumerate(module.states):
            kind = "string" if state.is_string else "int"
            lines.append(f"  [{index}] {state.name}  {kind}  init={state.init}")

    if module.strings:
        lines.append("strings")
        for index, text in enumerate(module.strings):
            lines.append(f"  [{index}] {text!r}")

    if module.layouts:
        lines.append("layouts")
        for index, layout in enumerate(module.layouts):
            fields = ", ".join(
                f"{field.kind.name.lower()}:{field.typecode.name}"
                f"x{field.count}" + (f"->buf{field.buffer_id}" if field.buffer_id >= 0 else "")
                for field in layout.fields
            )
            lines.append(f"  [{index}] id={layout.id}  {fields}")

    if module.funcs:
        lines.append("functions")
        for func in module.funcs:
            lines.append(
                f"  [{func.id}] {func.name}  arity={func.arity}"
                f"  regs={func.reg_count}  code={func.code_off}..{func.end}"
            )

    for func in module.funcs:
        lines.append("")
        lines.append(f"{func.name}:")
        for ip in range(func.code_off, func.end):
            ins = module.code[ip]
            name = ins.op.name
            operands = []
            for field in OPERANDS.get(name, ()):
                value = getattr(ins, field)
                if field in ("rd", "ra", "rb"):
                    operands.append(f"{field}=r{value}")
                else:
                    operands.append(f"{field}={value}")
            suffix = "  " + " ".join(operands) if operands else ""
            lines.append(f"  {ip:4d}  {name:14s}{suffix}")

    covered = {ip for func in module.funcs for ip in range(func.code_off, func.end)}
    orphans = [ip for ip in range(len(module.code)) if ip not in covered]
    if orphans:
        lines.append("")
        lines.append(f"orphan instructions: {orphans}")

    return "\n".join(lines)


def _read_module(path: str) -> bytes:
    """
    Read a module from a file, or from stdin when *path* is `"-"`.

    Args:
        path: a filesystem path, or `"-"`.

    Returns:
        The module bytes.
    """
    if path == "-":
        return sys.stdin.buffer.read()
    return Path(path).read_bytes()


def compiled_paths() -> list[tuple[str, Path]]:
    """
    The compiled machine libraries this installation can find.

    The shipped machine comes first; a development build follows when it is
    present. A backend with no library on disk is omitted rather than printed
    with an empty path.
    """
    from .abi.libs import compiled_backends, library_path
    from .backends.policy import SHIPPED

    order = (SHIPPED,) + tuple(name for name in compiled_backends() if name != SHIPPED)
    found = []
    for name in order:
        path = library_path(name)
        if path is not None:
            found.append((name, path))
    return found


def main(argv: list[str] | None = None) -> int:
    """
    Run the command line.

    Args:
        argv: the arguments to parse, or `None` for `sys.argv`.

    Returns:
        The process exit status.
    """
    parser = argparse.ArgumentParser(prog="detree", description=__doc__)
    parser.add_argument("--version", action="version", version=f"detree {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("backends", help="list known backends and their levels")
    subparsers.add_parser("paths", help="print the compiled library and C header locations")
    disasm = subparsers.add_parser("disasm", help="print a module's tables and code")
    disasm.add_argument(
        "module",
        help="a file written by a frontend, or '-' to read the module from stdin",
    )

    args = parser.parse_args(argv)

    if args.command == "backends":
        from .backends import available

        for name, level, reason in available():
            status = "available" if reason is None else f"unavailable: {reason}"
            print(f"{name:8} L{level}  {status}")
        return 0

    if args.command == "paths":
        from .abi.libs import include_dir

        status = 0
        try:
            include = include_dir()
        except FileNotFoundError as error:
            print(f"error: {error}", file=sys.stderr)
            status = 1
        else:
            print(f"include  {include}")

        libraries = compiled_paths()
        for name, path in libraries:
            print(f"library  {name:8} {path}")
        if not libraries:
            print("error: no compiled machine library was found", file=sys.stderr)
            status = 1
        return status

    if args.command == "disasm":
        from .spec.errors import ModuleError

        try:
            print(disassemble(_read_module(args.module)))
        except OSError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        except ModuleError as error:
            print(f"error: not a valid module: {error}", file=sys.stderr)
            return 1
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

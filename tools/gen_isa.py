#!/usr/bin/env python3
"""
Validate `spec/isa.toml`, its Markdown mirror, and the three opcode enums.

This enforces the single-source rule: the TOML is the authority, and the Python
reference, the C++ machine, and the Rust machine must carry the same names,
numbers, version, and magic. `--write` regenerates the one table that is
produced from the TOML (the operand table the disassembler reads); everything
else is checked, so a mismatch is an error rather than a silent repair.

Requires Python 3.11+ (`tomllib`).

    python tools/gen_isa.py --check
    python tools/gen_isa.py --write
    python tools/gen_isa.py --summary
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import tomllib

REPO = Path(__file__).resolve().parent.parent
# The machine-readable tables stay in `spec/`; the prose that mirrors them is
# part of the documentation site under `docs/spec/`.
SPEC = REPO / "spec"
DOC_SPEC = REPO / "docs" / "spec"

# A Markdown opcode row: | `NAME` | 0xNN | ...
_ROW = re.compile(r"^\|\s*`([A-Z0-9_]+)`\s*\|\s*0x([0-9A-Fa-f]{2})\s*\|")

# A Python `Op` member: `    HALT = 0x00`.
_PY_MEMBER = re.compile(r"^\s{4}([A-Z][A-Z0-9_]*)\s*=\s*0x([0-9A-Fa-f]{2})\s*,?\s*$", re.M)

# A C++ `Op` member: `        HALT = 0x00,`.
_CPP_MEMBER = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*0x([0-9A-Fa-f]{2})\s*,", re.M)

# A Rust `Op` member: `    Halt = 0x00,`.
_RUST_MEMBER = re.compile(r"^\s*([A-Z][A-Za-z0-9]*)\s*=\s*0x([0-9A-Fa-f]{2})\s*,", re.M)

# Instruction groups defined by `docs/spec/isa.md`.
GROUPS = frozenset(
    {
        "arithmetic",
        "buffer",
        "bulk",
        "compare",
        "control",
        "flow",
        "offsets",
        "ref",
        "state",
        "stream",
        "string",
        "verify",
    }
)

# Instruction flags defined by the header comment in `spec/isa.toml`.
FLAGS = frozenset(
    {
        "branch",
        "buffer",
        "bulk",
        "call",
        "can_trap",
        "debug",
        "jump",
        "pure",
        "ref",
        "state",
        "stream",
        "string",
        "terminator",
        "verify",
    }
)


def load_table() -> dict:
    with (SPEC / "isa.toml").open("rb") as stream:
        return tomllib.load(stream)


def markdown_opcodes(text: str) -> dict[str, int]:
    rows: dict[str, int] = {}
    for line in text.splitlines():
        match = _ROW.match(line)
        if match:
            rows[match.group(1)] = int(match.group(2), 16)
    return rows


def _block(text: str, start: str) -> str:
    """
    The text of one enum, from *start* to the end of its declaration.
    """
    tail = text.split(start, 1)[1]
    if start.startswith("class "):
        return tail.split("\nclass ", 1)[0]
    if start.startswith("pub enum"):
        return tail.split("\n}", 1)[0]
    return tail.split("};", 1)[0]


def python_opcodes() -> dict[str, int]:
    text = (REPO / "src" / "detree" / "spec" / "isa.py").read_text(encoding="utf-8")
    block = _block(text, "class Op(IntEnum):")
    return {name: int(value, 16) for name, value in _PY_MEMBER.findall(block)}


def cpp_opcodes() -> dict[str, int]:
    text = (REPO / "kernel" / "cpp" / "src" / "isa.hpp").read_text(encoding="utf-8")
    block = _block(text, "enum class Op")
    return {name: int(value, 16) for name, value in _CPP_MEMBER.findall(block)}


def rust_opcodes() -> dict[str, int]:
    text = (REPO / "kernel" / "rust" / "src" / "isa.rs").read_text(encoding="utf-8")
    block = _block(text, "pub enum Op")
    rows = {}
    for name, value in _RUST_MEMBER.findall(block):
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()
        rows[snake] = int(value, 16)
    return rows


# Where each implementation declares the ISA version and magic. The ISA
# version is frozen at 0.1 for the whole 0.x line (ADR 0006): new opcodes do
# not bump it, and a machine rejects an opcode it does not know. The check is
# still worth having -- a drift between the TOML and one implementation means
# the "same ISA version" claim is false.
_PY_VERSION = (
    ("isa_major", re.compile(r"^ISA_MAJOR = (\d+)$", re.M)),
    ("isa_minor", re.compile(r"^ISA_MINOR = (\d+)$", re.M)),
    ("magic", re.compile(r'^MAGIC = b"([A-Za-z0-9]{4})"$', re.M)),
)

_CPP_VERSION = (
    ("isa_major", re.compile(r"kIsaMajor = (\d+);")),
    ("isa_minor", re.compile(r"kIsaMinor = (\d+);")),
    (
        "magic",
        re.compile(r"kMagic\[4\] = \{ '([A-Z])', '([A-Z])', '([A-Z])', '([A-Z])' \};"),
    ),
)

_RUST_VERSION = (
    ("isa_major", re.compile(r"pub const ISA_MAJOR: u16 = (\d+);")),
    ("isa_minor", re.compile(r"pub const ISA_MINOR: u16 = (\d+);")),
    ("magic", re.compile(r'pub const MAGIC: \[u8; 4\] = \*b"([A-Za-z0-9]{4})";')),
)


def declared_versions(path: Path, patterns) -> dict[str, str | None]:
    text = path.read_text(encoding="utf-8")
    values: dict[str, str | None] = {}
    for key, pattern in patterns:
        match = pattern.search(text)
        if match is None:
            values[key] = None
        elif key == "magic" and len(match.groups()) == 4:
            values[key] = "".join(match.groups())
        else:
            values[key] = match.group(1)
    return values


def check_versions(data) -> list[str]:
    meta = data.get("meta", {})
    expected = {
        "isa_major": str(meta.get("isa_major")),
        "isa_minor": str(meta.get("isa_minor")),
        "magic": meta.get("magic"),
    }
    problems = []
    sources = (
        ("src/detree/spec/isa.py", _PY_VERSION),
        ("kernel/cpp/src/isa.hpp", _CPP_VERSION),
        ("kernel/rust/src/isa.rs", _RUST_VERSION),
    )
    for relative, patterns in sources:
        declared = declared_versions(REPO / relative, patterns)
        for key, want in expected.items():
            got = declared.get(key)
            if got != want:
                problems.append(f"{relative}: {key} is {got!r}, spec/isa.toml says {want!r}")
    return problems


# The generated operand table the disassembler reads.
OPINFO = REPO / "src" / "detree" / "spec" / "opinfo.py"


def operand_fields(operands: str) -> tuple[str, ...]:
    """
    Map a TOML operand list to the fields of a 20-byte instruction.

    Registers keep their name. Every other label is uppercase (`IMM`, `TC`,
    `BUF`, `LBL`, `MSG`, `MSG` ...) and is an immediate, filling
    `imm1` then `imm2` in the order the TOML lists them. An unexpected
    label is an error, not a guess: a new opcode shape must be classified.
    """
    fields: list[str] = []
    immediates = 0
    for label in (part.strip() for part in operands.split(",")):
        if not label:
            continue
        if label in ("rd", "ra", "rb"):
            fields.append(label)
        elif label.isupper():
            fields.append(("imm1", "imm2")[immediates])
            immediates += 1
        else:
            raise ValueError(f"unknown operand label {label!r}")
        if immediates > 2:
            raise ValueError(f"more than two immediates in {operands!r}")
    return tuple(fields)


def opinfo_source(data) -> str:
    """
    Render `src/detree/spec/opinfo.py`.
    """
    lines = [
        '"""',
        "Operand fields per opcode, generated from `spec/isa.toml`.",
        "",
        "Do not edit: run `python tools/gen_isa.py --write`. The disassembler",
        "prints exactly these fields, in this order.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "OPERANDS = {",
    ]
    for op in data["op"]:
        fields = operand_fields(op["operands"])
        # Black normalizes quotes, so emit the form Black produces: a check that
        # straddles a formatter is a check that fails for the wrong reason.
        rendered = ", ".join(f'"{field}"' for field in fields)
        if len(fields) == 1:
            rendered += ","
        lines.append(f'    "{op["name"]}": ({rendered}),')
    lines += ["}", "", '__all__ = ["OPERANDS"]', ""]
    return "\n".join(lines)


def check_enum(
    label: str, path: str, found: dict[str, int], table: dict[str, int]
) -> list[str]:
    problems = []
    for name in sorted(set(table) - set(found)):
        problems.append(f"{path}: opcode {name} is missing from the {label} enum")
    for name in sorted(set(found) - set(table)):
        problems.append(f"{path}: opcode {name} is not in the TOML")
    for name in sorted(set(table) & set(found)):
        if table[name] != found[name]:
            problems.append(
                f"{path}: {name} is 0x{found[name]:02X}, the TOML says 0x{table[name]:02X}"
            )
    return problems


def check() -> list[str]:
    problems: list[str] = []
    data = load_table()

    meta = data.get("meta", {})
    for key in ("isa_major", "isa_minor", "magic", "instruction_size"):
        if key not in meta:
            problems.append(f"spec/isa.toml: missing [meta].{key}")
    if meta.get("instruction_size") != 20:
        problems.append("spec/isa.toml: instruction_size must be 20")

    typecodes = data.get("typecodes", {})
    if len(set(typecodes.values())) != len(typecodes):
        problems.append("spec/isa.toml: typecode values are not unique")

    ops = data.get("op", [])
    names = [op.get("name") for op in ops]
    codes = [op.get("code") for op in ops]

    if len(set(names)) != len(names):
        problems.append("spec/isa.toml: duplicate opcode names")
    if len(set(codes)) != len(codes):
        problems.append("spec/isa.toml: duplicate opcode numbers")
    for op in ops:
        name = op.get("name", "?")
        code = op.get("code")
        if not isinstance(code, int) or not 0 <= code <= 0xFF:
            problems.append(f"spec/isa.toml: {name} has an invalid code {code!r}")
        for field in ("group", "operands", "reads", "writes", "flags", "summary"):
            if field not in op:
                problems.append(f"spec/isa.toml: {name} is missing {field!r}")
        if op.get("group") not in GROUPS:
            problems.append(f"spec/isa.toml: {name} has an unknown group {op.get('group')!r}")
        for flag in op.get("flags", []):
            if flag not in FLAGS:
                problems.append(f"spec/isa.toml: {name} has an unknown flag {flag!r}")

    documented = markdown_opcodes((DOC_SPEC / "isa.md").read_text(encoding="utf-8"))
    table = {op["name"]: op["code"] for op in ops if "name" in op and "code" in op}

    for name in sorted(set(table) - set(documented)):
        problems.append(
            f"docs/spec/isa.md: opcode {name} exists in the TOML but not in a table"
        )
    for name in sorted(set(documented) - set(table)):
        problems.append(f"docs/spec/isa.md: opcode {name} is documented but not in the TOML")
    for name in sorted(set(table) & set(documented)):
        if table[name] != documented[name]:
            problems.append(
                f"docs/spec/isa.md: {name} is 0x{documented[name]:02X}, "
                f"the TOML says 0x{table[name]:02X}"
            )

    problems.extend(check_enum("Python", "src/detree/spec/isa.py", python_opcodes(), table))
    problems.extend(check_enum("C++", "kernel/cpp/src/isa.hpp", cpp_opcodes(), table))
    problems.extend(check_enum("Rust", "kernel/rust/src/isa.rs", rust_opcodes(), table))
    problems.extend(check_versions(data))

    try:
        generated = opinfo_source(data)
    except ValueError as error:
        problems.append(f"spec/isa.toml: {error}")
    else:
        relative = OPINFO.relative_to(REPO).as_posix()
        if not OPINFO.is_file():
            problems.append(f"{relative}: missing; run python tools/gen_isa.py --write")
        elif OPINFO.read_text(encoding="utf-8") != generated:
            problems.append(f"{relative}: stale; run python tools/gen_isa.py --write")

    return problems


def summary() -> None:
    data = load_table()
    meta = data["meta"]
    ops = data["op"]
    groups: dict[str, int] = {}
    for op in ops:
        groups[op["group"]] = groups.get(op["group"], 0) + 1

    print(f"ISA {meta['isa_major']}.{meta['isa_minor']}  magic={meta['magic']}")
    print(f"{len(ops)} opcodes, {len(data['typecodes'])} typecodes")
    for group in sorted(groups):
        print(f"  {group:10} {groups[group]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on any inconsistency")
    parser.add_argument("--summary", action="store_true", help="print the inventory")
    parser.add_argument(
        "--write",
        action="store_true",
        help="regenerate src/detree/spec/opinfo.py from the ISA table",
    )
    args = parser.parse_args(argv)

    if args.summary:
        summary()

    if args.write:
        data = load_table()
        OPINFO.write_text(opinfo_source(data), encoding="utf-8")
        print(f"wrote {OPINFO.relative_to(REPO)}")

    problems = check()
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    if args.check:
        print("isa: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

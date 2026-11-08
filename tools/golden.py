#!/usr/bin/env python3
"""
Write or check the golden corpus.

The corpus is written by the Python reference machine and replayed by every
backend. The format is defined in `docs/spec/conformance.md` §2.

    python tools/golden.py --write
    python tools/golden.py --check
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from corpus_cases import CASES  # noqa: E402

from detree.spec.errors import (  # noqa: E402
    AssertionFailed,
    DetreeError,
    InvalidByteCount,
    ReadLengthMismatch,
    StackOverflow,
    StreamUnderflow,
    UnsupportedFeature,
)
from detree.spec.isa import Op  # noqa: E402
from detree.spec.module import Module  # noqa: E402
from detree.vm import PythonMachine  # noqa: E402

CORPUS = REPO / "tests" / "data"

# The status each error kind maps to; the same values as `detree_status`.
STATUS = {
    StreamUnderflow: 2,
    InvalidByteCount: 3,
    ReadLengthMismatch: 4,
    UnsupportedFeature: 5,
    AssertionFailed: 6,
    StackOverflow: 7,
    DetreeError: 8,
}


def _text(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<H", len(raw)) + raw


def status_of(error: DetreeError) -> int:
    for cls in type(error).__mro__:
        if cls in STATUS:
            return STATUS[cls]
    return 8


def encode(case) -> bytes:
    """
    Run *case* on the reference machine and serialize the result.
    """
    module = case.module.to_bytes()
    machine = PythonMachine(module)
    try:
        outputs = machine.run_bytes(case.data, case.offsets, case.cursor_offset)
        states = machine.state_values()
        status = 0
        ip = -1
    except DetreeError as error:
        outputs = {}
        states = {}
        status = status_of(error)
        ip = error.ip
    raw_states = list(machine.states)

    out = bytearray()
    out += b"DTGL"
    out += struct.pack("<BI", 1, len(module))
    out += module
    out += struct.pack("<I", len(case.data))
    out += case.data
    out += struct.pack("<I", len(case.offsets))
    for offset in case.offsets:
        out += struct.pack("<q", offset)
    out += struct.pack("<Q", case.cursor_offset)
    out += struct.pack("<B3sq", status, b"\x00\x00\x00", ip)

    out += struct.pack("<I", len(outputs))
    for name in case.module.buffer_names():
        dtype, count, payload = outputs[name]
        out += _text(name)
        out += _text(dtype)
        out += struct.pack("<QQ", count, len(payload))
        out += payload

    out += struct.pack("<I", len(states))
    for index, decl in enumerate(case.module.states):
        value = states.get(decl.name)
        text = value if decl.is_string and isinstance(value, str) else ""
        raw = raw_states[index] if index < len(raw_states) else 0
        out += _text(decl.name)
        out += struct.pack("<BQ", 1 if decl.is_string else 0, raw)
        out += _text(text)
    return bytes(out)


def corpus_files() -> dict[str, bytes]:
    files = {}
    for factory in CASES:
        case = factory()
        files[case.name] = encode(case)
    return files


@dataclass(frozen=True)
class RecordedCase:
    """
    The inputs of a `.dtg` file, without the recorded expectations.
    """

    name: str
    module: Module
    data: bytes
    offsets: tuple[int, ...]
    cursor_offset: int


class _Cursor:
    """
    Read the little-endian fields of a `.dtg` file.
    """

    def __init__(self, raw: bytes):
        self.raw = raw
        self.at = 0

    def take(self, size: int) -> bytes:
        chunk = self.raw[self.at : self.at + size]
        if len(chunk) != size:
            raise ValueError("truncated .dtg file")
        self.at += size
        return chunk

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.take(size))

    def text(self) -> str:
        (length,) = self.unpack("<H")
        return self.take(length).decode("utf-8")


def read_case(raw: bytes, name: str = "case") -> RecordedCase:
    """
    Decode a `.dtg` file into the inputs that produced it.
    """
    cursor = _Cursor(raw)
    if cursor.take(4) != b"DTGL":
        raise ValueError("not a golden case: bad magic")
    (version,) = cursor.unpack("<B")
    if version != 1:
        raise ValueError(f"unsupported golden case version {version}")

    (module_len,) = cursor.unpack("<I")
    module = Module.from_bytes(cursor.take(module_len))

    (data_len,) = cursor.unpack("<I")
    data = cursor.take(data_len)

    (n_offsets,) = cursor.unpack("<I")
    offsets = tuple(cursor.unpack("<q")[0] for _ in range(n_offsets))
    (cursor_offset,) = cursor.unpack("<Q")

    # The expectations that follow are not needed to reduce a case: the
    # reducer reruns both machines and compares them live.
    return RecordedCase(name, module, data, offsets, cursor_offset)


def reachable_functions(module: Module) -> set[int]:
    """
    Function ids reachable from the entry point through `CALL`.
    """
    seen = {module.entry}
    pending = [module.entry]
    while pending:
        func_id = pending.pop()
        func = module.funcs[func_id]
        for ins in module.code[func.code_off : func.end]:
            if ins.op is Op.CALL and 0 <= ins.imm1 < len(module.funcs):
                if ins.imm1 not in seen:
                    seen.add(ins.imm1)
                    pending.append(ins.imm1)
    return seen


def prune_functions(module: Module) -> Module | None:
    """
    Drop functions unreachable from the entry point, or return `None`.

    A function id is rewritten to its new index, and every `CALL` is
    remapped. Instruction order inside a function is untouched, so jump targets
    stay valid.
    """
    keep = sorted(reachable_functions(module))
    if len(keep) == len(module.funcs):
        return None

    renumber = {old: new for new, old in enumerate(keep)}
    code: list = []
    funcs: list = []
    for old in keep:
        func = module.funcs[old]
        body = module.code[func.code_off : func.end]
        offset = len(code)
        code.extend(
            replace(ins, imm1=renumber.get(ins.imm1, ins.imm1)) if ins.op is Op.CALL else ins
            for ins in body
        )
        funcs.append(replace(func, id=renumber[old], code_off=offset, code_len=len(body)))

    return Module(
        entry=renumber[module.entry],
        regs_max=module.regs_max,
        buffers=module.buffers,
        states=module.states,
        strings=module.strings,
        layouts=module.layouts,
        funcs=funcs,
        code=code,
        isa_major=module.isa_major,
        isa_minor=module.isa_minor,
        magic=module.magic,
    )


def _observe(module: Module, data: bytes, offsets, cursor_offset: int, backend: str):
    """
    One run as a comparable value: status, output bytes, and states.
    """
    raw = module.to_bytes()
    if backend == "python":
        machine = PythonMachine(raw)
        try:
            outputs = machine.run_bytes(data, offsets, cursor_offset)
            status = "ok"
            states = machine.state_values()
        except DetreeError as error:
            return (type(error).__name__, {}, {})
        return (status, {name: payload for name, (_, _, payload) in outputs.items()}, states)

    from detree.abi import capi

    machine = capi.build(raw, backend)
    try:
        outputs = machine.run(data, offsets, cursor_offset)
    except DetreeError as error:
        return (type(error).__name__, {}, {})
    payloads = {name: array.tobytes() for name, array in outputs.items()}
    return ("ok", payloads, machine.state_values())


def reduce_case(case: RecordedCase, backend: str) -> tuple[RecordedCase, list[str]]:
    """
    Shrink *case* while *backend* keeps disagreeing with the reference.

    Only the inputs are reduced: entry offsets, trailing basket bytes, and
    functions unreachable from the entry point. Instructions are never deleted,
    because a deletion invalidates jump targets and changes the program rather
    than the test case.
    """

    def diverges(candidate: RecordedCase) -> bool:
        reference = _observe(
            candidate.module,
            candidate.data,
            candidate.offsets,
            candidate.cursor_offset,
            "python",
        )
        other = _observe(
            candidate.module,
            candidate.data,
            candidate.offsets,
            candidate.cursor_offset,
            backend,
        )
        return reference != other

    current = case
    steps = []
    if not diverges(current):
        raise SystemExit(f"nothing to reduce: python and {backend} agree on {case.name!r}")

    pruned = prune_functions(current.module)
    if pruned is not None:
        candidate = replace(current, module=pruned)
        if diverges(candidate):
            current = candidate
            steps.append(
                f"dropped {len(case.module.funcs) - len(pruned.funcs)} unreachable function(s)"
            )

    while len(current.offsets) > 1:
        candidate = replace(current, offsets=current.offsets[:-1])
        if not diverges(candidate):
            break
        current = candidate
        steps.append(f"dropped an entry ({len(current.offsets)} left)")

    while current.data:
        candidate = replace(current, data=current.data[: len(current.data) // 2])
        if not diverges(candidate):
            break
        current = candidate
        steps.append(f"halved the basket ({len(current.data)} B left)")

    return current, steps


def write() -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    for name, payload in corpus_files().items():
        (CORPUS / f"{name}.dtg").write_bytes(payload)
    print(f"golden: wrote {len(CASES)} cases to {CORPUS}")


def check() -> int:
    expected = corpus_files()
    problems = []
    present = {path.stem: path for path in CORPUS.glob("*.dtg")}
    for name, payload in expected.items():
        path = present.get(name)
        if path is None:
            problems.append(f"{name}.dtg is missing")
        elif path.read_bytes() != payload:
            problems.append(f"{name}.dtg is stale")
    for name in sorted(set(present) - set(expected)):
        problems.append(f"{name}.dtg has no case")
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    print(f"golden: OK ({len(expected)} cases)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="rewrite the corpus")
    mode.add_argument("--check", action="store_true", help="fail if the corpus is stale")
    mode.add_argument(
        "--reduce",
        metavar="CASE",
        help="shrink a .dtg case while a compiled backend diverges from the reference",
    )
    parser.add_argument("--backend", default="rust", help="backend to compare (default: rust)")
    parser.add_argument("--output", help="where to write the reduced .dtg case")
    args = parser.parse_args(argv)

    if sys.byteorder != "little":
        print(
            "error: the golden corpus is defined for little-endian hosts",
            file=sys.stderr,
        )
        return 1
    if args.write:
        write()
        return 0
    if args.reduce:
        path = Path(args.reduce)
        try:
            case = read_case(path.read_bytes(), name=path.stem)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        reduced, steps = reduce_case(case, args.backend)
        output = Path(args.output or f"build/reduced/{case.name}.dtg")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(encode(reduced))
        for step in steps or ["already minimal"]:
            print(f"  {step}")
        print(
            f"golden: wrote {output} ({len(reduced.data)} B, {len(reduced.offsets)} entries)"
        )
        return 0
    return check()


if __name__ == "__main__":
    sys.exit(main())

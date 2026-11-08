#!/usr/bin/env python3
"""
Compare the machines per basket.

Two numbers are reported for each case:

* `ffi` -- the Python `ctypes` path, one call per basket;
* `native` -- the C harness in `tools/bench_capi.c`, which calls
  `detree_run` directly (the gate in ADR 0008 uses this one).

    python tools/bench.py --repeat 7
    python tools/bench.py --case bulk --json build/bench.json
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import detree  # noqa: E402
from detree.abi import capi  # noqa: E402
from detree.abi.libs import library_path  # noqa: E402
from detree.ir import (  # noqa: E402
    BufferAppend,
    Const,
    Local,
    ProgramBuilder,
    ReadBulk,
    ReadFields,
    StrConst,
    StreamRead,
    StreamReadCString,
    StrEq,
    nodes,
)
from detree.spec.isa import FieldKind, Typecode  # noqa: E402
from detree.vm import PythonMachine  # noqa: E402


def _scalar_case():
    builder = ProgramBuilder()
    out = builder.buffer("out", "int32")
    func = builder.function("read")
    func.emit(BufferAppend(out, StreamRead(Typecode.INT32)))
    func.ret()
    data = b"".join(struct.pack(">i", value) for value in range(1000))
    return builder.build(), data, tuple(range(0, len(data) + 1, 4))


def _bulk_case():
    builder = ProgramBuilder()
    out = builder.buffer("out", "float64")
    func = builder.function("read")
    func.assign(func.local("n"), StreamRead(Typecode.UINT32))
    func.emit(ReadBulk(out, Typecode.FLOAT64, Local("n")))
    func.ret()
    values = [float(value) for value in range(2000)]
    data = struct.pack(">I", len(values)) + struct.pack(f">{len(values)}d", *values)
    return builder.build(), data, (0, len(data))


def _flat_case():
    builder = ProgramBuilder()
    buffers = [builder.buffer(f"f{index}", "int32") for index in range(5)]
    layout = builder.layout(
        [
            nodes.LayoutField(FieldKind.SCALAR, Typecode.INT32, 1, buffer_id)
            for buffer_id in buffers
        ]
    )
    func = builder.function("read")
    func.emit(ReadFields(layout, Const(1)))
    func.ret()
    record = struct.pack(">5i", 1, 2, 3, 4, 5)
    data = record * 500
    return builder.build(), data, tuple(range(0, len(data) + 1, len(record)))


def _jagged_case():
    builder = ProgramBuilder()
    out = builder.buffer("out", "float64")
    func = builder.function("read")
    func.assign(func.local("n"), StreamRead(Typecode.UINT32))
    func.emit(ReadBulk(out, Typecode.FLOAT64, Local("n")))
    func.ret()
    data = b""
    offsets = [0]
    for index in range(40):
        count = 20 + (index % 10)
        data += struct.pack(">I", count)
        data += struct.pack(f">{count}d", *[float(value) for value in range(count)])
        offsets.append(len(data))
    return builder.build(), data, tuple(offsets)


def _string_case():
    builder = ProgramBuilder()
    out = builder.buffer("out", "int8")
    text = builder.constant("EVT")
    func = builder.function("read")
    func.append(out, StrEq(StreamReadCString(), StrConst(text)))
    func.ret()
    event = b"EVT\x00"
    data = event * 500
    return builder.build(), data, tuple(range(0, len(data) + 1, len(event)))


def _pointer_case():
    builder = ProgramBuilder()
    out = builder.buffer("out", "uint32")
    func = builder.function("read")
    tag = func.local("tag")
    func.assign(tag, StreamRead(Typecode.UINT32))
    func.emit(nodes.RefSetObj(Local("tag"), Const(1)))
    func.append(out, nodes.RefGetObj(Local("tag")))
    func.ret()
    data = b"".join(struct.pack(">I", value) for value in range(500))
    return builder.build(), data, tuple(range(0, len(data) + 1, 4))


CASES = {
    "scalar": _scalar_case,
    "bulk": _bulk_case,
    "flat": _flat_case,
    "jagged": _jagged_case,
    "string": _string_case,
    "pointer": _pointer_case,
}


def build_cases(names=None):
    selected = []
    for name, factory in CASES.items():
        if names and name not in names:
            continue
        program, data, offsets = factory()
        selected.append((name, detree.compile(program), data, offsets))
    return selected


def measure_ffi(machine, data, offsets, repeat: int) -> int:
    machine.run(data, offsets)
    samples = []
    for _ in range(repeat):
        start = time.perf_counter_ns()
        machine.run(data, offsets)
        samples.append(time.perf_counter_ns() - start)
    return int(statistics.median(samples))


def compile_native() -> Path | None:
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        return None
    output = REPO / "build" / "bench_capi"
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            compiler,
            "-O2",
            "-std=c99",
            "-I",
            str(REPO / "kernel" / "include"),
            str(REPO / "tools" / "bench_capi.c"),
            "-o",
            str(output),
            "-ldl",
        ],
        capture_output=True,
        text=True,
    )
    return output if result.returncode == 0 else None


def measure_native(driver, library, module_path, data_path, offsets, repeat: int) -> int:
    result = subprocess.run(
        [
            str(driver),
            str(library),
            str(module_path),
            str(data_path),
            ",".join(str(value) for value in offsets),
            "0",
            str(repeat),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("native_ns="):
            return int(line.split("=", 1)[1])
    raise RuntimeError(f"native harness produced no timing: {result.stdout!r}")


def geomean(values):
    values = [value for value in values if value and value > 0]
    if not values:
        return None
    return math.exp(sum(math.log(value) for value in values) / len(values))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=7, help="samples per measurement")
    parser.add_argument("--case", help="only cases whose name contains this")
    parser.add_argument("--json", help="also write the measurements here")
    parser.add_argument("--no-native", action="store_true", help="skip the C harness")
    args = parser.parse_args(argv)
    repeat = max(1, args.repeat)

    names = None
    if args.case:
        names = {name for name in CASES if args.case in name}
        if not names:
            print(f"bench: no case matches {args.case!r}", file=sys.stderr)
            return 2

    backends = ["python"]
    for name in ("cpp", "rust"):
        if library_path(name) is not None:
            backends.append(name)

    driver = None if args.no_native else compile_native()
    rows = []
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        for name, raw, data, offsets in build_cases(names):
            row = {"name": name, "ffi_ns": {}, "native_ns": {}}
            for backend in backends:
                if backend == "python":
                    machine = PythonMachine(raw)
                else:
                    machine = capi.build(raw, backend)
                row["ffi_ns"][backend] = measure_ffi(machine, data, offsets, repeat)

            if driver is not None:
                module_path = directory / f"{name}.module"
                data_path = directory / f"{name}.data"
                module_path.write_bytes(raw)
                data_path.write_bytes(data)
                for backend in ("cpp", "rust"):
                    library = library_path(backend)
                    if library is None:
                        continue
                    try:
                        row["native_ns"][backend] = measure_native(
                            driver, library, module_path, data_path, offsets, repeat
                        )
                    except (subprocess.CalledProcessError, RuntimeError) as error:
                        print(f"bench: {backend}/{name}: {error}", file=sys.stderr)

            cpp = row["native_ns"].get("cpp")
            rust = row["native_ns"].get("rust")
            row["rust_over_cpp"] = (rust / cpp) if cpp and rust else None
            rows.append(row)

    header = f"{'case':12} {'py us':>10} {'cpp ffi':>10} {'rust ffi':>10}"
    if driver is not None:
        header += f" {'cpp native':>12} {'rust native':>12} {'rust/cpp':>9}"
    print(header)
    for row in rows:
        line = f"{row['name']:12}"
        for backend in ("python", "cpp", "rust"):
            value = row["ffi_ns"].get(backend)
            line += f" {value / 1000:10.1f}" if value else f" {'-':>10}"
        if driver is not None:
            for backend in ("cpp", "rust"):
                value = row["native_ns"].get(backend)
                line += f" {value / 1000:12.1f}" if value else f" {'-':>12}"
            ratio = row["rust_over_cpp"]
            line += f" {ratio:9.3f}" if ratio else f" {'-':>9}"
        print(line)

    summary = {
        "repeat": repeat,
        "cases": rows,
        "geomean_rust_over_cpp": geomean(
            [row["rust_over_cpp"] for row in rows if row["rust_over_cpp"]]
        ),
    }
    if summary["geomean_rust_over_cpp"]:
        print(f"geomean rust/cpp: {summary['geomean_rust_over_cpp']:.3f}")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

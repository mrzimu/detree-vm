#!/usr/bin/env python3
"""
Describe what a module costs, so a rewrite is designed against real code.

The report is static: it decodes module bytes and never runs them. For every
module it prints the instruction count, the opcodes each function executes,
and the loops a backward jump closes -- the bodies a per-element cost is paid
in. That is the input an optimizer pass is chosen from, and the check after it
fires.

    python tools/opt_report.py build/modules/*.dtm
    python tools/opt_report.py --dir build/modules --top 6
    python tools/opt_report.py --json build/opt-report.json build/modules/*.dtm

A module without a `.dtm`-specific header is accepted as raw bytes, so a file
written by any frontend works as long as it is a valid module.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from detree.spec.isa import Op  # noqa: E402
from detree.spec.module import Module  # noqa: E402

# Jumps that can close a loop, and the operand that holds their target.
_JUMP_TARGET = {
    Op.JMP: "imm1",
    Op.JZ: "imm1",
    Op.JNZ: "imm1",
    Op.BR_EQ: "imm1",
    Op.BR_NE: "imm1",
    Op.BR_LT_S: "imm1",
    Op.BR_LE_S: "imm1",
    Op.BR_GT_S: "imm1",
    Op.BR_GE_S: "imm1",
    Op.BR_LT_U: "imm1",
    Op.BR_LE_U: "imm1",
    Op.BR_GT_U: "imm1",
    Op.BR_GE_U: "imm1",
}


def _op_name(ins) -> str:
    return getattr(ins.op, "name", str(ins.op))


def _loops(code) -> list[tuple[int, int]]:
    """
    `(start, end)` for every backward jump, end inclusive, per function.
    """
    found = []
    for index, ins in enumerate(code):
        if ins.op not in _JUMP_TARGET:
            continue
        target = getattr(ins, _JUMP_TARGET[ins.op])
        if 0 <= target <= index:
            found.append((target, index))
    return found


def describe(name: str, raw: bytes, top: int) -> dict:
    """
    One module as a JSON-ready report.
    """
    module = Module.from_bytes(raw)
    functions = []
    for function in module.funcs:
        start = function.code_off
        code = module.code[start : start + function.code_len]
        histogram = Counter(_op_name(ins) for ins in code)
        loops = []
        for loop_start, loop_end in _loops(code):
            body = code[loop_start : loop_end + 1]
            loops.append(
                {
                    "start": loop_start,
                    "end": loop_end,
                    "size": len(body),
                    "ops": dict(Counter(_op_name(ins) for ins in body).most_common()),
                }
            )
        functions.append(
            {
                "name": function.name,
                "arity": function.arity,
                "regs": function.reg_count,
                "size": len(code),
                "ops": dict(histogram.most_common(top)),
                "loops": sorted(loops, key=lambda loop: loop["size"], reverse=True),
            }
        )
    return {
        "name": name,
        "bytes": len(raw),
        "isa": f"{module.isa_major}.{module.isa_minor}",
        "instructions": len(module.code),
        "functions": len(module.funcs),
        "buffers": len(module.buffers),
        "states": len(module.states),
        "layouts": len(module.layouts),
        "table": functions,
    }


def _lines(report: dict, top: int) -> list[str]:
    out = [
        f"== {report['name']}  {report['bytes']} B  "
        f"{report['instructions']} insns  {report['functions']} funcs  "
        f"{report['buffers']} bufs  isa {report['isa']}"
    ]
    for function in report["table"]:
        head = f"  {function['name']}  {function['size']} insns  {function['regs']} regs"
        out.append(head)
        ops = ", ".join(f"{name}×{count}" for name, count in function["ops"].items())
        if ops:
            out.append(f"    ops: {ops}")
        for loop in function["loops"]:
            ops = ", ".join(f"{name}×{count}" for name, count in loop["ops"].items())
            out.append(
                f"    loop {loop['start']}..{loop['end']} ({loop['size']} insns): {ops}"
            )
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "modules", nargs="*", type=Path, help="module files (.dtm or any module bytes)"
    )
    parser.add_argument(
        "--dir", type=Path, action="append", default=[], help="directory to scan"
    )
    parser.add_argument(
        "--pattern", default="*.dtm", help="glob used by --dir (default: *.dtm)"
    )
    parser.add_argument(
        "--top", type=int, default=8, help="opcodes shown per function (default 8)"
    )
    parser.add_argument("--json", type=Path, help="write the report as JSON here")
    args = parser.parse_args(argv)

    paths = list(args.modules)
    for directory in args.dir:
        paths.extend(sorted(directory.rglob(args.pattern)))
    if not paths:
        parser.error("no module given; pass files or --dir")

    reports = []
    for path in paths:
        try:
            reports.append(describe(path.name, path.read_bytes(), args.top))
        except Exception as error:  # noqa: BLE001 - a bad file must not stop the scan
            print(f"{path}: {error}", file=sys.stderr)
            continue

    for report in reports:
        print("\n".join(_lines(report, args.top)))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json}", file=sys.stderr)
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())

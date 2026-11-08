#!/usr/bin/env python3
"""
Render the optimizer documentation from `spec/opt.toml`.

The TOML is the single source of truth: every pass states what it looks for,
what it rewrites to, when it must not fire, and why it is worth having. This
script turns that into

* the pass table in `docs/spec/ir.md` §9 (between the generated markers);
* `dev/impl/optimizer.md`, one section per pass, when that document tree is
  present.

    python tools/gen_opt.py            # rewrite both files
    python tools/gen_opt.py --check    # fail on drift or an inconsistent manager

`--check` also compares the TOML with `src/detree/opt/manager.py`, so a pass
added to the code without a TOML entry (or the reverse) is an error rather than
a stale document.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import tomllib

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from detree.opt.manager import LEVEL1, PASS_ORDER  # noqa: E402

TOML = REPO / "spec" / "opt.toml"
TABLE_DOC = REPO / "docs" / "spec" / "ir.md"
PAGE = REPO / "dev" / "impl" / "optimizer.md"

START = "<!-- gen:opt-table -->"
END = "<!-- /gen:opt-table -->"

# Fields every pass entry must carry.
FIELDS = (
    "name",
    "phase",
    "level",
    "matches",
    "rewrites",
    "must_not_fire",
    "value",
)

PHASES = {"ir": "IR passes", "lower": "Lowering peepholes", "machine": "Machine"}


def load() -> dict:
    with TOML.open("rb") as handle:
        return tomllib.load(handle)


def problems(data: dict) -> list[str]:
    """
    Everything that keeps the TOML from describing the code exactly.
    """
    found: list[str] = []
    passes = data.get("pass", [])
    names = [entry.get("name") for entry in passes]

    if len(set(names)) != len(names):
        found.append("spec/opt.toml: duplicate pass names")
    for entry in passes:
        name = entry.get("name", "?")
        for field in FIELDS:
            if field not in entry:
                found.append(f"spec/opt.toml: {name} is missing {field!r}")
        if entry.get("phase") not in PHASES:
            found.append(f"spec/opt.toml: {name} has an unknown phase {entry.get('phase')!r}")
        if entry.get("level") not in (1, 2):
            found.append(f"spec/opt.toml: {name} has an unknown level {entry.get('level')!r}")
        if entry.get("value") not in ("high", "medium", "small"):
            found.append(f"spec/opt.toml: {name} has an unknown value {entry.get('value')!r}")
        for key in ("before", "after"):
            if key in entry and not isinstance(entry[key], str):
                found.append(f"spec/opt.toml: {name}.{key} must be a string")
        for test in entry.get("tests", []):
            if not isinstance(test, str):
                found.append(f"spec/opt.toml: {name}.tests must be strings")

    if tuple(names) != PASS_ORDER:
        found.append(
            "spec/opt.toml: the pass order is "
            f"{list(names)}, manager.PASS_ORDER is {list(PASS_ORDER)}"
        )
    level1 = [entry.get("name") for entry in passes if entry.get("level") == 1]
    if set(level1) != set(LEVEL1):
        found.append(
            "spec/opt.toml: level 1 is "
            f"{sorted(level1)}, manager.LEVEL1 is {sorted(LEVEL1)}"
        )
    return found


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def render_table(passes: list) -> str:
    lines = [
        "| Pass | Level | Finds | Rewrites to | Must not fire when | Value |",
        "|---|---|---|---|---|---|",
    ]
    for entry in passes:
        lines.append(
            "| `{name}` | {level} | {matches} | {rewrites} | {guard} | {value} |".format(
                name=entry["name"],
                level=entry.get("level", 2),
                matches=_cell(entry.get("matches", "")),
                rewrites=_cell(entry.get("rewrites", "")),
                guard=_cell(entry.get("must_not_fire", "")),
                value=entry.get("value", ""),
            )
        )
    return "\n".join(lines)


def render_page(data: dict, passes: list) -> str:
    meta = data.get("meta", {})
    status = meta.get("status", "Pre-release: passes may change without a version bump.")
    out = [
        "# Optimizer",
        "",
        f"**Status: implementation note.** {status}",
        "",
        "The optimizer rewrites the IR before lowering. "
        "[`spec/opt.toml`](../../spec/opt.toml) is the single source of truth for "
        "what every pass does; this page is generated from it by "
        "`tools/gen_opt.py`, and `--check` runs in CI, so the two cannot drift. "
        "The pass list is part of [the IR specification](../../docs/spec/ir.md).",
        "",
        "## 1. How the passes run",
        "",
        "`detree.compile(program)` calls `detree.opt.optimize` and then "
        "`detree.lower.lower`. The manager copies the program once, then runs the "
        "passes in the order below, repeating the whole sequence until a round "
        "changes nothing (at most eight rounds). A pass is a pure function from a "
        "program to the same program, mutating in place, and returns whether it "
        "changed anything.",
        "",
        "Two switches exist for debugging:",
        "",
        "- `DETREE_OPT=0` skips optimization entirely; a module built that way is "
        "what the machines must still run correctly.",
        "- `DETREE_DUMP=ir` prints the program before and after every pass.",
        "",
        "A pass fires only when its rewrite is observationally identical. Reads, "
        "skips, buffer appends, state writes, reference writes and string "
        "interns are effects: no pass removes or reorders them, and the "
        "`Must not fire` column below is the list of cases each pass declines.",
        "",
        "## 2. Adding a pass",
        "",
        "1. Implement it in `src/detree/opt/` and add it to `PASS_ORDER` and "
        "`PASSES` in `src/detree/opt/manager.py`; add it to `LEVEL1` too when it "
        "is always safe.",
        "2. Add an entry to `spec/opt.toml` in the same position, with a "
        "`before`/`after` excerpt and the tests that pin it.",
        "3. Run `python tools/gen_opt.py` and commit the regenerated documents.",
        "4. Run `python tools/gen_opt.py --check`, `python tools/golden.py --write` "
        "and the test suite.",
        "",
        "## 3. The passes",
        "",
        "The order is the execution order. `Level` 1 means the conservative set, "
        "`Level 2` the default pipeline.",
        "",
        render_table(passes),
        "",
    ]

    for phase, title in PHASES.items():
        entries = [entry for entry in passes if entry.get("phase") == phase]
        if not entries:
            continue
        out.extend([f"## 4. {title}", ""])
        for entry in entries:
            out.extend(_section(entry))
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def _section(entry: dict) -> list[str]:
    out = [
        f"### `{entry['name']}`",
        "",
        f"- **Level:** {entry.get('level', 2)}",
        f"- **Finds:** {entry.get('matches', '')}",
        f"- **Rewrites to:** {entry.get('rewrites', '')}",
        f"- **Must not fire when:** {entry.get('must_not_fire', '')}",
        f"- **Value:** {entry.get('value', '')}",
    ]
    if entry.get("tests"):
        out.append("- **Tests:** " + ", ".join(f"`{name}`" for name in entry["tests"]))
    if entry.get("before") or entry.get("after"):
        out.extend(["", "Before:", "", "```text", entry.get("before", ""), "```", ""])
        out.extend(["After:", "", "```text", entry.get("after", ""), "```"])
    out.append("")
    return out


def rewrite_table(document: str, table: str) -> str:
    start = document.index(START) + len(START)
    end = document.index(END)
    return document[:start] + "\n" + table + "\n" + document[end:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on any drift")
    args = parser.parse_args(argv)

    data = load()
    passes = data.get("pass", [])
    found = problems(data)

    document = TABLE_DOC.read_text(encoding="utf-8")
    expected_table = rewrite_table(document, render_table(passes))
    expected_page = render_page(data, passes)

    if not args.check:
        TABLE_DOC.write_text(expected_table, encoding="utf-8")
        written = [str(TABLE_DOC.relative_to(REPO))]
        if PAGE.parent.is_dir():
            PAGE.write_text(expected_page, encoding="utf-8")
            written.append(str(PAGE.relative_to(REPO)))
        print("wrote " + " and ".join(written))

    if TABLE_DOC.read_text(encoding="utf-8") != expected_table:
        found.append("docs/spec/ir.md: the pass table is stale; run tools/gen_opt.py")
    if PAGE.parent.is_dir():
        if not PAGE.is_file():
            found.append(f"{PAGE.relative_to(REPO)}: missing; run tools/gen_opt.py")
        elif PAGE.read_text(encoding="utf-8") != expected_page:
            found.append(f"{PAGE.relative_to(REPO)}: stale; run tools/gen_opt.py")

    for problem in found:
        print(problem, file=sys.stderr)
    if found:
        return 1
    print(f"optimizer: OK ({len(passes)} passes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

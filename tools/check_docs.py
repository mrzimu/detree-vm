#!/usr/bin/env python3
"""
Check documentation links, status headers, and project-neutral wording.

The repository is host-neutral by design: no document may name a specific host
project or its internal object model. This script is the automated part of that
rule. It also keeps the introduction that the repository README and the site
home page share from drifting apart.

    python tools/check_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Documents that must carry an H1 and a status line.
STATUS_ROOTS = (REPO / "spec", REPO / "docs", REPO / "dev")

# The root documents checked for links and forbidden wording.
ROOT_DOCUMENTS = (
    REPO / "README.md",
    REPO / "index.md",
    REPO / "AGENTS.md",
    REPO / "CHANGELOG.md",
    REPO / "CONTRIBUTING.md",
)

# The introduction is one text in two places: the repository README and the
# home page of the published site. The markers delimit the part that must
# match; everything outside them is free to differ.
INTRO_SOURCES = (REPO / "README.md", REPO / "index.md")
INTRO_START = "<!-- intro:start -->"
INTRO_END = "<!-- intro:end -->"

# Terms that would tie the repository to one caller's object model, or describe
# a virtual machine as having hosts and readers instead of callers and programs.
# Naming prior art when explaining why the VM exists is allowed, so the list is
# deliberately short.
FORBIDDEN = (
    "ascustom",
    "host",
    "reader",
)

# A Markdown inline link. Images and reference links are out of scope.
_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def markdown_files() -> list[Path]:
    # A repository state may predate part of the documentation: check what is
    # there and let the rest appear without failing the build. The collected
    # roots below are the ones a complete checkout carries.
    files = [path for path in ROOT_DOCUMENTS if path.is_file()]
    for root in STATUS_ROOTS:
        files.extend(sorted(root.rglob("*.md")))
    return [path for path in files if path.is_file()]


def check_status(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    problems = []
    if not text.startswith("# "):
        problems.append(f"{path.relative_to(REPO)}: missing an H1 title")
    if "Status:" not in text:
        problems.append(f"{path.relative_to(REPO)}: missing a Status line")
    return problems


def check_links(path: Path) -> list[str]:
    problems = []
    text = path.read_text(encoding="utf-8")
    for target in _LINK.findall(text):
        target = target.strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        resolved = (path.parent / target).resolve()
        if not resolved.exists():
            problems.append(f"{path.relative_to(REPO)}: broken link {target!r}")
    return problems


def check_wording(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    # A phrase may be wrapped across lines; normalize whitespace before the
    # check so "reader\ntree" is still caught.
    normalized = " ".join(text.split()).lower()
    return [
        f"{path.relative_to(REPO)}: forbidden host-specific term {word!r}"
        for word in FORBIDDEN
        if word in normalized
    ]


def intro_block(path: Path) -> str | None:
    """
    Return the introduction between the markers, or `None` when absent.
    """
    text = path.read_text(encoding="utf-8")
    start = text.find(INTRO_START)
    if start < 0:
        return None
    start += len(INTRO_START)
    end = text.find(INTRO_END, start)
    if end < 0:
        return None
    return " ".join(text[start:end].split())


def check_intro() -> list[str]:
    """
    The README and the site home page must carry the same introduction.
    """
    problems: list[str] = []
    blocks: dict[Path, str] = {}
    missing = [path for path in INTRO_SOURCES if not path.is_file()]
    if missing:
        # The site home page and the introduction arrive with the documentation
        # tree; until then there is nothing to compare.
        return problems
    for path in INTRO_SOURCES:
        block = intro_block(path)
        if block is None:
            problems.append(f"{path.relative_to(REPO)}: missing intro markers")
        else:
            blocks[path] = block
    if len(blocks) == len(INTRO_SOURCES) and len(set(blocks.values())) != 1:
        problems.append("README.md and index.md: intro blocks differ; edit both or neither")
    # The published position is one Rust machine. Keep the first paragraph a
    # visitor reads from drifting back to several first-class machines.
    if blocks and not any("implemented in Rust" in block for block in blocks.values()):
        problems.append(
            "README.md/index.md: the introduction must state that the virtual "
            "machine is implemented in Rust"
        )
    return problems


def main() -> int:
    problems: list[str] = []
    files = markdown_files()

    for path in files:
        if path.is_relative_to(REPO / "spec") or path.is_relative_to(REPO / "docs"):
            problems.extend(check_status(path))
        problems.extend(check_links(path))
        problems.extend(check_wording(path))

    problems.extend(check_intro())

    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    print(f"docs: OK ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

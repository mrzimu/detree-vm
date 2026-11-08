"""
Python comments and docstrings follow the Google/Markdown convention.

The rule is stated in `AGENTS.md` and `dev/CONTRIBUTING.md`: docstrings use
the Google sections with Markdown formatting, so the project does not carry
three dialects at once (NumPy underlines, reST roles, and Sphinx `#:`
comments).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SOURCES = [
    path
    for path in sorted(
        list((ROOT / "src").rglob("*.py"))
        + list((ROOT / "tests").glob("*.py"))
        + list((ROOT / "tools").glob("*.py"))
    )
    # This file spells the forbidden forms to search for them.
    if path.name != "test_comment_style.py"
]

#: A reST inline role such as :class:`Name`.
_RST_ROLE = re.compile(r":(?:class|func|mod|data|attr|meth|obj):`")

#: A NumPy-style section: the title, then a dashed underline.
_NUMPY_SECTION = re.compile(
    r"^\s*(?:Parameters|Returns|Raises|Yields|Attributes)\s*\n\s*-{3,}\s*$",
    re.MULTILINE,
)

#: Exactly two backticks, not part of a three-backtick fence.
_DOUBLE_BACKTICK = re.compile(r"(?<!`)``(?!`)")

#: A Sphinx attribute comment.
_SPHINX_ATTRIBUTE = re.compile(r"^\s*#:", re.MULTILINE)


def problems_in(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(ROOT).as_posix()
    checks = (
        (_RST_ROLE, "reST role; use Markdown inline code"),
        (_NUMPY_SECTION, "NumPy-style section; use Google style"),
        (_DOUBLE_BACKTICK, "double backticks; use single backticks"),
        (_SPHINX_ATTRIBUTE, "Sphinx `#:` comment; use a plain `#` comment"),
    )
    return [f"{relative}: {reason}" for pattern, reason in checks if pattern.search(text)]


def test_python_sources_use_google_style_and_markdown():
    problems = [problem for path in SOURCES for problem in problems_in(path)]
    assert not problems, "\n".join(problems)

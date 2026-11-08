"""Sphinx configuration for the Detree documentation site.

The Markdown files under `docs/` are the source; this file only tells Sphinx
how to assemble them. `docs/index.md` is the site root and holds every
toctree, and `docs/spec/` carries the normative specification; `spec/` holds
only the machine-readable tables that the prose mirrors.

Build locally with:

    uv pip install --group docs
    uv run sphinx-build -b html -c docs -d build/docs/doctrees -W docs build/docs/html
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Autodoc imports the package. A source checkout is enough: importing
# `detree` does not load NumPy or the compiled machine.
sys.path.insert(0, str(ROOT / "src"))

project = "Detree-VM"
author = "Mingrun Li"
copyright = "2026, Mingrun Li"  # noqa: A001 - a Sphinx setting

# The package version is the project version; the ISA, the C ABI, and the IR
# carry their own versions (see docs/spec/README.md).
try:
    from detree import __version__ as release
except Exception:  # pragma: no cover - a checkout without the generated file
    release = "0.1.0.dev0"
version = release

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "sphinxcontrib.mermaid",
]

source_suffix = {
    ".md": "markdown",
    ".rst": "restructuredtext",
}
root_doc = "index"

# The source directory is `docs/`, so only the site's own files are visible;
# `_build` is the local output.
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

language = "en"

# Markdown dialect: only what the documents actually use.
myst_enable_extensions = ["attrs_inline", "colon_fence", "deflist"]
myst_heading_anchors = 2
myst_url_schemes = ["http", "https", "mailto"]
# GitHub renders ```mermaid as a plain code block; MyST has to route the fence
# to the extension's directive instead of looking for a Pygments lexer.
myst_fence_as_directive = ["mermaid"]

autodoc_default_options = {
    "member-order": "bysource",
    "show-inheritance": True,
}
napoleon_google_docstring = True
napoleon_numpy_docstring = False
autosectionlabel_prefix_document = True
autosectionlabel_maxdepth = 2

copybutton_prompt_text = r"\$ |>>> "
copybutton_prompt_is_regexp = True

html_theme = "furo"
html_title = "Detree-VM"
html_baseurl = "https://mrzimu.github.io/detree-vm/"
html_theme_options = {"navigation_with_keys": True}
html_show_sourcelink = False
html_copy_source = False

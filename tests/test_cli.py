"""
The command-line entry point.
"""

from __future__ import annotations

import pytest
from module_builder import Builder, ins

from detree.abi.libs import compiled_backends, include_dir, library_path
from detree.cli import main
from detree.spec.isa import Op, Typecode


def one_module() -> bytes:
    builder = Builder()
    out = builder.buffer("out", "int32")
    builder.function(
        "read",
        [
            ins(Op.SREAD, rd=0, imm1=Typecode.INT32),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    return builder.build().to_bytes()


def test_backends_command(capsys):
    assert main(["backends"]) == 0

    output = capsys.readouterr().out
    assert "python" in output
    assert "cpp" in output
    assert "rust" in output


def test_paths_command(capsys):
    if not any(library_path(name) is not None for name in compiled_backends()):
        pytest.skip("no compiled machine library is installed")

    assert main(["paths"]) == 0

    output = capsys.readouterr().out
    assert f"include  {include_dir()}" in output
    assert "library" in output
    for name in compiled_backends():
        path = library_path(name)
        if path is not None:
            assert str(path) in output


def test_paths_command_reports_a_missing_machine(monkeypatch, capsys):
    from detree.abi import libs

    monkeypatch.setattr(libs, "library_path", lambda name: None)

    assert main(["paths"]) == 1
    assert "no compiled machine library" in capsys.readouterr().err


def test_disasm_prints_tables_and_code(tmp_path, capsys):
    path = tmp_path / "module.dtm"
    path.write_bytes(one_module())

    assert main(["disasm", str(path)]) == 0

    output = capsys.readouterr().out
    assert "module  isa=0.1" in output
    assert "out" in output
    assert "int32" in output
    assert "SREAD" in output
    assert "rd=r0" in output
    assert "BUF_APPEND" in output
    assert "RET" in output


def test_disasm_rejects_bytes_that_are_not_a_module(tmp_path, capsys):
    path = tmp_path / "junk.bin"
    path.write_bytes(b"not a module")

    assert main(["disasm", str(path)]) == 1
    assert "not a valid module" in capsys.readouterr().err

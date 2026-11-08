from __future__ import annotations

import zipfile

from tools.inspect_wheel import check


def test_check_ignores_library_directory_entry(tmp_path):
    path = tmp_path / "detree_vm-0.1.0-py3-none-manylinux_2_28_x86_64.whl"
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr("detree/_libs/", "")
        wheel.writestr("detree/_libs/librustdetree.so", "")
        wheel.writestr("detree/include/detree.h", "")
        wheel.writestr("detree/py.typed", "")

    assert check(path) == []

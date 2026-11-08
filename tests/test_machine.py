"""
The public machine: build instances, run baskets, read results.
"""

from __future__ import annotations

import struct
import threading

import pytest
from module_builder import Builder, ins

import detree
from detree.abi.libs import library_path
from detree.backends import policy, registry
from detree.spec.errors import BackendUnavailable
from detree.spec.isa import ISA_MAJOR, ISA_MINOR, Op, Typecode

# The default policy has one entry and no fallback, so any test that runs a
# module without an explicit backend needs the shipped library.
SHIPPED_AVAILABLE = library_path(policy.SHIPPED) is not None
SKIP_NO_SHIPPED = pytest.mark.skipif(
    not SHIPPED_AVAILABLE,
    reason=f"the shipped {policy.SHIPPED} library is not built",
)


def read_one_module() -> bytes:
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


def available(name: str) -> bool:
    try:
        registry.get(name)
    except BackendUnavailable:
        return False
    return True


def test_public_names_are_exported():
    for name in detree.PUBLIC_API:
        assert hasattr(detree, name), name


def test_the_public_api_is_the_machine_surface():
    assert set(detree.PUBLIC_API) == {"Machine", "compile", "include_dir"}
    for name in ("Program", "run", "use", "backend", "available_backends"):
        assert not hasattr(detree, name), name


@SKIP_NO_SHIPPED
def test_machine_runs_and_reuses_its_instance():
    with detree.Machine(read_one_module()) as machine:
        first = machine.run(struct.pack(">i", 41), [0, 4])
        second = machine.run(struct.pack(">i", 42), [0, 4])

    assert first["out"].tolist() == [41]
    assert second["out"].tolist() == [42]


def test_machine_selects_each_backend():
    raw = read_one_module()
    with detree.Machine(raw, backend="python") as machine:
        reference = machine.run(struct.pack(">i", 7), [0, 4])

    assert reference["out"].tolist() == [7]
    for name in ("cpp", "rust"):
        if not available(name):
            continue
        with detree.Machine(raw, backend=name) as machine:
            outputs = machine.run(struct.pack(">i", 7), [0, 4])
        assert outputs["out"].tolist() == [7]


def test_two_machines_from_the_same_bytes_share_nothing():
    raw = read_one_module()
    with detree.Machine(raw, backend="python") as first:
        with detree.Machine(raw, backend="python") as second:
            assert first.run(struct.pack(">i", 1), [0, 4])["out"].tolist() == [1]
            assert second.run(struct.pack(">i", 2), [0, 4])["out"].tolist() == [2]
            assert first.run(struct.pack(">i", 3), [0, 4])["out"].tolist() == [3]


def test_machine_class():
    with detree.Machine(read_one_module(), backend="python") as machine:
        assert machine.backend == "python"
        assert machine.isa == (ISA_MAJOR, ISA_MINOR)
        assert machine.run(struct.pack(">i", 5), [0, 4])["out"].tolist() == [5]
        machine.reset()


def test_independent_machines_do_not_share_state():
    raw = read_one_module()
    results = {}
    barrier = threading.Barrier(2)

    def worker(value):
        barrier.wait()
        with detree.Machine(raw, backend="python") as machine:
            for _ in range(50):
                outputs = machine.run(struct.pack(">i", value), [0, 4])
                assert outputs["out"].tolist() == [value]
        results[value] = outputs["out"].tolist()

    threads = [threading.Thread(target=worker, args=(value,)) for value in (11, 22)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == {11: [11], 22: [22]}


@SKIP_NO_SHIPPED
def test_the_shipped_machine_is_per_thread():
    raw = read_one_module()
    results = {}
    barrier = threading.Barrier(2)

    def worker(value):
        barrier.wait()
        with detree.Machine(raw) as machine:
            for _ in range(100):
                outputs = machine.run(struct.pack(">i", value), [0, 4])
                assert outputs["out"].tolist() == [value]
        results[value] = outputs["out"].tolist()

    threads = [threading.Thread(target=worker, args=(value,)) for value in (7, 9)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == {7: [7], 9: [9]}


def test_compile_passes_module_bytes_through():
    raw = read_one_module()
    assert detree.compile(raw) == raw
    with pytest.raises(TypeError):
        detree.compile(object())


def test_compile_accepts_an_ir_program():
    from detree.ir import Local, ProgramBuilder, StreamRead

    builder = ProgramBuilder()
    out = builder.buffer("out", "int32")
    func = builder.function("read")
    func.assign(func.local("x"), StreamRead(Typecode.INT32))
    func.append(out, Local("x"))
    func.ret()

    raw = detree.compile(builder.build())
    with detree.Machine(raw, backend="python") as machine:
        assert machine.run(struct.pack(">i", 8), [0, 4])["out"].tolist() == [8]

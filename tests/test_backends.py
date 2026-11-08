"""
Backend policy and compiled-library identity.
"""

from __future__ import annotations

import ctypes

import pytest

from detree.abi.libs import library_path
from detree.backends import policy, registry
from detree.spec.errors import BackendUnavailable


def test_shipped_backend_is_known():
    assert policy.SHIPPED in registry.names()


def test_the_shipped_backend_is_the_only_default():
    assert policy.PREFERENCE == (policy.SHIPPED,)


def test_default_is_the_shipped_backend():
    if library_path(policy.SHIPPED) is None:
        pytest.skip(f"{policy.SHIPPED} is not built")
    assert registry.default().name == policy.SHIPPED


def test_default_raises_instead_of_falling_back(monkeypatch):
    monkeypatch.setattr(registry._BUILTINS[policy.SHIPPED], "available", lambda: False)
    monkeypatch.setattr(
        registry._BUILTINS[policy.SHIPPED], "reason", "librustdetree was not found"
    )

    with pytest.raises(BackendUnavailable) as error:
        registry.default()

    message = str(error.value)
    assert f"shipped backend {policy.SHIPPED!r} is unavailable" in message
    assert "librustdetree was not found" in message
    assert 'backend="python"' in message


def test_shipped_backend_object():
    assert registry.shipped().name == policy.SHIPPED


@pytest.mark.parametrize("name", ["cpp", "rust"])
def test_compiled_backend_identity(name):
    path = library_path(name)
    if path is None:
        pytest.skip(f"{name} is not built")

    library = ctypes.CDLL(str(path))
    library.detree_backend_name.restype = ctypes.c_char_p
    library.detree_backend_version.restype = ctypes.c_char_p
    library.detree_c_api_version.restype = ctypes.c_uint32
    library.detree_isa_major.restype = ctypes.c_uint16
    library.detree_isa_minor.restype = ctypes.c_uint16
    library.detree_capabilities.restype = ctypes.c_uint64

    assert library.detree_backend_name().decode() == name
    assert library.detree_c_api_version() == 1
    assert library.detree_isa_major() == 0
    assert library.detree_isa_minor() == 1
    assert isinstance(library.detree_capabilities(), int)

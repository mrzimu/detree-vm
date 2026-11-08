"""
The error hierarchy and the C status mapping.
"""

from __future__ import annotations

import pytest

from detree.abi.capi import STATUS_TO_EXCEPTION
from detree.spec.errors import (
    AssertionFailed,
    BackendUnavailable,
    DetreeError,
    InvalidByteCount,
    ModuleError,
    ModuleValidationError,
    ReadLengthMismatch,
    StackOverflow,
    StreamUnderflow,
    UnsupportedFeature,
)


@pytest.mark.parametrize(
    "error",
    [
        ModuleError,
        ModuleValidationError,
        StreamUnderflow,
        InvalidByteCount,
        ReadLengthMismatch,
        UnsupportedFeature,
        AssertionFailed,
        StackOverflow,
        BackendUnavailable,
    ],
)
def test_every_error_is_a_detree_error(error):
    assert issubclass(error, DetreeError)


def test_module_validation_error_is_a_module_error():
    assert issubclass(ModuleValidationError, ModuleError)


def test_status_mapping_covers_every_status():
    assert set(STATUS_TO_EXCEPTION) == set(range(10))
    assert STATUS_TO_EXCEPTION[0] is None
    assert STATUS_TO_EXCEPTION[1] is ModuleError

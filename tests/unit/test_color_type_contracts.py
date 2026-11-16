"""Baseline operation-token parity before the Color runtime type is enabled."""

from types import MappingProxyType

import pytest

from NodeForge.nf_types import NFType
from NodeForge.type_capabilities import (
    ATTRIBUTE_DATA_TYPES,
    CAPTURE_ITEM_TYPES,
    REPEAT_ITEM_TYPES,
    SWITCH_INPUT_TYPES,
)
from NodeForge.semantic.builtin_calls import _capture_result_type
from NodeForge.blender.geometry import _attribute_data_type
from NodeForge.blender.ir_lowering import _repeat_item_type_for_nf_type
from NodeForge.errors import CompileError

pytestmark = pytest.mark.unit


def test_supported_operation_tokens_match_pre_color_contract():
    """One immutable map supplies each existing semantic and backend token."""
    assert all(isinstance(table, MappingProxyType) for table in (
        SWITCH_INPUT_TYPES, REPEAT_ITEM_TYPES, CAPTURE_ITEM_TYPES, ATTRIBUTE_DATA_TYPES
    ))
    assert dict(SWITCH_INPUT_TYPES) == {
        NFType.FLOAT: 'FLOAT', NFType.INT: 'INT', NFType.VECTOR: 'VECTOR',
        NFType.BOOL: 'BOOLEAN', NFType.GEOMETRY: 'GEOMETRY',
        NFType.STRING: 'STRING', NFType.BUNDLE: 'BUNDLE',
        NFType.COLOR: 'RGBA',
    }
    assert dict(REPEAT_ITEM_TYPES) == {
        NFType.GEOMETRY: 'GEOMETRY', NFType.VECTOR: 'VECTOR',
        NFType.FLOAT: 'FLOAT', NFType.INT: 'INT', NFType.BOOL: 'BOOLEAN',
        NFType.BUNDLE: 'BUNDLE', NFType.COLOR: 'RGBA',
    }
    assert dict(CAPTURE_ITEM_TYPES) == {
        NFType.FLOAT: 'FLOAT', NFType.INT: 'INT', NFType.BOOL: 'BOOLEAN',
        NFType.VECTOR: 'VECTOR',
    }
    assert dict(ATTRIBUTE_DATA_TYPES) == {
        NFType.FLOAT: 'FLOAT', NFType.INT: 'INT', NFType.VECTOR: 'FLOAT_VECTOR',
        NFType.BOOL: 'BOOLEAN',
    }
    for typ, token in REPEAT_ITEM_TYPES.items():
        assert _repeat_item_type_for_nf_type(typ) == token
    for typ, token in CAPTURE_ITEM_TYPES.items():
        assert _capture_result_type(typ, None) is typ
        assert _capture_result_type(NFType.FLOAT, token) is typ
    for typ, token in ATTRIBUTE_DATA_TYPES.items():
        assert _attribute_data_type(typ) == token


def test_unsupported_capabilities_and_explicit_capture_conversions():
    """Unsupported operations reject, while explicit overrides choose result types."""
    assert NFType.ROTATION not in SWITCH_INPUT_TYPES
    assert NFType.STRING not in REPEAT_ITEM_TYPES
    assert NFType.BUNDLE not in CAPTURE_ITEM_TYPES
    assert NFType.BUNDLE not in ATTRIBUTE_DATA_TYPES
    assert _capture_result_type(NFType.FLOAT, 'VECTOR') is NFType.VECTOR
    assert _capture_result_type(NFType.VECTOR, 'FLOAT') is NFType.FLOAT
    with pytest.raises(CompileError, match='supports Float'):
        _capture_result_type(NFType.FLOAT, 'INVALID')
    with pytest.raises(CompileError, match='repeat_range state has unsupported type'):
        _repeat_item_type_for_nf_type(NFType.STRING)

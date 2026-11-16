"""Blender integration checks for the first-class NodeForge Color type."""

import pytest

from NodeForge import compiler
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType, deserialize_nf_type, serialize_nf_type
from NodeForge.blender.socket_types import socket_type_for_nf_type, runtime_socket_nf_type
from NodeForge.semantic.callable_contracts import canonicalize_group_input_default
from NodeForge.semantic.constants import TYPE_TOKEN_NAMES

pytestmark = pytest.mark.blender


def _group(source: str):
    """Materialize one standalone .nf source for physical socket assertions."""
    return compiler.create_expression_group(source, 'NFTest_Color_Runtime')


def _interfaces(group, direction: str):
    """Enumerate public interface sockets of one direction in creation order."""
    return [item for item in group.interface.items_tree
            if item.item_type == 'SOCKET' and item.in_out == direction]


def test_color_identity_and_defaulted_group_interfaces():
    """Color is an exact runtime type, with four persistent RGBA components."""
    assert serialize_nf_type(NFType.COLOR) == 'COLOR'
    assert deserialize_nf_type('COLOR') is NFType.COLOR
    assert TYPE_TOKEN_NAMES['Color'] is NFType.COLOR
    assert socket_type_for_nf_type(NFType.COLOR) == 'NodeSocketColor'
    group = _group('''def echo(value: Color = (0.2, 0.3, 0.4, 0.5)) -> Color:
    return value
''')
    ins, outs = _interfaces(group, 'INPUT'), _interfaces(group, 'OUTPUT')
    assert len(ins) == len(outs) == 1
    assert ins[0].socket_type == outs[0].socket_type == 'NodeSocketColor'
    assert tuple(ins[0].default_value) == pytest.approx((0.2,0.3,0.4,0.5),abs=1e-7)
    assert runtime_socket_nf_type(next(n for n in group.nodes if n.bl_idname == 'NodeGroupInput').outputs['value']) is NFType.COLOR


def test_color_required_and_multiple_returns():
    """Required Color inputs and two Color outputs retain socket identities."""
    group = _group('''def pair(value: Color):
    return (value, value)
''')
    assert [x.socket_type for x in _interfaces(group,'INPUT')] == ['NodeSocketColor']
    assert [x.socket_type for x in _interfaces(group,'OUTPUT')] == ['NodeSocketColor','NodeSocketColor']
    assert all(link.is_valid for link in group.links)


@pytest.mark.parametrize('raw', [
    '(0.1, 0.2, 0.3)', '(0.1,0.2,0.3,0.4,0.5)',
    '(True,0.2,0.3,0.4)', '("a",0.2,0.3,0.4)',
    '(-0.1,0.2,0.3,0.4)', '(1e50,0.2,0.3,0.4)',
])
def test_invalid_group_color_defaults_fail_at_signature_boundary(raw):
    """Reject invalid RGBA components before creating an incorrect physical group."""
    with pytest.raises(CompileError, match='Color default|Float value'):
        _group(f'def echo(value: Color = {raw}) -> Color:\n    return value\n')


def test_color_hdr_and_vector_default_independence():
    """Finite HDR Color defaults survive; Vector keeps three components."""
    group = _group('''def keep_color(value: Color = (2.0,0.3,0.4,1.5)) -> Color:
    return value
''')
    assert tuple(_interfaces(group,'INPUT')[0].default_value) == pytest.approx((2.0,0.3,0.4,1.5))
    assert canonicalize_group_input_default(NFType.VECTOR, (1,2,3)) == (1.0,2.0,3.0)
    with pytest.raises(CompileError):
        canonicalize_group_input_default(NFType.VECTOR, (1,2,3,4))

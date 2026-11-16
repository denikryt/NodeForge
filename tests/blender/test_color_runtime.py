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


def test_raw_color_output_input_and_named_outputs_preserve_physical_types():
    """A Color Ramp output links to a Color consumer and named output values."""
    group = _group('''def colors(factor: Float):
    sample = node('ShaderNodeValToRGB', inputs={'Factor': factor}, outputs={'Color': Color, 'Alpha': Float})
    red = node('FunctionNodeSeparateColor', inputs={'Color': sample.Color}, output='Red', typ=Float)
    return (sample.Color, sample.Alpha, red)
''')
    ramp = next(n for n in group.nodes if n.bl_idname == 'ShaderNodeValToRGB')
    separator = next(n for n in group.nodes if n.bl_idname == 'FunctionNodeSeparateColor')
    assert ramp.outputs['Color'].bl_idname == 'NodeSocketColor'
    assert separator.inputs['Color'].is_linked
    assert [x.socket_type for x in _interfaces(group,'OUTPUT')] == [
        'NodeSocketColor','NodeSocketFloat','NodeSocketFloat'
    ]
    assert all(link.is_valid for link in group.links)
    from NodeForge.blender.raw_nodes import raw_node_spec, RAW_SCHEMA_VERSION
    assert raw_node_spec(ramp)['schema_version'] == RAW_SCHEMA_VERSION
    assert raw_node_spec(ramp)['outputs'][0].socket_type == 'NodeSocketColor'


def test_raw_color_literal_metadata_copy_and_validation():
    """Four-component Color literals keep alpha and their durable physical identity."""
    import bpy
    from NodeForge.blender.raw_nodes import copy_raw_node_properties, raw_node_spec, validate_raw_node_after_cutover

    group = _group('''def color_red() -> Float:
    return node('FunctionNodeSeparateColor', inputs={'Color':(2.0,0.2,0.3,0.4)}, output='Red', typ=Float)
''')
    src = next(n for n in group.nodes if n.bl_idname == 'FunctionNodeSeparateColor')
    assert tuple(src.inputs['Color'].default_value) == pytest.approx((2.0,0.2,0.3,0.4))
    spec = raw_node_spec(src)
    assert spec['schema_version'] == 2
    assert spec['inputs'][0]['socket'].socket_type == 'NodeSocketColor'
    assert spec['inputs'][0]['default'] == pytest.approx([2.0,0.2,0.3,0.4])
    dst = group.nodes.new('FunctionNodeSeparateColor')
    copy_raw_node_properties(src, dst)
    assert tuple(dst.inputs['Color'].default_value) == pytest.approx((2.0,0.2,0.3,0.4))
    validate_raw_node_after_cutover(dst, group)


@pytest.mark.parametrize('value,part', [
    ('(0.1,0.2,0.3)', 'Color default'),
    ('(0.1,0.2,0.3,0.4,0.5)', '3-/4-tuples'),
    ('(True,0.2,0.3,0.4)', '3-/4-tuples'),
    ('(-0.1,0.2,0.3,0.4)', 'Color default'),
])
def test_raw_color_bad_literals_fail(value, part):
    """Reject malformed Color literals at the nearest type-aware boundary."""
    with pytest.raises(CompileError, match=part):
        _group(f'''def red() -> Float:
    return node('FunctionNodeSeparateColor', inputs={{'Color':{value}}}, output='Red', typ=Float)
''')


def test_raw_color_vector_mismatches_fail_before_links():
    """Typed output declarations and input links must match physical socket types."""
    with pytest.raises(CompileError, match='expects COLOR, got VECTOR'):
        _group('''def wrong(f: Float) -> Vector:
    return node('ShaderNodeValToRGB', inputs={'Factor':f}, output='Color', typ=Vector)
''')
    with pytest.raises(CompileError, match='expects VECTOR, got COLOR'):
        _group('''def wrong() -> Color:
    return node('GeometryNodeInputPosition', output='Position', typ=Color)
''')
    with pytest.raises(CompileError, match='expects COLOR, got VECTOR'):
        _group('''def wrong() -> Float:
    return node('FunctionNodeSeparateColor', inputs={'Color':position()}, output='Red', typ=Float)
''')
    with pytest.raises(CompileError, match='actual Color socket'):
        _group('''def wrong() -> Vector:
    return node('ShaderNodeVectorMath', inputs={0:(1.0,0.2,0.3,0.4)}, output='Vector', typ=Vector)
''')

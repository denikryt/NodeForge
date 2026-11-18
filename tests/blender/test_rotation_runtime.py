"""Real Blender checks for public Rotation socket identity and interface defaults."""

from __future__ import annotations

import json

import bpy
import pytest
from mathutils import Euler

from NodeForge import compiler, packages
from NodeForge.blender.interface import _get_group_input_defaults
from NodeForge.blender.socket_types import runtime_socket_nf_type
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.semantic.numeric_semantics import normalize_float_constant

pytestmark = pytest.mark.blender


def _inputs(group, direction):
    """Select the public sockets of one Blender interface direction."""
    return [item for item in group.interface.items_tree
            if item.item_type == 'SOCKET' and item.in_out == direction]


def _compile(source, name):
    """Build a standalone FunctionDef group through the public compiler."""
    return compiler.create_expression_group(source, name)


def test_rotation_default_required_and_euler_readback():
    """Materialized Rotation uses a separate physical socket and Euler XYZ readback."""
    source = '''def echo(value: Rotation = (0.1, -0.2, 7.0)) -> Rotation:
    return value
'''
    group = _compile(source, 'NFTest_RotationDefault')
    inp, out = _inputs(group, 'INPUT'), _inputs(group, 'OUTPUT')
    assert len(inp) == len(out) == 1
    assert inp[0].socket_type == out[0].socket_type == 'NodeSocketRotation'
    assert isinstance(inp[0].default_value, Euler)
    assert inp[0].default_value.order == 'XYZ'
    expected = tuple(normalize_float_constant(v) for v in (0.1, -0.2, 7.0))
    assert tuple(inp[0].default_value) == expected
    assert runtime_socket_nf_type(next(n for n in group.nodes if n.bl_idname == 'NodeGroupInput').outputs[0]) is NFType.ROTATION
    assert all(link.is_valid for link in group.links)
    records = _get_group_input_defaults(group)
    assert len(records) == 1
    entry = next(iter(records.values()))
    assert entry['nf_type'] == 'ROTATION'
    assert entry['socket_type'] == 'NodeSocketRotation'
    assert tuple(entry['default']) == expected

    required = _compile('''def required(value: Rotation) -> Rotation:
    return value
''', 'NFTest_RotationRequired')
    assert _inputs(required, 'INPUT')[0].socket_type == 'NodeSocketRotation'
    missing_call = bpy.data.node_groups.new('NFTest_RotationMissingRequired', 'GeometryNodeTree')
    with pytest.raises(CompileError):
        compiler.compose_source_initial('''def need_rotation(value: Rotation) -> Rotation:
    return value

result = need_rotation()
''', missing_call)


def test_rotation_raw_nodes_convert_euler_explicitly_and_link_instances():
    """Euler-to-Rotation outputs connect to Rotation inputs without implicit casts."""
    group = _compile('''def wrap_instance_on_points(point_geo: Geometry, instance: Geometry,
                              rotation: Rotation = (0.0, 0.0, 0.0)) -> Geometry:
    return node('GeometryNodeInstanceOnPoints',
                inputs={ID('Points'): point_geo, ID('Instance'): instance,
                        ID('Rotation'): rotation},
                output=ID('Instances'), typ=Geometry)
''', 'NFTest_RotationInstances')
    raw = next(n for n in group.nodes if n.bl_idname == 'GeometryNodeInstanceOnPoints')
    assert raw.inputs['Rotation'].bl_idname == 'NodeSocketRotation'
    assert raw.inputs['Rotation'].is_linked
    assert all(link.is_valid for link in group.links)

    converted = _compile('''def convert(euler: Vector) -> Rotation:
    return node('FunctionNodeEulerToRotation', inputs={ID('Euler'): euler},
                output=ID('Rotation'), typ=Rotation)
''', 'NFTest_RotationConvert')
    node = next(n for n in converted.nodes if n.bl_idname == 'FunctionNodeEulerToRotation')
    assert node.outputs['Rotation'].bl_idname == 'NodeSocketRotation'
    assert all(link.is_valid for link in converted.links)

    named = _compile('''def converted(euler: Vector) -> Rotation:
    out = node('FunctionNodeEulerToRotation', inputs={ID('Euler'): euler},
               outputs={'Rotation': Rotation})
    return out.Rotation
''', 'NFTest_RotationNamed')
    assert _inputs(named, 'OUTPUT')[0].socket_type == 'NodeSocketRotation'
    assert all(link.is_valid for link in named.links)


@pytest.mark.parametrize('source', [
    '''def instance_on_points(point_geo: Geometry, instance: Geometry) -> Geometry:
    return point_geo
''',
    '''def invalid(points: Geometry) -> Geometry:
    return points
''',
    '''def invalid(value: Vector) -> Rotation:
    return value
''',
    '''def invalid(value: Vector) -> Rotation:
    return node('GeometryNodeInputPosition', output='Position', typ=Rotation)
''',
])
def test_rotation_invalid_dsl_boundaries_rejected(source):
    """Existing builtin reservations and exact physical type rules still apply."""
    with pytest.raises(CompileError):
        _compile(source, 'NFTest_RotationInvalid')


def test_rotation_package_function_call_keeps_runtime_identity(tmp_path):
    """An imported source callable forwards Rotation through a package group."""
    root = tmp_path / 'rotation_package'
    functions = root / 'functions'
    functions.mkdir(parents=True)
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.rotation.source', 'import_name': 'rotation_source',
        'name': 'Rotation Source', 'version': '1.0.0', 'author': 'Tests',
        'nodeforge_min_version': '0.65.0', 'contents': {'functions': 'functions'},
    }), encoding='utf-8')
    (functions / 'rotate.nf').write_text('''def forward(value: Rotation) -> Rotation:
    return value
''', encoding='utf-8')
    packages.install_package_directory(root)
    group = _compile('''from packages import rotation_source

def bridge(value: Rotation) -> Rotation:
    return rotation_source.forward(value)
''', 'NFTest_RotationPkg')
    assert _inputs(group, 'INPUT')[0].socket_type == 'NodeSocketRotation'
    assert _inputs(group, 'OUTPUT')[0].socket_type == 'NodeSocketRotation'
    assert all(link.is_valid for link in group.links)

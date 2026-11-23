"""Blender 5.2 socket/default and lifecycle checks for omitted Extension API v2 parameters."""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import bpy
import pytest

from helpers import compile_group
from NodeForge import compiler, packages
from NodeForge.blender.extension_group_materializer import (
    EXTENSION_AUTHORITY_PROP, EXTENSION_FINGERPRINT_PROP,
)
from NodeForge.errors import CompileError

pytestmark = pytest.mark.blender


@pytest.fixture(autouse=True)
def track_optional_writes():
    """Record only socket writes by the test backend, not Blender initialization."""
    builtins._nf_optional_writes = []
    yield
    del builtins._nf_optional_writes


def _install_optional_package(root: Path):
    """Register one synthetic backend that only configures explicitly supplied sockets."""
    owner = root / 'systems' / 'resample'
    owner.mkdir(parents=True)
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1,
        'id': 'vendor.optional.resample',
        'name': 'Optional Resample',
        'version': '1.0.0',
        'author': 'Tests',
        'description': 'Omission default preservation probe',
        'nodeforge_min_version': '0.97.4',
        'nodeforge_max_version': None,
        'contents': {'systems': 'systems'},
        'permissions': {'python': True},
    }), encoding='utf-8')
    (owner / 'interface.py').write_text('''from typing import Annotated
from NodeForge import Bool, EvaluationMode, Float, Geometry, Int, String
EXTENSION_API = 2
EXTENSIONS = {'resample_curve': '.backend:resample_curve'}
def resample_curve(
    curve: Annotated[Geometry, EvaluationMode.RUNTIME_ONLY],
    *,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = None,
    count: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    length: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.RUNTIME_ONLY] = None,
) -> Geometry: ...
''', encoding='utf-8')
    (owner / 'backend.py').write_text('''import builtins
from NodeForge.extension_api import ExtensionBackendValue, NFType

def resample_curve(context, curve, *, mode=None, count=None, length=None, selection=None):
    node = context.group.nodes.new('GeometryNodeResampleCurve')
    node.location = context.location
    context.group.links.new(curve.socket, node.inputs['Curve'])
    if mode == 'BROKEN':
        context.new_generated_mesh(role='optional-failure-probe', name_hint='OptionalFailureProbe')
        raise ValueError('intentional optional extension failure')
    if mode is not None:
        if mode not in {'Count', 'Length', 'Evaluated'}:
            raise ValueError('invalid Resample Curve mode')
        node.inputs['Mode'].default_value = mode
        builtins._nf_optional_writes.append('Mode')
    effective_mode = node.inputs['Mode'].default_value
    if count is not None and effective_mode != 'Count':
        raise ValueError('Count is unavailable in selected mode')
    if length is not None and effective_mode != 'Length':
        raise ValueError('Length is unavailable in selected mode')
    for key, argument in (('Count', count), ('Length', length), ('Selection', selection)):
        if argument is None:
            continue
        if isinstance(argument, ExtensionBackendValue):
            context.group.links.new(argument.socket, node.inputs[key])
        else:
            node.inputs[key].default_value = argument
            builtins._nf_optional_writes.append(key)
    return context.value(node.outputs['Curve'], NFType.GEOMETRY)
''', encoding='utf-8')
    packages.install_package_directory(root)


@pytest.fixture
def optional_package(tmp_path):
    """Keep each test package version independent of user-installed inventory."""
    _install_optional_package(tmp_path / 'optional-source')


def _source(call, *, args='curve: Geometry', statements=None):
    """Build a normal, compiled package call in a function body."""
    body = statements or f'return {call}'
    return f'from packages import resample\n\ndef nf_fixture({args}) -> Geometry:\n    {body}\n'


def _resample_nodes(group):
    """Find native resample nodes in the provided tree without relying on node labels."""
    return [node for node in group.nodes if node.bl_idname == 'GeometryNodeResampleCurve']


def _assert_native_defaults(group, *, mode='Count', count=10, length=0.1):
    """Require Blender's actual input defaults and no optional socket links."""
    node, = _resample_nodes(group)
    assert node.inputs['Mode'].default_value == mode
    assert node.inputs['Count'].default_value == count
    assert node.inputs['Length'].default_value == pytest.approx(length)
    assert not any(link.to_socket in {node.inputs['Mode'], node.inputs['Count'], node.inputs['Length'],
                                       node.inputs['Selection']} for link in group.links)
    return node


@pytest.mark.parametrize('inline', [None, True, False])
def test_omitted_sockets_stay_at_blender_defaults_in_both_modes(optional_package, inline):
    """Only the Curve runtime operand is present; Count/Length retain native defaults."""
    modifier = '' if inline is None else f', __inline__={inline}'
    group = compile_group(_source(f'resample_curve(curve{modifier})'), 'NFOptionalDefault_'+str(inline))
    if inline is False:
        node, = [node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']
        child = node.node_tree
        assert [sock.name for sock in node.inputs] == ['curve']
        assert child.get(EXTENSION_AUTHORITY_PROP)
    else:
        child = group
    _assert_native_defaults(child)
    assert builtins._nf_optional_writes == []


@pytest.mark.parametrize('call,mode,count,length,writes', [
    ('resample_curve(curve, count=20)', 'Count', 20, 0.1, ['Count']),
    ('resample_curve(curve, mode="Length")', 'Length', 10, 0.1, ['Mode']),
    ('resample_curve(curve, mode="Length", length=0.5)', 'Length', 10, 0.5, ['Mode', 'Length']),
])
def test_explicit_static_inputs_only_update_selected_sockets(optional_package, call, mode, count, length, writes):
    """The test wrapper owns conditional socket assignment and does not duplicate defaults."""
    group = compile_group(_source(call), 'NFOptionalStatic_'+mode+str(count))
    _assert_native_defaults(group, mode=mode, count=count, length=length)
    assert builtins._nf_optional_writes == writes


def test_runtime_count_and_runtime_only_selection_link_sockets(optional_package):
    """Explicit runtime optional arguments become links; omitted ones do not."""
    group = compile_group(_source(
        'resample_curve(curve, count=count, selection=selection)',
        args='curve: Geometry, count: Int, selection: Bool',
    ), 'NFOptionalRuntime')
    node, = _resample_nodes(group)
    for name in ('Count', 'Selection'):
        assert len([link for link in group.links if link.to_socket == node.inputs[name]]) == 1
    assert node.inputs['Mode'].default_value == 'Count'
    assert node.inputs['Length'].default_value == pytest.approx(0.1)
    assert builtins._nf_optional_writes == []


def test_group_runtime_inputs_only_and_fingerprint_tracks_static_none(optional_package):
    """Grouped children keep only runtime sockets and rebuild after static call changes."""
    initial = _source('resample_curve(curve, __inline__=False)')
    group = compile_group(initial, 'NFOptionalGroupFingerprint')
    node, = [node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert [sock.name for sock in node.inputs] == ['curve']
    old_authority = node.node_tree.get(EXTENSION_AUTHORITY_PROP)
    old_fingerprint = node.node_tree.get(EXTENSION_FINGERPRINT_PROP)
    old_pointer = node.node_tree.as_pointer()
    compiler.update_expression_group(group, initial)
    same, = [node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert same.node_tree.as_pointer() == old_pointer
    assert same.node_tree.get(EXTENSION_FINGERPRINT_PROP) == old_fingerprint
    modified = _source('resample_curve(curve, count=20, __inline__=False)')
    compiler.update_expression_group(group, modified)
    refreshed, = [node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert refreshed.node_tree.get(EXTENSION_AUTHORITY_PROP) == old_authority
    assert refreshed.node_tree.get(EXTENSION_FINGERPRINT_PROP) != old_fingerprint
    _assert_native_defaults(refreshed.node_tree, count=20)


@pytest.mark.parametrize('inline', [True, False])
def test_inapplicable_explicit_socket_is_rejected_and_rolled_back(optional_package, inline):
    """Package-specific Count/Length validation rolls back unsuccessful compilation."""
    before_groups = {g.as_pointer() for g in bpy.data.node_groups}
    before_meshes = {mesh.as_pointer() for mesh in bpy.data.meshes}
    with pytest.raises(CompileError, match='Count is unavailable'):
        compile_group(_source(f'resample_curve(curve, mode="Length", count=20, __inline__={inline})'),
                      'NFOptionalInvalid'+str(inline))
    assert {g.as_pointer() for g in bpy.data.node_groups} == before_groups
    assert {mesh.as_pointer() for mesh in bpy.data.meshes} == before_meshes


def test_backend_resource_failure_rolls_back_groups_and_meshes(optional_package):
    """An intentionally failed grouped call releases its generated resource transaction."""
    before_groups = {g.as_pointer() for g in bpy.data.node_groups}
    before_meshes = {mesh.as_pointer() for mesh in bpy.data.meshes}
    with pytest.raises(CompileError, match='intentional optional extension failure'):
        compile_group(_source('resample_curve(curve, mode="BROKEN", __inline__=False)'),
                      'NFOptionalResourceFailure')
    assert {g.as_pointer() for g in bpy.data.node_groups} == before_groups
    assert {mesh.as_pointer() for mesh in bpy.data.meshes} == before_meshes


def test_nested_and_root_materialization_preserves_omitted_defaults(optional_package):
    """Nested calls and executable root composition share the same omission semantics."""
    group = compile_group(
        _source('resample_curve(resample_curve(curve))'), 'NFOptionalNested'
    )
    nested = _resample_nodes(group)
    assert len(nested) == 2
    assert all(node.inputs['Mode'].default_value == 'Count' for node in nested)
    assert all(node.inputs['Count'].default_value == 10 for node in nested)

    root = bpy.data.node_groups.new('NFOptionalRootComposition', 'GeometryNodeTree')
    source = '''from packages import resample
curve = node("GeometryNodeCurvePrimitiveLine", output="Curve", typ=Geometry)
result = resample_curve(curve)
'''
    outcome = compiler.compile_source_in_tree(source, root)
    assert outcome.outcome == 'ROOT_REPLACEMENT'
    _assert_native_defaults(root)


def test_grouped_runtime_optional_sockets_have_only_supplied_inputs(optional_package):
    """The child interface includes runtime Count and Selection but not omitted Mode/Length."""
    group = compile_group(_source(
        'resample_curve(curve, count=count, selection=selection, __inline__=False)',
        args='curve: Geometry, count: Int, selection: Bool',
    ), 'NFOptionalGroupedRuntime')
    group_node, = [node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert [socket.name for socket in group_node.inputs] == ['curve', 'count', 'selection']
    assert [socket.name for socket in group_node.outputs] == ['Result']
    child = group_node.node_tree
    resample, = _resample_nodes(child)
    assert resample.inputs['Mode'].default_value == 'Count'
    assert resample.inputs['Length'].default_value == pytest.approx(0.1)
    assert len([link for link in child.links if link.to_socket == resample.inputs['Count']]) == 1
    assert len([link for link in child.links if link.to_socket == resample.inputs['Selection']]) == 1
    assert builtins._nf_optional_writes == []


def test_optional_compile_time_mode_rejects_runtime_source(optional_package):
    """An omitted parameter does not bypass COMPILE_TIME_ONLY for a supplied value."""
    before = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match='compile time'):
        compile_group(_source('resample_curve(curve, mode=mode)', args='curve: Geometry, mode: String'),
                      'NFOptionalInvalidRuntimeMode')
    assert {group.as_pointer() for group in bpy.data.node_groups} == before

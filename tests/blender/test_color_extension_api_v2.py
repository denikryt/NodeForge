"""Blender physical integration tests for typed Color values in Extension API v2."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import bpy
import pytest

from helpers import compile_group
from NodeForge import Color, packages
from NodeForge.errors import CompileError
from NodeForge.extension_api import ExtensionBackendContext
from NodeForge.extension_annotations import MARKER_TO_NF_TYPE
from NodeForge.nf_types import NFType

pytestmark = pytest.mark.blender


def _write_color_extension(root: Path, *, source_functions: bool = False) -> None:
    """Install one owner-local package exercising exact and finite-union Color arguments."""
    owner = root / 'systems' / 'color_runtime'
    owner.mkdir(parents=True)
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1,
        'id': 'vendor.extension.color_runtime',
        'name': 'Color Runtime Extension',
        'version': '1.0.0',
        'author': 'Tests',
        'description': 'Typed Color Extension v2 fixture',
        'nodeforge_min_version': '0.59.0',
        'nodeforge_max_version': None,
        'contents': {'systems': 'systems', **({'functions': 'functions'} if source_functions else {})},
        'permissions': {'python': True},
    }), encoding='utf-8')
    (owner / 'interface.py').write_text('''
from typing import Annotated
from NodeForge import EvaluationMode, Float, Color, Vector
EXTENSION_API = 2
EXTENSIONS = {
    "make_color": ".operations:make_color",
    "identity_color": ".operations:identity_color",
    "union_component": ".operations:union_component",
    "bad_color_result": ".operations:bad_color_result",
    "emit_static_color": ".operations:emit_color",
    "emit_mixed_color": ".operations:emit_color",
}
def make_color(factor: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Color: ...
def identity_color(value: Annotated[Color, EvaluationMode.RUNTIME_ONLY]) -> Color: ...
def union_component(value: Annotated[Color | Vector, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
def bad_color_result(factor: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Color: ...
def emit_static_color(tint: Annotated[Color, EvaluationMode.COMPILE_TIME_ONLY] = (2.0,0.2,0.3,0.5)) -> Color: ...
def emit_mixed_color(tint: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = (0.1,0.2,0.3,0.4)) -> Color: ...
''', encoding='utf-8')
    (owner / 'operations.py').write_text('''
from NodeForge.extension_api import ExtensionBackendValue, NFType

def make_color(context, factor):
    node = context.group.nodes.new('ShaderNodeValToRGB')
    context.group.links.new(factor.socket, node.inputs['Factor'])
    return context.value(node.outputs['Color'], NFType.COLOR)

def identity_color(context, value):
    if value.typ is not NFType.COLOR:
        raise AssertionError('Color argument lost its runtime identity')
    return value

def union_component(context, value):
    node_id = 'FunctionNodeSeparateColor' if value.typ is NFType.COLOR else 'ShaderNodeSeparateXYZ'
    node = context.group.nodes.new(node_id)
    context.group.links.new(value.socket, node.inputs[0])
    name = 'Red' if value.typ is NFType.COLOR else 'X'
    return context.value(node.outputs[name], NFType.FLOAT)

def emit_color(context, tint):
    node = context.group.nodes.new('GeometryNodeSwitch')
    node.input_type = 'RGBA'
    node.inputs['Switch'].default_value = True
    if isinstance(tint, ExtensionBackendValue):
        if tint.typ is not NFType.COLOR:
            raise AssertionError('mixed runtime tint lost Color identity')
        context.group.links.new(tint.socket, node.inputs['True'])
    else:
        if not isinstance(tint, tuple) or len(tint) != 4:
            raise AssertionError('static Color was not detached as RGBA tuple')
        node.inputs['True'].default_value = tint
    return context.value(node.outputs[0], NFType.COLOR)

def bad_color_result(context, factor):
    node = context.group.nodes.new('ShaderNodeValToRGB')
    return ExtensionBackendValue(node.outputs['Color'], NFType.VECTOR)
''', encoding='utf-8')
    if source_functions:
        functions = root / 'functions'
        functions.mkdir()
        (functions / 'color_bridge.nf').write_text('''from packages import color_runtime

def extension_to_raw(factor: Float):
    tint = make_color(factor)
    red = node('FunctionNodeSeparateColor', inputs={'Color': tint}, output='Red', typ=Float)
    return (tint, red)

def raw_to_extension(factor: Float) -> Color:
    tint = node('ShaderNodeValToRGB', inputs={'Factor': factor}, output='Color', typ=Color)
    return identity_color(tint)
''', encoding='utf-8')


def test_public_color_marker_and_v2_runtime_bridge():
    """Typed Color travels between a Python-backed node and an ordinary .nf function boundary."""
    assert MARKER_TO_NF_TYPE[Color] is NFType.COLOR
    with pytest.raises(TypeError, match='annotation markers'):
        Color()
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / 'package'
        _write_color_extension(source)
        packages.install_package_directory(source)
        group = compile_group('''from packages import color_runtime

def bridge(color: Color, factor: Float) -> Color:
    made = make_color(factor)
    received = identity_color(color)
    return identity_color(made)
''', 'NFTest_ColorExt_Runtime')
    ramp = next(n for n in group.nodes if n.bl_idname == 'ShaderNodeValToRGB')
    assert ramp.outputs['Color'].bl_idname == 'NodeSocketColor'
    outputs = [x.socket_type for x in group.interface.items_tree if getattr(x,'item_type',None)=='SOCKET' and x.in_out=='OUTPUT']
    assert outputs == ['NodeSocketColor']
    assert all(link.is_valid for link in group.links)


def test_color_vector_finite_union_dispatch_uses_actual_runtime_type():
    """Finite Color|Vector argument selects the concrete physical socket path."""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / 'package'
        _write_color_extension(source)
        packages.install_package_directory(source)
        group = compile_group('''from packages import color_runtime

def choose(tint: Color, direction: Vector):
    return (union_component(tint), union_component(direction))
''', 'NFTest_ColorExt_Union')
    assert sum(n.bl_idname=='FunctionNodeSeparateColor' for n in group.nodes)==1
    assert sum(n.bl_idname=='ShaderNodeSeparateXYZ' for n in group.nodes)==1
    assert all(link.is_valid for link in group.links)


def test_color_extension_parameter_rejects_vector_at_semantic_boundary():
    """A Vector argument cannot satisfy the exact runtime Color marker."""
    with tempfile.TemporaryDirectory() as tmp:
        source=Path(tmp)/'package'
        _write_color_extension(source)
        packages.install_package_directory(source)
        with pytest.raises(CompileError, match='COLOR|Color|color|type'):
            compile_group('''from packages import color_runtime

def bad(value: Vector) -> Color:
    return identity_color(value)
''','NFTest_ColorExt_BadArgument')


def test_invalid_color_backend_result_is_rejected():
    """A forged Vector wrapper on a physical Color output fails at the result boundary."""
    with tempfile.TemporaryDirectory() as tmp:
        source=Path(tmp)/'package'
        _write_color_extension(source)
        packages.install_package_directory(source)
        with pytest.raises(CompileError, match='expected COLOR'):
            compile_group('''from packages import color_runtime

def bad(factor: Float) -> Color:
    return bad_color_result(factor)
''','NFTest_ColorExt_BadResult')


def test_context_value_validates_color_output_type_direction_and_owner():
    """Type and owner validation rejects invalid physical Color sockets before values are published."""
    group=bpy.data.node_groups.new('NFTest_ColorExt_Owner','GeometryNodeTree')
    foreign=bpy.data.node_groups.new('NFTest_ColorExt_Foreign','GeometryNodeTree')
    try:
        ramp=group.nodes.new('ShaderNodeValToRGB')
        foreign_ramp=foreign.nodes.new('ShaderNodeValToRGB')
        context=ExtensionBackendContext(group=group,location=(0,0),generated_resource_transaction=object())
        assert context.value(ramp.outputs['Color'],NFType.COLOR).typ is NFType.COLOR
        with pytest.raises(CompileError, match='physical socket type'):
            context.value(ramp.outputs['Color'],NFType.VECTOR)
        with pytest.raises(CompileError, match='output sockets'):
            context.value(ramp.inputs['Factor'],NFType.FLOAT)
        with pytest.raises(CompileError, match='does not belong'):
            context.value(foreign_ramp.outputs['Color'],NFType.COLOR)
        with pytest.raises(CompileError, match='unsupported Blender runtime socket'):
            context.value(object(),NFType.COLOR)
    finally:
        bpy.data.node_groups.remove(group)
        bpy.data.node_groups.remove(foreign)


def test_color_compile_time_and_mixed_extension_use_canonical_rgba_or_typed_runtime_socket():
    """An extension sees detached RGBA tuples and exact runtime Color wrappers in separate invocations."""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / 'package'
        _write_color_extension(source)
        packages.install_package_directory(source)
        group = compile_group('''from packages import color_runtime

def outputs(tint: Color):
    a = emit_static_color()
    b = emit_static_color((3.0,0.1,0.2,0.6))
    c = emit_mixed_color()
    d = emit_mixed_color((0.2,0.3,0.4,0.5))
    runtime_color = emit_mixed_color(tint)
    return (a,b,c,d,runtime_color)
''', 'NFTest_ColorExt_StaticMixed')
        switches=[n for n in group.nodes if n.bl_idname=='GeometryNodeSwitch']
        assert len(switches)==5
        assert all(n.input_type=='RGBA' for n in switches)
        assert sum(n.inputs['True'].is_linked for n in switches)==1
        assert any(tuple(n.inputs['True'].default_value)==pytest.approx((3.0,0.1,0.2,0.6)) for n in switches)
        assert all(l.is_valid for l in group.links)


@pytest.mark.parametrize('rgba', [
    '(0.1,0.2,0.3)',
    '(0.1,0.2,0.3,0.4,0.5)',
    '(True,0.2,0.3,0.4)',
    '(0.1,-0.2,0.3,0.4)',
    '(1e100,0.2,0.3,0.4)',
])
def test_invalid_static_color_arguments_fail_in_compiler(rgba):
    """Static RGBA requires the declared four-component finite nonnegative Color contract."""
    with tempfile.TemporaryDirectory() as tmp:
        source=Path(tmp)/'package'
        _write_color_extension(source)
        packages.install_package_directory(source)
        with pytest.raises(CompileError):
            compile_group(f'''from packages import color_runtime

def bad() -> Color:
    return emit_static_color({rgba})
''', 'NFTest_ColorExt_BadStatic')


def test_package_source_color_roundtrips_through_python_and_raw_nodes(tmp_path):
    """Installed .nf callables bridge Extension API Color in both directions and multi-returns."""
    from NodeForge import compiler
    from NodeForge.environment_resolution import resolve_environment
    packages.set_packages_dir_for_tests(tmp_path / 'installed')
    try:
        root=tmp_path/'source'
        _write_color_extension(root, source_functions=True)
        packages.install_package_directory(root)
        wrapper=bpy.data.node_groups.new('NFTest_ColorExt_Root','GeometryNodeTree')
        compiler.compose_source_initial('''from packages import color_runtime

first = color_runtime.extension_to_raw(0.2)
second = color_runtime.extension_to_raw(0.3, __unique__=True)
third = color_runtime.raw_to_extension(0.5)
''', wrapper)
        groups=[n.node_tree for n in wrapper.nodes if n.bl_idname=='GeometryNodeGroup']
        assert len(groups)==3
        assert groups[0] is not groups[1]
        assert all(all(link.is_valid for link in group.links) for group in groups)
        assert any(n.bl_idname=='ShaderNodeValToRGB' for group in groups for n in group.nodes)
        assert any(n.bl_idname=='FunctionNodeSeparateColor' for group in groups for n in group.nodes)
        assert any(s.bl_idname=='NodeSocketColor' for n in wrapper.nodes for s in n.outputs)

        # Source Reload replaces only the selected .nf group while retaining external users.
        target=next(g for g in groups if any(n.bl_idname=='FunctionNodeSeparateColor' for n in g.nodes))
        pointer=target.as_pointer()
        external_links=[(l.from_socket.bl_idname,l.to_socket.bl_idname) for l in wrapper.links]
        record=resolve_environment().package_by_id('vendor.extension.color_runtime').find('extension_to_raw').record
        path=Path(record.source_path)
        old=path.read_text(encoding='utf8')
        path.write_text(old.replace("output='Red'", "output='Green'"),encoding='utf8')
        try:
            assert compiler.update_library_catalog_group(target) is target
            assert target.as_pointer()==pointer
            assert all(link.is_valid for link in target.links)
            assert any(n.bl_idname=='FunctionNodeSeparateColor' for n in target.nodes)
            assert [(l.from_socket.bl_idname,l.to_socket.bl_idname) for l in wrapper.links]==external_links
        finally:
            path.write_text(old,encoding='utf8')
    finally:
        packages.set_packages_dir_for_tests(None)

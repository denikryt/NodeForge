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


def _write_color_extension(root: Path) -> None:
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
        'contents': {'systems': 'systems'},
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
}
def make_color(factor: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Color: ...
def identity_color(value: Annotated[Color, EvaluationMode.RUNTIME_ONLY]) -> Color: ...
def union_component(value: Annotated[Color | Vector, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
def bad_color_result(factor: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Color: ...
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

def bad_color_result(context, factor):
    node = context.group.nodes.new('ShaderNodeValToRGB')
    return ExtensionBackendValue(node.outputs['Color'], NFType.VECTOR)
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

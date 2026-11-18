"""Real Blender integration of Rotation defaults and typed Extension API v2 outputs."""

from __future__ import annotations

import json

import bpy
import pytest

from NodeForge import compiler, packages
from NodeForge.errors import CompileError
from NodeForge.semantic.numeric_semantics import normalize_float_constant

pytestmark = pytest.mark.blender


def _install_rotation_extension(root):
    """Install a package whose backend emits/returns genuine Rotation sockets."""
    owner = root / 'systems' / 'rotation'
    owner.mkdir(parents=True)
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.extension.rotation_defaults',
        'name': 'Rotation Defaults', 'version': '1.0.0', 'author': 'Tests',
        'nodeforge_min_version': '0.65.0',
        'contents': {'systems': 'systems'}, 'permissions': {'python': True},
    }), encoding='utf-8')
    (owner / 'interface.py').write_text('''from typing import Annotated
from NodeForge import EvaluationMode, Rotation
EXTENSION_API = 2
EXTENSIONS = {
    'rotation_static': '.operations:emit_rotation',
    'rotation_mixed': '.operations:emit_rotation',
    'rotation_identity': '.operations:identity',
    'rotation_bad_type': '.operations:bad_type',
}
def rotation_static(value: Annotated[Rotation, EvaluationMode.COMPILE_TIME_ONLY] = (0.1,-0.2,7.0)) -> Rotation: ...
def rotation_mixed(value: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = (0.0,0.0,0.0)) -> Rotation: ...
def rotation_identity(value: Annotated[Rotation, EvaluationMode.RUNTIME_ONLY]) -> Rotation: ...
def rotation_bad_type(value: Annotated[Rotation, EvaluationMode.RUNTIME_ONLY]) -> Rotation: ...
''', encoding='utf-8')
    (owner / 'operations.py').write_text('''from NodeForge.extension_api import NFType, ExtensionBackendValue

def emit_rotation(context, value):
    if isinstance(value, ExtensionBackendValue):
        assert value.typ is NFType.ROTATION
        return value
    assert isinstance(value, tuple) and len(value) == 3
    node = context.group.nodes.new('FunctionNodeEulerToRotation')
    node.inputs['Euler'].default_value = value
    return context.value(node.outputs['Rotation'], NFType.ROTATION)

def identity(context, value):
    assert value.typ is NFType.ROTATION
    return value

def bad_type(context, value):
    return ExtensionBackendValue(value.socket, NFType.VECTOR)
''', encoding='utf-8')
    packages.install_package_directory(root)


def _compile(source, name):
    """Compile a source wrapper with the installed package's functions in scope."""
    return compiler.create_expression_group(source, name)


def test_rotation_extension_static_default_and_explicit_tuple(tmp_path):
    """Omitted and explicit static Rotation values remain canonical Euler triples."""
    _install_rotation_extension(tmp_path / 'rotation_ext')
    for call, expected in [('rotation_static()', (0.1, -0.2, 7.0)),
                           ('rotation_static((0.5, 0.0, 8.0))', (0.5, 0.0, 8.0))]:
        group = _compile('''from packages import rotation_defaults

def emit() -> Rotation:
    return %s
''' % call, 'NFTest_RotationExtStatic')
        node = next(n for n in group.nodes if n.bl_idname == 'FunctionNodeEulerToRotation')
        actual = tuple(float(v) for v in node.inputs['Euler'].default_value)
        assert actual == tuple(normalize_float_constant(v) for v in expected)
        assert all(link.is_valid for link in group.links)


def test_rotation_extension_mixed_runtime_and_typed_return(tmp_path):
    """A typed runtime Rotation reference survives a mixed extension parameter."""
    _install_rotation_extension(tmp_path / 'rotation_ext')
    group = _compile('''from packages import rotation_defaults

def pass_through(value: Rotation) -> Rotation:
    return rotation_identity(rotation_mixed(value))
''', 'NFTest_RotationExtRuntime')
    inputs = [s for s in group.interface.items_tree if s.item_type == 'SOCKET' and s.in_out == 'INPUT']
    outputs = [s for s in group.interface.items_tree if s.item_type == 'SOCKET' and s.in_out == 'OUTPUT']
    assert inputs[0].socket_type == outputs[0].socket_type == 'NodeSocketRotation'
    assert all(link.is_valid for link in group.links)
    static = _compile('''from packages import rotation_defaults

def emit_default() -> Rotation:
    return rotation_mixed()
''', 'NFTest_RotationExtMixedDefault')
    assert any(n.bl_idname == 'FunctionNodeEulerToRotation' for n in static.nodes)
    assert all(link.is_valid for link in static.links)


def test_rotation_extension_wrong_runtime_or_return_type_fails(tmp_path):
    """Extension return validation rejects incompatible runtime socket metadata."""
    _install_rotation_extension(tmp_path / 'rotation_ext')
    with pytest.raises(CompileError):
        _compile('''from packages import rotation_defaults

def wrong(value: Vector) -> Rotation:
    return rotation_identity(value)
''', 'NFTest_RotationExtWrongArg')
    with pytest.raises(CompileError):
        _compile('''from packages import rotation_defaults

def wrong(value: Rotation) -> Rotation:
    return rotation_bad_type(value)
''', 'NFTest_RotationExtWrongResult')

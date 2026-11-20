"""Blender 5.2 physical validation for invocation-dependent extension results."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import bpy
import pytest

from helpers import compile_group
from NodeForge import packages
from NodeForge.errors import CompileError
from NodeForge.extension_api import ExtensionBackendContext
from NodeForge.nf_types import NFType

pytestmark = pytest.mark.blender


def _write_package(root: Path, *, bad: str = "") -> None:
    """Create one dynamically typed physical owner with selectable result violations."""
    owner = root / "systems" / "dynamic"
    owner.mkdir(parents=True)
    (root / "nodeforge_package.json").write_text(json.dumps({
        "schema_version": 1, "id": "vendor.extension.dynamic",
        "name": "Dynamic Result Fixture", "version": "1.0.0", "author": "Tests",
        "description": "Dynamic tuple physical verification", "nodeforge_min_version": "0.93.3",
        "nodeforge_max_version": None, "contents": {"systems": "systems"},
        "permissions": {"python": True},
    }), encoding="utf-8")
    (owner / "interface.py").write_text('''
from typing import Annotated
from NodeForge import DynamicResults, EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> DynamicResults: ...
''', encoding="utf-8")
    (owner / "semantic.py").write_text('''
from NodeForge.nf_types import NFType
def _result_types_foo(arguments):
    return (NFType.FLOAT, NFType.FLOAT)
''', encoding="utf-8")
    variant = {
        "": "return (context.value(node.outputs[0], NFType.FLOAT), value)",
        "short": "return (context.value(node.outputs[0], NFType.FLOAT),)",
        "long": "return (context.value(node.outputs[0], NFType.FLOAT), value, value)",
        "scalar": "return context.value(node.outputs[0], NFType.FLOAT)",
        "wrong_type": "return (ExtensionBackendValue(node.outputs[0], NFType.BOOL), value)",
        "input_socket": "return (ExtensionBackendValue(node.inputs[0], NFType.FLOAT), value)",
        "wrong_socket_type": "other = context.group.nodes.new('GeometryNodeInputPosition'); return (ExtensionBackendValue(other.outputs[0], NFType.FLOAT), value)",
        "foreign_socket": "return (ExtensionBackendValue(bpy.data.node_groups['NFTest_other_dynamic'].nodes[0].outputs[0], NFType.FLOAT), value)",
    }[bad]
    (owner / "backend.py").write_text(f'''
import bpy
from NodeForge.extension_api import ExtensionBackendValue
from NodeForge.nf_types import NFType

def foo(context, value):
    assert context.result_types == (NFType.FLOAT, NFType.FLOAT)
    context.new_generated_mesh(role="dynamic-rollback", name_hint="Probe")
    node = context.group.nodes.new("ShaderNodeMath")
    node.operation = "MULTIPLY"
    context.group.links.new(value.socket, node.inputs[0])
    node.inputs[1].default_value = 2.0
    {variant}
''', encoding="utf-8")


_SOURCE = '''from packages import dynamic

def nf_fixture(value: Float['Value']=3.0):
    a, b = foo(value)
    return a, b
'''


def test_dynamic_tuple_creates_valid_typed_nodes_and_immutable_context():
    """The selected IR result types reach one backend and validate exact sockets."""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "package"
        _write_package(source)
        packages.install_package_directory(source)
        group = compile_group(_SOURCE, "NFTest_dynamic_result_ok")
        assert len([node for node in group.nodes if node.bl_idname == "ShaderNodeMath"]) == 1
        assert len([socket for socket in group.interface.items_tree if socket.item_type == "SOCKET" and socket.in_out == "OUTPUT"]) == 2
        test_context = ExtensionBackendContext(group=group, location=(0, 0),
                                               generated_resource_transaction=object(),
                                               result_types=(NFType.FLOAT,))
        with pytest.raises(AttributeError):
            test_context.result_types = (NFType.INT,)
        bpy.data.node_groups.remove(group)


@pytest.mark.parametrize("bad,pattern", [
    ("short", "2-item tuple"), ("long", "2-item tuple"),
    ("scalar", "2-item tuple"), ("wrong_type", "expected FLOAT"),
    ("input_socket", "output sockets"), ("wrong_socket_type", "physical socket type"),
    ("foreign_socket", "current Geometry Nodes group"),
])
def test_dynamic_backend_rejects_malformed_result_and_rolls_back(bad, pattern):
    """No returned malformed leaf or generated resource can publish a candidate."""
    foreign = None
    try:
        if bad == "foreign_socket":
            foreign = bpy.data.node_groups.new("NFTest_other_dynamic", "GeometryNodeTree")
            foreign.nodes.new("ShaderNodeMath")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "package"
            _write_package(source, bad=bad)
            packages.install_package_directory(source)
            groups = {item.as_pointer() for item in bpy.data.node_groups}
            meshes = {item.as_pointer() for item in bpy.data.meshes}
            with pytest.raises(CompileError, match=pattern):
                compile_group(_SOURCE, f"NFTest_dynamic_bad_{bad}")
            assert {item.as_pointer() for item in bpy.data.node_groups} == groups
            assert {item.as_pointer() for item in bpy.data.meshes} == meshes
    finally:
        if foreign is not None and foreign.name in bpy.data.node_groups:
            bpy.data.node_groups.remove(foreign)


def test_dynamic_extension_keeps_existing_root_tree_physical_restriction():
    """Issue 62 does not bypass the root-owned tree restriction from Issue 61."""
    from NodeForge import compiler
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "package"
        _write_package(source)
        packages.install_package_directory(source)
        root = bpy.data.node_groups.new("NFTest_dynamic_direct_root", "GeometryNodeTree")
        try:
            with pytest.raises(CompileError, match="existing base GeometryNodeTree"):
                compiler.compose_source_initial('''from packages import dynamic
value_a, value_b = foo(1.0)
''', root)
        finally:
            bpy.data.node_groups.remove(root)

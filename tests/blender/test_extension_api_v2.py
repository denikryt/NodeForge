"""Real-Blender integration coverage for backend-only declarative Python extensions."""

from __future__ import annotations

import builtins
import json
import os
import tempfile
from pathlib import Path

import bpy
import pytest

from helpers import compile_group
from NodeForge import packages
from NodeForge.errors import CompileError

pytestmark = pytest.mark.blender


def _write_extension_package(
    root: Path, *, bad_result: bool = False, create_resource: bool = False
) -> None:
    """Create one minimal v2 system package used by physical-dispatch fixtures."""
    owner = root / "systems" / "demo"
    owner.mkdir(parents=True)
    (root / "nodeforge_package.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "vendor.extension.v2",
                "name": "Extension V2",
                "version": "1.0.0",
                "author": "Tests",
                "description": "Physical extension fixture",
                "nodeforge_min_version": "0.59.0",
                "nodeforge_max_version": "0.61.3",
                "contents": {"systems": "systems"},
                "permissions": {"python": True},
            }
        ),
        encoding="utf-8",
    )
    (owner / "interface.py").write_text(
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"ext_scale": ".operations:build_scale"}
def ext_scale(
    value: Annotated[Float, EvaluationMode.RUNTIME_ONLY],
    *,
    factor: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = 2.0,
) -> Float: ...
""",
        encoding="utf-8",
    )
    result_expr = (
        "ExtensionBackendValue(node.inputs[0], NFType.FLOAT)"
        if bad_result
        else "context.value(node.outputs[0], NFType.FLOAT)"
    )
    (owner / "operations.py").write_text(
        f"""
import builtins
from NodeForge.extension_api import ExtensionBackendValue, NFType
builtins._nodeforge_stage32_blender_impl_hits = getattr(builtins, "_nodeforge_stage32_blender_impl_hits", 0) + 1

def build_scale(context, value, *, factor=2.0):
    {"context.new_generated_mesh(role=\"rollback-probe\", name_hint=\"Probe\")" if create_resource else ""}
    node = context.group.nodes.new("ShaderNodeMath")
    node.operation = "MULTIPLY"
    node.location = context.location
    context.group.links.new(value.socket, node.inputs[0])
    if isinstance(factor, ExtensionBackendValue):
        context.group.links.new(factor.socket, node.inputs[1])
    else:
        node.inputs[1].default_value = float(factor)
    return {result_expr}
""",
        encoding="utf-8",
    )


def _write_rotation_extension_package(root: Path) -> None:
    """Create one v2 owner that produces and consumes a real Rotation socket."""
    owner = root / "systems" / "rotation"
    owner.mkdir(parents=True)
    (root / "nodeforge_package.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "vendor.extension.rotation",
                "name": "Extension Rotation",
                "version": "1.0.0",
                "author": "Tests",
                "description": "Rotation physical-type fixture",
                "nodeforge_min_version": "0.59.0",
                "nodeforge_max_version": "0.61.3",
                "contents": {"systems": "systems"},
                "permissions": {"python": True},
            }
        ),
        encoding="utf-8",
    )
    (owner / "interface.py").write_text(
        """
from typing import Annotated
from NodeForge import EvaluationMode, Rotation, Vector
EXTENSION_API = 2
EXTENSIONS = {
    "make_rotation": ".operations:make_rotation",
    "identity_rotation": ".operations:identity_rotation",
}
def make_rotation(value: Annotated[Vector, EvaluationMode.RUNTIME_ONLY], /) -> Rotation: ...
def identity_rotation(value: Annotated[Rotation, EvaluationMode.RUNTIME_ONLY], /) -> Rotation: ...
""",
        encoding="utf-8",
    )
    (owner / "operations.py").write_text(
        """
from NodeForge.extension_api import NFType

def make_rotation(context, value):
    node = context.group.nodes.new("FunctionNodeEulerToRotation")
    node.location = context.location
    context.group.links.new(value.socket, node.inputs[0])
    return context.value(node.outputs[0], NFType.ROTATION)

def identity_rotation(context, value):
    return value
""",
        encoding="utf-8",
    )


def _write_nested_import_extension_package(root: Path) -> None:
    """Create one v2 owner whose implementation uses nested owner-local modules lazily."""
    owner = root / "systems" / "nested"
    (owner / "helpers").mkdir(parents=True)
    (owner / "pkg").mkdir()
    (owner / "impl").mkdir()
    (root / "nodeforge_package.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "vendor.extension.nested",
                "name": "Extension Nested Imports",
                "version": "1.0.0",
                "author": "Tests",
                "description": "Nested owner-local import fixture",
                "nodeforge_min_version": "0.59.0",
                "nodeforge_max_version": "0.61.3",
                "contents": {"systems": "systems"},
                "permissions": {"python": True},
            }
        ),
        encoding="utf-8",
    )
    (owner / "helpers" / "deep.py").write_text("CONST = 2.0\n", encoding="utf-8")
    (owner / "pkg" / "__init__.py").write_text("FLAG = 3.0\n", encoding="utf-8")
    (owner / "interface.py").write_text(
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float

EXTENSION_API = 2
EXTENSIONS = {"nested_scale": ".impl.ops:build_scale"}

def nested_scale(
    value: Annotated[Float, EvaluationMode.RUNTIME_ONLY],
    *,
    factor: Annotated[Float, EvaluationMode.COMPILE_TIME_ONLY] = 5.0,
) -> Float: ...
""",
        encoding="utf-8",
    )
    (owner / "impl" / "ops.py").write_text(
        """
def build_scale(context, value, *, factor=5.0):
    from ..helpers.deep import CONST
    from ..pkg import FLAG
    from NodeForge.extension_api import NFType
    if factor != CONST + FLAG:
        raise AssertionError("captured implementation helpers produced an unexpected factor")
    node = context.group.nodes.new("ShaderNodeMath")
    node.operation = "MULTIPLY"
    node.location = context.location
    context.group.links.new(value.socket, node.inputs[0])
    node.inputs[1].default_value = float(factor)
    return context.value(node.outputs[0], NFType.FLOAT)
""",
        encoding="utf-8",
    )


def _write_semantic_extension_package(root: Path) -> None:
    """Create one owner covering semantic-only and semantic-then-backend execution."""
    owner = root / "systems" / "semantic"
    owner.mkdir(parents=True)
    (root / "nodeforge_package.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "vendor.extension.semantic",
                "name": "Extension Semantic",
                "version": "1.0.0",
                "author": "Tests",
                "description": "Package semantic-state Blender fixture",
                "nodeforge_min_version": "0.60.0",
                "nodeforge_max_version": "0.61.3",
                "contents": {"systems": "systems"},
                "permissions": {"python": True},
            }
        ),
        encoding="utf-8",
    )
    (owner / "interface.py").write_text(
        """
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float

EXTENSION_API = 2

@dataclass(frozen=True)
class Part:
    value: Float

@dataclass(frozen=True)
class DerivedPart(Part):
    label: str

@dataclass(frozen=True)
class _Inner:
    enabled: bool

@dataclass(frozen=True)
class _State:
    parts: list[Part]
    inner: _Inner

EXTENSIONS = {
    "make_part": None,
    "ignore_part_input": None,
    "runtime_resource": ".backend:runtime_resource",
    "consume_part": ".backend:consume_part",
    "bad_consume_part": ".backend:bad_consume_part",
}

def make_part(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
def ignore_part_input(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
def runtime_resource(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
def consume_part(part: Part) -> Float: ...
def bad_consume_part(part: Part) -> Float: ...
""",
        encoding="utf-8",
    )
    (owner / "semantic.py").write_text(
        """
from .interface import DerivedPart, Part, _Inner, _State

def make_part(value) -> Part:
    return DerivedPart(value, "derived")

def ignore_part_input(value) -> Part:
    return DerivedPart(1.0, "derived")

def consume_part(part) -> _State:
    return _State([part], _Inner(True))

def bad_consume_part(part) -> _State:
    return _State([part], _Inner(True))
""",
        encoding="utf-8",
    )
    (owner / "backend.py").write_text(
        """
import builtins
from .interface import DerivedPart, _Inner, _State
from NodeForge.extension_api import ExtensionBackendValue, NFType

def make_part(context, semantic_state):
    builtins._nodeforge_semantic_backend_calls.append("make_part")
    raise AssertionError("semantic-only make_part must never invoke backend code")

def _runtime_leaf(semantic_state):
    if type(semantic_state) is not _State:
        raise AssertionError("semantic state did not reconstruct as the exact session _State class")
    if type(semantic_state.inner) is not _Inner or semantic_state.inner.enabled is not True:
        raise AssertionError("nested private semantic state was reconstructed incorrectly")
    if len(semantic_state.parts) != 1 or type(semantic_state.parts[0]) is not DerivedPart:
        raise AssertionError("concrete semantic subtype identity was not preserved")
    if semantic_state.parts[0].label != "derived":
        raise AssertionError("detached package scalar state was not preserved")
    value = semantic_state.parts[0].value
    if not isinstance(value, ExtensionBackendValue) or value.typ is not NFType.INT:
        raise AssertionError("runtime semantic leaf did not reconstruct as the actual typed backend value")
    return value

def runtime_resource(context, value):
    builtins._nodeforge_semantic_backend_calls.append("runtime_resource")
    context.new_generated_mesh(role="discarded-runtime-resource", name_hint="DiscardedRuntimeResource")
    node = context.group.nodes.new("ShaderNodeMath")
    node.operation = "ADD"
    node.location = context.location
    context.group.links.new(value.socket, node.inputs[0])
    node.inputs[1].default_value = 0.0
    return context.value(node.outputs[0], NFType.FLOAT)

def consume_part(context, semantic_state):
    builtins._nodeforge_semantic_backend_calls.append("consume_part")
    value = _runtime_leaf(semantic_state)
    node = context.group.nodes.new("ShaderNodeMath")
    node.operation = "ADD"
    node.location = context.location
    context.group.links.new(value.socket, node.inputs[0])
    node.inputs[1].default_value = 0.0
    return context.value(node.outputs[0], NFType.FLOAT)

def bad_consume_part(context, semantic_state):
    builtins._nodeforge_semantic_backend_calls.append("bad_consume_part")
    value = _runtime_leaf(semantic_state)
    context.new_generated_mesh(role="semantic-state-rollback", name_hint="SemanticStateRollback")
    return ExtensionBackendValue(value.socket, NFType.FLOAT)
""",
        encoding="utf-8",
    )


def test_v2_system_dispatch_is_lazy_and_uses_public_backend_context():
    """Installation leaves implementation lazy; first compile realizes one ordinary Blender node."""
    if hasattr(builtins, "_nodeforge_stage32_blender_impl_hits"):
        delattr(builtins, "_nodeforge_stage32_blender_impl_hits")
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "package"
        _write_extension_package(source)
        packages.install_package_directory(source, allow_python=True)
        assert not hasattr(builtins, "_nodeforge_stage32_blender_impl_hits")

        group = compile_group(
            'value = input_float("Value", default=3.0)\n'
            'result = ext_scale(value)\n'
            'output("Result", result)\n',
            "NFTest_extension_v2_scale",
        )

        math_nodes = [node for node in group.nodes if node.bl_idname == "ShaderNodeMath"]
        assert len(math_nodes) == 1
        assert math_nodes[0].operation == "MULTIPLY"
        assert math_nodes[0].inputs[1].default_value == pytest.approx(2.0)
        assert getattr(builtins, "_nodeforge_stage32_blender_impl_hits") == 1
    if hasattr(builtins, "_nodeforge_stage32_blender_impl_hits"):
        delattr(builtins, "_nodeforge_stage32_blender_impl_hits")


def test_nested_owner_local_imports_work_through_install_and_physical_dispatch():
    """Nested namespace/captured-package imports survive install, registry, and Blender dispatch."""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "package"
        _write_nested_import_extension_package(source)
        packages.install_package_directory(source, allow_python=True)
        group = compile_group(
            'value = input_float("Value", default=3.0)\n'
            'result = nested_scale(value)\n'
            'output("Result", result)\n',
            "NFTest_extension_v2_nested_imports",
        )

        math_nodes = [node for node in group.nodes if node.bl_idname == "ShaderNodeMath"]
        assert len(math_nodes) == 1
        assert math_nodes[0].operation == "MULTIPLY"
        assert math_nodes[0].inputs[1].default_value == pytest.approx(5.0)


def test_rotation_extension_result_can_cross_another_extension_and_group_output():
    """A physical Rotation socket remains a first-class typed extension/group result."""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "package"
        _write_rotation_extension_package(source)
        packages.install_package_directory(source, allow_python=True)
        group = compile_group(
            'value = input_vector("Euler")\n'
            'rotation = make_rotation(value)\n'
            'result = identity_rotation(rotation)\n'
            'output("Rotation", result)\n',
            "NFTest_extension_v2_rotation",
        )
        output = next(
            item
            for item in group.interface.items_tree
            if getattr(item, "item_type", None) == "SOCKET"
            and getattr(item, "in_out", None) == "OUTPUT"
            and item.name == "Rotation"
        )
        assert output.socket_type == "NodeSocketRotation"


def test_direct_wrapper_cannot_return_an_input_socket_as_runtime_result():
    """Result-boundary validation rejects a forged wrapper even when type/tree match."""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "package"
        _write_extension_package(source, bad_result=True)
        packages.install_package_directory(source, allow_python=True)
        before = set(bpy.data.node_groups)
        with pytest.raises(CompileError, match="output sockets"):
            compile_group(
                'value = input_float("Value", default=3.0)\n'
                'result = ext_scale(value)\n'
                'output("Result", result)\n',
                "NFTest_extension_v2_bad_result",
            )
        assert set(bpy.data.node_groups) == before


def test_invalid_extension_result_rolls_back_generated_resources():
    """Result validation failure rolls back resources created by the same extension call."""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "package"
        _write_extension_package(source, bad_result=True, create_resource=True)
        packages.install_package_directory(source, allow_python=True)
        before_meshes = {mesh.as_pointer() for mesh in bpy.data.meshes}
        before_groups = {group.as_pointer() for group in bpy.data.node_groups}

        with pytest.raises(CompileError, match="output sockets"):
            compile_group(
                'value = input_float("Value", default=3.0)\n'
                'result = ext_scale(value)\n'
                'output("Result", result)\n',
                "NFTest_extension_v2_resource_rollback",
            )

        assert {mesh.as_pointer() for mesh in bpy.data.meshes} == before_meshes
        assert {group.as_pointer() for group in bpy.data.node_groups} == before_groups


def test_semantic_state_reconstructs_exact_records_and_runtime_leaf():
    """Semantic-only composition feeds exact package records and typed backend leaves to one physical call."""
    builtins._nodeforge_semantic_backend_calls = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "package"
            _write_semantic_extension_package(source)
            packages.install_package_directory(source, allow_python=True)
            group = compile_group(
                'value = input_int("Value", default=3)\n'
                'result = consume_part(make_part(value))\n'
                'output("Result", result)\n',
                "NFTest_extension_v2_semantic_state",
            )

            math_nodes = [
                node for node in group.nodes
                if node.bl_idname == "ShaderNodeMath" and node.operation == "ADD"
            ]
            assert len(math_nodes) == 1
            assert builtins._nodeforge_semantic_backend_calls == ["consume_part"]
    finally:
        delattr(builtins, "_nodeforge_semantic_backend_calls")


def test_semantic_backend_result_failure_rolls_back_generated_resources():
    """Semantic-then-backend result validation failure rolls back resources from that extension call."""
    builtins._nodeforge_semantic_backend_calls = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "package"
            _write_semantic_extension_package(source)
            packages.install_package_directory(source, allow_python=True)
            before_meshes = {mesh.as_pointer() for mesh in bpy.data.meshes}
            before_groups = {group.as_pointer() for group in bpy.data.node_groups}
            before_objects = {obj.as_pointer() for obj in bpy.data.objects}

            with pytest.raises(CompileError) as exc_info:
                compile_group(
                    'value = input_int("Value", default=3)\n'
                    'result = bad_consume_part(make_part(value))\n'
                    'output("Result", result)\n',
                    "NFTest_extension_v2_semantic_rollback",
                )

            message = str(exc_info.value)
            assert "physical" in message.lower() and "INT" in message and "FLOAT" in message
            assert builtins._nodeforge_semantic_backend_calls == ["bad_consume_part"]
            assert {mesh.as_pointer() for mesh in bpy.data.meshes} == before_meshes
            assert {group.as_pointer() for group in bpy.data.node_groups} == before_groups
            assert {obj.as_pointer() for obj in bpy.data.objects} == before_objects
    finally:
        delattr(builtins, "_nodeforge_semantic_backend_calls")



def test_persistent_semantic_assignment_reconstructs_runtime_leaf_later():
    """A body-persisted semantic record reconstructs its hidden runtime snapshot for later backend use."""
    builtins._nodeforge_semantic_backend_calls = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "package"
            _write_semantic_extension_package(source)
            packages.install_package_directory(source, allow_python=True)
            group = compile_group(
                'value = input_int("Value", default=3)\n'
                'part = make_part(value)\n'
                'result = consume_part(part)\n'
                'output("Result", result)\n',
                "NFTest_extension_v2_persistent_semantic_state",
            )
            math_nodes = [
                node for node in group.nodes
                if node.bl_idname == "ShaderNodeMath" and node.operation == "ADD"
            ]
            assert len(math_nodes) == 1
            assert builtins._nodeforge_semantic_backend_calls == ["consume_part"]
    finally:
        delattr(builtins, "_nodeforge_semantic_backend_calls")


def test_discarded_runtime_extension_argument_creates_no_backend_or_resource():
    """Semantic callback discard removes nested runtime demand before Blender dispatch/resources."""
    builtins._nodeforge_semantic_backend_calls = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "package"
            _write_semantic_extension_package(source)
            packages.install_package_directory(source, allow_python=True)
            before_meshes = {mesh.as_pointer() for mesh in bpy.data.meshes}
            group = compile_group(
                'value = input_int("Value", default=3)\n'
                'part = ignore_part_input(runtime_resource(value))\n'
                'output("Value", value)\n',
                "NFTest_extension_v2_discarded_runtime_demand",
            )
            assert group is not None
            assert builtins._nodeforge_semantic_backend_calls == []
            assert {mesh.as_pointer() for mesh in bpy.data.meshes} == before_meshes
    finally:
        delattr(builtins, "_nodeforge_semantic_backend_calls")


def test_persistent_semantic_backend_failure_still_rolls_back_resources():
    """Stage-32 transaction ownership remains authoritative after Stage-34 body persistence."""
    builtins._nodeforge_semantic_backend_calls = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "package"
            _write_semantic_extension_package(source)
            packages.install_package_directory(source, allow_python=True)
            before_meshes = {mesh.as_pointer() for mesh in bpy.data.meshes}
            before_groups = {group.as_pointer() for group in bpy.data.node_groups}
            before_objects = {obj.as_pointer() for obj in bpy.data.objects}
            with pytest.raises(CompileError) as exc_info:
                compile_group(
                    'value = input_int("Value", default=3)\n'
                    'part = make_part(value)\n'
                    'result = bad_consume_part(part)\n'
                    'output("Result", result)\n',
                    "NFTest_extension_v2_persistent_semantic_rollback",
                )
            message = str(exc_info.value)
            assert "physical" in message.lower() and "INT" in message and "FLOAT" in message
            assert builtins._nodeforge_semantic_backend_calls == ["bad_consume_part"]
            assert {mesh.as_pointer() for mesh in bpy.data.meshes} == before_meshes
            assert {group.as_pointer() for group in bpy.data.node_groups} == before_groups
            assert {obj.as_pointer() for obj in bpy.data.objects} == before_objects
    finally:
        delattr(builtins, "_nodeforge_semantic_backend_calls")

def test_nodeforge_math_v2_reference_package():
    """The migrated Math owner compiles through v2 without legacy Compiler handlers."""
    package_path = os.environ.get("NODEFORGE_MATH_PACKAGE")
    if not package_path:
        pytest.skip("NODEFORGE_MATH_PACKAGE is required for the reference-package integration test")
    packages.install_package_directory(Path(package_path), allow_python=True)

    sine_group = compile_group(
        'value = input_float("Value", default=0.5)\n'
        'result = sin(value)\n'
        'output("Result", result)\n',
        "NFTest_math_v2_sin",
    )
    sine_nodes = [
        node for node in sine_group.nodes
        if node.bl_idname == "ShaderNodeMath" and node.operation == "SINE"
    ]
    assert len(sine_nodes) == 1

    select_group = compile_group(
        'cond = input_bool("Condition", default=True)\n'
        'a = input_int("A", default=1)\n'
        'b = input_int("B", default=2)\n'
        'result = select(cond, a, b)\n'
        'output("Result", result)\n',
        "NFTest_math_v2_select_int",
    )
    switches = [node for node in select_group.nodes if node.bl_idname == "GeometryNodeSwitch"]
    assert len(switches) == 1
    assert switches[0].input_type == "INT"

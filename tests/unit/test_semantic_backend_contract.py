"""Exhaustive semantic-to-NodeForge-backend contract before Blender RNA."""

import ast
from types import MappingProxyType

import pytest

from NodeForge import blender_ir_lowering
from NodeForge.constants import (
    TYPE_BOOL,
    TYPE_BUNDLE,
    TYPE_FLOAT,
    TYPE_GEOMETRY,
    TYPE_INT,
    TYPE_MATERIAL,
    TYPE_OBJECT,
    TYPE_STRING,
    TYPE_VECTOR,
)
from NodeForge.errors import CompileError
from NodeForge.semantic_analysis import SemanticEnvironment, analyze_expression
from NodeForge.semantic_lowering import lower_analyzed_expression
from NodeForge.values import Value


pytestmark = pytest.mark.unit

_TYPES = (
    TYPE_FLOAT,
    TYPE_INT,
    TYPE_VECTOR,
    TYPE_BOOL,
    TYPE_GEOMETRY,
    TYPE_MATERIAL,
    TYPE_OBJECT,
    TYPE_STRING,
    TYPE_BUNDLE,
)


class _FakeSocket:
    """Minimal Blender-facing socket accepted by the real NodeForge node helpers."""

    def __init__(self, node=None, index=0):
        self.node = node
        self.index = index
        self.default_value = None


class _FakeNode:
    """Minimal mutable node object with generic sockets and RNA-like attributes."""

    def __init__(self, bl_idname):
        self.bl_idname = bl_idname
        self.location = (0, 0)
        self.label = ""
        self.operation = None
        self.data_type = None
        self.input_type = None
        self.integer = 0
        self.string = ""
        self.inputs = [_FakeSocket(self, i) for i in range(8)]
        self.outputs = [_FakeSocket(self, i) for i in range(4)]


class _FakeNodes(list):
    """Node collection implementing the subset used by NodeForge.nodes helpers."""

    def new(self, bl_idname):
        node = _FakeNode(bl_idname)
        self.append(node)
        return node


class _FakeLinks(list):
    """Link collection recording connections made by real node helpers."""

    def new(self, from_socket, to_socket):
        self.append((from_socket, to_socket))
        return self[-1]


class _FakeGroup:
    """Small fake graph substrate below the real NodeForge helper layer."""

    def __init__(self):
        self.nodes = _FakeNodes()
        self.links = _FakeLinks()


def _expr(source):
    """Parse one expression fixture."""
    return ast.parse(source, mode="eval").body


def _environment(bindings):
    """Build an immutable environment containing only runtime binding type facts."""
    return SemanticEnvironment(
        MappingProxyType(dict(bindings)),
        frozenset(),
        MappingProxyType({}),
        frozenset(),
        MappingProxyType({}),
    )


def _accepted_program(source, bindings):
    """Return analyzer-owned IR, or ``None`` for rejected/legacy cases."""
    expr = _expr(source)
    try:
        analysis = analyze_expression(expr, _environment(bindings))
    except CompileError:
        return None
    if analysis is None:
        return None
    return lower_analyzed_expression(expr, analysis)


def _materialize(source, bindings):
    """Execute one analyzer-accepted program through the real NodeForge backend."""
    program = _accepted_program(source, bindings)
    if program is None:
        return False
    group = _FakeGroup()
    comp = type(
        "Comp",
        (),
        {
            "group": group,
            "vars": {name: Value(_FakeSocket(), typ) for name, typ in bindings.items()},
        },
    )()
    result = blender_ir_lowering.lower_expression(comp, program)
    assert result.typ == program.result.typ
    return True


def test_exhaustive_analyzer_accepted_unary_shapes_reach_real_node_helpers():
    accepted = 0
    for operator in ("+", "-", "not "):
        for typ in _TYPES:
            if _materialize(f"{operator}a", {"a": typ}):
                accepted += 1
    assert accepted > 0


def test_exhaustive_analyzer_accepted_binary_shapes_reach_real_node_helpers():
    accepted = 0
    for operator in ("+", "-", "*", "/"):
        for left_typ in _TYPES:
            for right_typ in _TYPES:
                if _materialize(f"a {operator} b", {"a": left_typ, "b": right_typ}):
                    accepted += 1
    assert accepted > 0


def test_exhaustive_analyzer_accepted_boolean_shapes_reach_real_node_helpers():
    accepted = 0
    for operator in ("and", "or"):
        for left_typ in _TYPES:
            for right_typ in _TYPES:
                if _materialize(f"a {operator} b", {"a": left_typ, "b": right_typ}):
                    accepted += 1
    assert accepted == 2


def test_exhaustive_analyzer_accepted_comparison_shapes_reach_real_node_helpers():
    accepted = 0
    for operator in ("<", "<=", ">", ">=", "==", "!="):
        for left_typ in _TYPES:
            for right_typ in _TYPES:
                if _materialize(f"a {operator} b", {"a": left_typ, "b": right_typ}):
                    accepted += 1
    assert accepted > 0


def test_exhaustive_analyzer_accepted_conditional_shapes_reach_real_node_helpers():
    accepted = 0
    for condition_typ in _TYPES:
        for true_typ in _TYPES:
            for false_typ in _TYPES:
                if _materialize(
                    "a if flag else b",
                    {"flag": condition_typ, "a": true_typ, "b": false_typ},
                ):
                    accepted += 1
    assert accepted > 0


def test_all_analyzer_accepted_vector_components_reach_real_node_helpers():
    accepted = 0
    for typ in _TYPES:
        for component in ("x", "y", "z"):
            if _materialize(f"a.{component}", {"a": typ}):
                accepted += 1
    assert accepted == 3


def test_migrated_literal_realizations_reach_real_node_helpers():
    for source in ("1", "1.5", "True", '"name"'):
        assert _materialize(source, {})

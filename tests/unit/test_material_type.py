"""Unit coverage for the public Material DSL type."""

import ast

import pytest

from NodeForge.constants import TYPE_MATERIAL, TYPE_TOKEN_NAMES
from NodeForge.builtin_call_semantics import analyze_builtin_call

pytestmark = pytest.mark.unit


def test_material_type_token():
    assert TYPE_TOKEN_NAMES["Material"] == TYPE_MATERIAL


def test_raw_node_semantics_accept_material_type_token():
    first = ast.parse(
        'node("GeometryNodeSetMaterial", inputs={"Material": mat}, output="Geometry", typ=Geometry)',
        mode="eval",
    ).body

    def add_runtime(node, _parameter_name, _context):
        assert isinstance(node, ast.Name) and node.id == "mat"
        return TYPE_MATERIAL

    first_semantics = analyze_builtin_call("node", first, {}, add_runtime)
    assert first_semantics.result.typ == TYPE_TOKEN_NAMES["Geometry"]

    second = ast.parse(
        'node("GeometryNodeInputMaterial", output="Material", typ=Material)',
        mode="eval",
    ).body
    second_semantics = analyze_builtin_call(
        "node",
        second,
        {},
        lambda *_args: pytest.fail("input material node must not acquire runtime operands"),
    )
    assert second_semantics.result.typ == TYPE_MATERIAL

"""Unit coverage for the public Material DSL type."""

import ast

import pytest

from NodeForge.constants import TYPE_MATERIAL, TYPE_TOKEN_NAMES
from NodeForge.builtins import raw_nodes

pytestmark = pytest.mark.unit


def test_material_type_token():
    assert TYPE_TOKEN_NAMES["Material"] == TYPE_MATERIAL


def test_raw_node_parser_accepts_material_type_token():
    class DummyComp:
        consts = {}

    expr = ast.parse(
        'node("GeometryNodeSetMaterial", inputs={"Material": mat}, output="Geometry", typ=Geometry)'
    ).body[0].value
    parsed = raw_nodes._parse_node_call(DummyComp(), expr)
    assert parsed["typ"] == TYPE_TOKEN_NAMES["Geometry"]

    expr = ast.parse(
        'node("GeometryNodeInputMaterial", output="Material", typ=Material)'
    ).body[0].value
    parsed = raw_nodes._parse_node_call(DummyComp(), expr)
    assert parsed["typ"] == TYPE_MATERIAL

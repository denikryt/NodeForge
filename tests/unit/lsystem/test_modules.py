import pytest

from NodeForge.errors import CompileError
from NodeForge.systems.lsystem.model import LSystemMarker
from NodeForge.systems.lsystem.modules import marker_identity, parse_stream
from NodeForge.values import Value

pytestmark = pytest.mark.unit


def marker(name, *params):
    return LSystemMarker(name, tuple(params), marker_identity(name))


def names(modules):
    return [module.name for module in modules]


def test_parse_legacy_stream_as_single_character_modules():
    assert names(parse_stream("F+F--F", params={}, markers={}, context="fixture")) == list("F+F--F")


def test_parse_parameterized_builtins_and_marker_arguments():
    runtime = Value(None, "FLOAT")
    modules = parse_stream(
        "F(length)f(0.5)+(main_angle)-(35)Leaf(leaf_size)Bud",
        params={"length": runtime, "main_angle": 24.0, "leaf_size": 0.8},
        markers={"Leaf": marker("Leaf", "size"), "Bud": marker("Bud")},
        context="fixture",
    )
    assert names(modules) == ["F", "f", "+", "-", "Leaf", "Bud"]
    assert modules[0].args[0].is_runtime is True
    assert modules[4].args[0].value == 0.8


def test_marker_identity_uses_full_collision_resistant_tuple():
    assert marker_identity("Leaf") == marker_identity("Leaf")
    assert len(marker_identity("Leaf")) == 4
    assert marker_identity("M_3uxlqZl") != marker_identity("MGRFHDue9")


@pytest.mark.parametrize("source", ["F()", "+(unknown)", "F(1 + 2)", "F→G", "{Leaf(size)}", "F X"])
def test_rejects_invalid_module_syntax(source):
    with pytest.raises(CompileError):
        parse_stream(source, params={"size": 1.0}, markers={"Leaf": marker("Leaf", "size")}, context="fixture")


def test_rejects_undeclared_parenthesized_marker_and_wrong_arity():
    with pytest.raises(CompileError):
        parse_stream("Leaf(size)", params={"size": 1.0}, markers={}, context="fixture")
    with pytest.raises(CompileError):
        parse_stream("Leaf", params={}, markers={"Leaf": marker("Leaf", "size")}, context="fixture")


def test_tokenizer_priority_and_compact_ambiguity():
    assert names(parse_stream("Leaf", params={}, markers={}, context="fixture")) == list("Leaf")
    assert names(parse_stream("FFrame", params={}, markers={"Frame": marker("Frame")}, context="fixture")) == ["F", "Frame"]
    assert names(parse_stream("FFrame", params={}, markers={"FFrame": marker("FFrame")}, context="fixture")) == ["FFrame"]
    assert names(parse_stream("FFrame", params={}, markers={"Frame": marker("Frame"), "FFrame": marker("FFrame")}, context="fixture")) == ["FFrame"]


def test_declared_marker_after_builtin_prefix_parses_in_compact_syntax():
    modules = parse_stream(
        "FLeaf(size)FBud",
        params={"size": 0.75},
        markers={"Leaf": marker("Leaf", "size"), "Bud": marker("Bud")},
        context="fixture",
    )
    assert names(modules) == ["F", "Leaf", "F", "Bud"]
    assert modules[1].args[0].value == 0.75


def test_declared_marker_after_legacy_prefix_parses_in_compact_syntax():
    modules = parse_stream(
        "LLeaf(size)AApple(size)",
        params={"size": 0.75},
        markers={"Leaf": marker("Leaf", "size"), "Apple": marker("Apple", "size")},
        context="fixture",
    )
    assert names(modules) == ["L", "Leaf", "A", "Apple"]
    assert modules[1].args[0].value == 0.75
    assert modules[3].args[0].value == 0.75

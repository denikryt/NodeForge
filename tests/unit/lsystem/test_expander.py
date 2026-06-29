import pytest

from NodeForge.errors import CompileError
from NodeForge.systems.lsystem.expander import MAX_LSYSTEM_SYMBOLS, expand

pytestmark = pytest.mark.unit


def test_expand_applies_rules_for_requested_iterations():
    assert expand("F", {"F": "F+F"}, 2) == "F+F+F+F"


def test_expand_preserves_symbols_without_rules():
    assert expand("FX", {"F": "FF"}, 1) == "FFX"


def test_expand_enforces_symbol_limit_for_initial_axiom():
    with pytest.raises(CompileError):
        expand("F" * (MAX_LSYSTEM_SYMBOLS + 1), {}, 0)


def test_expand_enforces_symbol_limit_during_rewrite():
    with pytest.raises(CompileError):
        expand("F" * MAX_LSYSTEM_SYMBOLS, {"F": "FF"}, 1)


def test_expand_rewrites_parameterized_tokens_by_module_name():
    from NodeForge.systems.lsystem.model import LSystemModule, ModuleArg
    axiom = (LSystemModule("F", (ModuleArg("length", 1.0, False, "length"),), "F(length)"),)
    repl = (
        LSystemModule("F", (ModuleArg("length", 1.0, False, "length"),), "F(length)"),
        LSystemModule("F", (ModuleArg("length", 1.0, False, "length"),), "F(length)"),
    )
    assert len(expand(axiom, {"F": repl}, 2)) == 4


def test_expand_preserves_marker_modules():
    from NodeForge.systems.lsystem.model import LSystemModule
    marker = LSystemModule("Leaf", (), "Leaf")
    assert expand((marker,), {}, 1) == (marker,)

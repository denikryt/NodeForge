import ast

import pytest

from NodeForge.builtins import layout, registry
from NodeForge.errors import CompileError

pytestmark = pytest.mark.unit


MIGRATED_LAYOUT_NAMES = {
    "layout_grid",
    "grid_points",
    "layout_circle",
    "layout_spiral",
    "spiral_points",
    "layout_random",
    "random_points",
}


def test_layout_builtin_module_no_longer_registers_migrated_helpers():
    assert layout.NAMES == set()
    assert MIGRATED_LAYOUT_NAMES.isdisjoint(registry.CALLABLE_BUILTIN_NAMES)
    assert "circle_points" not in layout.NAMES
    assert "circle_points" not in registry.CALLABLE_BUILTIN_NAMES


def test_layout_compile_call_is_defensive_only():
    expr = ast.parse("layout_grid(points(1))", mode="eval").body
    with pytest.raises(CompileError):
        layout.compile_call(None, expr)

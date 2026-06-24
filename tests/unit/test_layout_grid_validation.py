import ast
from types import SimpleNamespace

import pytest

from NodeForge.builtins import layout
from NodeForge.errors import CompileError

pytestmark = pytest.mark.unit


def _expr(source):
    return ast.parse(source, mode="eval").body


def test_layout_grid_compile_time_count_rejects_zero_components():
    comp = SimpleNamespace(consts={})
    with pytest.raises(CompileError):
        layout._compile_grid_count(comp, _expr("vector(0, 2, 1)"), "layout_grid() count", allow_zero=False)


def test_grid_points_compile_time_count_allows_zero_components():
    comp = SimpleNamespace(consts={})
    assert layout._compile_grid_count(comp, _expr("vector(0, 2, 1)"), "grid_points() count", allow_zero=True) == (0.0, 2.0, 1.0)


def test_grid_compile_time_count_rejects_fractional_components():
    comp = SimpleNamespace(consts={})
    with pytest.raises(CompileError):
        layout._compile_grid_count(comp, _expr("vector(2.5, 2, 1)"), "grid_points() count", allow_zero=True)


def test_grid_compile_time_count_allows_whole_float_components():
    comp = SimpleNamespace(consts={})
    assert layout._compile_grid_count(comp, _expr("vector(2.0, 2, 1)"), "grid_points() count", allow_zero=True) == (2.0, 2.0, 1.0)


def test_layout_scalar_count_rejects_negative_for_existing_layouts():
    comp = SimpleNamespace(consts={})
    with pytest.raises(CompileError):
        layout._compile_scalar_count(comp, _expr("-1"), "layout_circle() count", reject_negative=True)
    with pytest.raises(CompileError):
        layout._compile_scalar_count(comp, _expr("-1"), "layout_spiral() count", reject_negative=True)


def test_layout_scalar_count_zero_remains_allowed_for_safe_denominators():
    comp = SimpleNamespace(consts={})
    assert layout._compile_scalar_count(comp, _expr("0"), "layout_circle() count", reject_negative=True) == 0
    assert layout._compile_scalar_count(comp, _expr("0"), "layout_spiral() count", reject_negative=True) == 0

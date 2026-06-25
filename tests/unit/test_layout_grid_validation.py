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


def test_layout_circle_bind_args_allows_omitted_count():
    args = layout._bind_args(
        _expr("layout_circle(points(4), radius=2.0)"),
        "layout_circle",
        ("geometry", "count", "radius", "start_angle", "end_angle", "include_endpoint"),
        {"count": None, "radius": 1.0, "start_angle": 0.0, "end_angle": 6.283185307179586, "include_endpoint": False},
    )
    assert args["count"] is None
    assert ast.unparse(args["radius"]) == "2.0"


def test_layout_spiral_bind_args_allows_omitted_count():
    args = layout._bind_args(
        _expr("layout_spiral(points(4), radius=2.0, turns=3.0)"),
        "layout_spiral",
        ("geometry", "count", "radius", "turns", "height", "start_radius", "start_angle"),
        {"count": None, "radius": 1.0, "turns": 1.0, "height": 0.0, "start_radius": 0.0, "start_angle": 0.0},
    )
    assert args["count"] is None
    assert ast.unparse(args["radius"]) == "2.0"
    assert ast.unparse(args["turns"]) == "3.0"


def test_layout_circle_second_positional_argument_remains_count():
    args = layout._bind_args(
        _expr("layout_circle(points(4), 4, radius=2.0)"),
        "layout_circle",
        ("geometry", "count", "radius", "start_angle", "end_angle", "include_endpoint"),
        {"count": None, "radius": 1.0, "start_angle": 0.0, "end_angle": 6.283185307179586, "include_endpoint": False},
    )
    assert ast.unparse(args["count"]) == "4"
    assert ast.unparse(args["radius"]) == "2.0"


def test_layout_circle_compile_call_allows_omitted_count(monkeypatch):
    captured = {}

    def fake_compile_value(comp, expr, label):
        return "geo"

    def fake_compile_numeric(comp, expr, label):
        return ast.unparse(expr) if hasattr(expr, "_fields") else expr

    def fake_layout_circle(group, geo, count, radius, start_angle, end_angle, include_endpoint, x, y):
        captured.update({"geo": geo, "count": count, "radius": radius})
        return "layout-result"

    monkeypatch.setattr(layout, "_compile_value", fake_compile_value)
    monkeypatch.setattr(layout, "_compile_numeric", fake_compile_numeric)
    monkeypatch.setattr(layout, "_compile_bool_const", lambda comp, expr, label: expr)
    monkeypatch.setattr(layout, "_layout_circle_geometry", fake_layout_circle)

    comp = SimpleNamespace(group="group", consts={})
    result = layout.compile_call(comp, _expr("layout_circle(points(4), radius=2.0)"))

    assert result == "layout-result"
    assert captured == {"geo": "geo", "count": None, "radius": "2.0"}


def test_layout_spiral_compile_call_allows_omitted_count(monkeypatch):
    captured = {}

    def fake_compile_value(comp, expr, label):
        return "geo"

    def fake_compile_numeric(comp, expr, label):
        return ast.unparse(expr) if hasattr(expr, "_fields") else expr

    def fake_layout_spiral(group, geo, count, radius, turns, height, start_radius, start_angle, x, y):
        captured.update({"geo": geo, "count": count, "radius": radius, "turns": turns})
        return "layout-result"

    monkeypatch.setattr(layout, "_compile_value", fake_compile_value)
    monkeypatch.setattr(layout, "_compile_numeric", fake_compile_numeric)
    monkeypatch.setattr(layout, "_layout_spiral_geometry", fake_layout_spiral)

    comp = SimpleNamespace(group="group", consts={})
    result = layout.compile_call(comp, _expr("layout_spiral(points(4), radius=2.0, turns=3.0)"))

    assert result == "layout-result"
    assert captured == {"geo": "geo", "count": None, "radius": "2.0", "turns": "3.0"}

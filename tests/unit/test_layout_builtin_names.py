import pytest

from NodeForge.builtins import layout, registry

pytestmark = pytest.mark.unit


def test_layout_names_are_registered():
    expected = {
        "layout_grid",
        "grid_points",
        "layout_circle",
        "circle_points",
        "layout_spiral",
        "spiral_points",
        "layout_random",
        "random_points",
    }
    assert layout.NAMES == expected
    assert expected.issubset(registry.CALLABLE_BUILTIN_NAMES)

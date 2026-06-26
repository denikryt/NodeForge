import pytest

from NodeForge.builtins import layout, registry

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


def test_layout_private_validation_api_removed_with_builtin_cutover():
    assert layout.NAMES == set()
    for private_name in (
        "_compile_grid_count",
        "_compile_scalar_count",
        "_bind_args",
    ):
        assert not hasattr(layout, private_name)


def test_migrated_layout_names_are_not_global_builtins():
    assert MIGRATED_LAYOUT_NAMES.isdisjoint(registry.CALLABLE_BUILTIN_NAMES)

import pytest

from NodeForge.constants import _FLOAT_FUNCS_1, _FLOAT_FUNCS_2
from NodeForge.builtins import math, registry, vector, node_wrappers
from NodeForge.consteval import _ALLOWED_MATH_FUNCS


pytestmark = pytest.mark.unit


STAGE5_MIGRATED_FUNCTIONS = {
    "inverse_lerp", "remap", "saturate", "step", "smoothstep", "smootherstep",
    "pingpong", "wrap", "sign", "rotate2d", "polar", "angle_between", "rotate_around_axis",
}

STAGE5_REMOVED_GLOBALS = {
    "lerp", "frac", "greater_than", "greater_equal", "less_than", "less_equal", "equal", "not_equal",
}

ACCEPTED_CORE_CALLABLES = {
    "input_geometry", "input_float", "input_int", "input_bool", "input_vector",
    "position", "normal", "index", "id",
    "vector",
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sqrt", "abs", "floor",
    "ceil", "round", "fract", "radians", "degrees", "exp", "ln", "log", "min", "max",
    "pow", "mod", "clamp", "mix", "select", "map_range",
    "length", "distance", "dot", "normalize", "cross", "reflect", "project",
    "noise", "random_value",
    "empty_geometry", "points", "point", "line", "grid", "grid_uv",
    "set_position", "store_named_attribute", "set_material",
    "cube", "polyline", "join", "transform",
    "instance_on_points", "realize_instances",
    "node",
}


def test_math_names_match_core_table_driven_specs():
    expected = set(_FLOAT_FUNCS_1) | set(_FLOAT_FUNCS_2) | {
        "ln", "clamp", "mix", "select", "map_range", "noise", "random_value",
    }

    assert math.NAMES == expected
    assert set(_FLOAT_FUNCS_1).issubset(math._SPECS)
    assert set(_FLOAT_FUNCS_2).issubset(math._SPECS)
    assert "frac" not in _FLOAT_FUNCS_1
    assert "sign" not in _FLOAT_FUNCS_1
    assert "fract" in _FLOAT_FUNCS_1


def test_vector_names_match_core_vector_primitives():
    assert vector.NAMES == {"vector", "length", "distance", "dot", "normalize", "cross", "reflect", "project"}


def test_node_wrapper_names_are_removed_from_public_surface():
    assert node_wrappers.NAMES == set()


def test_stage5_names_are_absent_from_callable_builtins():
    removed = STAGE5_MIGRATED_FUNCTIONS | STAGE5_REMOVED_GLOBALS
    assert removed.isdisjoint(registry.CALLABLE_BUILTIN_NAMES)
    assert registry.CALLABLE_BUILTIN_NAMES <= ACCEPTED_CORE_CALLABLES


def test_consteval_compile_time_surface_excludes_non_core_sign():
    assert "sign" not in _ALLOWED_MATH_FUNCS
    assert {"sin", "cos", "sqrt", "ln", "abs"}.issubset(_ALLOWED_MATH_FUNCS)


@pytest.mark.parametrize("name, spec", sorted(math._SPECS.items()))
def test_math_specs_have_stable_name_and_param_metadata(name, spec):
    assert spec.name == name
    assert isinstance(spec.params, tuple)
    assert all(isinstance(param, str) and param for param in spec.params)
    assert callable(spec.compile_fn)

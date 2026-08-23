import pytest
from pathlib import Path

from NodeForge.constants import _FLOAT_FUNCS_1, _FLOAT_FUNCS_2
from NodeForge import packages
from NodeForge.builtins import registry, vector, node_wrappers
from NodeForge.consteval import _ALLOWED_MATH_FUNCS
from NodeForge.systems import registry as systems_registry


pytestmark = pytest.mark.unit


MATH_LIBRARY_NAMES = set(_FLOAT_FUNCS_1) | set(_FLOAT_FUNCS_2) | {
    "ln", "clamp", "mix", "select", "map_range", "noise", "random_value",
}

MATH_LIBRARY_PARAM_ORDER = {
    **{name: ("value",) for name in _FLOAT_FUNCS_1},
    **{name: ("a", "b") for name in _FLOAT_FUNCS_2},
    "ln": ("value",),
    "clamp": ("value", "min", "max"),
    "mix": ("a", "b", "factor"),
    "select": ("cond", "true", "false"),
    "map_range": ("value", "from_min", "from_max", "to_min", "to_max"),
}


MIGRATED_DSL_FUNCTIONS = {
    "inverse_lerp", "remap", "saturate", "step", "smoothstep", "smootherstep",
    "pingpong", "wrap", "sign", "rotate2d", "polar", "angle_between", "rotate_around_axis",
}

REMOVED_GLOBALS = {
    "lerp", "frac", "greater_than", "greater_equal", "less_than", "less_equal", "equal", "not_equal",
}

ACCEPTED_CORE_CALLABLES = {
    "input_geometry", "input_float", "input_int", "input_bool", "input_vector", "input_material", "input_object", "input_string",
    "position", "normal", "index", "id",
    "vector",
    "length", "distance", "dot", "normalize", "cross", "reflect", "project",
    "empty_geometry", "geometry_builder", "points", "point", "line", "grid", "grid_uv",
    "set_position", "store_named_attribute", "capture_attribute", "set_material",
    "cube", "polyline", "join", "transform",
    "instance_on_points", "realize_instances",
    "node",
}


@pytest.fixture(autouse=True)
def package_inventory(tmp_path):
    packages.set_packages_dir_for_tests(tmp_path)
    project_root = Path(__file__).resolve().parents[3]
    packages.install_package_directory(project_root / "nodeforge.math", allow_python=True)
    systems_registry.clear_cache()
    yield
    packages.set_packages_dir_for_tests(None)
    systems_registry.clear_cache()


def test_math_library_names_are_owned_by_math_package():
    names = set(systems_registry.constructor_names())

    assert MATH_LIBRARY_NAMES <= names
    for name in ["sin", "sqrt", "clamp", "map_range", "noise", "random_value"]:
        owner = systems_registry.constructor_owner(name)
        assert owner is not None
        assert owner.package_id == "nodeforge.math"
        assert owner.system_id == "math"


def test_math_package_handlers_are_package_local():
    owner = systems_registry.constructor_owner("sin")
    assert owner is not None

    system_source = owner.module_path.read_text(encoding="utf-8")
    assert "NodeForge.builtins.math" not in system_source
    assert "from .constructors import HANDLERS" in system_source

    handlers = {name: systems_registry.get_handler(name) for name in MATH_LIBRARY_NAMES}
    assert set(handlers) == MATH_LIBRARY_NAMES
    assert {handler.__module__ for handler in handlers.values()} == {
        handler.__module__ for handler in handlers.values()
    }
    assert all("nodeforge_math" in handler.__module__ for handler in handlers.values())


def test_math_package_constructor_set_matches_current_math_tables():
    expected = MATH_LIBRARY_NAMES
    constructors_path = systems_registry.constructor_owner("sin").root / "constructors.py"
    text = constructors_path.read_text(encoding="utf-8")

    assert "_FLOAT_FUNCS_1" in text
    assert "_FLOAT_FUNCS_2" in text
    assert "NodeForge.builtins.math" not in text
    assert expected <= set(systems_registry.constructor_names())
    assert "frac" not in _FLOAT_FUNCS_1
    assert "sign" not in _FLOAT_FUNCS_1
    assert "fract" in _FLOAT_FUNCS_1


def test_vector_names_match_core_vector_primitives():
    assert vector.NAMES == {"vector", "length", "distance", "dot", "normalize", "cross", "reflect", "project"}


def test_node_wrapper_names_are_removed_from_public_surface():
    assert node_wrappers.NAMES == set()


def test_math_library_names_are_absent_from_callable_builtins():
    removed = MIGRATED_DSL_FUNCTIONS | REMOVED_GLOBALS | MATH_LIBRARY_NAMES
    assert removed.isdisjoint(registry.CALLABLE_BUILTIN_NAMES)
    assert registry.CALLABLE_BUILTIN_NAMES <= ACCEPTED_CORE_CALLABLES


def test_consteval_compile_time_surface_keeps_allowed_constant_math_names():
    assert "sign" not in _ALLOWED_MATH_FUNCS
    assert {"sin", "cos", "sqrt", "ln", "abs"}.issubset(_ALLOWED_MATH_FUNCS)


@pytest.mark.parametrize("name, params", sorted(MATH_LIBRARY_PARAM_ORDER.items()))
def test_math_library_keyword_param_contract(name, params):
    assert name in systems_registry.constructor_names()
    assert isinstance(params, tuple)
    assert all(isinstance(param, str) and param for param in params)

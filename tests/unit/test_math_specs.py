import pytest

from NodeForge.constants import _FLOAT_FUNCS_1, _FLOAT_FUNCS_2
from NodeForge.builtins import math

pytestmark = pytest.mark.unit


def test_math_names_match_table_driven_specs():
    expected = set(_FLOAT_FUNCS_1) | set(_FLOAT_FUNCS_2) | {
        "ln", "clamp", "mix", "lerp", "select", "map_range",
        "inverse_lerp", "remap", "saturate", "step", "smoothstep", "smootherstep", "pingpong", "wrap",
        "noise", "random_value",
    }

    assert math.NAMES == expected
    assert set(_FLOAT_FUNCS_1).issubset(math._SPECS)
    assert set(_FLOAT_FUNCS_2).issubset(math._SPECS)


@pytest.mark.parametrize("name, spec", sorted(math._SPECS.items()))
def test_math_specs_have_stable_name_and_param_metadata(name, spec):
    assert spec.name == name
    assert isinstance(spec.params, tuple)
    assert all(isinstance(param, str) and param for param in spec.params)
    assert callable(spec.compile_fn)

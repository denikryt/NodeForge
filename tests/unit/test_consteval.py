import ast

import pytest

from NodeForge.consteval import _const_eval
from NodeForge.errors import CompileError

pytestmark = pytest.mark.unit


def test_unimported_sign_is_not_allowed_in_compile_time_calls():
    expr = ast.parse("sign(-1)", mode="eval").body
    with pytest.raises(CompileError, match="Unsupported compile-time expression"):
        _const_eval(expr, {})


def test_unimported_sign_is_not_allowed_in_compile_time_list_context():
    expr = ast.parse("[sign(-1)]", mode="eval").body
    with pytest.raises(CompileError, match="Unsupported compile-time expression"):
        _const_eval(expr, {})


def _eval_expr(source, env=None):
    return _const_eval(ast.parse(source, mode="eval").body, env or {})


def test_compile_time_f_string_accepts_only_string_fragments():
    assert _eval_expr('f"A{part}B"', {"part": "X"}) == "AXB"
    assert _eval_expr('f"{{{part}}}"', {"part": "X"}) == "{X}"


@pytest.mark.parametrize(
    "source, env",
    [
        ('f"{missing}"', {}),
        ('f"{count}"', {"count": 1}),
        ('f"{flag}"', {"flag": True}),
        ('f"{items}"', {"items": ["A"]}),
        ('f"{part!r}"', {"part": "A"}),
        ('f"{part:>4}"', {"part": "A"}),
    ],
)
def test_compile_time_f_string_rejects_non_string_or_formatted_interpolation(source, env):
    with pytest.raises(CompileError):
        _eval_expr(source, env)


def test_literal_string_and_input_discovery_use_compile_time_fstrings():
    from NodeForge.parsing import _collect_inputs, _literal_string, _parse_source
    from NodeForge.statements import _kw_dict, _optional_string_kw

    expr = ast.parse('f"{prefix} Name"', mode="eval").body
    assert _literal_string(expr, "name", {"prefix": "Socket"}) == "Socket Name"

    call = ast.parse('store("c", x, domain=f"{domain_name}", type=f"{kind}")', mode="exec").body[0].value
    kws = _kw_dict(call)
    assert _optional_string_kw(kws, "domain", "POINT", {"domain_name": "FACE", "kind": "COLOR"}) == "FACE"
    assert _optional_string_kw(kws, "type", None, {"domain_name": "FACE", "kind": "COLOR"}) == "COLOR"

    stmts = _parse_source('prefix = "Result"\nvalue = input_float(f"{prefix} Value")\noutput(f"{prefix} Output", value)')
    from NodeForge.consteval import _preprocess_compile_time

    retained, consts = _preprocess_compile_time(stmts)
    assert _collect_inputs(retained, consts=consts) == []

    retained = _parse_source('output(f"{runtime_name}", 1)')
    assert _collect_inputs(retained, consts={}) == ["runtime_name"]


def _preprocess_source(source):
    from NodeForge.consteval import _preprocess_compile_time
    from NodeForge.parsing import _parse_source

    return _preprocess_compile_time(_parse_source(source))


def test_preprocess_preserves_integer_assignments_as_compile_time_range_candidates():
    retained, consts = _preprocess_source(
        """
BASE_SEGMENTS = 8
EXTRA_SEGMENTS = 4
MAX_SEGMENTS = BASE_SEGMENTS + EXTRA_SEGMENTS
parts = []
for i in range(MAX_SEGMENTS):
    parts.append(i)
output("count", MAX_SEGMENTS)
"""
    )

    assert consts["BASE_SEGMENTS"] == 8
    assert consts["EXTRA_SEGMENTS"] == 4
    assert consts["MAX_SEGMENTS"] == 12
    assert [stmt.targets[0].id for stmt in retained if isinstance(stmt, ast.Assign)] == [
        "BASE_SEGMENTS",
        "EXTRA_SEGMENTS",
        "MAX_SEGMENTS",
        "parts",
    ]


@pytest.mark.parametrize(
    "source, expected",
    [
        ("range(4)", [0, 1, 2, 3]),
        ("range(1, 4)", [1, 2, 3]),
        ("range(0, 6, 2)", [0, 2, 4]),
        ("range(-2, 2)", [-2, -1, 0, 1]),
        ("range(4, 0, -1)", [4, 3, 2, 1]),
        ("range(+3)", [0, 1, 2]),
    ],
)
def test_compile_time_range_accepts_integer_arguments_only(source, expected):
    assert _eval_expr(source) == expected


@pytest.mark.parametrize("source", ["range(2.5)", "range(True)"])
def test_compile_time_range_rejects_non_integer_arguments(source):
    with pytest.raises(CompileError, match=r"range\(\) arguments must be compile-time integers"):
        _eval_expr(source)


def test_compile_time_range_rejects_zero_step_with_compile_error():
    with pytest.raises(CompileError, match=r"range\(\) step must not be zero"):
        _eval_expr("range(0, 4, 0)")


def test_preprocess_invalidates_integer_range_candidate_after_non_integer_reassignment():
    retained, consts = _preprocess_source(
        """
MAX_SEGMENTS = 8
MAX_SEGMENTS = input_int("Segments")
parts = []
for i in range(MAX_SEGMENTS):
    parts.append(i)
output("count", MAX_SEGMENTS)
"""
    )

    assert "MAX_SEGMENTS" not in consts
    assert any(isinstance(stmt, ast.For) for stmt in retained)


def test_preprocess_invalidates_integer_range_candidate_after_empty_list_reassignment():
    retained, consts = _preprocess_source(
        """
COUNT = 16
COUNT = []
output("x", 1)
"""
    )

    assert "COUNT" not in consts
    assert [stmt.targets[0].id for stmt in retained if isinstance(stmt, ast.Assign)] == ["COUNT", "COUNT"]


def test_handle_stmt_invalidates_integer_candidate_for_preserved_runtime_state_range_state():
    from NodeForge.consteval import _handle_compile_time_stmt

    stmt = ast.parse("COUNT = 16").body[0]
    env = {"COUNT": 8}
    out = []

    _handle_compile_time_stmt(stmt, env, out, preserve_names={"COUNT"})

    assert "COUNT" not in env
    assert out == [stmt]


def test_preprocess_preserves_mixed_runtime_state_range_initializers():
    retained, consts = _preprocess_source(
        """
geo = cube(0.1)
pos = vector(0, 0, 0)
angle = 1
flag = True
for i in range(count):
    geo = geo
    pos = pos
    angle = angle + 1
    flag = flag
output("Geometry", geo)
"""
    )

    retained_assigns = [stmt.targets[0].id for stmt in retained if isinstance(stmt, ast.Assign)]
    assert "geo" in retained_assigns
    assert "pos" in retained_assigns
    assert "angle" in retained_assigns
    assert "flag" in retained_assigns
    assert any(isinstance(stmt, ast.For) for stmt in retained)
    assert "count" not in consts

    from NodeForge.consteval import _infer_input_types
    from NodeForge.constants import TYPE_INT

    assert _infer_input_types(retained).get("count") == TYPE_INT


def test_preprocess_invalidates_integer_candidate_after_augmented_assignment():
    retained, consts = _preprocess_source(
        """
COUNT = 16
COUNT += 1
output("x", 1)
"""
    )

    assert "COUNT" not in consts
    assert any(isinstance(stmt, ast.AugAssign) for stmt in retained)


def test_preprocess_rejects_invalid_constant_range_before_runtime_fallback():
    with pytest.raises(CompileError, match=r"range\(\) arguments must be compile-time integers"):
        _preprocess_source('for i in range(2.5):\n    output("x", i)\n')

    with pytest.raises(CompileError, match=r"range\(\) arguments must be compile-time integers"):
        _preprocess_source('for i in range(True):\n    output("x", i)\n')

    with pytest.raises(CompileError, match=r"range\(\) step must not be zero"):
        _preprocess_source('for i in range(0, 4, 0):\n    output("x", i)\n')


def test_preprocess_defers_range_with_runtime_name_to_statement_compiler():
    retained, consts = _preprocess_source('COUNT = input_int("Count")\nfor i in range(COUNT):\n    output("x", i)\n')

    assert "COUNT" not in consts
    assert any(isinstance(stmt, ast.For) for stmt in retained)

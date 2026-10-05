import ast

import pytest

from NodeForge.consteval import (
    ConstEvalUnavailable,
    NOT_FOLDABLE,
    _collect_preprocessing_written_names,
    _const_eval,
    _is_const_number,
    _is_const_vector_like,
    try_runtime_fold,
)
from NodeForge.compile_time import CompileTimeState, ConstVector
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.numeric_semantics import normalize_float_constant

pytestmark = pytest.mark.unit


def test_unimported_sign_is_not_allowed_in_compile_time_calls():
    expr = ast.parse("sign(-1)", mode="eval").body
    with pytest.raises(ConstEvalUnavailable):
        _const_eval(expr, {})


def test_unimported_sign_is_not_allowed_in_compile_time_list_context():
    expr = ast.parse("[sign(-1)]", mode="eval").body
    with pytest.raises(ConstEvalUnavailable):
        _const_eval(expr, {})


def _eval_expr(source, env=None):
    return _const_eval(ast.parse(source, mode="eval").body, env or {})


@pytest.mark.parametrize(
    "source",
    [
        "sin(0.5)",
        "abs(-3)",
        "floor(1.9)",
        "ln(2.0)",
        "count_zero(0, 1, 0)",
        "pow(2, 3)",
        "mod(5, 2)",
    ],
)
def test_core_consteval_does_not_own_named_package_math_calls(source):
    with pytest.raises(ConstEvalUnavailable):
        _eval_expr(source)


def test_core_consteval_retains_structural_helpers_and_constants():
    from math import e, pi, tau

    assert _eval_expr("range(1, 4)") == [1, 2, 3]
    assert _eval_expr("len([1, 2, 3])") == 3
    assert _eval_expr("sum([1, 2, 3])") == 6
    assert tuple(_eval_expr("vector(1, 2, 3)")) == (1.0, 2.0, 3.0)
    assert _eval_expr("pi") == normalize_float_constant(pi)
    assert _eval_expr("tau") == normalize_float_constant(tau)
    assert _eval_expr("e") == normalize_float_constant(e)


def test_core_consteval_keeps_operator_ownership_separate_from_named_math_calls():
    with pytest.raises(ConstEvalUnavailable):
        _eval_expr("2 ** 3")
    assert _eval_expr("5 % 2") == 1

    with pytest.raises(ConstEvalUnavailable):
        _eval_expr("pow(2, 3)")
    with pytest.raises(ConstEvalUnavailable):
        _eval_expr("mod(5, 2)")


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
    expected = ConstEvalUnavailable if "missing" in source else CompileError
    with pytest.raises(expected):
        _eval_expr(source, env)


def test_literal_string_and_input_discovery_use_compile_time_fstrings():
    from NodeForge.builtin_call_semantics import analyze_builtin_call
    from NodeForge.parsing import _collect_inputs, _literal_string, _parse_source

    expr = ast.parse('f"{prefix} Name"', mode="eval").body
    assert _literal_string(expr, "name", {"prefix": "Socket"}) == "Socket Name"

    call = ast.parse(
        'store_named_attribute(geo, "c", x, domain=f"{domain_name}", type=f"{kind}")',
        mode="eval",
    ).body
    runtime_types = {"geo": NFType.GEOMETRY, "x": NFType.FLOAT}

    def add_runtime(node, _parameter_name, _context):
        assert isinstance(node, ast.Name)
        return runtime_types[node.id]

    semantics = analyze_builtin_call(
        "store_named_attribute",
        call,
        {"domain_name": "FACE", "kind": "FLOAT"},
        add_runtime,
    )
    assert dict(semantics.options)["domain"] == "FACE"
    assert dict(semantics.options)["data_type"] == "FLOAT"

    stmts = _parse_source('prefix = "Result"\nvalue = input_float(f"{prefix} Value")\noutput(f"{prefix} Output", value)')
    from NodeForge.consteval import _preprocess_compile_time

    preprocessed = _preprocess_compile_time(stmts)
    assert _collect_inputs(
        preprocessed.statements,
        consts=preprocessed.final_compile_time.values,
    ) == []

    retained = _parse_source('output(f"{runtime_name}", 1)')
    assert _collect_inputs(retained, consts={}) == ["runtime_name"]

    with pytest.raises(CompileError, match="compile-time f-string interpolations must be strings"):
        _collect_inputs(_parse_source('output(f"{1}", 1)'), consts={})

    with pytest.raises(CompileError, match="Expected a non-empty compile-time string for name"):
        _literal_string(ast.parse("runtime_name", mode="eval").body, "name", {})
    with pytest.raises(CompileError, match="not expects Bool"):
        _literal_string(ast.parse("not 1", mode="eval").body, "name", {})


def _preprocess_source(source):
    from NodeForge.consteval import _preprocess_compile_time
    from NodeForge.parsing import _parse_source

    preprocessed = _preprocess_compile_time(_parse_source(source))
    return list(preprocessed.statements), preprocessed.final_compile_time.values


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
    assert [stmt.targets[0].id for stmt in retained if isinstance(stmt, ast.Assign)] == ["MAX_SEGMENTS", "parts"]


def test_consteval_unavailability_is_not_a_compile_error():
    assert not issubclass(ConstEvalUnavailable, CompileError)


def test_unknown_call_ownership_is_decided_before_argument_evaluation():
    expr = ast.parse("sin(range(0, 3, 0))", mode="eval").body
    with pytest.raises(ConstEvalUnavailable):
        _const_eval(expr, {})


def test_compile_time_boolean_operators_require_exact_bool_operands():
    assert _eval_expr("not True") is False
    assert _eval_expr("True and False") is False
    assert _eval_expr("True or False") is True
    with pytest.raises(CompileError, match="not expects Bool"):
        _eval_expr("not 1")
    with pytest.raises(CompileError, match="Boolean operations expect Bool values"):
        _eval_expr("True and 1")


def test_runtime_fold_is_minimal_closed_world_and_independent_from_consteval():
    assert try_runtime_fold(ast.parse("1", mode="eval").body, {}) == 1
    assert try_runtime_fold(ast.parse("pi", mode="eval").body, {}) == pytest.approx(3.141592653589793)
    assert try_runtime_fold(ast.parse("1 + 2", mode="eval").body, {}) is NOT_FOLDABLE
    assert try_runtime_fold(ast.parse("not True", mode="eval").body, {}) is NOT_FOLDABLE
    assert try_runtime_fold(ast.parse("vector(1, 2, 3)", mode="eval").body, {}) is NOT_FOLDABLE
    assert try_runtime_fold(ast.parse("known", mode="eval").body, {"known": 4}) is NOT_FOLDABLE


def test_runtime_owned_chained_comparison_is_consteval_unavailable_not_invalid():
    """CTFE limitations must not reject a comparison form owned by runtime semantics."""
    expr = ast.parse("a < b < c", mode="eval").body

    with pytest.raises(ConstEvalUnavailable):
        _const_eval(expr, {"a": 1.0, "b": 2.0, "c": 3.0})


def test_assignment_preprocessing_keeps_known_nonfoldable_runtime_source_and_fact():
    retained, consts = _preprocess_source("d = 1 / 2\noutput(d)")
    assigns = [stmt for stmt in retained if isinstance(stmt, ast.Assign)]
    assert [stmt.targets[0].id for stmt in assigns] == ["d"]
    assert consts["d"] == 0.5


def test_compile_time_owned_assignment_roots_are_erased_but_ownership_is_root_only():
    retained, consts = _preprocess_source(
        'n = len([1, 2, 3])\nxs = range(n)\nitems = [1, 2]\nlabel = f"ok"\nx = len([1, 2, 3]) / 2\n'
    )
    retained_names = [stmt.targets[0].id for stmt in retained if isinstance(stmt, ast.Assign)]
    assert retained_names == ["x"]
    assert consts["n"] == 3
    assert consts["xs"] == [0, 1, 2]
    assert consts["items"] == [1, 2]
    assert consts["label"] == "ok"
    assert consts["x"] == 1.5


def test_retained_runtime_if_invalidates_written_names_before_following_preprocessing():
    retained, consts = _preprocess_source(
        'x = 1\nflag = input_bool("Flag")\nif flag:\n    x = 2\nelse:\n    x = 3\nfor i in range(x):\n    y = i\n'
    )
    assert "x" not in consts
    assert any(isinstance(stmt, ast.If) for stmt in retained)
    assert any(isinstance(stmt, ast.For) for stmt in retained)


@pytest.mark.parametrize("condition", ["True", "False"])
def test_preprocess_retains_literal_ordinary_if_and_invalidates_branch_writes(condition):
    retained, consts = _preprocess_source(
        f'x = 1\nif {condition}:\n    x = 2\nelse:\n    x = 3\nfor i in range(x):\n    y = i\n'
    )
    branches = [stmt for stmt in retained if isinstance(stmt, ast.If)]
    assert len(branches) == 1
    assert ast.unparse(branches[0].test) == condition
    assert "x" not in consts
    assert any(isinstance(stmt, ast.For) for stmt in retained)


def test_runtime_if_seed_prescan_is_removed():
    import NodeForge.consteval as consteval

    assert not hasattr(consteval, "_collect_runtime_if_seed_names")


def test_runtime_if_seed_assignment_is_erased_and_write_barrier_still_invalidates_future_preprocessing():
    retained, consts = _preprocess_source(
        'x = 0.0\n'
        'flag = input_bool("Flag")\n'
        'if flag:\n'
        '    x = x + 1.0\n'
        'else:\n'
        '    x = x + 2.0\n'
        'output("X", x)\n'
    )
    assigns = [
        stmt
        for stmt in retained
        if isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and stmt.targets[0].id == "x"
    ]
    assert assigns == []
    assert "x" not in consts
    assert any(isinstance(stmt, ast.If) for stmt in retained)


def test_preprocessing_written_name_collector_is_recursive_and_binding_only():
    stmts = ast.parse(
        "a = b = 1\n"
        "c, [d, e] = values\n"
        "f: Int = 1\n"
        "g += 1\n"
        "obj.attr = 1\n"
        "arr[0] = 1\n"
        "if flag:\n    h = 1\nelse:\n    for i in values:\n        j = i\n"
    ).body
    assert _collect_preprocessing_written_names(stmts) == {"a", "b", "c", "d", "e", "f", "g", "h", "i", "j"}


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
    assert [stmt.targets[0].id for stmt in retained if isinstance(stmt, ast.Assign)] == ["COUNT"]


def test_handle_stmt_invalidates_integer_candidate_for_preserved_repeat_range_state():
    from NodeForge.consteval import _PreprocessRecorder, _handle_compile_time_stmt

    stmt = ast.parse("COUNT = 16").body[0]
    env = {"COUNT": 8}
    recorder = _PreprocessRecorder()

    _handle_compile_time_stmt(stmt, CompileTimeState(env, adopt_mapping=True), recorder, preserve_names={"COUNT"})

    assert "COUNT" not in env
    assert recorder.statements == [stmt]


def test_preprocess_preserves_mixed_repeat_range_state_initializers():
    retained, consts = _preprocess_source(
        """
geo = cube(0.1)
pos = vector(0, 0, 0)
angle = 1
flag = True
for i in repeat_range(count):
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


def test_preprocess_preserves_state_mutated_only_inside_nested_repeat_range():
    retained, consts = _preprocess_source(
        """
x = 0
for i in repeat_range(2):
    for j in repeat_range(3):
        x = x + 1
output("x", x)
"""
    )

    retained_assigns = [
        stmt.targets[0].id
        for stmt in retained
        if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name)
    ]
    assert "x" in retained_assigns
    assert "x" not in consts


def test_preprocess_preserves_nested_repeat_state_under_runtime_if():
    retained, consts = _preprocess_source(
        """
x = 0
for i in repeat_range(2):
    for j in repeat_range(3):
        if flag:
            x = x + 1
        else:
            x = x
output("x", x)
"""
    )

    retained_assigns = [
        stmt.targets[0].id
        for stmt in retained
        if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name)
    ]
    assert "x" in retained_assigns
    assert "x" not in consts


def test_infer_input_types_finds_implicit_nested_repeat_count():
    from NodeForge.consteval import _infer_input_types
    from NodeForge.constants import TYPE_INT
    from NodeForge.parsing import _parse_source

    stmts = _parse_source(
        """
x = 0
for i in repeat_range(2):
    for j in repeat_range(inner_count):
        x = x + 1
output("x", x)
"""
    )

    assert _infer_input_types(stmts).get("inner_count") == TYPE_INT


def test_preprocess_failed_runtime_loop_trial_retains_original_for_without_partial_ast():
    """Any retained runtime statement makes preprocessing keep the original loop exactly once."""
    retained, consts = _preprocess_source(
        "items = []\n"
        "for i in range(2):\n"
        "    value = input_float('X')\n"
        "    items.append(i)\n"
        "output('x', 1)\n"
    )
    loops = [stmt for stmt in retained if isinstance(stmt, ast.For)]
    assert len(loops) == 1
    assert ast.unparse(loops[0]).startswith("for i in range(2):")
    assert "i" not in consts


def test_compile_time_append_preserves_legacy_ignored_keyword_compatibility():
    """Compile-time list append keeps the v0.51.3 behavior of ignoring keyword arguments."""
    retained, constants = _preprocess_source(
        "items = [1]\n"
        "items.append(2, ignored=missing)\n"
    )
    assert retained == []
    assert constants["items"] == [1, 2]


def test_nested_speculative_loop_append_journal_rolls_back_to_outer_savepoint():
    """Successful inner trials remain rollback-visible when a later outer statement rejects the trial."""
    from NodeForge.consteval import _PreprocessRecorder, _handle_compile_time_stmt

    shared = []
    state = CompileTimeState({"items": shared})
    stmt = ast.parse(
        "for i in range(2):\n"
        "    for j in range(1):\n"
        "        items.append(j)\n"
        "    runtime_statement()\n"
    ).body[0]
    recorder = _PreprocessRecorder()
    _handle_compile_time_stmt(stmt, state, recorder)
    assert recorder.statements == [stmt]
    assert shared == []
    assert state.get("items") is shared


def test_preprocess_retains_flat_unpack_for_array_mutation():
    """Array append loops retain flat targets for semantic lowering, preserving graph behavior."""
    retained, constants = _preprocess_source(
        "items = []\n"
        "for x, y in [[1, 2]]:\n"
        "    items.append(x + y)\n"
        "output(items[0])\n"
    )
    assert any(isinstance(stmt, ast.For) for stmt in retained)
    assert "items" not in constants
    assert isinstance(retained[0], ast.Assign)


def test_preprocess_rejects_flat_unpack_for_without_builder_specific_semantics():
    """Generic flat unpack is rejected uniformly outside the builder-specific retained rule."""
    with pytest.raises(CompileError, match="Only simple compile-time for targets are supported"):
        _preprocess_source(
            "for x, y in [[1, 2]]:\n"
            "    total = x + y\n"
        )

def test_geometry_builder_simple_name_loop_has_no_builder_specific_preprocess_route():
    """Builder loops use ordinary preprocessing; Semantic Body owns retained runtime statements."""
    retained, _consts = _preprocess_source(
        "builder = geometry_builder()\n"
        "for i in range(3):\n"
        "    builder.add(cube(i + 1))\n"
        "builder.geometry\n"
    )
    loops = [stmt for stmt in retained if isinstance(stmt, ast.For)]
    assert len(loops) == 1
    assert isinstance(loops[0].target, ast.Name)
    assert loops[0].target.id == "i"


def test_compile_time_append_journal_savepoint_rolls_back_only_its_suffix():
    """Nested speculative transactions preserve parent journal entries below their savepoint."""
    from NodeForge.consteval import (
        _compile_time_list_append_savepoint,
        _rollback_compile_time_list_appends_to,
    )

    outer = []
    inner = []
    journal = [(outer, 0)]
    outer.append("outer")
    savepoint = _compile_time_list_append_savepoint(journal)
    journal.append((inner, 0))
    inner.append("inner")

    _rollback_compile_time_list_appends_to(journal, savepoint)

    assert outer == ["outer"]
    assert inner == []
    assert journal == [(outer, 0)]


def test_shared_static_number_and_vector_like_predicates_preserve_backend_shapes():
    """Frontend/backend static-shape authority keeps the pre-Stage-29 contract."""
    assert _is_const_number(1)
    assert _is_const_number(1.0)
    assert _is_const_number(_eval_expr("1 / 1.0"))
    assert not _is_const_number(True)
    assert not _is_const_number("bad")

    assert _is_const_vector_like(ConstVector((1.0, 2.0, 3.0)))
    assert _is_const_vector_like((1, 2, 3))
    assert _is_const_vector_like([1.0, 2, 3])
    assert not _is_const_vector_like((1, 2))
    assert not _is_const_vector_like((1, True, 3))
    assert not _is_const_vector_like("bad")

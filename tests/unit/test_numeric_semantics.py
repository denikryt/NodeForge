"""canonical numeric regression coverage for canonical NodeForge numeric semantics."""

import ast
import math
import struct
from types import MappingProxyType

import pytest

from NodeForge.semantic.call_resolution import CallableEnvironment
from NodeForge.semantic.compile_time import CompileTimeSnapshot, ConstVector
from NodeForge.compiler_identities import BindingId
from NodeForge.semantic.constants import TYPE_BOOL, TYPE_FLOAT, TYPE_INT, TYPE_VECTOR
from NodeForge.semantic.consteval import ConstEvalUnavailable, NOT_FOLDABLE, _const_eval, try_runtime_fold
from NodeForge.errors import CompileError
from NodeForge.semantic.numeric_semantics import (
    FLOAT_MAX,
    INT_MAX,
    INT_MIN,
    evaluate_float_basic,
    evaluate_float_divide,
    evaluate_float_floor_divide,
    evaluate_float_floored_modulo,
    evaluate_float_negate,
    evaluate_int_floor_divide,
    evaluate_int_floored_modulo,
    normalize_float_constant,
    normalize_int_constant,
    resolve_numeric_binary,
)
from NodeForge.semantic.analysis import (
    RuntimeBindingSymbol,
    RuntimeResultShape,
    SemanticEnvironment,
    analyze_expression,
    build_semantic_constant_snapshot,
)
from NodeForge.semantic.lowering import lower_analyzed_expression
from NodeForge.semantic.ir import IRBinary, IRLiteral


pytestmark = pytest.mark.unit


def _bits(value):
    """Return one canonical float32 bit pattern for exact representation assertions."""
    return struct.unpack("!I", struct.pack("!f", value))[0]


def _eval(source, env=None):
    """Evaluate one canonical numeric compile-time expression."""
    return _const_eval(ast.parse(source, mode="eval").body, env or {})


def _env(*, bindings=None, consts=None):
    """Build the smallest immutable semantic environment needed by numeric tests."""
    runtime_bindings = {
        name: RuntimeBindingSymbol(BindingId("numeric_semantics", index), typ)
        for index, (name, typ) in enumerate((bindings or {}).items())
    }
    constants, const_eval_values = build_semantic_constant_snapshot(
        CompileTimeSnapshot(consts or {})
    )
    return SemanticEnvironment(
        MappingProxyType(runtime_bindings),
        constants,
        const_eval_values,
        MappingProxyType({}),
        callable_environment=CallableEnvironment(frozenset(), {}, {}, {}),
    )


def _analyze(source, **kwargs):
    """Analyze one numeric expression under a compact semantic environment."""
    expr = ast.parse(source, mode="eval").body
    analysis = analyze_expression(expr, _env(**kwargs))
    assert analysis is not None
    return expr, analysis


def _root_type(analysis):
    """Return the scalar runtime NFType attached to an analyzed root."""
    fact = analysis.facts[analysis.root]
    assert isinstance(fact.result_shape, RuntimeResultShape)
    return fact.result_shape.typ


@pytest.mark.parametrize(
    "operation,left,right,expected",
    [
        ("ADD", TYPE_INT, TYPE_INT, TYPE_INT),
        ("SUBTRACT", TYPE_INT, TYPE_INT, TYPE_INT),
        ("MULTIPLY", TYPE_INT, TYPE_INT, TYPE_INT),
        ("FLOOR_DIVIDE", TYPE_INT, TYPE_INT, TYPE_INT),
        ("MODULO", TYPE_INT, TYPE_INT, TYPE_INT),
        ("DIVIDE", TYPE_INT, TYPE_INT, TYPE_FLOAT),
        ("POWER", TYPE_INT, TYPE_INT, TYPE_FLOAT),
        ("ADD", TYPE_INT, TYPE_FLOAT, TYPE_FLOAT),
        ("SUBTRACT", TYPE_FLOAT, TYPE_INT, TYPE_FLOAT),
        ("MULTIPLY", TYPE_FLOAT, TYPE_FLOAT, TYPE_FLOAT),
        ("FLOOR_DIVIDE", TYPE_FLOAT, TYPE_INT, TYPE_FLOAT),
        ("MODULO", TYPE_INT, TYPE_FLOAT, TYPE_FLOAT),
    ],
)
def test_numeric_result_matrix(operation, left, right, expected):
    assert resolve_numeric_binary(operation, left, right) is expected


def test_numeric_result_matrix_never_promotes_bool():
    assert resolve_numeric_binary("ADD", TYPE_BOOL, TYPE_INT) is None
    assert resolve_numeric_binary("ADD", TYPE_INT, TYPE_BOOL) is None


@pytest.mark.parametrize("value", [INT_MIN, -1, 0, 1, INT_MAX])
def test_int_domain_accepts_signed_32_bit_values(value):
    assert normalize_int_constant(value) == value


@pytest.mark.parametrize("value", [INT_MIN - 1, INT_MAX + 1, True, 1.0])
def test_int_domain_rejects_values_outside_exact_int32(value):
    with pytest.raises(CompileError, match="signed 32-bit range"):
        normalize_int_constant(value)


@pytest.mark.parametrize(
    "left,right,quotient,remainder",
    [
        (-3, 2, -2, 1),
        (3, -2, -2, -1),
        (-3, -2, 1, -1),
        (3, 2, 1, 1),
        (INT_MAX, -2, -1073741824, -1),
    ],
)
def test_int_floor_pair_matches_mathematical_floor_identity(left, right, quotient, remainder):
    q = evaluate_int_floor_divide(left, right)
    r = evaluate_int_floored_modulo(left, right)
    assert (q, r) == (quotient, remainder)
    # Deliberately unbounded host arithmetic: this checks the mathematical pair only.
    assert left == q * right + r


def test_int_floor_pair_zero_divisor_matches_integer_math():
    for left in (-1, 0, 1):
        assert evaluate_int_floor_divide(left, 0) == 0
        assert evaluate_int_floored_modulo(left, 0) == 0


def test_int_floor_pair_rejects_blender_sigfpe_pair_for_both_operations():
    message = "undefined for -2147483648 and -1"
    with pytest.raises(CompileError, match=message):
        evaluate_int_floor_divide(INT_MIN, -1)
    with pytest.raises(CompileError, match=message):
        evaluate_int_floored_modulo(INT_MIN, -1)


def test_int_source_reconstruction_still_obeys_intermediate_int32_overflow():
    q = evaluate_int_floor_divide(INT_MAX, -2)
    r = evaluate_int_floored_modulo(INT_MAX, -2)
    assert INT_MAX == q * -2 + r
    with pytest.raises(CompileError, match="signed 32-bit range"):
        normalize_int_constant(q * -2)


def test_int_source_reconstruction_is_valid_when_every_intermediate_fits_int32():
    assert _eval("(-3 // 2) * 2 + (-3 % 2)") == -3
    assert _eval("(3 // -2) * -2 + (3 % -2)") == 3


@pytest.mark.parametrize(
    "value,bits",
    [
        (0.1, 0x3DCCCCCD),
        (math.pi, 0x40490FDB),
        (math.tau, 0x40C90FDB),
        (math.e, 0x402DF854),
        (16777217.0, 0x4B800000),
    ],
)
def test_float_leaves_are_canonical_binary32(value, bits):
    assert _bits(normalize_float_constant(value)) == bits


def test_float_leaf_domain_rejects_nonfinite_and_too_large_values():
    for value in (math.inf, -math.inf, math.nan, FLOAT_MAX * 2.0):
        with pytest.raises(CompileError, match="finite and binary32-representable"):
            normalize_float_constant(value)


def test_float_basic_operations_apply_binary32_boundary_per_operation():
    left = normalize_float_constant(0.1)
    right = normalize_float_constant(0.2)
    assert evaluate_float_basic("ADD", left, right) == normalize_float_constant(left + right)
    assert evaluate_float_basic("SUBTRACT", left, right) == normalize_float_constant(left - right)
    assert evaluate_float_basic("MULTIPLY", left, right) == normalize_float_constant(left * right)


def test_float_basic_helper_is_deliberately_limited_to_three_operations():
    with pytest.raises(ValueError, match="ADD, SUBTRACT and MULTIPLY only"):
        evaluate_float_basic("DIVIDE", 1.0, 2.0)


def test_float_divide_matches_characterized_zero_and_regular_semantics():
    assert _bits(evaluate_float_divide(1.0, 0.0)) == 0x00000000
    assert _bits(evaluate_float_divide(-1.0, -0.0)) == 0x00000000
    assert evaluate_float_divide(1.0, 3.0) == normalize_float_constant(
        normalize_float_constant(1.0) / normalize_float_constant(3.0)
    )


def test_float_floor_divide_uses_divide_boundary_before_floor():
    assert evaluate_float_floor_divide(-3.0, 2.0) == -2.0
    assert _bits(evaluate_float_floor_divide(1.0, 0.0)) == 0x00000000


def test_float_floored_modulo_uses_characterized_per_operation_boundaries():
    result = evaluate_float_floored_modulo(932907.0625, -1185.3480224609375)
    assert result == -1147.1875
    assert _bits(result) == 0xC48F6600
    assert _bits(evaluate_float_floored_modulo(1.0, 0.0)) == 0x00000000


def test_float_unary_minus_mirrors_zero_subtract_signed_zero_behavior():
    assert _bits(evaluate_float_negate(+0.0)) == 0x00000000
    assert _bits(evaluate_float_negate(-0.0)) == 0x00000000
    assert evaluate_float_negate(2.5) == -2.5


def test_float_operation_overflow_is_ctfe_unavailable_not_leaf_error():
    expr = ast.parse("3e38 * 2.0", mode="eval").body
    with pytest.raises(ConstEvalUnavailable):
        _const_eval(expr, {})


def test_core_consteval_uses_numeric_semantics_scalar_contract():
    assert _eval("1 + 2") == 3
    assert type(_eval("1 + 2")) is int
    assert _eval("-3 // 2") == -2
    assert _eval("-3 % 2") == 1
    assert _eval("1 / 2") == normalize_float_constant(0.5)
    assert type(_eval("1 / 2")) is float


@pytest.mark.parametrize(
    "source",
    [
        "True + 1",
        "1 + True",
        "True + True",
        "True - False",
        "False * 2",
        "True / 3.0",
    ],
)
def test_bool_never_enters_numeric_consteval_through_python_fallback(source):
    with pytest.raises(CompileError, match="Numeric operation expects Int or Float operands"):
        _eval(source)


def test_power_is_runtime_typed_but_compile_time_unavailable():
    with pytest.raises(ConstEvalUnavailable, match="POWER"):
        _eval("2 ** 3")
    expr, analysis = _analyze("2 ** 3")
    assert _root_type(analysis) is TYPE_FLOAT
    program = lower_analyzed_expression(expr, analysis)
    operation = next(op for op in program.operations if isinstance(op, IRBinary))
    assert operation.op == "POWER"
    assert operation.result.typ is TYPE_FLOAT


def test_named_nodeforge_math_functions_remain_outside_core_consteval():
    for source in ("sin(0.5)", "cos(0.5)", "sqrt(4.0)", "pow(2.0, 3.0)"):
        with pytest.raises(ConstEvalUnavailable):
            _eval(source)


def test_len_and_sum_have_explicit_nodeforge_numeric_contract():
    length = _eval("len([1, 2, 3])")
    assert type(length) is int and length == 3

    int_sum = _eval("sum([1, 2, 3])")
    assert type(int_sum) is int and int_sum == 6

    float_sum = _eval("sum([0.1, 0.2])")
    expected = evaluate_float_basic("ADD", normalize_float_constant(0.1), normalize_float_constant(0.2))
    assert type(float_sum) is float
    assert _bits(float_sum) == _bits(expected)

    mixed_sum = _eval("sum([1, 2.0, 3])")
    assert type(mixed_sum) is float
    assert mixed_sum == _eval("((0 + 1) + 2.0) + 3")


def test_sum_rejects_bool_and_checks_int_overflow_at_each_reduction_step():
    with pytest.raises(CompileError, match="does not accept Bool"):
        _eval("sum([1, True])")
    with pytest.raises(CompileError, match="signed 32-bit range"):
        _eval(f"sum([{INT_MAX}, 1, -1])")


def test_compile_time_vector_division_mirrors_reciprocal_plus_scale_topology():
    value = _eval("vector(10000000000.0, 1.0, -1.0) / 3.0")
    assert isinstance(value, ConstVector)
    assert value[0] == 3333333504.0
    assert _bits(value[0]) == 0x4F46AEA2


def test_compile_time_vector_arithmetic_canonicalizes_each_component_operation():
    result = _eval("vector(0.1, 0.2, 0.3) + vector(0.2, 0.3, 0.4)")
    expected = tuple(
        evaluate_float_basic("ADD", left, right)
        for left, right in zip(
            map(normalize_float_constant, (0.1, 0.2, 0.3)),
            map(normalize_float_constant, (0.2, 0.3, 0.4)),
        )
    )
    assert tuple(map(_bits, result)) == tuple(map(_bits, expected))


def test_runtime_fold_does_not_expand_numeric_graph_substitution_and_canonicalizes_float_leaves():
    assert try_runtime_fold(ast.parse("1 + 2", mode="eval").body, {}) is NOT_FOLDABLE
    assert try_runtime_fold(ast.parse("1.0 < 2.0", mode="eval").body, {}) is NOT_FOLDABLE
    pi = try_runtime_fold(ast.parse("pi", mode="eval").body, {})
    literal = try_runtime_fold(ast.parse("0.1", mode="eval").body, {})
    assert _bits(pi) == 0x40490FDB
    assert _bits(literal) == 0x3DCCCCCD


@pytest.mark.parametrize(
    "source,typ",
    [
        ("1", TYPE_INT),
        ("1.0", TYPE_FLOAT),
        ("True", TYPE_BOOL),
        ("-1", TYPE_INT),
        ("-1.0", TYPE_FLOAT),
        ("index_value + 1", TYPE_INT),
        ("index_value / 1", TYPE_FLOAT),
        ("index_value // 2", TYPE_INT),
        ("index_value % 2", TYPE_INT),
        ("float_value + 1", TYPE_FLOAT),
    ],
)
def test_semantic_frontend_owns_direct_numeric_types(source, typ):
    bindings = {
        "index_value": TYPE_INT,
        "float_value": TYPE_FLOAT,
    }
    _, analysis = _analyze(source, bindings=bindings)
    assert _root_type(analysis) is typ


def test_exact_int_min_literal_is_lowered_without_materializing_positive_out_of_range_operand():
    expr, analysis = _analyze(str(INT_MIN))
    assert _root_type(analysis) is TYPE_INT
    program = lower_analyzed_expression(expr, analysis)
    literals = [operation for operation in program.operations if isinstance(operation, IRLiteral)]
    assert len(literals) == 1
    assert literals[0].value == INT_MIN
    assert literals[0].result.typ is TYPE_INT


@pytest.mark.parametrize("source", [str(INT_MAX + 1), str(INT_MIN - 1)])
def test_source_int_literals_outside_int32_are_rejected(source):
    with pytest.raises(CompileError, match="signed 32-bit range"):
        _analyze(source)


def test_detached_compile_time_float_and_int_keep_canonical_semantic_types():
    _, int_analysis = _analyze("v", consts={"v": 7})
    assert _root_type(int_analysis) is TYPE_INT

    _, float_analysis = _analyze("v", consts={"v": math.pi})
    assert _root_type(float_analysis) is TYPE_FLOAT
    fact = float_analysis.facts[float_analysis.root]
    assert _bits(fact.resolved_name.value.value) == 0x40490FDB


def test_compile_time_range_items_materialize_as_int_for_runtime_arithmetic():
    """Compile-time range unrolling publishes Int literals into permanent runtime expressions."""
    from NodeForge.semantic.builtin_calls import INPUT_DECLARATION_BUILTIN_NAMES, IR_CAPABLE_BUILTIN_NAMES
    from NodeForge.semantic.call_resolution import CallableEnvironment
    from NodeForge.semantic.residualization import _preprocess_compile_time
    from NodeForge.semantic.body import lower_basic_body
    from NodeForge.semantic.ir import IRAssign

    source = """
for i in range(3):
    x = index() + i
output(x)
"""
    statements = ast.parse(source, mode="exec").body
    preprocessed = _preprocess_compile_time(statements)
    result = lower_basic_body(
        list(preprocessed.statements),
        initial_runtime_bindings={},
        initial_compile_time=preprocessed.initial_compile_time,
        reserved_name_labels={},
        callable_environment=CallableEnvironment(
            frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES),
            {},
            {},
            {},
        ),
        owner_scope="scope",
        compile_time_effects_before=preprocessed.effects_before,
        trailing_compile_time_effects=preprocessed.trailing_effects,
    )

    assignments = [statement for statement in result.body.statements if isinstance(statement, IRAssign)]
    assert len(assignments) == 3
    for expected, statement in enumerate(assignments):
        literals = [op for op in statement.value.operations if isinstance(op, IRLiteral)]
        binaries = [op for op in statement.value.operations if isinstance(op, IRBinary)]
        assert [(literal.value, literal.result.typ) for literal in literals] == [(expected, TYPE_INT)]
        assert len(binaries) == 1
        assert binaries[0].op == "ADD"
        assert binaries[0].left.typ is TYPE_INT
        assert binaries[0].right.typ is TYPE_INT
        assert binaries[0].result.typ is TYPE_INT


def test_backend_numeric_lowering_selects_typed_node_families(monkeypatch):
    """Blender lowering realizes typed numeric IR without re-deciding semantic result types."""
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.semantic.ir import IRValue
    from NodeForge.values import Value

    calls = []

    def fake_integer_math(group, operation, args, x=0, y=0):
        calls.append(("int", operation, tuple(value.typ for value in args)))
        return Value(object(), TYPE_INT)

    def fake_math(group, operation, args, x=0, y=0):
        calls.append(("float", operation, tuple(value.typ for value in args)))
        return Value(object(), TYPE_FLOAT)

    monkeypatch.setattr(backend, "_integer_math", fake_integer_math)
    monkeypatch.setattr(backend, "_math", fake_math)

    class Context:
        group = object()

    context = Context()

    def lower(operation):
        materialized = {
            operation.left.id: Value(object(), operation.left.typ),
            operation.right.id: Value(object(), operation.right.typ),
        }
        backend._lower_binary(context, operation, materialized, 0, 0)
        return materialized[operation.result.id]

    def binary(op, left_type, right_type, result_type):
        return IRBinary(
            IRValue(2, result_type),
            0,
            op,
            IRValue(0, left_type),
            IRValue(1, right_type),
        )

    assert lower(binary("ADD", TYPE_INT, TYPE_INT, TYPE_INT)).typ is TYPE_INT
    assert calls.pop()[:2] == ("int", "ADD")

    assert lower(binary("MODULO", TYPE_INT, TYPE_INT, TYPE_INT)).typ is TYPE_INT
    assert calls.pop()[:2] == ("int", "FLOORED_MODULO")

    assert lower(binary("FLOOR_DIVIDE", TYPE_INT, TYPE_INT, TYPE_INT)).typ is TYPE_INT
    assert calls.pop()[:2] == ("int", "DIVIDE_FLOOR")

    assert lower(binary("MODULO", TYPE_FLOAT, TYPE_FLOAT, TYPE_FLOAT)).typ is TYPE_FLOAT
    assert calls.pop()[:2] == ("float", "FLOORED_MODULO")

    result = lower(binary("FLOOR_DIVIDE", TYPE_FLOAT, TYPE_INT, TYPE_FLOAT))
    assert result.typ is TYPE_FLOAT
    assert calls == [
        ("float", "DIVIDE", (TYPE_FLOAT, TYPE_INT)),
        ("float", "FLOOR", (TYPE_FLOAT,)),
    ]


def test_backend_compare_uses_int_or_float_contract_and_zero_float_epsilon():
    """Compare realization selects INT only for Int/Int and fixes Float equality epsilon to zero."""
    from NodeForge.nodes import _compare
    from NodeForge.values import Value

    class Socket:
        def __init__(self, name=""):
            self.name = name
            self.default_value = None

    class SocketList(list):
        def __getitem__(self, key):
            if isinstance(key, str):
                for socket in self:
                    if socket.name == key:
                        return socket
                raise KeyError(key)
            return super().__getitem__(key)

    class Node:
        def __init__(self):
            self.bl_idname = "FunctionNodeCompare"
            self.location = (0, 0)
            self.operation = None
            self.data_type = None
            self.inputs = SocketList([Socket("A"), Socket("B"), Socket("Epsilon")])
            self.outputs = SocketList([Socket("Result")])

    class Nodes(list):
        def new(self, bl_idname):
            assert bl_idname == "FunctionNodeCompare"
            node = Node()
            self.append(node)
            return node

    class Links(list):
        def new(self, from_socket, to_socket):
            self.append((from_socket, to_socket))

    class Group:
        def __init__(self):
            self.nodes = Nodes()
            self.links = Links()

    int_group = Group()
    result = _compare(
        int_group,
        "EQUAL",
        Value(Socket(), TYPE_INT),
        Value(Socket(), TYPE_INT),
    )
    assert result.typ is TYPE_BOOL
    assert int_group.nodes[0].data_type == "INT"

    float_group = Group()
    result = _compare(
        float_group,
        "EQUAL",
        Value(Socket(), TYPE_INT),
        Value(Socket(), TYPE_FLOAT),
    )
    assert result.typ is TYPE_BOOL
    assert float_group.nodes[0].data_type == "FLOAT"
    assert float_group.nodes[0].inputs[2].default_value == 0.0


def test_float_subnormal_and_float_max_are_valid_language_leaves():
    assert _bits(normalize_float_constant(1e-45)) == 0x00000001
    assert normalize_float_constant(FLOAT_MAX) == FLOAT_MAX


def test_float_multistep_ctfe_rounds_at_each_operation_boundary():
    # With one host-float64 calculation followed by one final f32 this would be 2.0.
    result = _eval("(16777217.0 + 1.0) - 16777216.0")
    assert result == 0.0
    assert _bits(result) == 0x00000000


def test_float_and_mixed_comparison_ctfe_uses_canonical_operands():
    assert _eval("16777217.0 == 16777216.0") is True
    assert _eval("16777217 == 16777216.0") is True
    assert _eval("1.0 < 2") is True


def test_int_floor_pair_edge_rules_are_visible_through_consteval():
    assert _eval("1 // 0") == 0
    assert _eval("-1 % 0") == 0
    with pytest.raises(CompileError, match="undefined for -2147483648 and -1"):
        _eval(f"{INT_MIN} // -1")
    with pytest.raises(CompileError, match="undefined for -2147483648 and -1"):
        _eval(f"{INT_MIN} % -1")


def test_float_runtime_expression_remains_semantically_valid_when_ctfe_overflows():
    with pytest.raises(ConstEvalUnavailable):
        _eval("3e38 * 2.0")
    _, analysis = _analyze("3e38 * 2.0")
    assert _root_type(analysis) is TYPE_FLOAT


def test_empty_len_and_sum_results_are_explicit_int_values():
    assert type(_eval("len([])")) is int and _eval("len([])") == 0
    assert type(_eval("sum([])")) is int and _eval("sum([])") == 0


def test_runtime_fold_keeps_all_required_numeric_roots_fail_closed():
    for source in ("1 + 2", "7 // 2", "-3 % 2", "1.0 + 2.0", "1.0 < 2.0"):
        assert try_runtime_fold(ast.parse(source, mode="eval").body, {}) is NOT_FOLDABLE


def test_input_defaults_keep_consumer_specific_numeric_conversion_rules():
    from NodeForge.semantic.builtin_calls import analyze_input_declaration_call

    def analyze(source):
        return analyze_input_declaration_call(ast.parse(source, mode="eval").body, {})

    float_input = analyze('input_float("X", default=1)')
    assert float_input.typ is TYPE_FLOAT
    assert type(float_input.default) is float and float_input.default == 1.0

    int_input = analyze('input_int("X", default=1.9)')
    assert int_input.typ is TYPE_INT
    assert type(int_input.default) is int and int_input.default == 1

    vector_input = analyze('input_vector("X", default=(1, 2.0, 3))')
    assert vector_input.typ is TYPE_VECTOR
    assert tuple(map(_bits, vector_input.default)) == tuple(
        map(_bits, map(normalize_float_constant, (1, 2.0, 3)))
    )

    with pytest.raises(CompileError):
        analyze('input_float("X", default=True)')
    with pytest.raises(CompileError):
        analyze('input_int("X", default=True)')
    with pytest.raises(CompileError):
        analyze('input_vector("X", default=(1, True, 3))')
    with pytest.raises(CompileError, match="Numeric operation expects Int or Float operands"):
        analyze('input_int("X", default=True + True)')
    with pytest.raises(CompileError, match="Numeric operation expects Int or Float operands"):
        analyze('input_float("X", default=True + 1)')


class _FakeSocket:
    """Minimal socket used to exercise the real Blender-lowering helpers without bpy."""

    def __init__(self, name=""):
        self.name = name
        self.default_value = None


class _FakeSockets(list):
    """List-like socket collection with Blender-style string lookup."""

    def __getitem__(self, key):
        if isinstance(key, str):
            for socket in self:
                if socket.name == key:
                    return socket
            raise KeyError(key)
        return super().__getitem__(key)


class _FakeNode:
    """Small mutable node record sufficient for numeric backend contract tests."""

    def __init__(self, bl_idname):
        self.bl_idname = bl_idname
        self.location = (0, 0)
        self.label = ""
        self.integer = 0
        self.operation = None
        self.data_type = None
        if bl_idname == "FunctionNodeCompare":
            names = ["A", "B", "Epsilon", "", "", "", "", ""]
            self.inputs = _FakeSockets([_FakeSocket(name) for name in names])
        else:
            self.inputs = _FakeSockets([_FakeSocket() for _ in range(8)])
        self.outputs = _FakeSockets([_FakeSocket() for _ in range(4)])


class _FakeNodes(list):
    """Node collection implementing ``nodes.new`` for numeric backend tests."""

    def new(self, bl_idname):
        node = _FakeNode(bl_idname)
        self.append(node)
        return node


class _FakeLinks(list):
    """Link collection recording backend connections."""

    def new(self, source, target):
        self.append((source, target))
        return self[-1]


class _FakeGroup:
    """Minimal Geometry Nodes group substrate for pure backend selection tests."""

    def __init__(self):
        self.nodes = _FakeNodes()
        self.links = _FakeLinks()


def _materialize_numeric_source(source):
    """Analyze, lower and realize one literal-only numeric source expression."""
    from NodeForge.blender_ir_lowering import BlenderIRLoweringContext, lower_expression

    expr, analysis = _analyze(source)
    program = lower_analyzed_expression(expr, analysis)
    group = _FakeGroup()
    result = lower_expression(BlenderIRLoweringContext(group, MappingProxyType({})), program)
    return group, result


@pytest.mark.parametrize(
    "source,node_type,operation",
    [
        ("1 + 2", "FunctionNodeIntegerMath", "ADD"),
        ("7 % 3", "FunctionNodeIntegerMath", "FLOORED_MODULO"),
        ("7 // 3", "FunctionNodeIntegerMath", "DIVIDE_FLOOR"),
        ("7.0 % 3.0", "ShaderNodeMath", "FLOORED_MODULO"),
    ],
)
def test_backend_uses_typed_numeric_node_family(source, node_type, operation):
    group, _ = _materialize_numeric_source(source)
    assert any(node.bl_idname == node_type and node.operation == operation for node in group.nodes)


def test_backend_float_floor_divide_is_divide_then_floor():
    group, result = _materialize_numeric_source("7.0 // 3.0")
    math_ops = [node.operation for node in group.nodes if node.bl_idname == "ShaderNodeMath"]
    assert math_ops[-2:] == ["DIVIDE", "FLOOR"]
    assert result.typ is TYPE_FLOAT


def test_backend_compare_uses_int_or_float_data_type_and_explicit_zero_epsilon():
    int_group, _ = _materialize_numeric_source("1 == 2")
    int_compare = next(node for node in int_group.nodes if node.bl_idname == "FunctionNodeCompare")
    assert int_compare.data_type == "INT"

    float_group, _ = _materialize_numeric_source("1 == 2.0")
    float_compare = next(node for node in float_group.nodes if node.bl_idname == "FunctionNodeCompare")
    assert float_compare.data_type == "FLOAT"
    epsilon = next(socket for socket in float_compare.inputs if socket.name == "Epsilon")
    assert epsilon.default_value == 0.0


def _body_callables():
    """Return the permanent builtin callable environment used by body numeric tests."""
    from NodeForge.semantic.builtin_calls import INPUT_DECLARATION_BUILTIN_NAMES, IR_CAPABLE_BUILTIN_NAMES

    return CallableEnvironment(
        frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES),
        {},
        {},
        {},
    )


def _lower_preprocessed_body(source):
    """Run source through real preprocessing and permanent Semantic Body lowering."""
    from NodeForge.semantic.residualization import _preprocess_compile_time
    from NodeForge.semantic.body import lower_basic_body

    statements = ast.parse(source, mode="exec").body
    preprocessed = _preprocess_compile_time(statements)
    return lower_basic_body(
        list(preprocessed.statements),
        initial_runtime_bindings={},
        initial_compile_time=preprocessed.initial_compile_time,
        reserved_name_labels={},
        callable_environment=_body_callables(),
        owner_scope="numeric_semantics-body",
        compile_time_effects_before=preprocessed.effects_before,
        trailing_compile_time_effects=preprocessed.trailing_effects,
    )


@pytest.mark.parametrize(
    "expression",
    [
        "2147483647 + 1",
        "-2147483648 - 1",
        "50000 * 50000",
        "-(-2147483648)",
        "-2147483648 // -1",
        "-2147483648 % -1",
    ],
)
def test_direct_output_rejects_statically_known_int_domain_errors(expression):
    """Direct runtime consumers cannot bypass hard signed-32 semantic validation."""
    with pytest.raises(CompileError):
        _lower_preprocessed_body(f'output("X", {expression})\n')


def test_direct_output_known_valid_int_expression_remains_runtime_add():
    """Hard-domain validation does not turn a valid known expression into a fold."""
    from NodeForge.semantic.ir import IRBinary, IROutput

    result = _lower_preprocessed_body('output("X", 1 + 2)\n')
    output = next(statement for statement in result.body.statements if isinstance(statement, IROutput))
    binaries = [op for op in output.value.operations if isinstance(op, IRBinary)]
    assert len(binaries) == 1
    assert binaries[0].op == "ADD"
    assert binaries[0].result.typ is TYPE_INT


def test_range_loop_items_materialize_as_int_and_feed_int_add():
    from NodeForge.semantic.ir import IRAssign

    result = _lower_preprocessed_body(
        "for i in range(3):\n"
        "    x = index() + i\n"
        "output(x)\n"
    )
    assigns = [statement for statement in result.body.statements if isinstance(statement, IRAssign)]
    assert len(assigns) == 3
    for expected, statement in enumerate(assigns):
        literal = next(op for op in statement.value.operations if isinstance(op, IRLiteral))
        binary = next(op for op in statement.value.operations if isinstance(op, IRBinary))
        assert literal.value == expected
        assert literal.result.typ is TYPE_INT
        assert binary.op == "ADD" and binary.result.typ is TYPE_INT

    from NodeForge.blender_ir_lowering import BlenderIRLoweringContext, lower_expression
    group = _FakeGroup()
    lowered = lower_expression(
        BlenderIRLoweringContext(group, MappingProxyType({})),
        assigns[0].value,
    )
    assert lowered.typ is TYPE_INT
    assert any(
        node.bl_idname == "FunctionNodeIntegerMath" and node.operation == "ADD"
        for node in group.nodes
    )


def test_len_and_sum_results_feed_permanent_int_runtime_arithmetic():
    from NodeForge.semantic.ir import IRAssign

    result = _lower_preprocessed_body(
        "items = [1, 2, 3]\n"
        "n = len(items)\n"
        "x = index() + n\n"
        "y = sum([1, 2, 3])\n"
        "z = index() + y\n"
        "output(z)\n"
    )
    assigns = [statement for statement in result.body.statements if isinstance(statement, IRAssign)]
    numeric_adds = [
        op
        for statement in assigns
        for op in statement.value.operations
        if isinstance(op, IRBinary) and op.op == "ADD"
    ]
    assert len(numeric_adds) >= 2
    assert all(op.result.typ is TYPE_INT for op in numeric_adds[-2:])


def test_input_float_runtime_value_plus_int_literal_remains_float():
    from NodeForge.semantic.ir import IRAssign

    result = _lower_preprocessed_body(
        'value = input_float("Value", default=1)\n'
        "x = value + 1\n"
        "output(x)\n"
    )
    x_assign = next(
        statement
        for statement in result.body.statements
        if isinstance(statement, IRAssign) and statement.source_name == "x"
    )
    binary = next(op for op in x_assign.value.operations if isinstance(op, IRBinary))
    assert binary.result.typ is TYPE_FLOAT


def test_runtime_if_remains_runtime_and_merges_int_state():
    from NodeForge.semantic.ir import IRAssign, IRIf

    result = _lower_preprocessed_body(
        'condition = input_bool("C")\n'
        "n = 2\n"
        "if condition:\n"
        "    n = 3\n"
        "else:\n"
        "    n = 4\n"
        "x = index() + n\n"
        "output(x)\n"
    )
    assert any(isinstance(statement, IRIf) for statement in result.body.statements)
    x_assign = next(
        statement
        for statement in result.body.statements
        if isinstance(statement, IRAssign) and statement.source_name == "x"
    )
    binary = next(op for op in x_assign.value.operations if isinstance(op, IRBinary))
    assert binary.result.typ is TYPE_INT


def test_repeat_repeat_exact_type_historical_type_split_and_marker_are_removed():
    from pathlib import Path
    import NodeForge

    root = Path(NodeForge.__file__).resolve().parent
    control_flow_source = (root / "semantic/control_flow.py").read_text()
    ir_source = (root / "semantic/ir.py").read_text()
    backend_source = (root / "blender_ir_lowering.py").read_text()
    combined = "\n".join((control_flow_source, ir_source, backend_source))
    assert "repeat_state_output_type" not in combined
    assert "Type-directed numeric semantics now keeps ordinary Int" not in combined
    assert "false_coerce_to" not in combined
    assert "true_coerce_to" not in combined
    assert "_coerce_branch_value" not in combined


def test_numeric_semantics_does_not_reintroduce_legacy_numeric_parity_or_residualization_numeric_todo():
    from pathlib import Path
    import NodeForge

    root = Path(NodeForge.__file__).resolve().parent
    compiler_source = (root / "compiler.py").read_text()
    consteval_source = (root / "semantic" / "consteval.py").read_text()
    assert "numeric_semantics" not in compiler_source
    assert "TODO(nodeforge-compat):" not in "\n".join(
        line for line in consteval_source.splitlines() if "numeric" in line.lower()
    )
    assert "TODO(nodeforge-migration): Keep numeric runtime expressions" not in consteval_source


def _materialize_bound_numeric_source(source, bindings):
    """Realize one typed expression whose operands come from fake runtime bindings."""
    from NodeForge.blender_ir_lowering import BlenderIRLoweringContext, lower_expression
    from NodeForge.values import Value

    expr, analysis = _analyze(source, bindings=bindings)
    program = lower_analyzed_expression(expr, analysis)
    group = _FakeGroup()
    runtime_bindings = MappingProxyType({
        BindingId("numeric_semantics", index): Value(_FakeSocket(), typ)
        for index, (_, typ) in enumerate(bindings.items())
    })
    result = lower_expression(BlenderIRLoweringContext(group, runtime_bindings), program)
    return group, result


@pytest.mark.parametrize(
    "source,bindings",
    [
        ("v * i", {"v": TYPE_VECTOR, "i": TYPE_INT}),
        ("i * v", {"i": TYPE_INT, "v": TYPE_VECTOR}),
        ("v / i", {"v": TYPE_VECTOR, "i": TYPE_INT}),
        ("v * f", {"v": TYPE_VECTOR, "f": TYPE_FLOAT}),
        ("f * v", {"f": TYPE_FLOAT, "v": TYPE_VECTOR}),
        ("v / f", {"v": TYPE_VECTOR, "f": TYPE_FLOAT}),
    ],
)
def test_vector_scalar_runtime_forms_accept_int_and_float_without_new_vector_operators(source, bindings):
    group, result = _materialize_bound_numeric_source(source, bindings)
    assert result.typ is TYPE_VECTOR
    vector_math = [node for node in group.nodes if node.bl_idname == "ShaderNodeVectorMath"]
    assert vector_math and vector_math[-1].operation == "SCALE"


def test_vector_divide_runtime_keeps_reciprocal_then_scale_topology():
    group, result = _materialize_bound_numeric_source(
        "v / i", {"v": TYPE_VECTOR, "i": TYPE_INT}
    )
    math_ops = [node.operation for node in group.nodes if node.bl_idname == "ShaderNodeMath"]
    vector_ops = [node.operation for node in group.nodes if node.bl_idname == "ShaderNodeVectorMath"]
    assert math_ops == ["DIVIDE"]
    assert vector_ops == ["SCALE"]
    assert result.typ is TYPE_VECTOR


def test_unrelated_new_vector_operators_remain_rejected():
    with pytest.raises(CompileError):
        _analyze("v // i", bindings={"v": TYPE_VECTOR, "i": TYPE_INT})
    with pytest.raises(CompileError):
        _analyze("v % i", bindings={"v": TYPE_VECTOR, "i": TYPE_INT})


def test_backend_unary_minus_selects_integer_math_only_for_int():
    int_group, int_result = _materialize_numeric_source("-1")
    assert any(
        node.bl_idname == "FunctionNodeIntegerMath" and node.operation == "NEGATE"
        for node in int_group.nodes
    )
    assert int_result.typ is TYPE_INT

    float_group, float_result = _materialize_numeric_source("-1.0")
    assert any(
        node.bl_idname == "ShaderNodeMath" and node.operation == "SUBTRACT"
        for node in float_group.nodes
    )
    assert float_result.typ is TYPE_FLOAT

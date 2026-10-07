"""Pure semantic contracts for compiler-owned builtin calls before Blender lowering."""

from __future__ import annotations

import ast

import pytest

from NodeForge.semantic.builtin_calls import (
    INPUT_DECLARATION_BUILTIN_NAMES,
    IR_CAPABLE_BUILTIN_NAMES,
    analyze_builtin_call,
    analyze_input_declaration_call,
)
from NodeForge.semantic.builtin_registry import BUILTIN_NAMES, CALLABLE_BUILTIN_NAMES
from NodeForge.semantic.call_resolution import (
    ContextReadCallResult, NamedOutputsCallResult, ProjectedCallResult, RuntimeCallResult, TupleCallResult,
)
from NodeForge.semantic.constants import (
    TYPE_BOOL, TYPE_BUNDLE, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_MATERIAL,
    TYPE_OBJECT, TYPE_ROTATION, TYPE_STRING, TYPE_VECTOR,
)
from NodeForge.errors import CompileError
from NodeForge.semantic.group_context import GroupContextSlot


pytestmark = pytest.mark.unit


def _call(source):
    """Parse one builtin call fixture."""
    return ast.parse(source, mode="eval").body


def _analyze(name, source, types=None, consts=None):
    """Analyze a builtin while supplying deterministic runtime operand types."""
    types = dict(types or {})

    def add_runtime(node, parameter_name, context):
        key = ast.unparse(node)
        try:
            return types[key]
        except KeyError as exc:
            raise AssertionError(f"missing runtime type for {key!r} in {context}") from exc

    return analyze_builtin_call(name, _call(source), consts or {}, add_runtime)


def test_every_callable_builtin_is_explicitly_ir_capable_or_input_declaration():
    assert IR_CAPABLE_BUILTIN_NAMES.isdisjoint(INPUT_DECLARATION_BUILTIN_NAMES)
    assert IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES == frozenset(CALLABLE_BUILTIN_NAMES)
    assert {"grid", "grid_uv", "sample_index"} <= IR_CAPABLE_BUILTIN_NAMES
    assert "sample_index" in CALLABLE_BUILTIN_NAMES
    assert "sample_index" in BUILTIN_NAMES
    assert {"input_float", "input_bundle"} <= INPUT_DECLARATION_BUILTIN_NAMES


def test_unknown_name_does_not_become_a_core_builtin_when_sample_index_is_registered():
    """Sample Index extends the existing inventory without widening builtin resolution."""
    unknown = "definitely_not_a_nodeforge_builtin"
    assert unknown not in IR_CAPABLE_BUILTIN_NAMES
    assert unknown not in CALLABLE_BUILTIN_NAMES
    assert unknown not in BUILTIN_NAMES
    with pytest.raises(KeyError, match=unknown):
        analyze_builtin_call(unknown, _call(f"{unknown}()"), {}, lambda *_args: TYPE_FLOAT)


def test_sample_index_static_index_normalizes_options_before_runtime_operands():
    """A compile-time Int index stays a static option and preserves value type."""
    analyzed = _analyze(
        "sample_index",
        'sample_index(geo, value, 3, domain="face", clamp=True)',
        types={"geo": TYPE_GEOMETRY, "value": TYPE_VECTOR},
    )
    assert tuple((operand.parameter_name, operand.typ) for operand in analyzed.operands) == (
        ("geometry", TYPE_GEOMETRY),
        ("value", TYPE_VECTOR),
    )
    assert dict(analyzed.options) == {
        "index": ("const", 3),
        "domain": "FACE",
        "clamp": True,
    }
    assert isinstance(analyzed.result, RuntimeCallResult)
    assert analyzed.result.typ is TYPE_VECTOR


def test_sample_index_runtime_index_is_third_runtime_operand():
    """Only CTFE unavailability selects a typed runtime Int index."""
    analyzed = _analyze(
        "sample_index",
        "sample_index(geo, value, i)",
        types={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT, "i": TYPE_INT},
    )
    assert tuple((operand.parameter_name, operand.typ) for operand in analyzed.operands) == (
        ("geometry", TYPE_GEOMETRY),
        ("value", TYPE_FLOAT),
        ("index", TYPE_INT),
    )
    assert dict(analyzed.options) == {
        "index": ("runtime", 2),
        "domain": "POINT",
        "clamp": False,
    }
    assert analyzed.result.typ is TYPE_FLOAT


@pytest.mark.parametrize("value_type", [TYPE_FLOAT, TYPE_INT, TYPE_BOOL, TYPE_VECTOR])
def test_sample_index_preserves_each_supported_sampled_value_type(value_type):
    """The semantic result type is exactly the sampled field type."""
    analyzed = _analyze(
        "sample_index",
        "sample_index(geo, value, 0)",
        types={"geo": TYPE_GEOMETRY, "value": value_type},
    )
    assert analyzed.result.typ is value_type


@pytest.mark.parametrize(
    "value_type",
    [TYPE_MATERIAL, TYPE_OBJECT, TYPE_STRING, TYPE_BUNDLE, TYPE_GEOMETRY, TYPE_ROTATION],
)
def test_sample_index_rejects_unsupported_sampled_value_types_before_backend(value_type):
    """Sample Index does not widen Sample Index beyond the core attribute-field vocabulary."""
    with pytest.raises(CompileError, match="value supports Float, Int, Bool and Vector"):
        _analyze(
            "sample_index",
            "sample_index(geo, value, 0)",
            types={"geo": TYPE_GEOMETRY, "value": value_type},
        )


@pytest.mark.parametrize("source", ["True", "3.0", '"3"'])
def test_sample_index_rejects_non_int_compile_time_indices(source):
    """Compile-time index acceptance uses exact Int identity rather than coercion."""
    with pytest.raises(CompileError, match=r"sample_index\(\) index must be Int"):
        _analyze(
            "sample_index",
            f"sample_index(geo, value, {source})",
            types={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT},
        )


@pytest.mark.parametrize("index", [-2147483648, -1, 0, 2147483647])
def test_sample_index_accepts_signed32_compile_time_index_boundaries(index):
    """Static Sample Index shares the canonical NodeForge signed-32 Int domain."""
    analyzed = _analyze(
        "sample_index",
        f"sample_index(geo, value, {index})",
        types={"geo": TYPE_GEOMETRY, "value": TYPE_INT},
    )
    assert dict(analyzed.options)["index"] == ("const", index)


@pytest.mark.parametrize("index", [-2147483649, 2147483648])
def test_sample_index_rejects_compile_time_index_outside_signed32_domain(index):
    """Global Int validation rejects out-of-range indices before runtime analysis."""
    calls = []

    def add_runtime(node, parameter_name, context):
        calls.append((parameter_name, context))
        return TYPE_GEOMETRY

    with pytest.raises(CompileError, match="signed 32-bit range"):
        analyze_builtin_call(
            "sample_index",
            _call(f"sample_index(geo, value, {index})"),
            {},
            add_runtime,
        )
    assert calls == []


@pytest.mark.parametrize("index_type", [TYPE_FLOAT, TYPE_BOOL, TYPE_VECTOR])
def test_sample_index_rejects_non_int_runtime_index(index_type):
    """Runtime representation availability never weakens the Int type contract."""
    with pytest.raises(CompileError, match=r"sample_index\(\) index must be Int"):
        _analyze(
            "sample_index",
            "sample_index(geo, value, i)",
            types={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT, "i": index_type},
        )


@pytest.mark.parametrize("domain", ["POINT", "edge", "Face", "corner", "CURVE", "instance"])
def test_sample_index_accepts_and_canonicalizes_all_current_domains(domain):
    """Sample Index consumes the shared six-domain NodeForge vocabulary."""
    analyzed = _analyze(
        "sample_index",
        f'sample_index(geo, value, 0, domain="{domain}")',
        types={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT},
    )
    assert dict(analyzed.options)["domain"] == domain.upper()


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ('sample_index(geo, value, 0, domain="")', "domain= must be a non-empty compile-time string"),
        ('sample_index(geo, value, 0, domain="LAYER")', "Unsupported sample_index\\(\\) domain"),
        ("sample_index(geo, value, 0, domain=1)", "domain= must be a compile-time string"),
        ("sample_index(geo, value, 0, domain=runtime_domain)", "domain= must be a compile-time string"),
    ],
)
def test_sample_index_rejects_invalid_or_runtime_domain_before_runtime_operands(source, message):
    """Static domain failures do not acquire geometry/value runtime operands."""
    calls = []

    def add_runtime(node, parameter_name, context):
        calls.append((parameter_name, context))
        return TYPE_STRING

    with pytest.raises(CompileError, match=message):
        analyze_builtin_call("sample_index", _call(source), {}, add_runtime)
    assert calls == []


@pytest.mark.parametrize("clamp", ["True", "False"])
def test_sample_index_accepts_compile_time_bool_clamp(clamp):
    """Clamp is normalized to an explicit exact Bool option."""
    analyzed = _analyze(
        "sample_index",
        f"sample_index(geo, value, 0, clamp={clamp})",
        types={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT},
    )
    assert dict(analyzed.options)["clamp"] is (clamp == "True")


@pytest.mark.parametrize("source", ["0", "1", "1.0", '"true"', "runtime_clamp"])
def test_sample_index_rejects_non_bool_or_runtime_clamp_before_runtime_operands(source):
    """Clamp is compile-time-only and does not use Python truthiness coercion."""
    calls = []

    def add_runtime(node, parameter_name, context):
        calls.append((parameter_name, context))
        return TYPE_BOOL

    with pytest.raises(CompileError, match="clamp= must be a compile-time Bool"):
        analyze_builtin_call(
            "sample_index",
            _call(f"sample_index(geo, value, 0, clamp={source})"),
            {},
            add_runtime,
        )
    assert calls == []


def test_sample_index_static_prefix_precedes_all_runtime_operand_analysis():
    """A deterministic static error cannot trigger arbitrary runtime operand traversal."""
    calls = []

    def add_runtime(node, parameter_name, context):
        calls.append((ast.unparse(node), parameter_name, context))
        raise AssertionError("runtime analysis must not run before static validation")

    with pytest.raises(CompileError, match="clamp= must be a compile-time Bool"):
        analyze_builtin_call(
            "sample_index",
            _call("sample_index(extension_geometry(), value, i, clamp=1)"),
            {},
            add_runtime,
        )
    assert calls == []


def test_sample_index_hard_ctfe_index_error_propagates_without_runtime_fallback():
    """A proven semantic error is not reinterpreted as runtime-index unavailability."""
    calls = []

    def add_runtime(node, parameter_name, context):
        calls.append((ast.unparse(node), parameter_name, context))
        return TYPE_INT

    with pytest.raises(CompileError, match="not expects Bool"):
        analyze_builtin_call(
            "sample_index",
            _call("sample_index(geo, value, not 1)"),
            {},
            add_runtime,
        )
    assert calls == []


def test_sample_index_runtime_index_analysis_occurs_after_geometry_and_value():
    """Once static validation succeeds, runtime acquisition follows the documented order."""
    calls = []
    types = {"geo": TYPE_GEOMETRY, "value": TYPE_BOOL, "i": TYPE_INT}

    def add_runtime(node, parameter_name, context):
        calls.append(parameter_name)
        return types[ast.unparse(node)]

    analyzed = analyze_builtin_call(
        "sample_index",
        _call("sample_index(geo, value, i)"),
        {},
        add_runtime,
    )
    assert calls == ["geometry", "value", "index"]
    assert dict(analyzed.options)["index"] == ("runtime", 2)


def test_sample_index_wrong_geometry_type_is_rejected_at_first_runtime_boundary():
    """Geometry type validation stops before sampled value/index runtime acquisition."""
    calls = []
    types = {"geo": TYPE_FLOAT, "value": TYPE_FLOAT, "i": TYPE_INT}

    def add_runtime(node, parameter_name, context):
        calls.append(parameter_name)
        return types[ast.unparse(node)]

    with pytest.raises(CompileError, match="first argument must be Geometry"):
        analyze_builtin_call("sample_index", _call("sample_index(geo, value, i)"), {}, add_runtime)
    assert calls == ["geometry"]


def test_sample_index_shape_and_keyword_errors_use_existing_call_contracts():
    """Arity and keyword syntax fail before feature runtime semantics."""
    for source in (
        "sample_index()",
        "sample_index(geo)",
        "sample_index(geo, value)",
        "sample_index(geo, value, 0, extra)",
    ):
        with pytest.raises(CompileError, match="expects 3 positional arguments"):
            _analyze("sample_index", source)
    with pytest.raises(CompileError, match="Unsupported keyword argument"):
        _analyze("sample_index", "sample_index(geo, value, 0, foo=True)")
    with pytest.raises(CompileError, match="Duplicate keyword argument: clamp"):
        _analyze("sample_index", "sample_index(geo, value, 0, clamp=True, clamp=False)")
    with pytest.raises(CompileError, match=r"\*\*kwargs are not supported"):
        _analyze("sample_index", "sample_index(geo, value, 0, **opts)")


def test_store_and_capture_domains_are_normalized_in_frontend_options():
    """Store/Capture publish canonical domains before Blender lowering."""
    store = _analyze(
        "store_named_attribute",
        'store_named_attribute(geo, "name", value, domain="face")',
        types={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT},
    )
    capture = _analyze(
        "capture_attribute",
        'capture_attribute(geo, value, domain="curve")',
        types={"geo": TYPE_GEOMETRY, "value": TYPE_VECTOR},
    )
    assert dict(store.options)["domain"] == "FACE"
    assert dict(capture.options)["domain"] == "CURVE"


@pytest.mark.parametrize("name", ["store_named_attribute", "capture_attribute"])
def test_store_and_capture_reject_invalid_domains_before_backend(name):
    """The shared frontend domain authority catches unsupported values before IR/backend."""
    source = (
        'store_named_attribute(geo, "name", value, domain="LAYER")'
        if name == "store_named_attribute"
        else 'capture_attribute(geo, value, domain="LAYER")'
    )
    with pytest.raises(CompileError, match="Unsupported .* domain"):
        _analyze(name, source, types={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT})


def test_frontend_owned_builtin_defaults_are_explicit_in_normalized_options():
    """Audited builtin defaults/omissions are explicit before backend lowering."""
    store = _analyze(
        "store_named_attribute",
        'store_named_attribute(geo, "name", value)',
        types={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT},
    )
    capture = _analyze(
        "capture_attribute",
        "capture_attribute(geo, value)",
        types={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT},
    )
    transform = _analyze("transform", "transform(geo)", types={"geo": TYPE_GEOMETRY})
    instances = _analyze(
        "instance_on_points",
        "instance_on_points(instance, points)",
        types={"instance": TYPE_GEOMETRY, "points": TYPE_GEOMETRY},
    )
    cube = _analyze("cube", "cube()")

    assert {"domain": "POINT", "data_type": None}.items() <= dict(store.options).items()
    assert {"domain": "POINT", "data_type": None}.items() <= dict(capture.options).items()
    assert dict(transform.options) == {"translation": None, "scale": None, "rotation": None}
    assert dict(instances.options) == {"scale": None, "rotation": None, "realize": True}
    assert dict(cube.options) == {"size": ("const", 1.0)}


def test_frontend_owned_selection_omission_stays_out_of_runtime_operands():
    """Omitted selection remains operand absence rather than a synthetic Bool constant."""
    set_position = _analyze(
        "set_position",
        "set_position(geo, pos)",
        types={"geo": TYPE_GEOMETRY, "pos": TYPE_VECTOR},
    )
    instances = _analyze(
        "instance_on_points",
        "instance_on_points(instance, points)",
        types={"instance": TYPE_GEOMETRY, "points": TYPE_GEOMETRY},
    )
    store = _analyze(
        "store_named_attribute",
        'store_named_attribute(geo, "name", value)',
        types={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT},
    )
    capture = _analyze(
        "capture_attribute",
        "capture_attribute(geo, value)",
        types={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT},
    )

    assert [operand.parameter_name for operand in set_position.operands] == ["geometry", "position"]
    assert [operand.parameter_name for operand in instances.operands] == ["instance", "points"]
    assert [operand.parameter_name for operand in store.operands] == ["geometry", "value"]
    assert [operand.parameter_name for operand in capture.operands] == ["geometry", "value"]


def test_raw_node_normalized_record_contains_explicit_empty_and_inactive_metadata():
    """Raw-node omission becomes explicit normalized metadata before semantic lowering."""
    single = _analyze("node", 'node("ShaderNodeValue", output="Value", typ=Float)')
    single_options = dict(single.options)
    assert tuple(single_options) == (
        "bl_idname", "props", "inputs", "raw_output_mode", "output", "typ", "outputs"
    )
    assert single_options["props"] == ()
    assert single_options["inputs"] == ()
    assert single_options["raw_output_mode"] == "SINGLE_OUTPUT"
    assert single_options["output"] == "Value"
    assert single_options["typ"] is TYPE_FLOAT
    assert single_options["outputs"] is None

    named = _analyze("node", 'node("ShaderNodeSeparateXYZ", outputs={"X": Float})')
    named_options = dict(named.options)
    assert named_options["props"] == ()
    assert named_options["inputs"] == ()
    assert named_options["raw_output_mode"] == "NAMED_OUTPUTS"
    assert named_options["output"] is None
    assert named_options["typ"] is None
    assert named_options["outputs"] == (("X", "X", TYPE_FLOAT),)


def test_raw_socket_selectors_normalize_name_position_and_contextual_identifier():
    """Raw selector syntax becomes detached canonical data before IR/backend work."""
    analyzed = _analyze(
        "node",
        'node("ShaderNodeMath", inputs={"Value": 1.0, 1: 2.0, ID("Value_002"): 3.0}, output=ID("Value"), typ=Float)',
    )
    options = dict(analyzed.options)
    assert tuple(selector for selector, _spec in options["inputs"]) == (
        "Value",
        1,
        ("identifier", "Value_002"),
    )
    assert options["output"] == ("identifier", "Value")


def test_raw_contextual_identifier_accepts_compile_time_string_binding():
    analyzed = _analyze(
        "node",
        'node("ShaderNodeValue", output=ID(socket_id), typ=Float)',
        consts={"socket_id": "Value"},
    )
    assert dict(analyzed.options)["output"] == ("identifier", "Value")


@pytest.mark.parametrize(
    ("selector", "message"),
    [
        ("True", "must be a non-empty compile-time string"),
        ("-1", "must be a non-empty compile-time string"),
        ('""', "must be a non-empty compile-time string"),
        ('ID("")', "must be a non-empty compile-time string"),
        ('ID("A", "B")', "expects exactly one positional"),
        ('ID(value="A")', "does not accept keyword"),
    ],
)
def test_raw_socket_selector_rejects_invalid_static_forms(selector, message):
    with pytest.raises(CompileError, match=message):
        _analyze("node", f'node("ShaderNodeValue", output={selector}, typ=Float)')


def test_raw_socket_selector_rejects_runtime_dependency_before_backend():
    with pytest.raises(CompileError, match="must be a non-empty compile-time string"):
        _analyze(
            "node",
            'node("ShaderNodeValue", output=selector, typ=Float)',
            types={"selector": TYPE_INT},
        )


def test_raw_inputs_reject_exact_duplicate_normalized_selectors():
    with pytest.raises(CompileError, match="duplicate selector"):
        _analyze(
            "node",
            'node("ShaderNodeMath", inputs={ID("Value"): 1.0, ID("Value"): 2.0}, output="Value", typ=Float)',
        )


def test_raw_named_outputs_keep_alias_separate_from_physical_selector():
    analyzed = _analyze(
        "node",
        'node("ShaderNodeSeparateXYZ", outputs={"left": (0, Float), "exact": (ID("Y"), Float), "Z": Float})',
    )
    assert dict(analyzed.options)["outputs"] == (
        ("left", 0, TYPE_FLOAT),
        ("exact", ("identifier", "Y"), TYPE_FLOAT),
        ("Z", "Z", TYPE_FLOAT),
    )
    assert isinstance(analyzed.result, NamedOutputsCallResult)
    assert analyzed.result.items == (("left", TYPE_FLOAT), ("exact", TYPE_FLOAT), ("Z", TYPE_FLOAT))


def test_raw_named_outputs_reject_duplicate_selector_even_with_distinct_aliases():
    with pytest.raises(CompileError, match="duplicate selector"):
        _analyze(
            "node",
            'node("ShaderNodeSeparateXYZ", outputs={"left": (0, Float), "also_left": (0, Float)})',
        )


def test_raw_named_outputs_reject_malformed_explicit_selector_tuple():
    with pytest.raises(CompileError, match="type token or \\(selector, TypeToken\\)"):
        _analyze("node", 'node("ShaderNodeSeparateXYZ", outputs={"left": (0,)})')


def test_raw_output_mode_is_syntax_driven_not_cardinality_driven():
    single = _analyze("node", 'node("ShaderNodeValue", output="Value", typ=Float)')
    assert isinstance(single.result, RuntimeCallResult)
    assert dict(single.options)["raw_output_mode"] == "SINGLE_OUTPUT"

    named_one = _analyze("node", 'node("ShaderNodeSeparateXYZ", outputs={"X": Float})')
    assert isinstance(named_one.result, NamedOutputsCallResult)
    assert named_one.result.items == (("X", TYPE_FLOAT),)
    assert dict(named_one.options)["raw_output_mode"] == "NAMED_OUTPUTS"

    named_many = _analyze("node", 'node("ShaderNodeSeparateXYZ", outputs={"X": Float, "Y": Float})')
    assert isinstance(named_many.result, NamedOutputsCallResult)
    assert tuple(name for name, _ in named_many.result.items) == ("X", "Y")


def test_raw_runtime_inputs_are_normalized_to_operand_indices_without_ast_payloads():
    analyzed = _analyze(
        "node",
        'node("FunctionNodeCompare", inputs={"A": position().z}, output="Result", typ=Bool)',
        types={"position().z": TYPE_FLOAT},
    )
    assert analyzed.operands[0].typ == TYPE_FLOAT
    input_specs = dict(analyzed.options)["inputs"]
    assert input_specs == (("A", ("runtime", 0)),)
    assert isinstance(analyzed.result, RuntimeCallResult) and analyzed.result.typ == TYPE_BOOL


def test_capture_attribute_has_explicit_tuple_result_shape():
    analyzed = _analyze(
        "capture_attribute",
        'capture_attribute(geo, value)',
        types={"geo": TYPE_GEOMETRY, "value": TYPE_VECTOR},
    )
    assert isinstance(analyzed.result, TupleCallResult)
    assert analyzed.result.types == (TYPE_GEOMETRY, TYPE_VECTOR)


def test_grid_and_grid_uv_have_explicit_contextual_semantic_contracts():
    grid = _analyze("grid", "grid(4, 3)")
    assert isinstance(grid.result, ProjectedCallResult)
    assert grid.result.types == (TYPE_GEOMETRY, TYPE_VECTOR)
    assert grid.result.exposed_index == 0
    assert grid.result.context_writes == ((GroupContextSlot.GRID_UV, 1),)
    assert dict(grid.options)["slots"] == (("const", 4), ("const", 3))
    assert grid.operands == ()

    uv = _analyze("grid_uv", "grid_uv()")
    assert isinstance(uv.result, ContextReadCallResult)
    assert uv.result.slot is GroupContextSlot.GRID_UV
    assert uv.result.typ is TYPE_VECTOR


def test_raw_outputs_reject_empty_named_mode():
    with pytest.raises(CompileError, match="outputs= cannot be empty"):
        _analyze("node", 'node("ShaderNodeSeparateXYZ", outputs={})')



@pytest.mark.parametrize(
    ("name", "source", "types", "result_type"),
    [
        ("position", "position()", {}, TYPE_VECTOR),
        ("normal", "normal()", {}, TYPE_VECTOR),
        ("index", "index()", {}, TYPE_INT),
        ("id", "id()", {}, TYPE_INT),
        ("vector", "vector(x, 2.0, z=3.0)", {"x": TYPE_FLOAT}, TYPE_VECTOR),
        ("length", "length(v)", {"v": TYPE_VECTOR}, TYPE_FLOAT),
        ("distance", "distance(a, b)", {"a": TYPE_VECTOR, "b": TYPE_VECTOR}, TYPE_FLOAT),
        ("dot", "dot(a, b)", {"a": TYPE_VECTOR, "b": TYPE_VECTOR}, TYPE_FLOAT),
        ("normalize", "normalize(v)", {"v": TYPE_VECTOR}, TYPE_VECTOR),
        ("cross", "cross(a, b)", {"a": TYPE_VECTOR, "b": TYPE_VECTOR}, TYPE_VECTOR),
        ("project", "project(a, b)", {"a": TYPE_VECTOR, "b": TYPE_VECTOR}, TYPE_VECTOR),
        ("reflect", "reflect(a, b)", {"a": TYPE_VECTOR, "b": TYPE_VECTOR}, TYPE_VECTOR),
        ("empty_geometry", "empty_geometry()", {}, TYPE_GEOMETRY),
        ("points", "points(count)", {"count": TYPE_INT}, TYPE_GEOMETRY),
        ("point", "point(pos)", {"pos": TYPE_VECTOR}, TYPE_GEOMETRY),
        ("line", "line(a, b)", {"a": TYPE_VECTOR, "b": TYPE_VECTOR}, TYPE_GEOMETRY),
        ("set_position", "set_position(geo, pos, selection=sel)", {"geo": TYPE_GEOMETRY, "pos": TYPE_VECTOR, "sel": TYPE_BOOL}, TYPE_GEOMETRY),
        ("store_named_attribute", 'store_named_attribute(geo, "name", value)', {"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT}, TYPE_GEOMETRY),
        ("set_material", 'set_material(geo, "Material")', {"geo": TYPE_GEOMETRY}, TYPE_GEOMETRY),
        ("cube", "cube(size)", {"size": TYPE_FLOAT}, TYPE_GEOMETRY),
        ("join", "join(a, b)", {"a": TYPE_GEOMETRY, "b": TYPE_GEOMETRY}, TYPE_GEOMETRY),
        ("transform", "transform(geo, translation=t, scale=s, rotation=r)", {"geo": TYPE_GEOMETRY, "t": TYPE_VECTOR, "s": TYPE_FLOAT, "r": TYPE_VECTOR}, TYPE_GEOMETRY),
        ("polyline", "polyline([(0, 0, 0), (1, 0, 0)])", {}, TYPE_GEOMETRY),
        ("instance_on_points", "instance_on_points(instance, pts, selection=sel, scale=scale, rotation=rot)", {"instance": TYPE_GEOMETRY, "pts": TYPE_GEOMETRY, "sel": TYPE_BOOL, "scale": TYPE_FLOAT, "rot": TYPE_VECTOR}, TYPE_GEOMETRY),
        ("realize_instances", "realize_instances(geo)", {"geo": TYPE_GEOMETRY}, TYPE_GEOMETRY),
        ("bundle", "bundle(x=x, geo=geo)", {"x": TYPE_FLOAT, "geo": TYPE_GEOMETRY}, TYPE_BUNDLE),
        ("bundle_get", "bundle_get(b, path, typ=Float)", {"b": TYPE_BUNDLE, "path": TYPE_STRING}, TYPE_FLOAT),
        ("bundle_set", "bundle_set(b, path, value)", {"b": TYPE_BUNDLE, "path": TYPE_STRING, "value": TYPE_FLOAT}, TYPE_BUNDLE),
        ("node", 'node("ShaderNodeValue", output="Value", typ=Float)', {}, TYPE_FLOAT),
    ],
)
def test_ir_capable_builtin_families_have_blender_independent_success_contracts(name, source, types, result_type):
    """Every migrated ordinary builtin family exposes a concrete semantic result without Blender."""
    analyzed = _analyze(name, source, types=types)
    assert isinstance(analyzed.result, RuntimeCallResult)
    assert analyzed.result.typ is result_type
    assert all(operand.typ in {TYPE_BOOL, TYPE_BUNDLE, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_MATERIAL, TYPE_STRING, TYPE_VECTOR} for operand in analyzed.operands)


def test_capture_attribute_success_is_structural_and_typed_without_blender():
    analyzed = _analyze(
        "capture_attribute",
        "capture_attribute(geo, value, selection=sel)",
        types={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT, "sel": TYPE_BOOL},
    )
    assert isinstance(analyzed.result, TupleCallResult)
    assert analyzed.result.types == (TYPE_GEOMETRY, TYPE_FLOAT)


@pytest.mark.parametrize(
    ("name", "source", "types", "message"),
    [
        ("position", "position(1)", {}, "expects no arguments"),
        ("vector", "vector(x, y, z)", {"x": TYPE_GEOMETRY, "y": TYPE_FLOAT, "z": TYPE_FLOAT}, "expects numeric arguments"),
        ("length", "length(geo)", {"geo": TYPE_GEOMETRY}, "unsupported type"),
        ("points", "points()", {}, "expects one Int argument"),
        ("point", "point(x)", {"x": TYPE_FLOAT}, "expects Vector"),
        ("set_position", "set_position(geo, pos, selection=sel)", {"geo": TYPE_GEOMETRY, "pos": TYPE_VECTOR, "sel": TYPE_FLOAT}, "expects Bool selection"),
        ("capture_attribute", "capture_attribute(geo, material)", {"geo": TYPE_GEOMETRY, "material": TYPE_MATERIAL}, "supports Float, Int, Bool and Vector"),
        ("set_material", "set_material(geo, material)", {"geo": TYPE_GEOMETRY, "material": TYPE_FLOAT}, "second argument must be Material"),
        ("join", "join(geo, value)", {"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT}, "expects Geometry arguments"),
        ("transform", "transform(geo, translation=t)", {"geo": TYPE_GEOMETRY, "t": TYPE_FLOAT}, "translation= must be Vector"),
        ("instance_on_points", "instance_on_points(instance, pts, selection=sel)", {"instance": TYPE_GEOMETRY, "pts": TYPE_GEOMETRY, "sel": TYPE_FLOAT}, "selection= must be a Bool"),
        ("realize_instances", "realize_instances(value)", {"value": TYPE_FLOAT}, "expects Geometry"),
        ("bundle_get", "bundle_get(value, path, typ=Float)", {"value": TYPE_FLOAT, "path": TYPE_STRING}, "first argument expects Bundle"),
        ("bundle_set", "bundle_set(b, value, x)", {"b": TYPE_BUNDLE, "value": TYPE_FLOAT, "x": TYPE_FLOAT}, "path expects String"),
    ],
)
def test_migrated_builtin_families_preserve_relevant_public_failures(name, source, types, message):
    with pytest.raises(CompileError, match=message):
        _analyze(name, source, types=types)




@pytest.mark.parametrize(
    ("name", "source", "expected"),
    [
        (
            "store_named_attribute",
            "store_named_attribute(geo, bad=1)",
            "Unsupported keyword argument(s): bad",
        ),
        (
            "set_position",
            "set_position(geo, weird=1)",
            "Unsupported keyword argument(s): weird",
        ),
    ],
)
def test_expression_store_and_set_position_preserve_keyword_before_arity_precedence(name, source, expected):
    """Expression-form normalization keeps the pre-refactor unsupported-keyword precedence."""
    with pytest.raises(CompileError) as exc_info:
        _analyze(name, source, types={"geo": TYPE_GEOMETRY})
    assert str(exc_info.value) == expected


def test_geometry_builder_expression_contract_remains_compile_time_only():
    with pytest.raises(CompileError, match="must be assigned to a simple name"):
        _analyze("geometry_builder", "geometry_builder()")


@pytest.mark.parametrize(
    ("name", "source", "types"),
    [
        ("vector", "vector(bad, missing, 3)", {"bad": TYPE_GEOMETRY}),
        ("distance", "distance(bad, missing)", {"bad": TYPE_GEOMETRY}),
        ("line", "line(bad, missing)", {"bad": TYPE_FLOAT}),
        ("set_position", "set_position(bad, pos, selection=missing)", {"bad": TYPE_FLOAT, "pos": TYPE_VECTOR}),
        ("capture_attribute", "capture_attribute(bad, missing)", {"bad": TYPE_FLOAT}),
        ("store_named_attribute", "store_named_attribute(bad, \"x\", missing)", {"bad": TYPE_FLOAT}),
        ("set_material", "set_material(bad, missing)", {"bad": TYPE_FLOAT}),
        ("join", "join(bad, missing)", {"bad": TYPE_FLOAT}),
        ("transform", "transform(geo, translation=bad, rotation=missing)", {"geo": TYPE_GEOMETRY, "bad": TYPE_FLOAT}),
        ("instance_on_points", "instance_on_points(instance, pts, scale=bad, rotation=missing)", {"instance": TYPE_GEOMETRY, "pts": TYPE_GEOMETRY, "bad": TYPE_GEOMETRY}),
    ],
)
def test_semantic_builtin_analysis_preserves_operand_evaluation_before_late_type_errors(name, source, types):
    """Later operand diagnostics win when the legacy builtin compiled them before backend type validation."""
    def add_runtime(node, parameter_name, context):
        key = ast.unparse(node)
        if key == "missing":
            raise CompileError("Unknown name: missing")
        return types[key]

    with pytest.raises(CompileError, match="Unknown name: missing"):
        analyze_builtin_call(name, _call(source), {}, add_runtime)


def test_vector_helper_compiles_positional_operands_before_arity_error():
    """Legacy vector helpers compile positional operands before checking their count."""
    def add_runtime(node, parameter_name, context):
        if ast.unparse(node) == "missing":
            raise CompileError("Unknown name: missing")
        return TYPE_VECTOR

    with pytest.raises(CompileError, match="Unknown name: missing"):
        analyze_builtin_call("length", _call("length(missing, other)"), {}, add_runtime)


def test_transform_validates_runtime_options_in_backend_order_after_compiling_all_options():
    """Transform preserves translation, rotation, then scale validation after operand compilation."""
    analyzed_types = {"geo": TYPE_GEOMETRY, "translation": TYPE_VECTOR, "scale": TYPE_GEOMETRY, "rotation": TYPE_FLOAT}
    with pytest.raises(CompileError, match="rotation= must be Vector in radians"):
        _analyze(
            "transform",
            "transform(geo, translation=translation, scale=scale, rotation=rotation)",
            types=analyzed_types,
        )


def test_bundle_semantics_remain_opaque_runtime_type_without_schema_inference():
    """Bundle semantics keep Bundle as one ordinary runtime leaf and require explicit get typing."""
    from NodeForge.nf_types import NFType

    assert TYPE_BUNDLE is NFType.BUNDLE
    assert not hasattr(NFType, "TUPLE")
    assert not hasattr(NFType, "NODE_RESULT")
    assert not hasattr(NFType, "NAMED_OUTPUTS")
    runtime_path = _analyze(
        "bundle_get",
        "bundle_get(b, path, typ=Vector)",
        types={"b": TYPE_BUNDLE, "path": TYPE_STRING},
    )
    assert isinstance(runtime_path.result, RuntimeCallResult)
    assert runtime_path.result.typ is TYPE_VECTOR
    with pytest.raises(CompileError):
        _analyze("bundle_get", "bundle_get(b, path)", types={"b": TYPE_BUNDLE, "path": TYPE_STRING})


@pytest.mark.parametrize("typ", [TYPE_INT, TYPE_FLOAT])
def test_grid_runtime_numeric_arguments_use_runtime_slots(typ):
    analyzed = _analyze("grid", "grid(width, height)", {"width": typ, "height": typ})
    assert [operand.typ for operand in analyzed.operands] == [typ, typ]
    assert dict(analyzed.options)["slots"] == (("runtime", 0), ("runtime", 1))


@pytest.mark.parametrize("value", [2, 2.75])
def test_grid_compile_time_numeric_arguments_preserve_raw_const_slots(value):
    analyzed = _analyze("grid", f"grid({value!r}, {value!r})")
    assert analyzed.operands == ()
    assert dict(analyzed.options)["slots"] == (("const", value), ("const", value))


def test_grid_rejects_compile_time_bool_and_preserves_arity_keyword_diagnostics():
    with pytest.raises(CompileError, match=r"grid\(\) width expects Float/Int"):
        _analyze("grid", "grid(True, 2)")
    with pytest.raises(CompileError, match=r"grid\(width, height\) expects two Int arguments"):
        _analyze("grid", "grid(2)")
    with pytest.raises(CompileError, match=r"grid\(\) does not support keyword arguments"):
        _analyze("grid", "grid(width=2, height=2)")


def test_const_or_runtime_builtin_falls_back_only_on_consteval_unavailability():
    """Genuine CTFE unavailability may select the existing runtime representation."""
    analyzed = _analyze("grid", "grid(width, 2)", types={"width": TYPE_INT})
    assert [operand.typ for operand in analyzed.operands] == [TYPE_INT]
    assert dict(analyzed.options)["slots"] == (("runtime", 0), ("const", 2))


def test_const_or_runtime_builtin_hard_ctfe_error_does_not_reinterpret_as_runtime():
    """A compiler-owned CTFE error remains authoritative instead of selecting runtime."""
    calls = []

    def add_runtime(node, parameter_name, context):
        calls.append((ast.unparse(node), parameter_name, context))
        return TYPE_INT

    with pytest.raises(CompileError, match="not expects Bool"):
        analyze_builtin_call("grid", _call("grid(not 1, 2)"), {}, add_runtime)
    assert calls == []


def test_raw_node_input_uses_runtime_only_for_genuine_ctfe_unavailability():
    """Raw-node socket values distinguish unavailable CTFE from hard CTFE failure."""
    runtime = _analyze(
        "node",
        'node("ShaderNodeValue", inputs={"Value": value}, output="Value", typ=Float)',
        types={"value": TYPE_FLOAT},
    )
    assert [operand.typ for operand in runtime.operands] == [TYPE_FLOAT]

    calls = []

    def add_runtime(node, parameter_name, context):
        calls.append(ast.unparse(node))
        return TYPE_FLOAT

    with pytest.raises(CompileError, match="not expects Bool"):
        analyze_builtin_call(
            "node",
            _call('node("ShaderNodeValue", inputs={"Value": not 1}, output="Value", typ=Float)'),
            {},
            add_runtime,
        )
    assert calls == []


def test_set_material_and_store_preserve_declared_static_or_runtime_contracts():
    """Known non-static kinds use runtime checking only where the public API already permits it."""
    material = _analyze(
        "set_material",
        "set_material(geo, material)",
        types={"geo": TYPE_GEOMETRY, "material": TYPE_MATERIAL},
    )
    assert [operand.typ for operand in material.operands] == [TYPE_GEOMETRY, TYPE_MATERIAL]

    material_from_known_non_string = _analyze(
        "set_material",
        "set_material(geo, 1)",
        types={"geo": TYPE_GEOMETRY, "1": TYPE_MATERIAL},
    )
    assert [operand.typ for operand in material_from_known_non_string.operands] == [TYPE_GEOMETRY, TYPE_MATERIAL]

    store = _analyze(
        "store_named_attribute",
        "store_named_attribute(geo, name, value)",
        types={"geo": TYPE_GEOMETRY, "name": TYPE_STRING, "value": TYPE_FLOAT},
    )
    assert [operand.typ for operand in store.operands] == [TYPE_GEOMETRY, TYPE_STRING, TYPE_FLOAT]

    store_from_known_non_string = _analyze(
        "store_named_attribute",
        "store_named_attribute(geo, 1, value)",
        types={"geo": TYPE_GEOMETRY, "1": TYPE_STRING, "value": TYPE_FLOAT},
    )
    assert [operand.typ for operand in store_from_known_non_string.operands] == [TYPE_GEOMETRY, TYPE_STRING, TYPE_FLOAT]


def test_static_builtin_arguments_distinguish_unavailable_from_hard_ctfe_errors():
    """Static configuration maps only unavailability to contextual diagnostics."""
    with pytest.raises(CompileError, match="input_float default= must be compile-time"):
        analyze_input_declaration_call(_call('input_float("X", default=runtime_default)'), {})
    with pytest.raises(CompileError, match="not expects Bool"):
        analyze_input_declaration_call(_call('input_float("X", default=not 1)'), {})

    with pytest.raises(CompileError, match=r"node\(\.\.\.\) props= values must be compile-time literals"):
        _analyze(
            "node",
            'node("ShaderNodeValue", props={"operation": runtime_prop}, output="Value", typ=Float)',
            types={"runtime_prop": TYPE_STRING},
        )
    with pytest.raises(CompileError, match="not expects Bool"):
        _analyze(
            "node",
            'node("ShaderNodeValue", props={"operation": not 1}, output="Value", typ=Float)',
        )

    with pytest.raises(CompileError, match="realize= must be a compile-time bool"):
        _analyze(
            "instance_on_points",
            "instance_on_points(instance, points, realize=runtime_flag)",
            types={"instance": TYPE_GEOMETRY, "points": TYPE_GEOMETRY, "runtime_flag": TYPE_BOOL},
        )
    with pytest.raises(CompileError, match="not expects Bool"):
        _analyze(
            "instance_on_points",
            "instance_on_points(instance, points, realize=not 1)",
            types={"instance": TYPE_GEOMETRY, "points": TYPE_GEOMETRY},
        )



def test_input_declaration_compile_time_only_acquisition_uses_known_fact():
    analyzed = analyze_input_declaration_call(
        _call('input_float("X", default=d)'),
        {"d": 0.5},
    )
    assert analyzed.display_name == "X"
    assert analyzed.typ is TYPE_FLOAT
    assert analyzed.default == 0.5


def test_instance_on_points_static_mixed_options_are_validated_before_call_semantics():
    analyzed = _analyze(
        "instance_on_points",
        "instance_on_points(instance, points, scale=2.0, rotation=vector(0, 0, 0), realize=True)",
        types={"instance": TYPE_GEOMETRY, "points": TYPE_GEOMETRY},
    )
    assert [operand.typ for operand in analyzed.operands] == [TYPE_GEOMETRY, TYPE_GEOMETRY]
    assert dict(analyzed.options) == {
        "scale": ("const", 2.0),
        "rotation": ("const", (0.0, 0.0, 0.0)),
        "realize": True,
    }

    with pytest.raises(CompileError, match=r"instance_on_points scale= expects Float/Int or Vector"):
        _analyze(
            "instance_on_points",
            'instance_on_points(instance, points, scale="bad")',
            types={"instance": TYPE_GEOMETRY, "points": TYPE_GEOMETRY},
        )
    with pytest.raises(CompileError, match=r"instance_on_points rotation= expects Vector in radians"):
        _analyze(
            "instance_on_points",
            "instance_on_points(instance, points, rotation=1.0)",
            types={"instance": TYPE_GEOMETRY, "points": TYPE_GEOMETRY},
        )


def test_instance_on_points_mixed_runtime_slots_preserve_direct_runtime_operand_order():
    calls = []
    types = {
        "instance": TYPE_GEOMETRY,
        "points": TYPE_GEOMETRY,
        "sel": TYPE_BOOL,
        "scale": TYPE_FLOAT,
        "rotation": TYPE_VECTOR,
    }

    def add_runtime(node, parameter_name, context):
        key = ast.unparse(node)
        calls.append((key, parameter_name, context))
        return types[key]

    analyzed = analyze_builtin_call(
        "instance_on_points",
        _call("instance_on_points(instance, points, selection=sel, scale=scale, rotation=rotation, realize=False)"),
        {},
        add_runtime,
    )
    assert [name for name, _, _ in calls] == ["instance", "points", "sel", "scale", "rotation"]
    assert [operand.typ for operand in analyzed.operands] == [
        TYPE_GEOMETRY,
        TYPE_GEOMETRY,
        TYPE_BOOL,
        TYPE_FLOAT,
        TYPE_VECTOR,
    ]
    assert dict(analyzed.options) == {
        "scale": ("runtime", 3),
        "rotation": ("runtime", 4),
        "realize": False,
    }


def test_instance_on_points_mixed_hard_ctfe_error_does_not_acquire_runtime_option():
    calls = []

    def add_runtime(node, parameter_name, context):
        calls.append(ast.unparse(node))
        return TYPE_GEOMETRY

    with pytest.raises(CompileError, match="not expects Bool"):
        analyze_builtin_call(
            "instance_on_points",
            _call("instance_on_points(instance, points, scale=not 1)"),
            {},
            add_runtime,
        )
    assert calls == ["instance", "points"]


def test_transform_mixed_options_validate_static_values_in_frontend():
    analyzed = _analyze(
        "transform",
        "transform(geo, translation=vector(1,2,3), scale=2.0, rotation=vector(0,0,0))",
        types={"geo": TYPE_GEOMETRY},
    )
    assert [operand.typ for operand in analyzed.operands] == [TYPE_GEOMETRY]
    assert dict(analyzed.options) == {
        "translation": ("const", (1.0, 2.0, 3.0)),
        "scale": ("const", 2.0),
        "rotation": ("const", (0.0, 0.0, 0.0)),
    }

    for source, message in [
        ("transform(geo, translation=1.0)", "translation= must be Vector"),
        ('transform(geo, rotation="bad")', "rotation= must be Vector in radians"),
        ('transform(geo, scale="bad")', "scale= must be Float/Int or Vector"),
    ]:
        with pytest.raises(CompileError, match=message):
            _analyze("transform", source, types={"geo": TYPE_GEOMETRY})


def test_transform_runtime_mixed_options_preserve_visitation_and_operand_slots():
    calls = []
    types = {
        "geo": TYPE_GEOMETRY,
        "translation": TYPE_VECTOR,
        "scale": TYPE_INT,
        "rotation": TYPE_VECTOR,
    }

    def add_runtime(node, parameter_name, context):
        key = ast.unparse(node)
        calls.append((key, parameter_name, context))
        return types[key]

    analyzed = analyze_builtin_call(
        "transform",
        _call("transform(geo, translation=translation, scale=scale, rotation=rotation)"),
        {},
        add_runtime,
    )
    assert [name for name, _, _ in calls] == ["geo", "translation", "scale", "rotation"]
    assert [operand.typ for operand in analyzed.operands] == [
        TYPE_GEOMETRY,
        TYPE_VECTOR,
        TYPE_INT,
        TYPE_VECTOR,
    ]
    assert dict(analyzed.options) == {
        "translation": ("runtime", 1),
        "scale": ("runtime", 2),
        "rotation": ("runtime", 3),
    }


def test_set_material_remains_outside_simple_mixed_selector_policy():
    static = _analyze(
        "set_material",
        'set_material(geo, "Stone")',
        types={"geo": TYPE_GEOMETRY},
    )
    assert [operand.typ for operand in static.operands] == [TYPE_GEOMETRY]
    assert dict(static.options)["material"] == ("const", "Stone")

    runtime_unavailable = _analyze(
        "set_material",
        "set_material(geo, material)",
        types={"geo": TYPE_GEOMETRY, "material": TYPE_MATERIAL},
    )
    assert [operand.typ for operand in runtime_unavailable.operands] == [TYPE_GEOMETRY, TYPE_MATERIAL]

    runtime_after_inadmissible_ct = _analyze(
        "set_material",
        "set_material(geo, 1)",
        types={"geo": TYPE_GEOMETRY, "1": TYPE_MATERIAL},
    )
    assert [operand.typ for operand in runtime_after_inadmissible_ct.operands] == [TYPE_GEOMETRY, TYPE_MATERIAL]

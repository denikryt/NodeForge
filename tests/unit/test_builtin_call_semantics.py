"""Pure semantic contracts for compiler-owned builtin calls before Blender lowering."""

from __future__ import annotations

import ast

import pytest

from NodeForge.builtin_call_semantics import (
    INPUT_DECLARATION_BUILTIN_NAMES,
    IR_CAPABLE_BUILTIN_NAMES,
    analyze_builtin_call,
    analyze_input_declaration_call,
)
from NodeForge.builtins.registry import CALLABLE_BUILTIN_NAMES
from NodeForge.call_resolution import (
    ContextReadCallResult, NamedOutputsCallResult, ProjectedCallResult, RuntimeCallResult, TupleCallResult,
)
from NodeForge.constants import (
    TYPE_BOOL, TYPE_BUNDLE, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_MATERIAL,
    TYPE_STRING, TYPE_VECTOR,
)
from NodeForge.errors import CompileError
from NodeForge.group_context import GroupContextSlot


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
    assert {"grid", "grid_uv"} <= IR_CAPABLE_BUILTIN_NAMES
    assert {"input_float", "input_bundle"} <= INPUT_DECLARATION_BUILTIN_NAMES


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
    """Structural/Object/Bundle semantics migration keeps Bundle as one ordinary runtime leaf and requires explicit get typing."""
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

"""Pure value-based Semantic IR contracts and migration-boundary regressions."""

import ast
import dataclasses
import sys
from pathlib import Path

import pytest

from NodeForge.constants import (
    TYPE_BOOL,
    TYPE_BUNDLE,
    TYPE_FLOAT,
    TYPE_GEOMETRY,
    TYPE_INT,
    TYPE_MATERIAL,
    TYPE_OBJECT,
    TYPE_STRING,
    TYPE_VECTOR,
)
from NodeForge.errors import CompileError
from NodeForge.compiler_identities import BindingId, CallSiteId, local_function_id
from NodeForge.semantic_ir import (
    IRBinary,
    IRFunctionMaterialization,
    IRFunctionMaterializationMode,
    IRBinding,
    IRBoolBinary,
    IRCompare,
    IRConditional,
    IRLiteral,
    IRProgram,
    IRUnary,
    IRValue,
    IRVectorComponent,
)
from NodeForge.semantic_analysis import RuntimeBindingSymbol, SemanticEnvironment, analyze_expression
from NodeForge.semantic_lowering import lower_analyzed_expression


pytestmark = pytest.mark.unit

_TEST_BINDING_LOCAL_IDS = {}


def _test_binding_id(name):
    """Return one stable test binding-slot identity across independent programs."""
    local_id = _TEST_BINDING_LOCAL_IDS.setdefault(name, len(_TEST_BINDING_LOCAL_IDS))
    return BindingId("test-owner", local_id)



def _expr(source):
    """Parse one expression fixture."""
    return ast.parse(source, mode="eval").body


def _environment(*, bindings=None, consts=None, labels=None, legacy_names=()):
    """Build one immutable semantic environment for pure IR tests."""
    from types import MappingProxyType

    scalar_constants = {}
    unsupported_constants = set()
    for name, value in (consts or {}).items():
        if isinstance(value, bool):
            scalar_constants[name] = (TYPE_BOOL, value)
        elif isinstance(value, (int, float)):
            scalar_constants[name] = (TYPE_FLOAT, value)
        elif isinstance(value, str):
            scalar_constants[name] = (TYPE_STRING, value)
        else:
            unsupported_constants.add(name)
    runtime_bindings = {
        name: RuntimeBindingSymbol(_test_binding_id(name), typ)
        for name, typ in (bindings or {}).items()
    }
    return SemanticEnvironment(
        MappingProxyType(runtime_bindings),
        frozenset(legacy_names),
        MappingProxyType(scalar_constants),
        frozenset(unsupported_constants),
        MappingProxyType(dict(labels or {})),
    )


def _lower(source, *, bindings=None, consts=None, labels=None, legacy_names=()):
    """Analyze and lower one source expression through the pure frontend stages."""
    expr = _expr(source)
    analysis = analyze_expression(
        expr,
        _environment(bindings=bindings, consts=consts, labels=labels, legacy_names=legacy_names),
    )
    return None if analysis is None else lower_analyzed_expression(expr, analysis)


def _operations(program, operation_type):
    """Return operations of one concrete IR record type."""
    return [operation for operation in program.operations if isinstance(operation, operation_type)]


def _producer_map(program):
    """Map program-local result IDs to the operations that produce them."""
    return {operation.result.id: operation for operation in program.operations}


def test_function_materialization_ir_is_immutable_hashable_and_blender_independent():
    """Reusable-call materialization semantics stay typed and backend-independent."""
    function_id = local_function_id("owner", "helper", "x:FLOAT")
    call_site = CallSiteId("owner", function_id, 0)
    shared = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    unique = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.UNIQUE, call_site)

    assert shared == IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    assert len({unique, IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.UNIQUE, call_site)}) == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        shared.call_site = call_site

    field_names = {field.name for field in dataclasses.fields(IRFunctionMaterialization)}
    assert field_names == {"callee", "mode", "call_site"}


def test_function_materialization_ir_enforces_shared_unique_invariants():
    """Shared calls are callsite-less and unique calls target their exact callee."""
    first = local_function_id("owner", "first", "x:FLOAT")
    second = local_function_id("owner", "second", "x:FLOAT")
    first_site = CallSiteId("owner", first, 0)

    assert IRFunctionMaterialization(first, IRFunctionMaterializationMode.SHARED).call_site is None
    assert IRFunctionMaterialization(first, IRFunctionMaterializationMode.UNIQUE, first_site).call_site == first_site
    with pytest.raises(ValueError):
        IRFunctionMaterialization(first, IRFunctionMaterializationMode.SHARED, first_site)
    with pytest.raises(ValueError):
        IRFunctionMaterialization(first, IRFunctionMaterializationMode.UNIQUE)
    with pytest.raises(ValueError):
        IRFunctionMaterialization(second, IRFunctionMaterializationMode.UNIQUE, first_site)
    with pytest.raises(TypeError):
        IRFunctionMaterialization("first", IRFunctionMaterializationMode.SHARED)


def test_semantic_modules_are_blender_independent_and_ir_is_immutable():
    assert "bpy" not in sys.modules
    program = _lower("a + 1", bindings={"a": TYPE_FLOAT})
    assert isinstance(program, IRProgram)
    assert dataclasses.is_dataclass(program)
    assert dataclasses.is_dataclass(program.result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        program.result.typ = TYPE_INT

    def contains_ast(value):
        if isinstance(value, ast.AST):
            return True
        if dataclasses.is_dataclass(value):
            return any(contains_ast(getattr(value, field.name)) for field in dataclasses.fields(value))
        if isinstance(value, tuple):
            return any(contains_ast(item) for item in value)
        return False

    assert not contains_ast(program)


def test_ir_value_is_program_local_shape_without_backend_state():
    value = IRValue(0, TYPE_FLOAT)
    assert value.id == 0
    assert value.typ == TYPE_FLOAT
    assert tuple(field.name for field in dataclasses.fields(value)) == ("id", "typ")
    assert not hasattr(value, "socket")

    first = _lower("1")
    second = _lower("2")
    assert first.result == IRValue(0, TYPE_FLOAT)
    assert second.result == IRValue(0, TYPE_FLOAT)
    assert first is not second


def test_value_ids_are_deterministic_unique_and_dependencies_are_ordered():
    program = _lower("a * 2 + 1", bindings={"a": TYPE_FLOAT})
    assert [operation.result.id for operation in program.operations] == [0, 1, 2, 3, 4]
    produced = set()
    for operation in program.operations:
        for field in dataclasses.fields(operation):
            field_value = getattr(operation, field.name)
            if isinstance(field_value, IRValue) and field.name != "result":
                assert field_value.id in produced
        produced.add(operation.result.id)
    assert program.result.id in produced


def test_arithmetic_is_explicit_ordered_value_program_with_relative_depths():
    program = _lower("a * 2 + 1", bindings={"a": TYPE_FLOAT})
    assert [type(operation) for operation in program.operations] == [
        IRBinding,
        IRLiteral,
        IRBinary,
        IRLiteral,
        IRBinary,
    ]
    assert [operation.depth for operation in program.operations] == [2, 2, 1, 1, 0]
    multiply = program.operations[2]
    add = program.operations[4]
    assert multiply.op == "MULTIPLY"
    assert multiply.left == program.operations[0].result
    assert multiply.right == program.operations[1].result
    assert add.op == "ADD"
    assert add.left == multiply.result
    assert add.right == program.operations[3].result
    assert program.result == add.result


@pytest.mark.parametrize(
    ("source", "value", "typ"),
    [("1", 1, TYPE_FLOAT), ("1.5", 1.5, TYPE_FLOAT), ("True", True, TYPE_BOOL), ('"name"', "name", TYPE_STRING)],
)
def test_literal_typing(source, value, typ):
    program = _lower(source)
    operation = program.operations[0]
    assert isinstance(operation, IRLiteral)
    assert operation.value == value
    assert operation.result.typ == typ
    assert program.result == operation.result


def test_ir_binding_shape_uses_canonical_slot_identity_only():
    program = _lower("a + a", bindings={"a": TYPE_FLOAT})
    bindings = _operations(program, IRBinding)
    assert tuple(field.name for field in dataclasses.fields(IRBinding)) == ("result", "depth", "binding_id")
    assert len(bindings) == 2
    assert bindings[0].binding_id == bindings[1].binding_id == _test_binding_id("a")
    assert bindings[0].result != bindings[1].result
    assert not hasattr(bindings[0], "name")
    assert not hasattr(bindings[0], "socket")


def test_distinct_source_bindings_receive_distinct_canonical_slots():
    program = _lower("a + b", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT})
    bindings = _operations(program, IRBinding)
    assert bindings[0].binding_id != bindings[1].binding_id


def test_runtime_binding_precedes_compile_time_constant():
    program = _lower("a", bindings={"a": TYPE_FLOAT}, consts={"a": 1})
    assert isinstance(program.operations[0], IRBinding)
    assert program.operations[0].binding_id == _test_binding_id("a")
    assert program.result.typ == TYPE_FLOAT


def test_supported_scalar_const_and_allowed_const_lower_to_literals():
    assert isinstance(_lower("a", consts={"a": 7}).operations[0], IRLiteral)
    pi = _lower("pi")
    assert isinstance(pi.operations[0], IRLiteral)
    assert pi.result.typ == TYPE_FLOAT


def test_reached_unknown_name_is_error_and_unrepresented_constant_is_unsupported():
    with pytest.raises(CompileError, match="Unknown name: unknown"):
        _lower("unknown")
    assert _lower("v", consts={"v": (1, 2, 3)}) is None


def test_type_token_and_reserved_value_diagnostics_are_preserved():
    with pytest.raises(CompileError, match="Type token Float"):
        _lower("Float")
    with pytest.raises(CompileError, match="registered as DSL builtin"):
        _lower("foo", labels={"foo": "DSL builtin"})


@pytest.mark.parametrize(
    ("source", "bindings", "typ"),
    [
        ("a + b", {"a": TYPE_FLOAT, "b": TYPE_FLOAT}, TYPE_FLOAT),
        ("a + b", {"a": TYPE_INT, "b": TYPE_INT}, TYPE_FLOAT),
        ("a + b", {"a": TYPE_VECTOR, "b": TYPE_VECTOR}, TYPE_VECTOR),
        ("v * a", {"v": TYPE_VECTOR, "a": TYPE_FLOAT}, TYPE_VECTOR),
        ("a * v", {"v": TYPE_VECTOR, "a": TYPE_FLOAT}, TYPE_VECTOR),
        ("v / a", {"v": TYPE_VECTOR, "a": TYPE_FLOAT}, TYPE_VECTOR),
    ],
)
def test_binary_result_types_match_current_backend(source, bindings, typ):
    program = _lower(source, bindings=bindings)
    operation = _operations(program, IRBinary)[-1]
    assert operation.result.typ == typ


def test_invalid_owned_binary_operation_is_compile_error():
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and FLOAT"):
        _lower("flag + a", bindings={"flag": TYPE_BOOL, "a": TYPE_FLOAT})


@pytest.mark.parametrize("typ", [TYPE_FLOAT, TYPE_MATERIAL, TYPE_OBJECT, TYPE_GEOMETRY, TYPE_BUNDLE, TYPE_STRING])
def test_unary_plus_is_explicit_identity_operation_for_any_owned_runtime_value(typ):
    program = _lower("+a", bindings={"a": typ})
    unary = program.operations[-1]
    assert isinstance(unary, IRUnary)
    assert unary.op == "+"
    assert unary.result.typ == typ
    assert unary.operand == program.operations[0].result


def test_unary_minus_and_not_types():
    assert _lower("-a", bindings={"a": TYPE_FLOAT}).result.typ == TYPE_FLOAT
    assert _lower("-v", bindings={"v": TYPE_VECTOR}).result.typ == TYPE_VECTOR
    assert _lower("not flag", bindings={"flag": TYPE_BOOL}).result.typ == TYPE_BOOL
    with pytest.raises(CompileError, match="not expects Bool"):
        _lower("not a", bindings={"a": TYPE_FLOAT})


def test_boolop_emits_pairwise_operations_and_preserves_validation_order():
    program = _lower("a and b and c", bindings={"a": TYPE_BOOL, "b": TYPE_BOOL, "c": TYPE_BOOL})
    bool_ops = _operations(program, IRBoolBinary)
    assert len(bool_ops) == 2
    assert all(operation.op == "AND" for operation in bool_ops)
    assert all(operation.depth == 0 for operation in bool_ops)
    assert bool_ops[1].left == bool_ops[0].result
    assert program.result == bool_ops[1].result

    with pytest.raises(CompileError, match="Boolean operations expect Bool values"):
        _lower("True and 1 and (True + 1)")
    with pytest.raises(CompileError, match="Boolean operations expect Bool values"):
        _lower("True and 1 and legacy_call()")


def test_comparison_chain_emits_explicit_compare_and_and_values():
    program = _lower("a < b <= c", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT})
    compares = _operations(program, IRCompare)
    bool_ops = _operations(program, IRBoolBinary)
    assert [operation.op for operation in compares] == ["LESS_THAN", "LESS_EQUAL"]
    assert len(bool_ops) == 1 and bool_ops[0].op == "AND"
    assert bool_ops[0].left == compares[0].result
    assert bool_ops[0].right == compares[1].result
    assert program.result == bool_ops[0].result

    assert _operations(_lower("a == b", bindings={"a": TYPE_BOOL, "b": TYPE_BOOL}), IRCompare)
    assert _operations(_lower("a == b", bindings={"a": TYPE_VECTOR, "b": TYPE_VECTOR}), IRCompare)
    with pytest.raises(CompileError, match="Comparison inputs must both be numeric"):
        _lower("a == b", bindings={"a": TYPE_BOOL, "b": TYPE_VECTOR})


def test_comparison_chain_duplicates_middle_expression_values_and_operations():
    program = _lower("a < b * 2 < c", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT})
    multiplies = [operation for operation in _operations(program, IRBinary) if operation.op == "MULTIPLY"]
    compares = _operations(program, IRCompare)
    bool_ops = _operations(program, IRBoolBinary)
    assert len(multiplies) == 2
    assert multiplies[0].result.id != multiplies[1].result.id
    assert multiplies[0].depth == multiplies[1].depth == 1
    assert len(compares) == 2 and all(operation.depth == 0 for operation in compares)
    assert len(bool_ops) == 1 and bool_ops[0].depth == 0 and bool_ops[0].op == "AND"


@pytest.mark.parametrize("typ", [TYPE_FLOAT, TYPE_INT, TYPE_VECTOR, TYPE_BOOL, TYPE_GEOMETRY, TYPE_STRING, TYPE_BUNDLE])
def test_conditional_supported_backend_types_are_owned(typ):
    program = _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": typ, "b": typ})
    conditional = program.operations[-1]
    assert isinstance(conditional, IRConditional)
    assert conditional.result.typ == typ


def test_conditional_validation_and_backend_ownership_order():
    with pytest.raises(CompileError, match="cond must be Bool"):
        _lower("a if cond else b", bindings={"cond": TYPE_FLOAT, "a": TYPE_FLOAT, "b": TYPE_FLOAT})
    with pytest.raises(CompileError, match="same type"):
        _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_FLOAT, "b": TYPE_VECTOR})
    assert _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_MATERIAL, "b": TYPE_MATERIAL}) is None
    assert _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_OBJECT, "b": TYPE_OBJECT}) is None
    with pytest.raises(CompileError, match="cond must be Bool"):
        _lower("a if cond else b", bindings={"cond": TYPE_FLOAT, "a": TYPE_MATERIAL, "b": TYPE_MATERIAL})


def test_vector_component_consumes_vector_value_and_object_attribute_falls_back():
    program = _lower("v.x", bindings={"v": TYPE_VECTOR})
    component = program.operations[-1]
    assert isinstance(component, IRVectorComponent)
    assert component.value == program.operations[0].result
    assert component.value.typ == TYPE_VECTOR
    assert component.result.typ == TYPE_FLOAT
    with pytest.raises(CompileError, match="only be used on Vector"):
        _lower("a.x", bindings={"a": TYPE_FLOAT})
    assert _lower("obj.location", bindings={"obj": TYPE_OBJECT}) is None
    assert _lower("obj.x", bindings={"obj": TYPE_OBJECT}) is None


def test_mixed_fallback_and_diagnostic_boundaries_are_preserved():
    assert _lower("legacy_call() + (True + 1)") is None
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and FLOAT"):
        _lower("(True + 1) + legacy_call()")
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and FLOAT"):
        _lower("(True + 1) if 1 else 2")
    assert _lower("1 < legacy_call() < (True + 1)") is None


@pytest.mark.parametrize("source", ["foo(a)", "obj.info()", "[a, b]", "(a, b)", "a[0]"])
def test_explicit_first_slice_boundaries_are_unsupported(source):
    assert _lower(source, bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "obj": TYPE_OBJECT}) is None


def test_ir_operations_store_no_ast_or_backend_objects():
    from NodeForge.values import Value

    program = _lower("(a + 1) if flag else v.x", bindings={"a": TYPE_FLOAT, "flag": TYPE_BOOL, "v": TYPE_VECTOR})
    forbidden = (ast.AST, Value)
    for operation in program.operations:
        for field in dataclasses.fields(operation):
            value = getattr(operation, field.name)
            assert not isinstance(value, forbidden)
            assert field.name not in {"socket", "node", "group", "compiler", "comp"}



def test_ir_emitter_consumes_analyzed_operation_facts_without_renormalizing_ast():
    from types import MappingProxyType
    from NodeForge.semantic_analysis import ExpressionAnalysis, ExpressionFact

    expr = _expr("a < b")
    analysis = analyze_expression(expr, _environment(bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT}))
    facts = dict(analysis.facts)
    facts[expr] = ExpressionFact(TYPE_BOOL, compare_operations=("GREATER_THAN",))
    rewritten = ExpressionAnalysis(expr, MappingProxyType(facts))
    program = lower_analyzed_expression(expr, rewritten)
    compare = _operations(program, IRCompare)[0]
    assert compare.op == "GREATER_THAN"


def test_ir_emitter_rejects_invalid_analysis_root_and_compare_fact_shape():
    from types import MappingProxyType
    from NodeForge.semantic_analysis import ExpressionAnalysis, ExpressionFact

    expr = _expr("a < b")
    other = _expr("a < b")
    analysis = analyze_expression(expr, _environment(bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT}))
    with pytest.raises(CompileError, match="invalid Semantic IR expression analysis"):
        lower_analyzed_expression(other, analysis)

    facts = dict(analysis.facts)
    facts[expr] = ExpressionFact(TYPE_BOOL, compare_operations=())
    malformed = ExpressionAnalysis(expr, MappingProxyType(facts))
    with pytest.raises(CompileError, match="comparison semantic operation count mismatch"):
        lower_analyzed_expression(expr, malformed)



def _backend_context(backend, bindings=None, group=None):
    """Build an immutable explicit backend context for unit lowering tests."""
    from types import MappingProxyType

    return backend.BlenderIRLoweringContext(
        object() if group is None else group,
        MappingProxyType(dict(bindings or {})),
    )


def _backend_bindings(bindings):
    """Map source-ordered test bindings to the BindingIds emitted by _environment()."""
    return {
        _test_binding_id(name): value
        for name, value in bindings.items()
    }

def test_exact_semantic_migration_markers_are_present_at_source_decisions():
    root = Path(__file__).resolve().parents[2]
    sources = {
        name: (root / path).read_text(encoding="utf-8")
        for name, path in {
            "analysis": "semantic_analysis.py",
            "semantic": "semantic_lowering.py",
            "dispatcher": "expression_compiler.py",
            "backend": "blender_ir_lowering.py",
            "compiler": "compiler.py",
            "local": "local_functions.py",
            "library_calls": "library_calls.py",
            "library": "library.py",
            "materializer": "function_materializer.py",
        }.items()
    }

    assert sources["analysis"].count("SEMANTIC_ANALYSIS_MIGRATION") == 1
    assert sources["semantic"].count("SEMANTIC_RESOLUTION_MIGRATION") == 0
    assert sources["semantic"].count("SEMANTIC_IR_VALUE_MIGRATION") == 1
    assert sources["dispatcher"].count("SEMANTIC_IR_MIGRATION") == 1
    assert sources["dispatcher"].count("SEMANTIC_IR_VALUE_MIGRATION") == 1
    assert sources["dispatcher"].count("SEMANTIC_ANALYSIS_LEGACY_BINDING_MIGRATION") == 1
    assert sources["dispatcher"].count("SEMANTIC_ANALYSIS_CONSTANT_SNAPSHOT_MIGRATION") == 1
    assert sources["backend"].count("SEMANTIC_IR_VALUE_MIGRATION") == 1
    assert sources["compiler"].count("CANONICAL_BINDING_ID_MIGRATION") == 1
    assert sources["compiler"].count("CANONICAL_CALL_ID_MIGRATION") == 0
    assert sources["local"].count("CANONICAL_CALL_ID_MIGRATION") == 1
    assert sources["library_calls"].count("CANONICAL_CALL_ID_MIGRATION") == 1
    assert sources["compiler"].count("REUSABLE_CALL_IR_MIGRATION") == 1
    assert sources["local"].count("REUSABLE_CALL_IR_MIGRATION") == 1
    assert sources["library_calls"].count("REUSABLE_CALL_IR_MIGRATION") == 2
    assert sources["library"].count("REUSABLE_CALL_IR_MIGRATION") == 0
    assert sources["materializer"].count("FUNCTION_MATERIALIZER_TRANSACTION_MIGRATION") == 0
    assert sources["materializer"].count("FUNCTION_MATERIALIZER_CATALOG_METADATA_MIGRATION") == 0
    assert sources["materializer"].count("FUNCTION_MATERIALIZER_RELOAD_METADATA_MIGRATION") == 0
    assert sources["compiler"].count("BLENDER_TRANSACTION_LEGACY_ALIAS_MIGRATION") == 1

    normalized = {name: "\n".join(line.lstrip() for line in source.splitlines()) for name, source in sources.items()}
    required_markers = (
        (normalized["compiler"], "# CANONICAL_BINDING_ID_MIGRATION: comp.vars remains the legacy heterogeneous\n# source-name store while statement, loop, call, and compile-time binding migration\n# is incomplete. Project current Value entries into stable BindingId-based semantic\n# and backend snapshots here without changing comp.vars ownership. Remove this bridge\n# when runtime bindings are stored canonically by BindingId and source names exist\n# only in the frontend symbol table."),
        (normalized["local"], "# CANONICAL_CALL_ID_MIGRATION: Local calls still reach this legacy AST compiler\n# before call semantics are represented in Semantic IR. Construct the canonical\n# FunctionId here from the already-resolved specialization contract. Remove this\n# bridge when semantic call resolution produces FunctionId/CallSiteId before\n# backend function-group materialization."),
        (normalized["library_calls"], "# CANONICAL_CALL_ID_MIGRATION: Imported reusable calls still resolve through the\n# legacy AST/library dispatcher. Construct their canonical FunctionId at this\n# boundary without changing call behavior. Remove this bridge when semantic call\n# resolution owns imported callable identity before function-group materialization."),
        (normalized["compiler"], "# REUSABLE_CALL_IR_MIGRATION: Unique materialization is now represented explicitly\n# by IRFunctionMaterialization, but its CallSiteId ordinal is still allocated when\n# the legacy AST call path reaches reusable-call preparation. Preserve the current\n# unique-only per-owner/per-callee sequence here. Remove this allocator bridge when\n# reusable calls are emitted by semantic lowering before backend/materialization."),
        (normalized["local"], "# REUSABLE_CALL_IR_MIGRATION: This stage moves shared/unique materialization policy\n# into compiler-owned IR only. Local call argument evaluation, specialization-type\n# discovery, captures, and return realization still use the legacy Value/socket path.\n# Remove this boundary when reusable call arguments/results have Blender-independent\n# semantic types/IR values and local FunctionId is resolved before materialization."),
        (normalized["library_calls"], "# REUSABLE_CALL_IR_MIGRATION: Imported callable identity/materialization policy is\n# compiler-owned, but argument names/types are still discovered from the materialized\n# Blender node-group interface. Keep this probe behavior unchanged in this stage.\n# Remove it when imported functions expose a Blender-independent callable signature\n# that semantic analysis can validate before function-group materialization."),
        (normalized["library_calls"], "# REUSABLE_CALL_IR_MIGRATION: Local catalog entries keep their existing materialization\n# semantics in this behavior-preserving stage because the current namespace=\"local\"\n# path does not assign durable per-occurrence instance keys/owner scopes for __unique__.\n# Remove this exclusion only after Local catalog shared/unique ownership is explicitly\n# specified, compatibility-tested, and migrated as its own semantic contract."),
        (normalized["dispatcher"], "# SEMANTIC_IR_VALUE_MIGRATION: comp.vars still stores legacy socket-bound Value\n# objects while statement and call migration is incomplete. Snapshot those Values\n# only at the frontend/backend boundary: semantic analysis receives detached types,\n# while Blender lowering receives the backend Value map. Remove this bridge when\n# runtime bindings use canonical compiler-owned references and comp.vars no longer\n# owns backend sockets."),
        (normalized["dispatcher"], "# SEMANTIC_IR_MIGRATION: Expressions outside the current IR slice continue on\n# the existing AST-to-Blender path while migration is incremental. The target\n# architecture is for migrated expression families to lower through Semantic IR\n# before Blender materialization. Remove this fallback only for an expression\n# family after that family is covered end-to-end by IR and its duplicated AST\n# lowering branch is removed in the same planned change set."),
        (normalized["semantic"], "# SEMANTIC_IR_VALUE_MIGRATION: Re-lower the shared middle source expression for\n# each comparison pair so this behavior-preserving stage emits distinct IR values\n# and preserves the current duplicated Geometry Nodes topology. Remove this rule\n# only in a dedicated topology-changing plan that defines IR value reuse and\n# updates the corresponding graph-shape contract tests."),
        (normalized["analysis"], "# SEMANTIC_ANALYSIS_MIGRATION: TYPE_OBJECT attribute semantics still belong to\n# the legacy ObjectValue.resolve_property() path. Keep Object property access\n# outside semantic analysis until its property resolution and result typing are\n# represented frontend-side. Remove this fallback when TYPE_OBJECT attribute\n# access is migrated end-to-end and ObjectValue is no longer the semantic owner."),
        (normalized["compiler"], "# BLENDER_TRANSACTION_LEGACY_ALIAS_MIGRATION: Keep the historical transaction class\n# names importable from compiler.py for one compatibility stage after physical Blender\n# transaction ownership moves to blender_group_backend.py. New production code must\n# import BlenderGroupBuildTransaction from the Blender group backend module and must not\n# depend on these aliases. Remove them after the next compatibility sweep confirms no\n# supported external/test consumer imports FunctionGroupBuildTransaction or\n# LocalHelperBuildTransaction from compiler.py."),
    )
    for source, marker in required_markers:
        assert marker in source

    assert "IRBinding still stores the legacy source binding name" not in sources["semantic"]
    assert "source-name -> legacy Value map" not in sources["backend"]

    assert "next_unique_function_call_site" not in sources["compiler"]
    local_after_materialization = sources["local"].split("materialization = comp.resolve_reusable_function_materialization", 1)[1]
    imported_after_materialization = sources["library_calls"].split("materialization = comp.resolve_reusable_function_materialization", 1)[1]
    assert "modifiers.unique" not in local_after_materialization
    assert "modifiers.unique" not in imported_after_materialization
    assert "instance_key_for_materialization(materialization)" not in local_after_materialization
    assert "instance_key_for_materialization(materialization)" not in imported_after_materialization
    assert "instance_key = materialized.instance_key" in imported_after_materialization
    assert "function_group = materialized.group" in imported_after_materialization
    assert "instance_key_for_materialization(materialization)" in sources["materializer"]
    assert "comp.compile_group_callback(" not in sources["local"]
    assert "compile_group_callback(" not in sources["library"]

def test_blender_lowering_context_is_minimal_immutable_and_compiler_independent():
    from dataclasses import FrozenInstanceError, fields
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    value = Value(object(), TYPE_FLOAT)
    source_bindings = {_test_binding_id("a"): value}
    context = backend.BlenderIRLoweringContext(object(), source_bindings)
    assert tuple(field.name for field in fields(context)) == ("group", "runtime_bindings")
    assert context.runtime_bindings[_test_binding_id("a")] is value
    source_bindings[_test_binding_id("a")] = Value(object(), TYPE_VECTOR)
    assert context.runtime_bindings[_test_binding_id("a")] is value
    with pytest.raises(TypeError):
        context.runtime_bindings[_test_binding_id("b")] = value
    with pytest.raises(FrozenInstanceError):
        context.group = object()

    source = (Path(__file__).resolve().parents[2] / "blender_ir_lowering.py").read_text(encoding="utf-8")
    assert "comp." not in source
    assert "from .compiler import" not in source
    assert "import compiler" not in source
    assert "expression_compiler" not in source
    assert "semantic_analysis" not in source
    assert "import ast" not in source


def test_backend_materialization_state_is_per_lowering_call():
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    first = Value(object(), TYPE_FLOAT)
    second = Value(object(), TYPE_FLOAT)
    program = _lower("a", bindings={"a": TYPE_FLOAT})
    assert backend.lower_expression(_backend_context(backend, {_test_binding_id("a"): first}), program) is first
    assert backend.lower_expression(_backend_context(backend, {_test_binding_id("a"): second}), program) is second


def test_backend_binding_bridge_and_invariant_drift():
    from NodeForge.blender_ir_lowering import lower_expression
    from NodeForge.values import Value

    socket = object()
    value = Value(socket, TYPE_FLOAT)
    from NodeForge import blender_ir_lowering as backend

    context = _backend_context(backend, {_test_binding_id("a"): value}, group=None)
    program = _lower("a", bindings={"a": TYPE_FLOAT})
    assert lower_expression(context, program) is value

    drifted = _backend_context(backend, {_test_binding_id("a"): Value(socket, TYPE_VECTOR)}, group=None)
    with pytest.raises(CompileError, match="changed type"):
        lower_expression(drifted, program)
    missing = _backend_context(backend, {}, group=None)
    with pytest.raises(CompileError, match="no longer a runtime Value"):
        lower_expression(missing, program)


def test_backend_executes_program_order_and_applies_nonzero_base_depth(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    calls = []
    context = _backend_context(backend, _backend_bindings({"a": Value(object(), TYPE_FLOAT)}))

    def fake_value(group, value, x=0, y=0):
        calls.append(("value", value, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_math(group, operation, args, x=0, y=0):
        calls.append(("math", operation, x, y))
        return Value(object(), TYPE_FLOAT)

    monkeypatch.setattr(backend, "_value", fake_value)
    monkeypatch.setattr(backend, "_math", fake_math)

    result = backend.lower_expression(context, _lower("a * 2", bindings={"a": TYPE_FLOAT}), base_depth=3)
    assert result.typ == TYPE_FLOAT
    assert calls == [("value", 2, 960, -360), ("math", "MULTIPLY", 720, -270)]


def test_backend_missing_operand_is_controlled_internal_error():
    from NodeForge import blender_ir_lowering as backend

    missing = IRValue(99, TYPE_FLOAT)
    result = IRValue(0, TYPE_FLOAT)
    program = IRProgram((IRUnary(result, 0, "+", missing),), result)
    context = _backend_context(backend)
    with pytest.raises(CompileError, match="used before materialization"):
        backend.lower_expression(context, program)


def test_backend_unary_identity_and_current_node_policy_without_blender(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    calls = []
    bindings = {
        "a": Value(object(), TYPE_FLOAT),
        "v": Value(object(), TYPE_VECTOR),
        "flag": Value(object(), TYPE_BOOL),
    }
    context = _backend_context(backend, _backend_bindings(bindings))

    def fake_value(group, value, x=0, y=0):
        calls.append(("value", value, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_math(group, operation, args, x=0, y=0):
        calls.append(("math", operation, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_vector_math(group, operation, args, out_type=TYPE_VECTOR, x=0, y=0):
        calls.append(("vector_math", operation, x, y))
        return Value(object(), out_type)

    def fake_boolean_math(group, operation, args, x=0, y=0):
        calls.append(("boolean_math", operation, x, y))
        return Value(object(), TYPE_BOOL)

    monkeypatch.setattr(backend, "_value", fake_value)
    monkeypatch.setattr(backend, "_math", fake_math)
    monkeypatch.setattr(backend, "_vector_math", fake_vector_math)
    monkeypatch.setattr(backend, "_boolean_math", fake_boolean_math)

    plus = backend.lower_expression(context, _lower("+a", bindings={"a": TYPE_FLOAT}), base_depth=1)
    assert plus is bindings["a"]
    assert calls == []

    backend.lower_expression(context, _lower("-a", bindings={"a": TYPE_FLOAT}), base_depth=1)
    backend.lower_expression(context, _lower("-v", bindings={"v": TYPE_VECTOR}), base_depth=1)
    backend.lower_expression(context, _lower("not flag", bindings={"flag": TYPE_BOOL}), base_depth=1)
    assert ("value", 0.0, 240, -130) in calls
    assert ("math", "SUBTRACT", 240, -90) in calls
    assert ("value", -1.0, 240, -130) in calls
    assert ("vector_math", "SCALE", 240, -90) in calls
    assert ("boolean_math", "NOT", 240, -90) in calls


def test_backend_comparison_chain_executes_explicit_duplicate_operations(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    calls = []
    context = _backend_context(backend, _backend_bindings({
        "a": Value(object(), TYPE_FLOAT),
        "b": Value(object(), TYPE_FLOAT),
        "c": Value(object(), TYPE_FLOAT),
    }))

    def fake_value(group, value, x=0, y=0):
        calls.append(("value", value, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_math(group, operation, args, x=0, y=0):
        calls.append(("math", operation, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_compare(group, operation, left, right, x=0, y=0):
        calls.append(("compare", operation, x, y))
        return Value(object(), TYPE_BOOL)

    def fake_boolean_math(group, operation, args, x=0, y=0):
        calls.append(("boolean_math", operation, x, y))
        return Value(object(), TYPE_BOOL)

    monkeypatch.setattr(backend, "_value", fake_value)
    monkeypatch.setattr(backend, "_math", fake_math)
    monkeypatch.setattr(backend, "_compare", fake_compare)
    monkeypatch.setattr(backend, "_boolean_math", fake_boolean_math)

    program = _lower("a < b * 2 < c", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT})
    result = backend.lower_expression(context, program, base_depth=1)
    assert result.typ == TYPE_BOOL
    assert calls.count(("math", "MULTIPLY", 480, -180)) == 2
    assert calls.count(("compare", "LESS_THAN", 240, -90)) == 2
    assert calls.count(("boolean_math", "AND", 240, -90)) == 1


def test_backend_result_type_drift_is_controlled_internal_error(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    context = _backend_context(backend, _backend_bindings({"a": Value(object(), TYPE_FLOAT)}))

    def fake_value(group, value, x=0, y=0):
        return Value(object(), TYPE_FLOAT)

    def fake_math(group, operation, args, x=0, y=0):
        return Value(object(), TYPE_VECTOR)

    monkeypatch.setattr(backend, "_value", fake_value)
    monkeypatch.setattr(backend, "_math", fake_math)

    with pytest.raises(CompileError, match="expected type FLOAT, got VECTOR"):
        backend.lower_expression(context, _lower("a + 1", bindings={"a": TYPE_FLOAT}))


def test_backend_vector_conditional_component_and_boolean_primitives_without_blender(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    calls = []
    context = _backend_context(backend, _backend_bindings({
        "v": Value(object(), TYPE_VECTOR),
        "a": Value(object(), TYPE_FLOAT),
        "flag": Value(object(), TYPE_BOOL),
        "other": Value(object(), TYPE_FLOAT),
    }))

    def fake_value(group, value, x=0, y=0):
        calls.append(("value", value, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_math(group, operation, args, x=0, y=0):
        calls.append(("math", operation, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_vector_math(group, operation, args, out_type=TYPE_VECTOR, x=0, y=0):
        calls.append(("vector_math", operation, x, y))
        return Value(object(), out_type)

    def fake_boolean_math(group, operation, args, x=0, y=0):
        calls.append(("boolean_math", operation, x, y))
        return Value(object(), TYPE_BOOL)

    def fake_compare(group, operation, left, right, x=0, y=0):
        calls.append(("compare", operation, x, y))
        return Value(object(), TYPE_BOOL)

    def fake_switch(group, condition, false_value, true_value, x=0, y=0):
        calls.append(("switch", x, y))
        return Value(object(), true_value.typ)

    def fake_separate_xyz(group, value, component, x=0, y=0):
        calls.append(("component", component, x, y))
        return Value(object(), TYPE_FLOAT)

    monkeypatch.setattr(backend, "_value", fake_value)
    monkeypatch.setattr(backend, "_math", fake_math)
    monkeypatch.setattr(backend, "_vector_math", fake_vector_math)
    monkeypatch.setattr(backend, "_boolean_math", fake_boolean_math)
    monkeypatch.setattr(backend, "_compare", fake_compare)
    monkeypatch.setattr(backend, "_switch", fake_switch)
    monkeypatch.setattr(backend, "_separate_xyz", fake_separate_xyz)

    assert backend.lower_expression(context, _lower("v / a", bindings={"v": TYPE_VECTOR, "a": TYPE_FLOAT})).typ == TYPE_VECTOR
    assert ("math", "DIVIDE", 0, 0) in calls
    assert ("vector_math", "SCALE", 0, 0) in calls

    calls.clear()
    conditional = _lower(
        "a if flag else other",
        bindings={"a": TYPE_FLOAT, "flag": TYPE_BOOL, "other": TYPE_FLOAT},
    )
    assert backend.lower_expression(context, conditional).typ == TYPE_FLOAT
    assert calls == [("switch", 0, 0)]

    calls.clear()
    assert backend.lower_expression(context, _lower("v.z", bindings={"v": TYPE_VECTOR})).typ == TYPE_FLOAT
    assert calls == [("component", "z", 0, 0)]

    calls.clear()
    bool_program = _lower("flag and True", bindings={"flag": TYPE_BOOL})
    assert backend.lower_expression(context, bool_program).typ == TYPE_BOOL
    assert calls[-1] == ("boolean_math", "AND", 0, 0)

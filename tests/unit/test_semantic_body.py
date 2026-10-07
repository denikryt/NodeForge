"""Pure tests for compiler-owned Semantic Body IR."""

import ast
from types import MappingProxyType, SimpleNamespace

import pytest

from NodeForge.semantic.compile_time import CompileTimeSnapshot

from NodeForge.semantic.builtin_calls import INPUT_DECLARATION_BUILTIN_NAMES, IR_CAPABLE_BUILTIN_NAMES
from NodeForge.semantic.call_resolution import CallableEnvironment
from NodeForge.compiler_identities import BindingId, InputDeclarationId
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.semantic.runtime_bindings import RuntimeBindingSymbol
from NodeForge.semantic.values import StructuralArrayRef, StructuralRuntimeLeaf
from NodeForge.semantic.body import _runtime_if_invalidated_incoming_runtime_bindings, lower_basic_body
from NodeForge.semantic.ir import (
    IRAssign, IRArray, IRBindLeaves, IRBody, IRFinalExpression, IRIf,
    IRContextRead, IRContextWrite, IRDiscardExpression, IRInputDeclaration, IROutput, IRPanelDeclaration,
    IRBinary, IRLiteral, IRProgram, IRRepeat, IRValue,
)


def _callables(**overrides):
    data = dict(
        callable_builtins=frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES),
        local_functions={},
        imported_functions={},
    )
    data.update(overrides)
    return CallableEnvironment(**data)


def _stmts(source):
    return ast.parse(source, mode="exec").body


def _lower(
    source, *, bindings=None, constants=None, reserved=None, callables=None,
    geometry_mode=False, input_origins=None,
):
    return lower_basic_body(
        _stmts(source),
        initial_runtime_bindings=bindings or {},
        initial_compile_time=CompileTimeSnapshot(constants or {}),
        reserved_name_labels=reserved or {},
        callable_environment=callables or _callables(),
        owner_scope="scope",
        geometry_mode=geometry_mode,
        initial_interface_input_origins=input_origins or {},
    )


def _preprocessed_lower(source, *, bindings=None, callables=None):
    """Lower source through the real ordered preprocessing handoff."""
    from NodeForge.semantic.residualization import _preprocess_compile_time

    preprocessed = _preprocess_compile_time(_stmts(source))
    result = lower_basic_body(
        list(preprocessed.statements),
        initial_runtime_bindings=bindings or {},
        initial_compile_time=preprocessed.initial_compile_time,
        reserved_name_labels={},
        callable_environment=callables or _callables(),
        owner_scope="scope",
        compile_time_effects_before=preprocessed.effects_before,
        trailing_compile_time_effects=preprocessed.trailing_effects,
    )
    return preprocessed, result


def _binding(name, local_id, typ=NFType.FLOAT):
    return name, RuntimeBindingSymbol(BindingId("scope", local_id), typ)


def test_body_records_are_frozen_and_require_runtime_program_results():
    program = _lower("x = 1\nx").body.statements[0].value
    assign = IRAssign(BindingId("scope", 0), "x", program)
    body = IRBody((assign,))
    assert body.statements == (assign,)
    with pytest.raises(TypeError):
        IRAssign(BindingId("scope", 0), "x", object())
    with pytest.raises(ValueError):
        IROutput("", program)
    structural = IRProgram((), IRArray(()))
    with pytest.raises(TypeError):
        IROutput("Out", structural)
    with pytest.raises(TypeError):
        IRFinalExpression(structural)
    with pytest.raises(TypeError):
        IRBody((object(),))
    with pytest.raises(TypeError):
        IRInputDeclaration(BindingId("scope", 1), "x", "X", NFType.FLOAT, ast.Constant(value=1))


def test_straight_line_rebinding_reuses_binding_id_and_later_reads_it():
    result = _lower("x = a\nx = x + 1\noutput(x)", bindings=dict([_binding("a", 0)]))
    first, second, output = result.body.statements
    assert isinstance(first, IRAssign)
    assert isinstance(second, IRAssign)
    assert isinstance(output, IROutput)
    assert first.binding_id == second.binding_id == BindingId("scope", 1)
    assert output.name == "out"
    assert isinstance(second.value.result, IRValue)


def test_new_binding_ids_follow_active_seed_ids_deterministically():
    bindings = dict([_binding("a", 2), _binding("b", 5)])
    result = _lower("x = a\ny = b\noutput(y)", bindings=bindings)
    assigns = [statement for statement in result.body.statements if isinstance(statement, IRAssign)]
    assert [statement.binding_id.local_id for statement in assigns] == [6, 7]


def test_augassign_desugars_to_assign_and_keeps_existing_binding_identity():
    result = _lower("x = a\nx += 2\noutput(x)", bindings=dict([_binding("a", 0)]))
    assigns = [statement for statement in result.body.statements if isinstance(statement, IRAssign)]
    assert len(assigns) == 2
    assert assigns[0].binding_id == assigns[1].binding_id
    assert "IRAugAssign" not in __import__("NodeForge.semantic.ir", fromlist=["*"]).__dict__


def test_unknown_augassign_keeps_existing_public_diagnostic():
    with pytest.raises(CompileError, match="Unknown name for augmented assignment: x"):
        _lower("x += 1")


def test_explicit_outputs_are_uniqued_and_final_expression_is_explicit_ir():
    outputs = _lower("output(a)\noutput(a)", bindings=dict([_binding("a", 0)])).body.statements
    assert [statement.name for statement in outputs] == ["out", "out_2"]
    final = _lower("a", bindings=dict([_binding("a", 0)])).body.statements
    assert len(final) == 1 and isinstance(final[0], IRFinalExpression)


def test_const_state_is_detached_and_updated_without_mutating_input_mapping():
    initial = {"seed": 4}
    result = _lower("x = 2 + 3\noutput(x)", constants=initial)
    assert dict(result.final_compile_time.values)["x"] == 5
    assert initial == {"seed": 4}
    with pytest.raises(TypeError):
        result.final_compile_time.values["x"] = 9


@pytest.mark.parametrize(
    ("call", "typ"),
    [
        ('input_geometry("G")', NFType.GEOMETRY),
        ('input_float("F", default=1.5)', NFType.FLOAT),
        ('input_int("I", default=2)', NFType.INT),
        ('input_bool("B", default=True)', NFType.BOOL),
        ('input_vector("V", default=(1, 2, 3))', NFType.VECTOR),
        ('input_material("M")', NFType.MATERIAL),
        ('input_object("O")', NFType.OBJECT),
        ('input_string("S", default="x")', NFType.STRING),
        ('input_bundle("U")', NFType.BUNDLE),
    ],
)
def test_direct_input_declarations_are_target_owned_body_effects(call, typ):
    statement = _lower(f"x = {call}\noutput(x)").body.statements[0]
    assert isinstance(statement, IRInputDeclaration)
    assert statement.target_name == "x"
    assert statement.target_binding_id == BindingId("scope", 0)
    assert statement.typ is typ
    assert not hasattr(statement, "input_binding_id")


def test_input_default_can_consume_compile_time_fact_without_making_input_value_compile_time():
    result = _lower(
        'd = 1 / 2\n'
        'x = input_float("X", default=d)\n'
        'output(x)'
    )
    declaration = next(
        statement for statement in result.body.statements
        if isinstance(statement, IRInputDeclaration)
    )
    assert declaration.default == 0.5
    assert "x" not in result.final_compile_time.values
    assert "d" in result.final_compile_time.values


def test_duplicate_input_labels_create_distinct_target_bindings_and_preserve_defaults():
    result = _lower(
        'x = input_float("Scale", default=1.0)\n'
        'y = input_float("Scale", default=7.0)\n'
        'output("X", x)\noutput("Y", y)'
    )
    first, second = result.body.statements[:2]
    assert isinstance(first, IRInputDeclaration) and isinstance(second, IRInputDeclaration)
    assert first.display_name == second.display_name == "Scale"
    assert first.target_binding_id != second.target_binding_id
    assert (first.default, second.default) == (1.0, 7.0)


def test_input_declaration_ids_survive_unrelated_insertion_reorder_and_display_rename():
    """Durable input identity follows owner/target, not display label or global position."""
    original = _lower('first = input_float("Scale")\nsecond = input_float("Scale")').body.statements
    edited = _lower(
        'second = input_float("Renamed")\n'
        'inserted = input_float("Scale")\n'
        'first = input_float("Scale")'
    ).body.statements
    original_ids = {statement.target_name: statement.declaration_id for statement in original}
    edited_ids = {statement.target_name: statement.declaration_id for statement in edited}
    assert original_ids["first"] == edited_ids["first"] == InputDeclarationId("scope", "first", 0)
    assert original_ids["second"] == edited_ids["second"] == InputDeclarationId("scope", "second", 0)
    assert edited_ids["inserted"] == InputDeclarationId("scope", "inserted", 0)


def test_repeated_input_declarations_for_one_target_get_per_target_ordinals():
    """Rebinding one source target creates distinct durable interface declarations."""
    statements = _lower('x = input_float("A")\nx = input_float("B")\noutput(x)').body.statements
    declarations = [statement for statement in statements if isinstance(statement, IRInputDeclaration)]
    assert [statement.declaration_id for statement in declarations] == [
        InputDeclarationId("scope", "x", 0),
        InputDeclarationId("scope", "x", 1),
    ]


def test_display_label_never_aliases_existing_runtime_binding():
    result = _lower('a = 1.0\nx = input_float("a", default=7.0)\noutput(x)')
    first, second = result.body.statements[:2]
    assert isinstance(first, IRAssign)
    assert isinstance(second, IRInputDeclaration)
    assert first.binding_id != second.target_binding_id
    assert second.display_name == "a"
    assert second.default == 7.0


def test_same_label_different_input_types_are_independent():
    result = _lower('x = input_float("Same")\ny = input_vector("Same")\ny')
    first, second = result.body.statements[:2]
    assert first.display_name == second.display_name == "Same"
    assert first.typ is NFType.FLOAT
    assert second.typ is NFType.VECTOR


def test_late_unknown_call_error_does_not_mutate_input_semantic_state():
    bindings = dict([_binding("a", 0)])
    constants = {"k": 3}
    with pytest.raises(
        CompileError,
        match=r"Unsupported function: backend_helper",
    ):
        lower_basic_body(
            _stmts('x = a + 1\ny = backend_helper(x)\nx'),
            initial_runtime_bindings=MappingProxyType(bindings),
            initial_compile_time=CompileTimeSnapshot(MappingProxyType(constants)),
                reserved_name_labels={},
            callable_environment=_callables(),
            owner_scope="scope",
        )
    assert constants == {"k": 3}
    assert bindings == dict([_binding("a", 0)])

def test_unknown_call_error_does_not_publish_speculative_compile_time_changes():
    """A direct semantic error cannot publish speculative compile-time bindings."""
    from NodeForge.semantic.compile_time import CompileTimeState

    committed = CompileTimeState({"c": 2})
    with pytest.raises(
        CompileError,
        match=r"Unsupported function: backend_helper",
    ):
        lower_basic_body(
            _stmts("c = 3\nx = 1\ny = backend_helper()\nx"),
            initial_runtime_bindings={},
            initial_compile_time=committed.snapshot(),
                reserved_name_labels={},
            callable_environment=_callables(),
            owner_scope="scope",
        )
    assert committed.get("c") == 2
    assert not committed.contains("x")

def test_semantic_body_exception_does_not_mutate_committed_compile_time_owner():
    """Semantic errors after speculative constant updates cannot publish compile-time state."""
    from NodeForge.semantic.compile_time import CompileTimeState

    committed = CompileTimeState({"c": 2})
    with pytest.raises(CompileError, match="Unknown name: missing"):
        lower_basic_body(
            _stmts("c = 3\nx = missing + 1"),
            initial_runtime_bindings={},
            initial_compile_time=committed.snapshot(),
                reserved_name_labels={},
            callable_environment=_callables(),
            owner_scope="scope",
        )
    assert committed.get("c") == 2


def test_rejected_semantic_body_does_not_mutate_inherited_compile_time_list():
    """A rejected compile-time-list promotion leaves the inherited list unchanged."""
    shared = [1]
    snapshot = CompileTimeSnapshot({"items": shared})
    with pytest.raises(
        CompileError,
        match=r"append\(\) cannot promote a compile-time list to a runtime structural array",
    ):
        lower_basic_body(
            _stmts("x = 2\nitems.append(x)"),
            initial_runtime_bindings={},
            initial_compile_time=snapshot,
                reserved_name_labels={},
            callable_environment=_callables(),
            owner_scope="scope",
        )
    assert snapshot.values["items"] is shared
    assert shared == [1]

def test_migrated_arrays_and_loops_are_accepted_without_legacy_fallback():
    """Permanent body constructs stay accepted without relying on removed source-call failure routing."""
    array_result = _lower("items = [a]\nitems[0]", bindings=dict([_binding("a", 0)]))
    assert array_result.body.statements
    with pytest.raises(CompileError, match="Cannot unpack scalar result into 2 names"):
        _lower("a, b = pair", bindings=dict([_binding("pair", 0)]))
    _lower('builder = geometry_builder()\noutput("Geometry", builder.geometry)')
    with pytest.raises(CompileError, match="geometry_builder cannot escape script scope"):
        _lower("builder = geometry_builder()\nbuilder")
    _lower("x = grid(2, 2)\nx")
    _lower("if True:\n    x = 1\nelse:\n    x = 2")
    _lower("for i in [1]:\n    x = i")
    with pytest.raises(CompileError, match=r"panel\(\) expects exactly one positional list or tuple of group inputs"):
        _lower("panel('P', a)", bindings=dict([_binding("a", 0)]))
    _lower("store('x', a)", bindings=dict([_binding("a", 0)]), geometry_mode=True)
    _lower("set_position(position())", geometry_mode=True)
    with pytest.raises(CompileError, match=r"input_\*\(\) may only be used as the complete right-hand side of a simple assignment"):
        _lower("x = input_float('X') + 1")

@pytest.mark.parametrize(
    "source",
    [
        'input_float("X")',
        'x = input_float("X") + 1.0',
        'x = abs(input_float("X"))',
        'output(input_float("X"))',
        'x = [input_float("X")]',
        'x = (input_float("X"),)',
    ],
)
def test_input_builtins_are_declaration_only_and_never_fallback(source):
    """Input resource creation is valid only as one direct simple assignment declaration."""
    with pytest.raises(
        CompileError,
        match=r"input_\*\(\) may only be used as the complete right-hand side of a simple assignment",
    ):
        _lower(source)


def test_unknown_input_prefixed_name_uses_normal_callable_resolution():
    """An unknown input_-prefixed name is not classified as an input declaration."""
    with pytest.raises(CompileError, match="Unsupported function: input_not_registered"):
        _lower("input_not_registered()")


def test_statement_grammar_errors_are_direct_and_never_request_legacy_routing():
    """Known invalid statement placements receive permanent source diagnostics."""
    with pytest.raises(CompileError, match=r"output\(\) is only supported as a top-level call"):
        _lower("if True:\n    output(1)\nelse:\n    x = 1")
    with pytest.raises(
        CompileError,
        match=r"Only assignments, array append, for/if blocks, store\(\), set_position\(\) and output\(\) may appear before the final expression",
    ):
        _lower("position()\nx = 1")
    with pytest.raises(CompileError, match="Unsupported statement"):
        _lower("while True:\n    pass")
    with pytest.raises(CompileError, match=r"Object values support only the \.info\(\) method"):
        _lower("position().other()")


def test_reserved_target_validation_is_shared_language_semantics():
    with pytest.raises(CompileError, match="Cannot assign to foo: name is already registered as imported function"):
        _lower("foo = 1", reserved={"foo": "imported function"})


def test_group_semantic_preparation_publishes_entry_binding_identity_through_interface():
    """Implicit group inputs expose their final owner-scoped BindingId through the canonical interface."""
    from NodeForge.compiler_identities import GroupCompilationIdentity
    from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
    from NodeForge.semantic.group import analyze_group_source

    environment = ResolvedEnvironment(
        {name: ResolvedCatalog(name, {}) for name in ("functions", "examples", "local")},
    )
    identity = GroupCompilationIdentity(None, "ROOT/test-owner", "definition", "declaration")
    compilation = analyze_group_source(
        "output(x)\n",
        compilation_identity=identity,
        resolved_environment=environment,
    )
    entry_origins = [
        item.interface_origin
        for item in compilation.interface.inputs
        if isinstance(item.interface_origin, BindingId)
    ]
    assert entry_origins == [BindingId("ROOT/test-owner", 0)]
    assert entry_origins[0].owner_scope == compilation.identity.owner_scope

def test_basic_body_frontend_has_no_backend_dependencies_or_compiler_mutation():
    """The body semantic phase remains pure and cannot publish backend Values."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "semantic/body.py").read_text(encoding="utf-8")
    import io
    import tokenize
    code_only = "".join(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in {tokenize.COMMENT, tokenize.STRING}
    )
    for forbidden in (
        "import bpy",
        "from .nodes",
        "from .values",
        "from .compiler import",
        "from .statement_compiler",
        "from .runtime import",
        "from .geometry_builder",
        "comp.compile(",
        "comp.bind_runtime_value(",
        "compile_statement(",
    ):
        assert forbidden not in code_only


def test_body_lowerer_is_ast_free():
    """Blender body lowering consumes only IR and compiler identities, never source AST."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "blender_ir_lowering.py").read_text(encoding="utf-8")
    assert "import ast" not in source
    assert "ast.AST" not in source


def test_library_duplicate_display_labels_bind_positionally_and_reject_ambiguous_keywords():
    """Imported source-call binding uses final positions and rejects ambiguous display labels."""
    from NodeForge.semantic.callable_contracts import SourceCallableParameter, bind_imported_source_arguments

    parameters = (
        SourceCallableParameter(0, "a", "Scale", "scale", NFType.FLOAT, True),
        SourceCallableParameter(1, "b", "Scale", "scale", NFType.FLOAT, True),
        SourceCallableParameter(2, "unique", "Unique", "unique", NFType.FLOAT, True),
    )
    positional = bind_imported_source_arguments(parameters, (1.0, 7.0, 9.0), (), "dup")
    assert [(parameter.input_index, value) for parameter, value in positional] == [
        (0, 1.0), (1, 7.0), (2, 9.0)
    ]
    unique_keyword = bind_imported_source_arguments(
        parameters, (1.0, 7.0), (("Unique", 9.0),), "dup"
    )
    assert [(parameter.input_index, value) for parameter, value in unique_keyword] == [
        (0, 1.0), (1, 7.0), (2, 9.0)
    ]
    with pytest.raises(CompileError, match="ambiguous because multiple inputs share that label"):
        bind_imported_source_arguments(parameters, (), (("Scale", 1.0),), "dup")
    with pytest.raises(CompileError, match="unknown keyword argument 'Nope'"):
        bind_imported_source_arguments(parameters, (), (("Nope", 1.0),), "dup")
    with pytest.raises(CompileError, match="multiple values for input 'Unique'"):
        bind_imported_source_arguments(parameters, (1.0, 7.0, 9.0), (("Unique", 4.0),), "dup")

def test_generated_local_function_source_prologue_is_basic_body_eligible():
    """Generated helper input prologues enter IRBody rather than forcing legacy fallback."""
    from NodeForge.semantic.source_callables import analyze_local_return_shape, local_function_source

    fn = ast.parse("def f(x):\n    doubled = x * 2\n    return doubled\n").body[0]
    shape = analyze_local_return_shape(fn)
    source = local_function_source(fn, {"x": NFType.FLOAT}, return_shape=shape)
    result = _lower(source)

    assert result.body is not None
    assert isinstance(result.body.statements[0], IRInputDeclaration)
    assert result.body.statements[0].target_name == "x"
    assert result.body.statements[0].display_name == "x"

def test_imported_source_call_argument_slots_are_final_interface_positions():
    """Semantic imported-call binding carries physical input positions without socket-name lookup."""
    from NodeForge.semantic.callable_contracts import SourceCallableParameter, bind_imported_source_arguments

    parameters = (
        SourceCallableParameter(0, "left", "Scale", "scale", NFType.FLOAT, True),
        SourceCallableParameter(1, "right", "Scale", "scale", NFType.FLOAT, True),
        SourceCallableParameter(2, "unique", "Unique", "unique", NFType.FLOAT, True),
    )
    bound = bind_imported_source_arguments(parameters, ("left", "right"), (("Unique", "tail"),), "dup")
    assert [(parameter.input_index, value) for parameter, value in bound] == [
        (0, "left"), (1, "right"), (2, "tail")
    ]

def test_direct_input_removes_target_const_before_evaluating_display_metadata():
    """Input declaration normalization preserves legacy assignment const-state ordering."""
    with pytest.raises(CompileError, match="Expected a non-empty compile-time string for input_float\\(\\) name"):
        _lower('x = input_float(x)', constants={"x": "Scale"})


def test_fixed_tuple_assignment_projection_and_unpack_use_one_leaf_binding_ir():
    """IRBody stores fixed tuple leaves by BindingId instead of TupleValue."""
    bindings = dict([_binding("geo", 0, NFType.GEOMETRY), _binding("value", 1, NFType.FLOAT)])
    stored = _lower(
        "pair = capture_attribute(geo, value)\n"
        "output('Captured', pair[1])",
        bindings=bindings,
    )
    from NodeForge.semantic.ir import IRBindLeaves, IROutput, IRBinding

    assert isinstance(stored.body.statements[0], IRBindLeaves)
    assert isinstance(stored.body.statements[1], IROutput)
    assert isinstance(stored.body.statements[1].value.operations[-1], IRBinding)

    unpacked = _lower(
        "a, b = capture_attribute(geo, value)\n"
        "output('Captured', b)",
        bindings=bindings,
    )
    assert isinstance(unpacked.body.statements[0], IRBindLeaves)
    assert [binding.destination for binding in unpacked.body.statements[0].bindings] == [
        BindingId("scope", 2),
        BindingId("scope", 3),
    ]

    list_unpacked = _lower(
        "[a, b] = capture_attribute(geo, value)\n"
        "output('Captured', b)",
        bindings=bindings,
    )
    assert isinstance(list_unpacked.body.statements[0], IRBindLeaves)


def test_fixed_named_outputs_are_stored_and_selected_without_legacy_structural_state():
    """Raw named outputs remain frontend structure across later statements."""
    from NodeForge.semantic.ir import IRBindLeaves, IROutput, IRBinding

    result = _lower(
        'parts = node("ShaderNodeSeparateXYZ", outputs={"X": Float, "Y": Float})\n'
        'output("X", parts.X)\n'
        'output("Y", parts["Y"])'
    )
    assert isinstance(result.body.statements[0], IRBindLeaves)
    assert all(isinstance(statement, IROutput) for statement in result.body.statements[1:])
    assert all(isinstance(statement.value.operations[-1], IRBinding) for statement in result.body.statements[1:])


def test_named_outputs_do_not_unpack_positionally():
    with pytest.raises(CompileError, match="Cannot unpack scalar result into 2 names"):
        _lower('a, b = node("ShaderNodeSeparateXYZ", outputs={"X": Float, "Y": Float})')


def test_object_info_alias_configuration_is_frontend_owned_in_basic_body():
    """Aliases share one Object identity and explicit Object Info configuration."""
    from NodeForge.semantic.ir import IRAssign, IRDiscardExpression, IRObjectProperty, IROutput

    result = _lower(
        'obj = input_object("Source")\n'
        'alias = obj\n'
        'obj.info(as_instance=False)\n'
        'output("Geometry", alias.geometry)'
    )
    assert isinstance(result.body.statements[1], IRAssign)
    assert isinstance(result.body.statements[2], IRDiscardExpression)
    output = result.body.statements[3]
    assert isinstance(output, IROutput)
    prop = output.value.operations[-1]
    assert isinstance(prop, IRObjectProperty)
    assert prop.as_instance is False
    assert prop.transform_space == "ORIGINAL"


def test_object_identity_survives_unary_plus_and_array_projection_and_locks_info():
    """Zero-operation Object pass-throughs preserve the same semantic identity."""
    for alias_expr in ("+obj", "[obj][0]"):
        with pytest.raises(CompileError, match=r"Object\.info\(\) cannot be changed after Object Info has been resolved"):
            _lower(
                'obj = input_object("Source")\n'
                f'alias = {alias_expr}\n'
                'geometry = alias.geometry\n'
                'obj.info(as_instance=False)\n'
                'output("Geometry", geometry)'
            )


def test_object_reassignment_to_scalar_does_not_fail_on_stale_semantic_state():
    result = _lower(
        'obj = input_object("Source")\n'
        'obj = 1.0\n'
        'output("Value", obj)'
    )
    assert isinstance(result.body.statements[-1], IROutput)
    assert result.body.statements[-1].value.result.typ is NFType.FLOAT


def test_standalone_object_info_is_discard_not_automatic_output():
    from NodeForge.semantic.ir import IRDiscardExpression, IRFinalExpression

    result = _lower('obj = input_object("Source")\nobj.info(as_instance=False)')
    assert isinstance(result.body.statements[-1], IRDiscardExpression)
    assert not any(isinstance(statement, IRFinalExpression) for statement in result.body.statements)


def test_temporary_object_info_statement_is_accepted_before_later_output():
    from NodeForge.semantic.ir import IRDiscardExpression, IROutput

    result = _lower(
        'node("GeometryNodeObjectInfo", output="Object", typ=Object).info(as_instance=False)\n'
        'output("Value", 1.0)'
    )
    assert isinstance(result.body.statements[0], IRDiscardExpression)
    assert isinstance(result.body.statements[1], IROutput)


def test_structural_projection_binding_ids_are_stable_by_key_and_never_recycled():
    """Historical projection slots reuse only the same source/key and new keys stay monotonic."""
    from NodeForge.semantic.ir import IRBindLeaves

    bindings = dict([_binding("geo", 0, NFType.GEOMETRY), _binding("value", 1, NFType.FLOAT)])
    result = _lower(
        'parts = node("ShaderNodeSeparateXYZ", outputs={"X": Float, "Y": Float})\n'
        'parts = node("ShaderNodeSeparateXYZ", outputs={"X": Float, "Z": Float})\n'
        'parts = node("ShaderNodeSeparateXYZ", outputs={"X": Float, "Y": Float})\n'
        'output("Y", parts.Y)',
        bindings=bindings,
    )
    bind_records = [statement for statement in result.body.statements if isinstance(statement, IRBindLeaves)]
    first_ids = [binding.destination for binding in bind_records[0].bindings]
    second_ids = [binding.destination for binding in bind_records[1].bindings]
    third_ids = [binding.destination for binding in bind_records[2].bindings]
    assert first_ids[0] == second_ids[0] == third_ids[0]
    assert third_ids[1] == first_ids[1]
    assert second_ids[1] not in first_ids
    assert second_ids[1].local_id > max(binding.local_id for binding in first_ids)


def test_ordinary_and_structural_ownership_are_mutually_exclusive_with_historical_reuse():
    """Rebinding a name across scalar/structural forms keeps separate stable slot identities."""
    from NodeForge.semantic.ir import IRAssign, IRBindLeaves

    result = _lower(
        'x = 1.0\n'
        'x = node("ShaderNodeSeparateXYZ", outputs={"X": Float, "Y": Float})\n'
        'x = 2.0\n'
        'output("Value", x)'
    )
    first_assign = result.body.statements[0]
    structural = result.body.statements[1]
    second_assign = result.body.statements[2]
    assert isinstance(first_assign, IRAssign)
    assert isinstance(structural, IRBindLeaves)
    assert isinstance(second_assign, IRAssign)
    assert first_assign.binding_id == second_assign.binding_id
    assert first_assign.binding_id not in {binding.destination for binding in structural.bindings}


def test_tuple_unpack_clears_stale_compile_time_constants():
    bindings = dict([_binding("geo", 0, NFType.GEOMETRY), _binding("value", 1, NFType.FLOAT)])
    result = _lower(
        'a, b = capture_attribute(geo, value)\noutput("Value", b)',
        bindings=bindings,
        constants={"a": 10.0, "b": 20.0},
    )
    assert "a" not in result.final_compile_time.values
    assert "b" not in result.final_compile_time.values


def test_partial_object_info_configuration_merges_before_resolution():
    """Separate pre-resolution info calls update one shared Object state rather than resetting it."""
    from NodeForge.semantic.ir import IRObjectProperty

    result = _lower(
        'obj = input_object("Source")\n'
        'obj.info(transform_space="RELATIVE")\n'
        'obj.info(as_instance=False)\n'
        'output("Location", obj.location)'
    )
    prop = result.body.statements[-1].value.operations[-1]
    assert isinstance(prop, IRObjectProperty)
    assert prop.transform_space == "RELATIVE"
    assert prop.as_instance is False


def test_rebinding_one_alias_does_not_change_surviving_object_alias_identity():
    result = _lower(
        'obj = input_object("Source")\n'
        'alias = obj\n'
        'obj = 1.0\n'
        'output("Geometry", alias.geometry)'
    )
    assert result.body.statements[-1].value.result.typ is NFType.GEOMETRY


def test_structural_object_leaves_preserve_independent_provenance():
    """Each Object leaf keeps its own semantic identity through stored named outputs."""
    result = _lower(
        'parts = node("GeometryNodeObjectInfo", outputs={"A": Object, "B": Object})\n'
        'a = parts.A\n'
        'b = parts.B\n'
        'ga = a.geometry\n'
        'b.info(as_instance=False)\n'
        'output("Geometry", ga)'
    )
    from NodeForge.semantic.ir import IRDiscardExpression
    assert any(isinstance(statement, IRDiscardExpression) for statement in result.body.statements)


def test_stored_tuple_negative_index_selects_existing_leaf_binding():
    from NodeForge.semantic.ir import IRBindLeaves, IROutput, IRBinding

    bindings = dict([_binding("geo", 0, NFType.GEOMETRY), _binding("value", 1, NFType.FLOAT)])
    result = _lower(
        'pair = capture_attribute(geo, value)\noutput("Value", pair[-1])',
        bindings=bindings,
    )
    stored = result.body.statements[0]
    output = result.body.statements[1]
    assert isinstance(stored, IRBindLeaves) and isinstance(output, IROutput)
    selected = output.value.operations[-1]
    assert isinstance(selected, IRBinding)
    assert selected.binding_id == stored.bindings[-1].destination


def test_structural_array_alias_append_reassignment_and_nested_identity_are_frontend_owned():
    """Array names share identity on alias, observe append, and detach on reassignment."""
    bindings = dict([_binding("x", 0), _binding("y", 1), _binding("z", 2)])
    result = _lower(
        "a = [x]\n"
        "b = a\n"
        "b.append(y)\n"
        "c = [a]\n"
        "a = [z]\n"
        "old = c[0][1]\n"
        "new = a[0]\n"
        "output(old + new)",
        bindings=bindings,
    )
    arrays = result.final_structural_arrays
    assert arrays.bindings["a"] != arrays.bindings["b"]
    assert arrays.bindings["b"] == arrays.states[arrays.bindings["c"]].items[0].array_id
    assert len(arrays.states[arrays.bindings["b"]].items) == 2
    assert len(arrays.states[arrays.bindings["a"]].items) == 1
    assert result.body is not None


def test_nested_projection_alias_keeps_array_identity_for_later_append():
    """Assigning outer[0] preserves StructuralArrayId provenance through the expression result."""
    bindings = dict([_binding("x", 0), _binding("y", 1)])
    result = _lower(
        "inner = [x]\n"
        "outer = [inner]\n"
        "alias = outer[0]\n"
        "alias.append(y)\n"
        "picked = inner[1]\n"
        "output(picked)",
        bindings=bindings,
    )
    arrays = result.final_structural_arrays
    assert arrays.bindings["alias"] == arrays.bindings["inner"]
    assert len(arrays.states[arrays.bindings["inner"]].items) == 2


def test_array_assignment_emits_one_recursive_bind_leaves_and_empty_array_emits_none():
    """Persistent runtime leaves are published once while empty arrays remain pure frontend state."""
    result = _lower("empty = []\nitems = [x, [y]]\noutput(items[1][0])", bindings=dict([_binding("x", 0), _binding("y", 1)]))
    bind_records = [statement for statement in result.body.statements if isinstance(statement, IRBindLeaves)]
    assert len(bind_records) == 1
    assert [binding.source.typ for binding in bind_records[0].bindings] == [NFType.FLOAT, NFType.FLOAT]
    assert result.final_structural_arrays.states[result.final_structural_arrays.bindings["empty"]].items == ()


def test_structural_array_cycle_creation_is_rejected_before_statement_publication():
    """Direct and indirect recursive array mutation fail before producing accepted body state."""
    with pytest.raises(CompileError, match="recursive structural arrays are not supported"):
        _lower("a = []\na.append(a)")
    with pytest.raises(CompileError, match="recursive structural arrays are not supported"):
        _lower("a = []\nb = [a]\na.append(b)")


def test_structural_array_final_expression_and_malformed_append_keep_controlled_diagnostics():
    """Array-specific user diagnostics remain controlled and occur before backend effects."""
    with pytest.raises(CompileError, match=r"A final expression cannot be an array; use join\(array\) or index it"):
        _lower("items = []\nitems")
    with pytest.raises(CompileError, match=r"append must look like items\.append\(value\)"):
        _lower("items = []\nitems.append()")
    with pytest.raises(CompileError, match="x is not an array"):
        _lower("x = 1\nx.append(2)")


def test_ordinary_for_unrolls_source_compile_time_iterables_without_irrepeat():
    """List, tuple, and range loops become repeated body IR and never runtime Repeat IR."""
    cases = (
        "total = x\nfor i in [1, 2]:\n    total = total + i\noutput(total)",
        "total = x\nfor i in (1, 2):\n    total = total + i\noutput(total)",
        "total = x\nfor i in range(2):\n    total = total + i\noutput(total)",
    )
    for source in cases:
        result = _lower(source, bindings=dict([_binding("x", 0)]))
        assert result.body is not None
        assert not any(isinstance(statement, IRRepeat) for statement in result.body.statements)


def test_ordinary_for_unrolls_named_structural_array_and_restores_runtime_target():
    """Array iteration uses fresh lexical slots and restores the pre-loop runtime binding."""
    bindings = dict([_binding("x", 0), _binding("a", 1), _binding("b", 2)])
    result = _lower(
        "items = [a, b]\n"
        "for x in items:\n"
        "    y = x\n"
        "output(x)",
        bindings=bindings,
    )
    assert result.body is not None
    assert result.body.statements[-1].value.result.typ is NFType.FLOAT
    assert not any(isinstance(statement, IRRepeat) for statement in result.body.statements)


def test_ordinary_for_restores_outer_array_alias_after_loop_target_shadowing():
    """Loop-target shadowing of an array name does not replace the outer source binding."""
    bindings = dict([_binding("a", 0), _binding("b", 1)])
    result = _lower(
        "items = [a]\n"
        "loops = [[b]]\n"
        "for items in loops:\n"
        "    inside = items[0]\n"
        "after = items[0]\n"
        "output(after)",
        bindings=bindings,
    )
    arrays = result.final_structural_arrays
    assert len(arrays.states[arrays.bindings["items"]].items) == 1


def test_structural_array_loop_unpacks_runtime_items():
    """Runtime structural items support flat targets and restore shadowed bindings."""
    result = _lower(
        "pairs = [[a, b], [b, a]]\n"
        "for a, y in pairs:\n"
        "    total = a + y\n"
        "output(a)\n",
        bindings=dict([_binding("a", 0), _binding("b", 1)]),
    )
    assert result.body is not None
    output = result.body.statements[-1]
    assert output.value.operations[0].binding_id == BindingId("scope", 0)
    assert "a" not in result.final_compile_time.values
    assert "y" not in result.final_compile_time.values


def test_flat_loop_target_with_array_append_preserves_runtime_arithmetic():
    """Array mutation loops lower literal operands rather than folding the graph away."""
    result = _lower(
        "items = []\n"
        "for x, y in [[1.0, 2.0]]:\n"
        "    items.append(x + y)\n"
        "output(items[0])"
    )
    assert result.body is not None
    assert result.body.statements[-1].value.result.typ is NFType.FLOAT
    arrays = result.final_structural_arrays
    assert len(arrays.states[arrays.bindings["items"]].items) == 1


@pytest.mark.parametrize("item, message", [
    ("[a]", "expected 2 values, got 1"),
    ("a", "Cannot unpack scalar"),
])
def test_structural_array_loop_rejects_invalid_unpacking(item, message):
    with pytest.raises(CompileError, match=message):
        _lower(f"pairs = [{item}]\nfor x, y in pairs:\n    total = x + y\n",
               bindings=dict([_binding("a", 0)]))


def test_runtime_range_keeps_repeat_range_guidance():
    """Ordinary range with runtime arguments stays rejected with the established guidance."""
    with pytest.raises(CompileError, match=r"range\(\.\.\.\) requires compile-time integer arguments; use repeat_range"):
        _lower('count = input_int("Count")\nfor i in range(count):\n    x = i')


def test_read_only_array_and_nonmutating_unrolled_loop_are_allowed_inside_runtime_if():
    """Runtime branches may read structural arrays and unroll ordinary loops without merging arrays."""
    source = (
        'items = [x]\nflag = input_bool("Flag")\ny = x\n'
        'if flag:\n'
        '    for item in items:\n'
        '        y = item + 1\n'
        'else:\n'
        '    y = items[0] + 2\n'
        'output(y)'
    )
    result = _lower(source, bindings=dict([_binding("x", 0)]))
    branch = next(statement for statement in result.body.statements if isinstance(statement, IRIf))
    assert branch is not None
    assert not any(isinstance(statement, IRRepeat) for statement in branch.true_body.statements)


def test_runtime_control_flow_array_mutation_and_rebind_have_permanent_diagnostics():
    """Runtime structural-array mutation and rebinding fail directly instead of requesting fallback."""
    with pytest.raises(CompileError, match="Structural array append inside runtime control flow is not supported"):
        _lower(
            'items = []\nflag = input_bool("Flag")\n'
            'if flag:\n    items.append(1)\nelse:\n    items.append(2)'
        )

    with pytest.raises(CompileError, match="Structural array append inside runtime control flow is not supported"):
        _lower(
            'items = []\n'
            'if True:\n    items.append(1)\nelse:\n    items.append(2)'
        )
    with pytest.raises(CompileError, match="Structural array rebinding inside runtime control flow is not supported"):
        _lower(
            'items = []\nflag = input_bool("Flag")\n'
            'if flag:\n    items = [x]\nelse:\n    items = [x]',
            bindings=dict([_binding("x", 0)]),
        )

def test_array_heap_statement_failure_does_not_publish_partial_object_or_array_state():
    """Cycle/leaf validation fails before a statement can mutate visible body-owned semantic state."""
    # The absence of a returned BasicBodyCompilation is the public atomicity signal; this also
    # exercises Object-leaf planning before the cyclic graph is rejected.
    with pytest.raises(CompileError, match="recursive structural arrays are not supported"):
        _lower(
            "a = []\n"
            "nested = [a]\n"
            "a.append(nested)\n",
        )


def test_array_items_preserve_bundle_object_and_fixed_structural_shapes():
    """Arrays keep runtime Bundle/Object leaves and fixed tuple/named-output structure distinct."""
    bundle_result = _lower(
        "items = [bundle]\nselected = items[0]",
        bindings=dict([_binding("bundle", 0, NFType.BUNDLE)]),
    )
    bundle_state = bundle_result.final_structural_arrays.states[bundle_result.final_structural_arrays.bindings["items"]]
    assert isinstance(bundle_state.items[0], StructuralRuntimeLeaf)
    assert bundle_state.items[0].typ is NFType.BUNDLE

    object_result = _lower(
        'obj = input_object("Object")\nitems = [obj]\nalias = items[0]\nalias.info(as_instance=False)\noutput("Geometry", alias.geometry)'
    )
    assert object_result.body.statements[-1].value.result.typ is NFType.GEOMETRY

    fixed = _lower(
        "pair = capture_attribute(geo, value)\n"
        "items = [pair]\n"
        "output('Captured', items[0][1])",
        bindings=dict([_binding("geo", 0, NFType.GEOMETRY), _binding("value", 1, NFType.FLOAT)]),
    )
    assert fixed.body is not None

    named = _lower(
        'parts = node("ShaderNodeSeparateXYZ", outputs={"X": Float, "Y": Float})\n'
        'items = [parts]\n'
        'output("Y", items[0].Y)'
    )
    assert named.body is not None


def test_loop_target_restoration_preserves_object_provenance():
    """Shadowing an Object source name during unrolling restores its ObjectSemanticId mapping."""
    result = _lower(
        'obj = input_object("Object")\n'
        'for obj in [1, 2]:\n'
        '    temp = obj\n'
        'output("Geometry", obj.geometry)'
    )
    assert result.body.statements[-1].value.result.typ is NFType.GEOMETRY


def test_semantic_structural_append_preserves_legacy_ignored_keyword_compatibility():
    """Body-owned array append ignores keywords just as the v0.51.3 legacy append branch did."""
    result = _lower(
        "items = [x]\nitems.append(y, ignored=missing)\noutput(items[1])",
        bindings=dict([_binding("x", 0), _binding("y", 1)]),
    )
    assert result.body is not None
    state = result.final_structural_arrays.states[result.final_structural_arrays.bindings["items"]]
    assert len(state.items) == 2


def test_nonempty_compile_time_list_dynamic_append_has_permanent_promotion_error():
    """A folded compile-time list cannot be silently promoted to a runtime structural array."""
    with pytest.raises(
        CompileError,
        match=r"append\(\) cannot promote a compile-time list to a runtime structural array",
    ):
        _lower(
            "items.append(x)",
            constants={"items": [1]},
            bindings=dict([_binding("x", 0)]),
        )

def test_ordinary_for_rejects_generic_python_iteration_protocol():
    """Semantic ordinary-for accepts only the explicitly supported compile-time sequence categories."""
    class CustomIterable:
        def __iter__(self):
            return iter((1, 2, 3))

    with pytest.raises(
        CompileError,
        match=r"for loop requires a compile-time iterable, an array, or repeat_range",
    ):
        _lower(
            "for item in custom:\n    x = item\n",
            constants={"custom": CustomIterable()},
        )


def test_contextual_store_and_statement_set_position_lower_to_context_call_write_ir():
    """Contextual geometry statements reuse typed Call IR and never add statement-specific IR."""
    for source, target in (
        ("store('a', 1.0)", "store_named_attribute"),
        ("set_position(position())", "set_position"),
    ):
        result = _lower(source, geometry_mode=True)
        statement = result.body.statements[0]
        assert isinstance(statement, IRDiscardExpression)
        operations = statement.value.operations
        assert any(isinstance(op, IRContextRead) for op in operations)
        assert any(getattr(getattr(op, "target", None), "name", None) == target for op in operations)
        assert any(isinstance(op, IRContextWrite) for op in operations)
    import NodeForge.semantic.ir as semantic_ir
    assert not hasattr(semantic_ir, "IRStore")
    assert not hasattr(semantic_ir, "IRSetPosition")


def test_grid_context_is_available_in_traversal_order_across_runtime_if():
    """The one attempt-owned availability cursor advances true -> false -> post-if."""
    true_to_false = _lower(
        'flag = input_bool("Flag")\n'
        'if flag:\n    geo = grid(2, 2)\n    result = 1\n'
        'else:\n    uv = grid_uv()\n    result = 2\n'
        'output(result)'
    )
    assert any(isinstance(stmt, IRIf) for stmt in true_to_false.body.statements)

    false_to_after = _lower(
        'flag = input_bool("Flag")\n'
        'if flag:\n    result = 1\n'
        'else:\n    geo = grid(2, 2)\n    result = 2\n'
        'uv = grid_uv()\noutput(uv)'
    )
    assert false_to_after.body is not None


def test_grid_in_runtime_if_condition_makes_uv_available_in_true_branch():
    result = _lower(
        'flag = input_bool("Flag")\n'
        'if (grid(2, 2), flag)[1]:\n    uv = grid_uv()\n    result = 1\n'
        'else:\n    result = 2\n'
        'output(result)'
    )
    assert any(isinstance(stmt, IRIf) for stmt in result.body.statements)


def test_grid_context_from_repeat_body_is_available_after_repeat_without_repeat_state():
    result = _lower(
        'x = 0\nfor i in repeat_range(1):\n    x = x + 1\n    geo = grid(2, 2)\n'
        'uv = grid_uv()\noutput(uv)'
    )
    repeat = next(stmt for stmt in result.body.statements if isinstance(stmt, IRRepeat))
    assert all(getattr(state, "source_name", None) != "GRID_UV" for state in repeat.states)


def test_panel_frontend_provenance_preserves_alias_and_rejects_nonphysical_results():
    x_id = BindingId("scope", 0)
    flag_id = BindingId("scope", 1)
    bindings = {
        "x": RuntimeBindingSymbol(x_id, NFType.FLOAT),
        "flag": RuntimeBindingSymbol(flag_id, NFType.BOOL),
    }
    origins = {x_id: x_id}

    alias = _lower('y = x\npanel([y], name="P")', bindings=bindings, input_origins=origins)
    panel = alias.body.statements[-1]
    assert isinstance(panel, IRPanelDeclaration)
    assert panel.member_origins == (x_id,)

    with pytest.raises(CompileError, match=r"panel\(\) duplicate input: y"):
        _lower('y = x\npanel([x, y], name="P")', bindings=bindings, input_origins=origins)
    with pytest.raises(CompileError, match=r"panel\(\) item y is not a group input"):
        _lower('y = x + 1\npanel([y], name="P")', bindings=bindings, input_origins=origins)
    with pytest.raises(CompileError, match=r"panel\(\) item y is not a group input"):
        _lower(
            'if flag:\n    y = x\nelse:\n    y = x\npanel([y], name="P")',
            bindings=bindings,
            input_origins=origins,
        )


def test_panel_omitted_collapsed_is_explicit_false_and_invalid_explicit_value_is_rejected():
    """Panel default ownership stays in body semantics rather than the interface helper."""
    result = _lower('x = input_float("X")\npanel([x], name="P")')
    panel = result.body.statements[-1]
    assert isinstance(panel, IRPanelDeclaration)
    assert panel.collapsed is False

    with pytest.raises(CompileError, match=r"panel\(\) collapsed= must be a compile-time bool"):
        _lower('x = input_float("X")\npanel([x], name="P", collapsed=1)')


def test_panel_explicit_input_uses_declaration_origin_and_rebinding_clears_stale_origin():
    accepted = _lower('x = input_float("X")\npanel([x], name="P")')
    assert isinstance(accepted.body.statements[-1], IRPanelDeclaration)
    with pytest.raises(CompileError, match=r"panel\(\) item x is not a group input"):
        _lower('x = input_float("X")\nx = x + 1\npanel([x], name="P")')
    repeated = _lower('x = input_float("A")\nx = input_float("B")\npanel([x], name="P")')
    assert isinstance(repeated.body.statements[-1], IRPanelDeclaration)


def test_panel_frontend_rejects_duplicate_name_membership_and_empty_name():
    x_id = BindingId("scope", 0)
    y_id = BindingId("scope", 1)
    bindings = {
        "x": RuntimeBindingSymbol(x_id, NFType.FLOAT),
        "y": RuntimeBindingSymbol(y_id, NFType.FLOAT),
    }
    origins = {x_id: x_id, y_id: y_id}
    with pytest.raises(CompileError, match="duplicate panel name"):
        _lower('panel([x], name="P")\npanel([y], name="P")', bindings=bindings, input_origins=origins)
    with pytest.raises(CompileError, match="already belongs to panel"):
        _lower('panel([x], name="P")\npanel([x], name="Q")', bindings=bindings, input_origins=origins)
    with pytest.raises(CompileError, match=r"Expected a non-empty compile-time string for panel\(\) name"):
        _lower('panel([x], name="")', bindings=bindings, input_origins=origins)


def test_panel_assignment_copies_current_source_origin_to_existing_target():
    x_id = BindingId("scope", 0)
    y_id = BindingId("scope", 1)
    bindings = {
        "x": RuntimeBindingSymbol(x_id, NFType.FLOAT),
        "y": RuntimeBindingSymbol(y_id, NFType.FLOAT),
    }
    origins = {x_id: x_id, y_id: y_id}
    result = _lower('x = y\npanel([x], name="P")', bindings=bindings, input_origins=origins)
    panel = result.body.statements[-1]
    assert isinstance(panel, IRPanelDeclaration)
    assert panel.member_origins == (y_id,)


def test_panel_repeat_publication_clears_input_origin_even_for_identity_assignment():
    x_id = BindingId("scope", 0)
    bindings = {"x": RuntimeBindingSymbol(x_id, NFType.FLOAT)}
    with pytest.raises(CompileError, match=r"panel\(\) item x is not a group input"):
        _lower(
            'for i in repeat_range(1):\n    x = x\npanel([x], name="P")',
            bindings=bindings,
            input_origins={x_id: x_id},
        )


def test_same_expression_grid_then_grid_uv_uses_ordered_context_availability():
    result = _lower('uv = (grid(2, 2), grid_uv())[1]\noutput(uv)')
    assign = result.body.statements[0]
    operations = assign.value.operations
    write_index = next(i for i, op in enumerate(operations) if isinstance(op, IRContextWrite))
    read_index = next(i for i, op in enumerate(operations) if isinstance(op, IRContextRead))
    assert write_index < read_index
    assert not any(type(op).__name__ == "IRLiteral" for op in operations[:write_index])


def test_literal_false_still_analyzes_true_branch_semantic_error():
    """Both ordinary-if branches are authoritative even when the condition is a literal."""
    with pytest.raises(CompileError, match="not expects Bool"):
        _lower(
            'x = 0.0\n'
            'if False:\n'
            '    x = not 1\n'
            'else:\n'
            '    x = 2.0\n'
            'output(x)'
        )


def test_literal_true_still_analyzes_false_branch_semantic_error():
    """A valid true branch does not suppress semantic validation of the false runtime branch."""
    with pytest.raises(CompileError, match="not expects Bool"):
        _lower(
            'x = 0.0\n'
            'if True:\n'
            '    x = 2.0\n'
            'else:\n'
            '    x = not 1\n'
            'output(x)'
        )


def test_literal_false_does_not_bypass_unknown_call_diagnostic():
    """Both literal-condition branches expose ordinary semantic errors."""
    callables = _callables()
    with pytest.raises(
        CompileError,
        match=r"Unsupported function: backend_helper",
    ):
        _lower(
            'if False:\n'
            '    x = backend_helper()\n'
            'else:\n'
            '    x = 1.0',
            callables=callables,
        )


def test_grid_type_diagnostics_preserve_width_height_legacy_wording():
    """Grid rejects invalid arguments with the same per-socket diagnostics as the backend contract."""
    with pytest.raises(CompileError) as exc_info:
        _lower("grid(True, 2)")
    assert str(exc_info.value) == "grid() width expects Float/Int"

    with pytest.raises(CompileError) as exc_info:
        _lower("grid(2, bad)", bindings=dict([_binding("bad", 0, NFType.VECTOR)]))
    assert str(exc_info.value) == "grid() height expects Float/Int"


def test_compile_time_loop_restores_physical_input_origin_after_shadowing():
    """A loop target shadowing a physical input restores its original panel provenance."""
    result = _lower(
        'x = input_float("X")\nitems = [x]\nfor x in items:\n    y = x\npanel([x], name="P")\noutput("X", x)'
    )
    panels = [statement for statement in result.body.statements if isinstance(statement, IRPanelDeclaration)]
    assert len(panels) == 1


def test_compile_time_loop_restores_alias_input_origin_after_shadowing():
    """An alias used as loop target regains the physical input origin it had before the loop."""
    result = _lower(
        'x = input_float("X")\nalias = x\nitems = [x]\nfor alias in items:\n    y = alias\npanel([alias], name="P")'
    )
    assert isinstance(result.body.statements[-1], IRPanelDeclaration)


def test_compile_time_loop_does_not_leak_temporary_input_origin_to_prior_noninput_target():
    """Temporary loop-item provenance is cleared when a non-input target is restored."""
    with pytest.raises(CompileError) as exc_info:
        _lower(
            'x = input_float("X")\ny = 1.0\nitems = [x]\nfor y in items:\n    z = y\npanel([y], name="P")'
        )
    assert str(exc_info.value) == "panel() item y is not a group input"


def test_late_compile_time_owned_assignment_uses_runtime_if_joined_fact_without_runtime_range_ir():
    """A retained CT-owned root can finish after runtime-if CT knowledge is soundly recovered."""
    from NodeForge.semantic.residualization import _preprocess_compile_time

    source = (
        'n = 2\n'
        'flag = input_bool("Flag")\n'
        'if flag:\n'
        '    n = 2\n'
        'else:\n'
        '    n = 2\n'
        'xs = range(n)\n'
        'for i in xs:\n'
        '    y = i\n'
        'output(y)\n'
    )
    preprocessed = _preprocess_compile_time(_stmts(source))
    retained = list(preprocessed.statements)
    assert any(isinstance(stmt, ast.If) for stmt in retained)
    assert any(
        isinstance(stmt, ast.Assign)
        and isinstance(stmt.targets[0], ast.Name)
        and stmt.targets[0].id == "xs"
        for stmt in retained
    )
    assert any(isinstance(stmt, ast.For) for stmt in retained)
    assert "n" not in preprocessed.final_compile_time.values

    result = lower_basic_body(
        retained,
        initial_runtime_bindings={},
        initial_compile_time=preprocessed.initial_compile_time,
        reserved_name_labels={},
        callable_environment=_callables(),
        owner_scope="scope",
        compile_time_effects_before=preprocessed.effects_before,
        trailing_compile_time_effects=preprocessed.trailing_effects,
    )

    assert result.body is not None
    assert result.final_compile_time.values["n"] == 2
    assert result.final_compile_time.values["xs"] == [0, 1]
    assert not any(
        isinstance(statement, IRAssign) and statement.source_name == "xs"
        for statement in result.body.statements
    )
    assert any(isinstance(statement, IRIf) for statement in result.body.statements)


@pytest.mark.parametrize(
    "branch_assignment, late_assignment, expected",
    [
        ("n = 2", "x = len([n, n])", 2),
        ("n = 2", "x = sum([n, n])", 4),
        ("n = 2", "x = [n, n]", [2, 2]),
        ("n = 2", "x = (n, n)", (2, 2)),
        ('label = "ok"', 'x = f"{label}"', "ok"),
    ],
)
def test_late_compile_time_owned_root_family_consumes_after_runtime_if_join(
    branch_assignment, late_assignment, expected
):
    """Shared CT-owned roots may finish after structured control flow recovers their inputs."""
    from NodeForge.semantic.residualization import _preprocess_compile_time

    source = (
        'flag = input_bool("Flag")\n'
        'if flag:\n'
        f'    {branch_assignment}\n'
        'else:\n'
        f'    {branch_assignment}\n'
        f'{late_assignment}\n'
    )
    preprocessed = _preprocess_compile_time(_stmts(source))
    retained = list(preprocessed.statements)
    assert "n" not in preprocessed.final_compile_time.values
    assert "label" not in preprocessed.final_compile_time.values
    assert any(
        isinstance(stmt, ast.Assign)
        and isinstance(stmt.targets[0], ast.Name)
        and stmt.targets[0].id == "x"
        for stmt in retained
    )

    result = lower_basic_body(
        retained,
        initial_runtime_bindings={},
        initial_compile_time=preprocessed.initial_compile_time,
        reserved_name_labels={},
        callable_environment=_callables(),
        owner_scope="scope",
        compile_time_effects_before=preprocessed.effects_before,
        trailing_compile_time_effects=preprocessed.trailing_effects,
    )

    assert result.final_compile_time.values["x"] == expected
    assert not any(
        isinstance(statement, IRAssign) and statement.source_name == "x"
        for statement in result.body.statements
    )


def test_ordered_preprocessing_handoff_does_not_leak_future_fact_backwards():
    """A later erased assignment cannot change an earlier residual RHS."""
    preprocessed, result = _preprocessed_lower(
        "a = 1.0\n"
        "b = a + 1.0\n"
        "a = 10.0\n"
        "output(b)\n"
    )

    assert [ast.unparse(stmt) for stmt in preprocessed.statements] == ["b = a + 1.0", "output(b)"]
    assign = next(stmt for stmt in result.body.statements if isinstance(stmt, IRAssign) and stmt.source_name == "b")
    literal_values = [op.value for op in assign.value.operations if isinstance(op, IRLiteral)]
    assert literal_values == [1.0, 1.0]
    assert result.final_compile_time.values["a"] == 10.0
    assert result.final_compile_time.values["b"] == 2.0


def test_ordered_preprocessing_handoff_does_not_publish_future_only_name_earlier():
    """A future erased binding remains unavailable at an earlier residual source position."""
    from NodeForge.semantic.residualization import _preprocess_compile_time

    preprocessed = _preprocess_compile_time(_stmts("y = x + 1.0\nx = 2.0\noutput(y)\n"))
    with pytest.raises(CompileError, match="Unknown name: x"):
        lower_basic_body(
            list(preprocessed.statements),
            initial_runtime_bindings={},
            initial_compile_time=preprocessed.initial_compile_time,
                reserved_name_labels={},
            callable_environment=_callables(),
            owner_scope="scope",
            compile_time_effects_before=preprocessed.effects_before,
            trailing_compile_time_effects=preprocessed.trailing_effects,
        )


def test_ordinary_assignment_runtime_rhs_reads_pre_assignment_compile_time_value():
    """Self-reference analyzes against the old target fact before publishing the new one."""
    _preprocessed, result = _preprocessed_lower("x = 1.0\nx = x + 1.0\noutput(x)\n")

    assign = next(stmt for stmt in result.body.statements if isinstance(stmt, IRAssign) and stmt.source_name == "x")
    literal_values = [op.value for op in assign.value.operations if isinstance(op, IRLiteral)]
    assert literal_values == [1.0, 1.0]
    assert result.final_compile_time.values["x"] == 2.0


def test_runtime_if_uses_erased_compile_time_seed_without_residual_seed_assignment():
    """Runtime branches materialize the source-ordered CT seed after seed-prescan removal."""
    preprocessed, result = _preprocessed_lower(
        'x = 0.0\n'
        'flag = input_bool("Flag")\n'
        'if flag:\n'
        '    x = x + 1.0\n'
        'else:\n'
        '    x = x + 2.0\n'
        'output(x)\n'
    )

    assert not any(
        isinstance(stmt, ast.Assign)
        and isinstance(stmt.targets[0], ast.Name)
        and stmt.targets[0].id == "x"
        and isinstance(stmt.value, ast.Constant)
        for stmt in preprocessed.statements
    )
    runtime_if = next(stmt for stmt in result.body.statements if isinstance(stmt, IRIf))
    true_assign = next(stmt for stmt in runtime_if.true_body.statements if isinstance(stmt, IRAssign))
    false_assign = next(stmt for stmt in runtime_if.false_body.statements if isinstance(stmt, IRAssign))
    assert [op.value for op in true_assign.value.operations if isinstance(op, IRLiteral)] == [0.0, 1.0]
    assert [op.value for op in false_assign.value.operations if isinstance(op, IRLiteral)] == [0.0, 2.0]


def test_compile_time_owned_seed_is_visible_to_runtime_if_branches_in_source_order():
    """An erased len() seed reaches both runtime branches without prepublishing branch targets."""
    _preprocessed, result = _preprocessed_lower(
        'x = len([1, 2, 3])\n'
        'flag = input_bool("Flag")\n'
        'if flag:\n'
        '    x = x + 1.0\n'
        'else:\n'
        '    x = x + 2.0\n'
        'output(x)\n'
    )

    runtime_if = next(stmt for stmt in result.body.statements if isinstance(stmt, IRIf))
    true_assign = next(stmt for stmt in runtime_if.true_body.statements if isinstance(stmt, IRAssign))
    false_assign = next(stmt for stmt in runtime_if.false_body.statements if isinstance(stmt, IRAssign))
    assert [op.value for op in true_assign.value.operations if isinstance(op, IRLiteral)] == [3, 1.0]
    assert [op.value for op in false_assign.value.operations if isinstance(op, IRLiteral)] == [3, 2.0]


def test_mutable_compile_time_effects_do_not_mutate_earlier_input_default():
    """A later erased append cannot change an earlier input declaration default."""
    _preprocessed, result = _preprocessed_lower(
        'items = [1]\n'
        'x = input_float("X", default=len(items))\n'
        'items.append(2)\n'
        'output(x)\n'
    )

    declaration = next(stmt for stmt in result.body.statements if isinstance(stmt, IRInputDeclaration))
    assert declaration.default == 1.0
    assert result.final_compile_time.values["items"] == [1, 2]


def test_literal_ordinary_if_is_retained_and_uses_conservative_runtime_join():
    """Literal conditions remain residual and do not publish branch-specific compile-time facts."""
    preprocessed, result = _preprocessed_lower(
        'x = 1.0\n'
        'if True:\n'
        '    x = 2.0\n'
        'else:\n'
        '    x = 3.0\n'
        'output(x)\n'
    )

    assert any(isinstance(stmt, ast.If) for stmt in preprocessed.statements)
    assert any(isinstance(stmt, IRIf) for stmt in result.body.statements)
    assert "x" not in result.final_compile_time.values


def test_erased_compile_time_for_replay_preserves_mutable_iteration_item_alias():
    """Loop-body mutation targets the exact object yielded by the replay-time iterable."""
    _preprocessed, result = _preprocessed_lower(
        'items = [[1]]\n'
        'for item in items:\n'
        '    item.append(2)\n'
        'x = input_int("X", default=len(items[0]))\n'
        'output(x)\n'
    )

    declaration = next(stmt for stmt in result.body.statements if isinstance(stmt, IRInputDeclaration))
    assert declaration.default == 2
    assert result.final_compile_time.values["items"] == [[1, 2]]


def test_erased_compile_time_for_replay_restores_exact_old_mutable_target_alias():
    """Loop target restoration keeps the exact pre-loop object rather than a copied value."""
    _preprocessed, result = _preprocessed_lower(
        'item = [1]\n'
        'holder = [item]\n'
        'for item in range(1):\n'
        '    scratch = 0\n'
        'item.append(2)\n'
        'x = input_int("X", default=len(holder[0]))\n'
        'output(x)\n'
    )

    declaration = next(stmt for stmt in result.body.statements if isinstance(stmt, IRInputDeclaration))
    assert declaration.default == 2
    assert result.final_compile_time.values["item"] is result.final_compile_time.values["holder"][0]


def test_erased_compile_time_for_replay_preserves_duplicate_aliases_in_iterable():
    """Repeated references in the iterable replay as repeated references to the same object."""
    _preprocessed, result = _preprocessed_lower(
        'a = [1]\n'
        'items = [a, a]\n'
        'for item in items:\n'
        '    item.append(2)\n'
        'x = input_int("X", default=len(a))\n'
        'output(x)\n'
    )

    declaration = next(stmt for stmt in result.body.statements if isinstance(stmt, IRInputDeclaration))
    assert declaration.default == 3
    assert result.final_compile_time.values["items"][0] is result.final_compile_time.values["items"][1]


def test_nested_erased_compile_time_for_replay_preserves_alias_identity():
    """Nested scoped loop effects preserve aliases without a separate replay stack."""
    _preprocessed, result = _preprocessed_lower(
        'items = [[1]]\n'
        'groups = [items]\n'
        'for group in groups:\n'
        '    for item in group:\n'
        '        item.append(2)\n'
        'x = input_int("X", default=len(items[0]))\n'
        'output(x)\n'
    )

    declaration = next(stmt for stmt in result.body.statements if isinstance(stmt, IRInputDeclaration))
    assert declaration.default == 2
    assert result.final_compile_time.values["groups"][0] is result.final_compile_time.values["items"]


def test_zero_iteration_erased_compile_time_for_restores_old_target_identity():
    """A zero-iteration loop still restores the exact pre-loop mutable target object."""
    _preprocessed, result = _preprocessed_lower(
        'item = [1]\n'
        'holder = [item]\n'
        'for item in range(0):\n'
        '    scratch = 0\n'
        'item.append(2)\n'
        'x = input_int("X", default=len(holder[0]))\n'
        'output(x)\n'
    )

    declaration = next(stmt for stmt in result.body.statements if isinstance(stmt, IRInputDeclaration))
    assert declaration.default == 2
    assert result.final_compile_time.values["item"] is result.final_compile_time.values["holder"][0]


def test_compile_time_unrolled_for_keeps_nested_literal_ordinary_if_runtime():
    """Unrolling a structural for does not restore implicit static branching for nested ordinary if."""
    _preprocessed, result = _preprocessed_lower(
        'x = input_float("X")\n'
        'for i in [1, 2]:\n'
        '    if True:\n'
        '        x = x + 1\n'
        '    else:\n'
        '        x = x + 2\n'
        'output(x)\n'
    )

    branches = [stmt for stmt in result.body.statements if isinstance(stmt, IRIf)]
    assert len(branches) == 2
    assert all(branch.condition.result.typ is NFType.BOOL for branch in branches)
    assert all(branch.true_body.statements and branch.false_body.statements for branch in branches)


def test_compile_time_for_effect_replay_rejects_fewer_iterations_than_preprocessing():
    """Composite loop replay fails closed when the replay iterable ends too early."""
    from NodeForge.semantic.residualization import CompileTimeForEffect

    effect = CompileTimeForEffect(
        target="item",
        iterable_expression=ast.parse("[1]", mode="eval").body,
        iteration_effects=((), ()),
    )
    with pytest.raises(CompileError, match="fewer iterations than preprocessing"):
        lower_basic_body(
            [],
            initial_runtime_bindings={},
            initial_compile_time=CompileTimeSnapshot({}),
                reserved_name_labels={},
            callable_environment=_callables(),
            owner_scope="scope",
            trailing_compile_time_effects=(effect,),
        )


def test_compile_time_for_effect_replay_rejects_more_iterations_than_preprocessing():
    """Composite loop replay fails closed when the replay iterable yields an extra item."""
    from NodeForge.semantic.residualization import CompileTimeForEffect

    effect = CompileTimeForEffect(
        target="item",
        iterable_expression=ast.parse("[1, 2]", mode="eval").body,
        iteration_effects=((),),
    )
    with pytest.raises(CompileError, match="more iterations than preprocessing"):
        lower_basic_body(
            [],
            initial_runtime_bindings={},
            initial_compile_time=CompileTimeSnapshot({}),
                reserved_name_labels={},
            callable_environment=_callables(),
            owner_scope="scope",
            trailing_compile_time_effects=(effect,),
        )


def test_runtime_control_flow_compile_time_owned_rebind_replaces_prior_runtime_owner_for_static_consumers():
    """A compile-time-owned source rebind owns the name instead of retaining the old runtime slot."""
    result = _lower(
        'x = input_int("X", default=1)\n'
        'x = [1, 2]\n'
        'for item in x:\n    y = item\n'
    )
    assert result.final_compile_time.values["x"] == [1, 2]


def test_runtime_control_flow_compile_time_owned_rebind_cannot_fall_back_to_prior_runtime_owner():
    """A runtime consumer after a compile-time-owned rebind cannot read the stale pre-rebind socket."""
    with pytest.raises(CompileError, match=r"output\(\) cannot output an array directly"):
        _lower(
            'x = input_int("X", default=1)\n'
            'x = [1, 2]\n'
            'output(x)\n'
        )

def test_runtime_if_survival_uses_exact_binding_identity_positive():
    """The convergence owner keeps an incoming runtime value when both exits retain its exact BindingId."""
    incoming = RuntimeBindingSymbol(BindingId("scope", 7), NFType.FLOAT)
    true_state = SimpleNamespace(runtime_bindings={"value": incoming})
    false_state = SimpleNamespace(runtime_bindings={"value": incoming})
    assert _runtime_if_invalidated_incoming_runtime_bindings(
        {"value": incoming}, true_state, false_state
    ) == ()


def test_runtime_if_survival_rejects_same_name_with_different_binding_identity():
    """Matching source spelling cannot preserve an incoming runtime owner with a different BindingId."""
    incoming = RuntimeBindingSymbol(BindingId("scope", 7), NFType.FLOAT)
    replacement = RuntimeBindingSymbol(BindingId("scope", 8), NFType.FLOAT)
    true_state = SimpleNamespace(runtime_bindings={"value": replacement})
    false_state = SimpleNamespace(runtime_bindings={"value": incoming})
    assert _runtime_if_invalidated_incoming_runtime_bindings(
        {"value": incoming}, true_state, false_state
    ) == (("value", incoming),)

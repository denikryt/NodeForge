"""Pure tests for stage-17 straight-line Semantic Body IR."""

import ast
from types import MappingProxyType

import pytest

from NodeForge.builtin_call_semantics import IR_CAPABLE_BUILTIN_NAMES, STATEFUL_FALLBACK_BUILTIN_NAMES
from NodeForge.call_resolution import CallableEnvironment
from NodeForge.compiler_identities import BindingId, InputDeclarationId
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.runtime_bindings import RuntimeBindingSymbol
from NodeForge.semantic_body import BODY_UNSUPPORTED, lower_basic_body
from NodeForge.semantic_ir import IRAssign, IRArray, IRBody, IRFinalExpression, IRInputDeclaration, IROutput, IRProgram, IRValue


def _callables(**overrides):
    data = dict(
        callable_builtins=frozenset(IR_CAPABLE_BUILTIN_NAMES | STATEFUL_FALLBACK_BUILTIN_NAMES),
        system_constructors={},
        local_functions={},
        backend_helper_names=frozenset(),
        imported_functions={},
    )
    data.update(overrides)
    return CallableEnvironment(**data)


def _stmts(source):
    return ast.parse(source, mode="exec").body


def _lower(source, *, bindings=None, constants=None, legacy=(), reserved=None, callables=None):
    return lower_basic_body(
        _stmts(source),
        initial_runtime_bindings=bindings or {},
        initial_constants=constants or {},
        legacy_binding_names=frozenset(legacy),
        reserved_name_labels=reserved or {},
        callable_environment=callables or _callables(),
        owner_scope="scope",
    )


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
    assert "IRAugAssign" not in __import__("NodeForge.semantic_ir", fromlist=["*"]).__dict__


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
    assert dict(result.final_constants)["x"] == 5
    assert initial == {"seed": 4}
    with pytest.raises(TypeError):
        result.final_constants["x"] = 9


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


def test_late_unsupported_statement_returns_whole_body_fallback_without_mutation():
    bindings = dict([_binding("a", 0)])
    constants = {"k": 3}
    result = lower_basic_body(
        _stmts("x = a + 1\nfor i in [1]:\n    x = x + i\nx"),
        initial_runtime_bindings=MappingProxyType(bindings),
        initial_constants=MappingProxyType(constants),
        legacy_binding_names=frozenset(),
        reserved_name_labels={},
        callable_environment=_callables(),
        owner_scope="scope",
    )
    assert result is BODY_UNSUPPORTED
    assert constants == {"k": 3}
    assert bindings == dict([_binding("a", 0)])


def test_structural_and_dynamic_categories_reject_entire_body():
    assert _lower("items = [a]\nitems", bindings=dict([_binding("a", 0)])) is BODY_UNSUPPORTED
    with pytest.raises(CompileError, match="Cannot unpack scalar result into 2 names"):
        _lower("a, b = pair", bindings=dict([_binding("pair", 0)]))
    assert _lower("builder = geometry_builder()\nbuilder") is BODY_UNSUPPORTED
    assert _lower("x = grid(2, 2)\nx") is BODY_UNSUPPORTED
    assert _lower("if True:\n    x = 1") is BODY_UNSUPPORTED
    assert _lower("for i in [1]:\n    x = i") is BODY_UNSUPPORTED
    assert _lower("panel('P', a)", bindings=dict([_binding("a", 0)])) is BODY_UNSUPPORTED
    assert _lower("store(a, 'x', a)", bindings=dict([_binding("a", 0)])) is BODY_UNSUPPORTED
    assert _lower("set_position(a)", bindings=dict([_binding("a", 0)])) is BODY_UNSUPPORTED
    with pytest.raises(CompileError, match=r"input_\*\(\) may only be used as the complete right-hand side of a simple assignment"):
        _lower("x = input_float('X') + 1")
    local_callables = _callables(local_functions={"foo": object()})
    assert _lower("x = foo()\nx", callables=local_callables) is BODY_UNSUPPORTED




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


def test_reserved_target_validation_is_shared_language_semantics():
    with pytest.raises(CompileError, match="Cannot assign to foo: name is already registered as imported function"):
        _lower("foo = 1", reserved={"foo": "imported function"})


def test_body_entry_allocator_invariant_is_structurally_guaranteed_before_first_compile_statements():
    """Initial BindingIds are only active group-input seeds before body compilation begins."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "compiler.py").read_text(encoding="utf-8")
    populate = source[source.index("def _populate_group("):]
    before_body = populate[:populate.index("compile_statements(ctx, stmts)")]
    compiler_session = before_body[before_body.index("comp = Compiler("):]
    assert "comp.bind_runtime_value(" in compiler_session
    assert "comp.unbind_runtime_binding(" not in compiler_session
    assert "comp.bind_legacy_structural(" not in compiler_session
    assert "comp._restore_binding_state(" not in compiler_session
    assert "compile_statement(" not in compiler_session


def test_basic_body_frontend_has_no_backend_dependencies_or_compiler_mutation():
    """The body semantic stage remains pure and cannot publish backend Values."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "semantic_body.py").read_text(encoding="utf-8")
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


def test_accepted_compile_statements_route_never_publishes_body_local_values_to_compiler():
    """Only the whole-body fallback may execute legacy binding publication APIs."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "statement_compiler.py").read_text(encoding="utf-8")
    function = source[source.index("def compile_statements("):]
    marker = function.index("# STRUCTURAL_SEMANTICS_WHOLE_BODY_FALLBACK:")
    accepted = function[:marker]
    after_legacy_loop = function.index("    comp.consts.clear()", marker)
    accepted += function[after_legacy_loop:]
    assert "comp.bind_runtime_value(" not in accepted
    assert "comp.bind_legacy_structural(" not in accepted
    assert "compile_statement(" not in accepted


def test_body_lowerer_is_ast_free():
    """Blender body lowering consumes only IR and compiler identities, never source AST."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "blender_ir_lowering.py").read_text(encoding="utf-8")
    assert "import ast" not in source
    assert "ast.AST" not in source


def test_library_duplicate_display_labels_bind_positionally_and_reject_ambiguous_keywords(monkeypatch):
    """Reusable calls retain physical slot identity when interface display labels repeat."""
    import importlib
    import sys
    import types
    from NodeForge.compiler_identities import library_function_id

    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace())
    library_calls = importlib.import_module("NodeForge.library_calls")

    sockets = [
        types.SimpleNamespace(name="Scale", bl_idname="NodeSocketFloat", enabled=True),
        types.SimpleNamespace(name="Scale", bl_idname="NodeSocketFloat", enabled=True),
        types.SimpleNamespace(name="Unique", bl_idname="NodeSocketFloat", enabled=True),
    ]
    function_group = object()

    class Probe:
        """Minimal group-node probe that exposes the cached function interface."""

        def __init__(self):
            self.location = None
            self.inputs = []
            self._node_tree = None

        @property
        def node_tree(self):
            return self._node_tree

        @node_tree.setter
        def node_tree(self, value):
            self._node_tree = value
            self.inputs = sockets if value is function_group else []

    class Nodes:
        """Minimal node collection used only for interface probing."""

        def new(self, _kind):
            return Probe()

        def remove(self, _node):
            return None

    record = types.SimpleNamespace(
        namespace="local",
        name="dup",
        package_id="vendor.pkg",
        source_path=None,
    )
    binding = types.SimpleNamespace(namespace="local", canonical_name="dup", record=record)
    comp = types.SimpleNamespace(
        group=types.SimpleNamespace(nodes=Nodes()),
        local_group_cache={("catalog", "local", "dup"): function_group},
        function_group_cache={},
        function_group_transaction=None,
        function_compilation_trace=None,
        group_backend=None,
        _const_or_compile_arg=lambda expr, _depth: (expr.value, False),
    )
    function_id = library_function_id("local", "vendor.pkg", "dup")
    observed = []
    monkeypatch.setattr(library_calls, "has_native_compile_call_for_record", lambda _record: False)
    monkeypatch.setattr(
        library_calls,
        "make_library_call_node",
        lambda _group, _function_group, compiled, constants, **_kwargs: observed.append((compiled, constants)) or "ok",
    )

    positional = ast.parse("dup(1.0, 7.0, 9.0)", mode="eval").body
    assert library_calls.compile_library_function_call(comp, positional, binding=binding, function_id=function_id) == "ok"
    assert observed[-1] == ([], [(0, 1.0), (1, 7.0), (2, 9.0)])

    unique_keyword = ast.parse("dup(1.0, 7.0, Unique=9.0)", mode="eval").body
    assert library_calls.compile_library_function_call(comp, unique_keyword, binding=binding, function_id=function_id) == "ok"
    assert observed[-1] == ([], [(0, 1.0), (1, 7.0), (2, 9.0)])

    ambiguous = ast.parse("dup(Scale=1.0)", mode="eval").body
    with pytest.raises(CompileError, match="ambiguous because multiple inputs share that label; use positional arguments"):
        library_calls.compile_library_function_call(comp, ambiguous, binding=binding, function_id=function_id)

    unknown = ast.parse("dup(Nope=1.0)", mode="eval").body
    with pytest.raises(CompileError, match="got unknown keyword argument 'Nope'"):
        library_calls.compile_library_function_call(comp, unknown, binding=binding, function_id=function_id)

    duplicate = ast.parse("dup(1.0, 7.0, 9.0, Unique=4.0)", mode="eval").body
    with pytest.raises(CompileError, match="got multiple values for input 'Unique'"):
        library_calls.compile_library_function_call(comp, duplicate, binding=binding, function_id=function_id)


def test_generated_local_function_source_prologue_is_basic_body_eligible(monkeypatch):
    """Generated helper input prologues enter IRBody rather than forcing legacy fallback."""
    import sys
    import types

    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace(data=types.SimpleNamespace(node_groups={})))
    sys.modules.pop("NodeForge.local_functions", None)
    from NodeForge import local_functions

    fn = ast.parse("def f(x):\n    doubled = x * 2\n    return doubled\n").body[0]
    shape = local_functions.analyze_local_return_shape(fn)
    source = local_functions.local_function_source(fn, {"x": NFType.FLOAT}, return_shape=shape)
    result = _lower(source)

    assert result is not BODY_UNSUPPORTED
    assert isinstance(result.body.statements[0], IRInputDeclaration)
    assert result.body.statements[0].target_name == "x"
    assert result.body.statements[0].display_name == "x"


def test_library_call_node_applies_duplicate_label_arguments_by_input_position(monkeypatch):
    """Physical call-node realization never re-addresses duplicate inputs by display name."""
    import importlib
    import sys
    import types
    from NodeForge.values import Value

    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace())
    library = importlib.import_module("NodeForge.library")

    class Socket:
        """Minimal enabled group-node socket."""

        def __init__(self, name, *, output=False):
            self.name = name
            self.bl_idname = "NodeSocketFloat"
            self.enabled = True
            self.hide = False
            self.is_output = output
            self.default_value = 0.0

    inputs = [Socket("Scale"), Socket("Scale"), Socket("Unique")]
    outputs = [Socket("Result", output=True)]
    node = types.SimpleNamespace(inputs=inputs, outputs=outputs, node_tree=None)
    links = []
    group = types.SimpleNamespace(links=types.SimpleNamespace(new=lambda source, target: links.append((source, target))))
    function_group = types.SimpleNamespace(name="Dup")
    monkeypatch.setattr(library, "_new_node", lambda *_args, **_kwargs: node)
    monkeypatch.setattr(library, "apply_function_node_display_name", lambda *_args: None)

    dynamic_socket = object()
    result = library.make_library_call_node(
        group,
        function_group,
        compiled_args=[(1, Value(dynamic_socket, NFType.FLOAT))],
        const_args=[(0, 1.0), (2, 9.0)],
    )

    assert inputs[0].default_value == 1.0
    assert inputs[2].default_value == 9.0
    assert links == [(dynamic_socket, inputs[1])]
    assert result.socket is outputs[0]


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
    from NodeForge.semantic_ir import IRBindLeaves, IROutput, IRBinding

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
    from NodeForge.semantic_ir import IRBindLeaves, IROutput, IRBinding

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
    from NodeForge.semantic_ir import IRAssign, IRDiscardExpression, IRObjectProperty, IROutput

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
    from NodeForge.semantic_ir import IRDiscardExpression, IRFinalExpression

    result = _lower('obj = input_object("Source")\nobj.info(as_instance=False)')
    assert isinstance(result.body.statements[-1], IRDiscardExpression)
    assert not any(isinstance(statement, IRFinalExpression) for statement in result.body.statements)


def test_temporary_object_info_statement_is_accepted_before_later_output():
    from NodeForge.semantic_ir import IRDiscardExpression, IROutput

    result = _lower(
        'node("GeometryNodeObjectInfo", output="Object", typ=Object).info(as_instance=False)\n'
        'output("Value", 1.0)'
    )
    assert isinstance(result.body.statements[0], IRDiscardExpression)
    assert isinstance(result.body.statements[1], IROutput)


def test_structural_projection_binding_ids_are_stable_by_key_and_never_recycled():
    """Historical projection slots reuse only the same source/key and new keys stay monotonic."""
    from NodeForge.semantic_ir import IRBindLeaves

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
    from NodeForge.semantic_ir import IRAssign, IRBindLeaves

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
    assert "a" not in result.final_constants
    assert "b" not in result.final_constants


def test_partial_object_info_configuration_merges_before_resolution():
    """Separate pre-resolution info calls update one shared Object state rather than resetting it."""
    from NodeForge.semantic_ir import IRObjectProperty

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
    from NodeForge.semantic_ir import IRDiscardExpression
    assert any(isinstance(statement, IRDiscardExpression) for statement in result.body.statements)


def test_stored_tuple_negative_index_selects_existing_leaf_binding():
    from NodeForge.semantic_ir import IRBindLeaves, IROutput, IRBinding

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

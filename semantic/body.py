"""Construct compiler-owned Semantic IR for executable function bodies.

This module owns ordinary runtime assignments, fixed tuple/named-output structural
bindings, direct explicit inputs, explicit outputs, and Object semantics. Unsupported
categories reject the complete body before Blender lowering begins.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Mapping

from .builtin_calls import (
    INPUT_DECLARATION_BUILTIN_NAMES,
    INPUT_DECLARATION_PLACEMENT_ERROR,
    analyze_input_declaration_call,
    analyze_contextual_store_call,
    analyze_contextual_set_position_call,
    _kw_dict,
    _check_extra,
)
from ..compiler_identities import BindingId, CallSiteId, FunctionId, InputDeclarationId, InterfaceInputOrigin
from .constants import TYPE_OBJECT
from .consteval import (
    ConstEvalUnavailable,
    _const_eval,
    _is_compile_time_owned_assignment_rhs,
)
from .residualization import (
    CompileTimeAppendExpression,
    CompileTimeBindExpression,
    CompileTimeForEffect,
)
from .compile_time import CompileTimeSnapshot, CompileTimeState
from ..errors import CompileError
from ..extensions.semantics import ExtensionDependencySource, ExtensionSemanticPayload
from ..nf_types import NFType
from .numeric_semantics import normalize_float_constant, normalize_int_constant
from .group_context import GroupContextAvailabilityCursor, GroupContextSlot
from .parsing import _literal_string
from .runtime_bindings import RuntimeBindingSymbol, validate_runtime_binding_target
from .analysis import analyze_expression, build_semantic_environment
from .ir import (
    IRAssign,
    IRBindLeaves,
    IRBody,
    IRDiscardExpression,
    IRContextRead,
    IRContextWrite,
    IRCall,
    IRCallArgument,
    IRCallableKind,
    IRCallableTarget,
    IRPanelDeclaration,
    IRArray,
    IRFinalExpression,
    IRInputDeclaration,
    IRLiteral,
    IRLeafBinding,
    IRNamedOutputs,
    IRIf,
    IRRepeat,
    IRRepeatState,
    IROutput,
    IRProgram,
    IRTuple,
    IRValue,
    IRBinding,
)
from .lowering import lower_analyzed_dependency_sources, lower_analyzed_expression
from .control_flow import (
    BranchMergePolicy,
    lower_runtime_if,
    parse_repeat_range_for,
    repeat_body_has_nonruntime_for,
    repeat_mutation_names,
    require_repeat_state_assignment,
    RuntimeMergeSymbol,
)
from .geometry_builder import (
    GeometryBuilderState,
    join_binding_program as builder_join_binding_program,
    validate_geometry_builder_constructor,
)
from .values import (
    ArrayResultShape,
    NamedOutputsResultShape,
    ObjectInfoState,
    ObjectSemanticId,
    ObjectSemanticSnapshot,
    RuntimeResultShape,
    SemanticResultShape,
    StructuralArrayId,
    StructuralArrayRef,
    StructuralArraySnapshot,
    StructuralArrayState,
    StructuralBindingKind,
    StructuralBindingSymbol,
    StructuralLeafBinding,
    StructuralRuntimeLeaf,
    TupleResultShape,
)



@dataclass(frozen=True)
class BodyOutputSummary:
    """Describe final frontend-owned group output selection without backend Values."""

    explicit_outputs: tuple[tuple[str, NFType], ...]
    auto_output: tuple[str, NFType] | None

    def __post_init__(self) -> None:
        """Freeze output order and require canonical semantic types."""
        object.__setattr__(self, "explicit_outputs", tuple(self.explicit_outputs))
        for item in self.explicit_outputs:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("explicit_outputs must contain (name, NFType) pairs")
            name, typ = item
            if not isinstance(name, str) or not name or not isinstance(typ, NFType):
                raise TypeError("explicit output summary entries require name and NFType")
        if self.auto_output is not None:
            if not isinstance(self.auto_output, tuple) or len(self.auto_output) != 2:
                raise TypeError("auto_output must be None or a (name, NFType) pair")
            name, typ = self.auto_output
            if not isinstance(name, str) or not name or not isinstance(typ, NFType):
                raise TypeError("auto output summary requires name and NFType")


def _summarize_body_outputs(body: IRBody, *, clear_auto_final_output: bool) -> BodyOutputSummary:
    """Mirror existing backend output selection using semantic IR types only."""
    explicit_outputs: list[tuple[str, NFType]] = []
    auto_output: tuple[str, NFType] | None = None
    for statement in body.statements:
        if isinstance(statement, IRInputDeclaration):
            auto_output = (statement.target_name, statement.typ)
        elif isinstance(statement, IRAssign):
            auto_output = (statement.source_name, statement.value.result.typ)
        elif isinstance(statement, (IRBindLeaves, IRDiscardExpression, IRPanelDeclaration)):
            auto_output = None
        elif isinstance(statement, IROutput):
            explicit_outputs.append((statement.name, statement.value.result.typ))
            auto_output = None
        elif isinstance(statement, IRFinalExpression):
            auto_output = ("out", statement.value.result.typ)
        elif isinstance(statement, IRIf):
            for merge in statement.merges:
                auto_output = (merge.source_name, merge.typ)
        elif isinstance(statement, IRRepeat):
            for state in statement.states:
                if state.publish_to_parent:
                    auto_output = (state.source_name, state.typ)
    if clear_auto_final_output:
        auto_output = None
    return BodyOutputSummary(tuple(explicit_outputs), auto_output)


@dataclass(frozen=True)
class BasicBodyCompilation:
    """Return one accepted body plus detached final frontend semantic state."""

    body: IRBody
    final_compile_time: CompileTimeSnapshot
    final_structural_arrays: StructuralArraySnapshot
    output_summary: BodyOutputSummary
    clear_auto_final_output: bool = False

    def __post_init__(self) -> None:
        """Require immutable detached frontend snapshots."""
        if not isinstance(self.final_compile_time, CompileTimeSnapshot):
            raise TypeError("final_compile_time must be a CompileTimeSnapshot")
        if not isinstance(self.final_structural_arrays, StructuralArraySnapshot):
            raise TypeError("final_structural_arrays must be a StructuralArraySnapshot")
        if not isinstance(self.output_summary, BodyOutputSummary):
            raise TypeError("output_summary must be a BodyOutputSummary")
        if not isinstance(self.clear_auto_final_output, bool):
            raise TypeError("clear_auto_final_output must be a bool")


@dataclass(frozen=True)
class _AnalyzedBodyExpression:
    """Carry one analyzed body expression as either executable IR or frontend semantic state."""

    program: IRProgram | None
    result_shape: object
    object_semantics: ObjectSemanticSnapshot
    analysis: object | None = None
    semantic_payload: ExtensionSemanticPayload | None = None

    def __post_init__(self) -> None:
        """Require exactly one root representation: executable IR or package semantic payload."""
        has_program = self.program is not None
        has_payload = self.semantic_payload is not None
        if has_program == has_payload:
            raise ValueError("body expression requires exactly one of program or semantic_payload")



def _unique_output_name(existing: set[str], requested: str) -> str:
    """Return the existing deterministic uniqued output display name."""
    base = requested or "out"
    name = base
    index = 2
    while name in existing:
        name = f"{base}_{index}"
        index += 1
    existing.add(name)
    return name


def _top_level_simple_call(stmt, name: str | None = None):
    """Return a direct ``name(...)`` expression call when present."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return None
    call = stmt.value
    if not isinstance(call.func, ast.Name):
        return None
    if name is not None and call.func.id != name:
        return None
    return call


def _direct_input_call(expr):
    """Return a direct explicit-input call or None."""
    if not isinstance(expr, ast.Call) or not isinstance(expr.func, ast.Name):
        return None
    return expr if expr.func.id in INPUT_DECLARATION_BUILTIN_NAMES else None


def validate_input_declaration_placement(stmts) -> None:
    """Reject input builtins outside the complete RHS of a simple assignment."""
    module = ast.Module(body=list(stmts), type_ignores=[])
    allowed_call_ids = set()
    for node in ast.walk(module):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        if not isinstance(node.targets[0], ast.Name):
            continue
        call = _direct_input_call(node.value)
        if call is not None:
            allowed_call_ids.add(id(call))

    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id in INPUT_DECLARATION_BUILTIN_NAMES and id(node) not in allowed_call_ids:
            raise CompileError(INPUT_DECLARATION_PLACEMENT_ERROR)


def _tuple_target_names(target_node: ast.Tuple | ast.List) -> list[str]:
    """Validate one supported flat tuple/list unpack target and return its names."""
    if any(isinstance(item, ast.Starred) for item in target_node.elts):
        raise CompileError("Starred tuple unpacking is not supported")
    if not target_node.elts or not all(isinstance(item, ast.Name) for item in target_node.elts):
        raise CompileError("Tuple unpacking target must be a flat sequence of names")
    names = [item.id for item in target_node.elts]
    if len(set(names)) != len(names):
        raise CompileError("Tuple unpacking target names must be unique")
    return names


def _ordinary_for_target_names(target_node) -> list[str]:
    """Return the one simple-name target accepted by migrated ordinary-for semantics."""
    if isinstance(target_node, ast.Name):
        return [target_node.id]
    raise CompileError("Only simple compile-time for targets are supported")


def _unpacking_loop_target_names(target_node, body, *, structural_iterable=False) -> list[str] | None:
    """Accept flat targets for structural arrays and collection mutation loops."""
    if not isinstance(target_node, (ast.Tuple, ast.List)):
        return None
    has_collection_method = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"add", "extend", "append"}
        and isinstance(node.func.value, ast.Name)
        for statement in body
        for node in ast.walk(statement)
    )
    if not structural_iterable and not has_collection_method:
        return None
    return _tuple_target_names(target_node)


@dataclass
class _BodyIdentityAllocator:
    """Own non-rewinding identities shared by all semantic forks in one root body."""

    owner_scope: str
    declaration_owner: str
    next_local_id: int
    ordinary_reservations: dict[str, BindingId]
    structural_reservations: dict[tuple[str, tuple[str, int | str]], BindingId]
    input_declaration_ordinals: dict[str, int]
    call_occurrence_ordinals: dict[FunctionId, int]
    next_object_id: int = 0
    next_array_id: int = 0

    def allocate_binding_id(self) -> BindingId:
        """Allocate one monotonic body-local BindingId."""
        binding_id = BindingId(self.owner_scope, self.next_local_id)
        self.next_local_id += 1
        return binding_id

    def reserve_ordinary(self, name: str) -> BindingId:
        """Return the stable body-local slot reserved for one source name."""
        binding_id = self.ordinary_reservations.get(name)
        if binding_id is None:
            binding_id = self.allocate_binding_id()
            self.ordinary_reservations[name] = binding_id
        return binding_id

    def reserve_structural(self, name: str, projection_key: tuple[str, int | str]) -> BindingId:
        """Return the stable body-local slot for one fixed structural leaf."""
        key = (name, projection_key)
        binding_id = self.structural_reservations.get(key)
        if binding_id is None:
            binding_id = self.allocate_binding_id()
            self.structural_reservations[key] = binding_id
        return binding_id

    def allocate_input_declaration_id(self, target_name: str) -> InputDeclarationId:
        """Allocate one durable declaration identity without branch-local rewind."""
        ordinal = self.input_declaration_ordinals.get(target_name, 0)
        self.input_declaration_ordinals[target_name] = ordinal + 1
        return InputDeclarationId(self.declaration_owner, target_name, ordinal)

    def allocate_call_site_id(self, callee: FunctionId) -> CallSiteId:
        """Allocate one monotonic owner-scoped source-call occurrence identity."""
        if not isinstance(callee, FunctionId):
            raise TypeError("callee must be a FunctionId")
        ordinal = self.call_occurrence_ordinals.get(callee, 0)
        self.call_occurrence_ordinals[callee] = ordinal + 1
        return CallSiteId(self.owner_scope, callee, ordinal)

    def allocate_object_id(self) -> ObjectSemanticId:
        """Allocate one monotonic Object semantic identity."""
        object_id = ObjectSemanticId(self.next_object_id)
        self.next_object_id += 1
        return object_id

    def allocate_array_id(self) -> StructuralArrayId:
        """Allocate one monotonic body-local structural-array identity."""
        array_id = StructuralArrayId(self.next_array_id)
        self.next_array_id += 1
        return array_id


@dataclass(kw_only=True)
class _BodySemanticState:
    """Store forkable active frontend state for one structured body point."""

    runtime_bindings: dict[str, RuntimeBindingSymbol]
    structural_bindings: dict[str, StructuralBindingSymbol]
    array_bindings: dict[str, StructuralArrayId]
    array_states: dict[StructuralArrayId, StructuralArrayState]
    builder_states: dict[str, GeometryBuilderState]
    extension_bindings: dict[str, ExtensionSemanticPayload]
    object_ids_by_binding: dict[BindingId, ObjectSemanticId]
    object_states: dict[ObjectSemanticId, ObjectInfoState]
    interface_input_origins: dict[BindingId, InterfaceInputOrigin]
    panel_member_origins: dict[InterfaceInputOrigin, str]
    panel_names: set[str]
    identities: _BodyIdentityAllocator
    changed_runtime_ids: set[BindingId]
    explicitly_assigned_runtime_ids: set[BindingId]
    lexical_iteration_ids: set[BindingId]
    clear_auto_final_output: bool

    def fork(self) -> "_BodySemanticState":
        """Copy active state while retaining the shared non-rewinding allocator."""
        return _BodySemanticState(
            runtime_bindings=dict(self.runtime_bindings),
            structural_bindings=dict(self.structural_bindings),
            array_bindings=dict(self.array_bindings),
            array_states=dict(self.array_states),
            builder_states=dict(self.builder_states),
            extension_bindings=dict(self.extension_bindings),
            object_ids_by_binding=dict(self.object_ids_by_binding),
            object_states=dict(self.object_states),
            interface_input_origins=dict(self.interface_input_origins),
            # panel declarations are root-only; branch states share the attempt-owned
            # membership/name registries instead of forking semantic panel ownership.
            panel_member_origins=self.panel_member_origins,
            panel_names=self.panel_names,
            identities=self.identities,
            changed_runtime_ids=set(),
            explicitly_assigned_runtime_ids=set(),
            lexical_iteration_ids=set(self.lexical_iteration_ids),
            clear_auto_final_output=self.clear_auto_final_output,
        )


    def array_snapshot(self) -> StructuralArraySnapshot:
        """Build one detached expression-facing structural-array snapshot."""
        return StructuralArraySnapshot(self.array_bindings, self.array_states)

    def object_snapshot(self) -> ObjectSemanticSnapshot:
        """Build an expression snapshot from active mappings and the root watermark."""
        return ObjectSemanticSnapshot(
            self.object_ids_by_binding,
            self.object_states,
            self.identities.next_object_id,
        )

    def adopt_object_snapshot(self, snapshot: ObjectSemanticSnapshot) -> None:
        """Adopt branch-local mappings and advance the shared Object identity watermark."""
        self.object_ids_by_binding = dict(snapshot.object_ids_by_binding)
        self.object_states = dict(snapshot.states)
        self.identities.next_object_id = max(self.identities.next_object_id, snapshot.next_object_id)


def _runtime_if_invalidated_incoming_runtime_bindings(
    incoming_runtime_bindings: dict[str, RuntimeBindingSymbol],
    true_state: _BodySemanticState,
    false_state: _BodySemanticState,
) -> tuple[tuple[str, RuntimeBindingSymbol], ...]:
    """Return incoming ordinary runtime bindings that do not survive both exits.

    Survival is an identity property: both runtime exits must still expose the
    exact incoming ``BindingId`` under the same source lookup name. Ownership
    changes to structural, builder, array, or extension state therefore remove
    the stale parent runtime mapping instead of synthesizing a cross-category join.
    """
    invalidated = []
    for name, incoming in incoming_runtime_bindings.items():
        true_symbol = true_state.runtime_bindings.get(name)
        false_symbol = false_state.runtime_bindings.get(name)
        if (
            true_symbol is not None
            and false_symbol is not None
            and true_symbol.binding_id == incoming.binding_id
            and false_symbol.binding_id == incoming.binding_id
        ):
            continue
        invalidated.append((name, incoming))
    return tuple(invalidated)


def _program_is_binding_identity(program: IRProgram, binding_id: BindingId) -> bool:
    """Return whether lowering *program* preserves the exact backend binding identity."""
    if len(program.operations) != 1 or not isinstance(program.operations[0], IRBinding):
        return False
    operation = program.operations[0]
    return operation.binding_id == binding_id and program.result == operation.result


def _program_binding_source(program: IRProgram) -> BindingId | None:
    """Return the exact forwarded BindingId for a one-operation identity program."""
    if len(program.operations) != 1 or not isinstance(program.operations[0], IRBinding):
        return None
    operation = program.operations[0]
    return operation.binding_id if program.result == operation.result else None


def _rebase_ir_data(value, offset: int):
    """Rebase program-local IRValue IDs while preserving all other semantic identities."""
    if isinstance(value, IRValue):
        return IRValue(value.id + offset, value.typ)
    if isinstance(value, tuple):
        return tuple(_rebase_ir_data(item, offset) for item in value)
    if isinstance(value, list):
        return [_rebase_ir_data(item, offset) for item in value]
    if is_dataclass(value) and value.__class__.__module__ == IRValue.__module__:
        changes = {field.name: _rebase_ir_data(getattr(value, field.name), offset) for field in fields(value)}
        return replace(value, **changes)
    return value


def _program_value_extent(program: IRProgram) -> int:
    """Return one plus the largest program-local IRValue id in *program*."""
    ids = []
    def visit(value):
        if isinstance(value, IRValue):
            ids.append(value.id)
        elif isinstance(value, tuple):
            for item in value:
                visit(item)
        elif is_dataclass(value) and value.__class__.__module__ == IRValue.__module__:
            for field in fields(value):
                visit(getattr(value, field.name))
    visit(program)
    return max(ids, default=-1) + 1



def lower_basic_body(
    stmts,
    *,
    initial_runtime_bindings: Mapping[str, RuntimeBindingSymbol],
    initial_compile_time: CompileTimeSnapshot,
    reserved_name_labels: Mapping[str, str],
    callable_environment,
    owner_scope: str,
    declaration_owner: str | None = None,
    geometry_mode: bool = False,
    initial_interface_input_origins: Mapping[BindingId, InterfaceInputOrigin] | None = None,
    compile_time_effects_before=(),
    trailing_compile_time_effects=(),
    source_callable_session=None,
    source_definition_owner: str | None = None,
    helper_namespace: str = "Group",
    extension_registry=None,
    extension_dependency_sink=None,
):
    """Lower one whole eligible source body to compiler-owned structured Semantic IR."""
    validate_input_declaration_placement(stmts)
    if any(symbol.binding_id.owner_scope != owner_scope for symbol in initial_runtime_bindings.values()):
        raise CompileError("Internal error: body runtime binding owner scope mismatch")
    local_ids = [symbol.binding_id.local_id for symbol in initial_runtime_bindings.values()]
    if len(local_ids) != len(set(local_ids)):
        raise CompileError("Internal error: duplicate body-entry BindingId")
    initial_interface_input_origins = dict(initial_interface_input_origins or {})
    initial_binding_ids = {symbol.binding_id for symbol in initial_runtime_bindings.values()}
    if any(binding_id not in initial_binding_ids for binding_id in initial_interface_input_origins):
        raise CompileError("Internal error: interface-input provenance references a non-entry binding")
    if not all(isinstance(origin, (BindingId, InputDeclarationId)) for origin in initial_interface_input_origins.values()):
        raise TypeError("interface-input provenance must use InterfaceInputOrigin identities")

    identities = _BodyIdentityAllocator(
        owner_scope=owner_scope,
        declaration_owner=declaration_owner or owner_scope,
        next_local_id=max(local_ids, default=-1) + 1,
        ordinary_reservations={name: symbol.binding_id for name, symbol in initial_runtime_bindings.items()},
        structural_reservations={},
        input_declaration_ordinals={},
        call_occurrence_ordinals={},
    )
    if not isinstance(initial_compile_time, CompileTimeSnapshot):
        raise TypeError("initial_compile_time must be a CompileTimeSnapshot")
    compile_time = CompileTimeState(initial_compile_time.values)
    if extension_dependency_sink is None:
        extension_dependency_sink = {}
    if not isinstance(extension_dependency_sink, dict):
        raise TypeError("extension_dependency_sink must be a dict")
    compile_time_effects_before = tuple(tuple(items) for items in compile_time_effects_before)
    trailing_compile_time_effects = tuple(trailing_compile_time_effects)
    if compile_time_effects_before and len(compile_time_effects_before) != len(stmts):
        raise CompileError("Internal error: compile-time effect/source alignment mismatch")
    if not compile_time_effects_before:
        compile_time_effects_before = tuple(() for _ in stmts)
    state = _BodySemanticState(
        runtime_bindings=dict(initial_runtime_bindings),
        structural_bindings={},
        array_bindings={},
        array_states={},
        builder_states={},
        extension_bindings={},
        object_ids_by_binding={},
        object_states={},
        interface_input_origins=dict(initial_interface_input_origins),
        panel_member_origins={},
        panel_names=set(),
        identities=identities,
        changed_runtime_ids=set(),
        explicitly_assigned_runtime_ids=set(),
        lexical_iteration_ids=set(),
        clear_auto_final_output=False,
    )
    group_context_cursor = GroupContextAvailabilityCursor(
        {GroupContextSlot.CURRENT_GEOMETRY} if geometry_mode else set()
    )
    for symbol in state.runtime_bindings.values():
        if symbol.typ is TYPE_OBJECT:
            object_id = identities.allocate_object_id()
            state.object_ids_by_binding[symbol.binding_id] = object_id
            state.object_states[object_id] = ObjectInfoState()

    output_names: set[str] = set()

    def bind_compile_time_name(
        active: _BodySemanticState,
        active_compile_time: CompileTimeState,
        name: str,
        value,
    ) -> None:
        """Publish one compile-time-owned rebind after clearing stale body ownership."""
        _clear_nonbuilder_name_ownership(active, name)
        active_compile_time.bind(name, value)

    def replay_compile_time_effect(
        effect, active: _BodySemanticState, active_compile_time: CompileTimeState
    ) -> None:
        """Replay one erased preprocessing action against source-ordered CT and body ownership state."""
        if isinstance(effect, CompileTimeBindExpression):
            try:
                value = _const_eval(effect.expression, active_compile_time.values)
            except ConstEvalUnavailable as exc:
                raise CompileError("Internal error: erased compile-time assignment became unavailable during replay") from exc
            bind_compile_time_name(active, active_compile_time, effect.name, value)
            return
        if isinstance(effect, CompileTimeForEffect):
            had_old_target = active_compile_time.contains(effect.target)
            old_target = active_compile_time.get(effect.target)
            old_extension = active.extension_bindings.pop(effect.target, None)
            try:
                try:
                    iterable = _const_eval(effect.iterable_expression, active_compile_time.values)
                except ConstEvalUnavailable as exc:
                    raise CompileError(
                        "Internal error: erased compile-time loop iterable became unavailable during replay"
                    ) from exc
                iterator = iter(iterable)
                for effects in effect.iteration_effects:
                    try:
                        item = next(iterator)
                    except StopIteration as exc:
                        raise CompileError(
                            "Internal error: erased compile-time loop replay produced fewer iterations than preprocessing"
                        ) from exc
                    active_compile_time.bind(effect.target, item)
                    replay_compile_time_effects(effects, active, active_compile_time)
                try:
                    next(iterator)
                except StopIteration:
                    pass
                else:
                    raise CompileError(
                        "Internal error: erased compile-time loop replay produced more iterations than preprocessing"
                    )
            finally:
                if had_old_target:
                    active_compile_time.bind(effect.target, old_target)
                else:
                    active_compile_time.discard(effect.target)
                if old_extension is not None:
                    active.extension_bindings[effect.target] = old_extension
            return
        if isinstance(effect, CompileTimeAppendExpression):
            target = active_compile_time.get(effect.name)
            if not isinstance(target, list):
                raise CompileError("Internal error: erased compile-time append lost its list target during replay")
            try:
                value = _const_eval(effect.expression, active_compile_time.values)
            except ConstEvalUnavailable as exc:
                raise CompileError("Internal error: erased compile-time append became unavailable during replay") from exc
            target.append(value)
            return
        raise TypeError(f"unsupported compile-time preprocessing effect: {type(effect).__name__}")

    def replay_compile_time_effects(
        effects, active: _BodySemanticState, active_compile_time: CompileTimeState
    ) -> None:
        """Replay one ordered erased-effect batch without replacing the active CT/body state."""
        for effect in effects:
            replay_compile_time_effect(effect, active, active_compile_time)

    def clear_binding_object(active: _BodySemanticState, binding_id: BindingId) -> None:
        active.object_ids_by_binding.pop(binding_id, None)

    def _clear_nonbuilder_name_ownership(active: _BodySemanticState, name: str) -> None:
        """Remove non-builder semantic ownership for one source name."""
        runtime = active.runtime_bindings.pop(name, None)
        if runtime is not None:
            clear_binding_object(active, runtime.binding_id)
            active.interface_input_origins.pop(runtime.binding_id, None)
        structural = active.structural_bindings.pop(name, None)
        if structural is not None:
            for leaf in structural.leaves:
                clear_binding_object(active, leaf.binding_id)
        active.array_bindings.pop(name, None)
        active.extension_bindings.pop(name, None)

    def _commit_builder_construction(active: _BodySemanticState, active_compile_time: CompileTimeState, name: str) -> None:
        """Publish one fully validated fresh frontend-only builder state atomically."""
        binding_id = identities.allocate_binding_id()
        _clear_nonbuilder_name_ownership(active, name)
        active_compile_time.discard(name)
        active.builder_states[name] = GeometryBuilderState(binding_id)

    def _builder_read_names(node, active: _BodySemanticState) -> tuple[str, ...]:
        """Return deterministic builder names reached through legal ``.geometry`` reads."""
        names = []
        seen = set()
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Attribute)
                and child.attr == "geometry"
                and isinstance(child.value, ast.Name)
                and child.value.id in active.builder_states
                and child.value.id not in seen
            ):
                seen.add(child.value.id)
                names.append(child.value.id)
        return tuple(names)

    def _builder_constructor_targets(stmts) -> set[str]:
        """Return source names directly rebound to fresh builders in a statement region."""
        targets = set()
        for statement in stmts:
            for child in ast.walk(statement):
                if (
                    isinstance(child, ast.Assign)
                    and len(child.targets) == 1
                    and isinstance(child.targets[0], ast.Name)
                    and isinstance(child.value, ast.Call)
                    and isinstance(child.value.func, ast.Name)
                    and child.value.func.id == "geometry_builder"
                ):
                    targets.add(child.targets[0].id)
        return targets

    def ensure_builder_current(active: _BodySemanticState, name: str, statement_sink: list) -> BindingId:
        """Materialize one frontend builder snapshot into its stable hidden Geometry slot."""
        state_record = active.builder_states.get(name)
        if state_record is None:
            raise CompileError(f"Unknown name: {name}")
        if state_record.has_current:
            return state_record.state_binding_id
        program = builder_join_binding_program(state_record.pending_binding_ids)
        if not isinstance(program.result, IRValue):
            raise CompileError("Internal error: GeometryBuilder snapshot did not produce Geometry")
        statement_sink.append(
            IRBindLeaves(
                program,
                (IRLeafBinding(program.result, state_record.state_binding_id, NFType.GEOMETRY),),
            )
        )
        active.builder_states[name] = state_record.current()
        return state_record.state_binding_id

    def _persist_builder_geometry_argument(
        active: _BodySemanticState,
        analyzed: _AnalyzedBodyExpression,
        statement_sink: list,
        *,
        diagnostic: str,
    ) -> BindingId:
        """Persist one validated Geometry expression into a hidden frontend-owned binding."""
        if not isinstance(analyzed.result_shape, RuntimeResultShape) or analyzed.result_shape.typ is not NFType.GEOMETRY:
            raise CompileError(diagnostic)
        if not isinstance(analyzed.program.result, IRValue):
            raise CompileError(diagnostic)
        binding_id = identities.allocate_binding_id()
        statement_sink.append(
            IRBindLeaves(
                analyzed.program,
                (IRLeafBinding(analyzed.program.result, binding_id, NFType.GEOMETRY),),
            )
        )
        active.adopt_object_snapshot(analyzed.object_semantics)
        return binding_id

    def _append_builder_binding(active: _BodySemanticState, name: str, binding_id: BindingId, statement_sink: list) -> None:
        """Apply one builder contribution while preserving pre/post-snapshot Join topology."""
        state_record = active.builder_states[name]
        if not state_record.has_current:
            active.builder_states[name] = state_record.with_pending(binding_id)
            return
        program = builder_join_binding_program((state_record.state_binding_id, binding_id))
        statement_sink.append(
            IRBindLeaves(
                program,
                (IRLeafBinding(program.result, state_record.state_binding_id, NFType.GEOMETRY),),
            )
        )
        active.changed_runtime_ids.add(state_record.state_binding_id)

    def bind_runtime(active: _BodySemanticState, name: str, shape: RuntimeResultShape, *, changed=True) -> RuntimeBindingSymbol:
        current = active.runtime_bindings.get(name)
        binding_id = current.binding_id if current is not None else identities.reserve_ordinary(name)
        active.array_bindings.pop(name, None)
        active.extension_bindings.pop(name, None)
        old_structural = active.structural_bindings.pop(name, None)
        if old_structural is not None:
            for leaf in old_structural.leaves:
                clear_binding_object(active, leaf.binding_id)
        symbol = RuntimeBindingSymbol(binding_id, shape.typ)
        active.runtime_bindings[name] = symbol
        clear_binding_object(active, binding_id)
        active.interface_input_origins.pop(binding_id, None)
        if shape.typ is TYPE_OBJECT:
            active.object_ids_by_binding[binding_id] = shape.object_id
        if changed:
            active.changed_runtime_ids.add(binding_id)
        return symbol

    def bind_input(active: _BodySemanticState, name: str, typ) -> RuntimeBindingSymbol:
        object_id = None
        if typ is TYPE_OBJECT:
            object_id = identities.allocate_object_id()
            active.object_states[object_id] = ObjectInfoState()
        return bind_runtime(active, name, RuntimeResultShape(typ, object_id), changed=True)

    def bind_structural(active: _BodySemanticState, name: str, shape) -> StructuralBindingSymbol:
        active.array_bindings.pop(name, None)
        active.extension_bindings.pop(name, None)
        runtime = active.runtime_bindings.pop(name, None)
        if runtime is not None:
            clear_binding_object(active, runtime.binding_id)
            active.interface_input_origins.pop(runtime.binding_id, None)
        previous = active.structural_bindings.get(name)
        previous_by_key = {} if previous is None else {leaf.projection_key: leaf for leaf in previous.leaves}
        if isinstance(shape, TupleResultShape):
            kind = StructuralBindingKind.TUPLE
            keyed_shapes = [(('index', index), item) for index, item in enumerate(shape.items)]
        elif isinstance(shape, NamedOutputsResultShape):
            kind = StructuralBindingKind.NAMED_OUTPUTS
            keyed_shapes = [(('name', item_name), item_shape) for item_name, item_shape in shape.items]
        else:
            raise TypeError("fixed structural binding requires tuple or named-output shape")
        leaves = []
        active_ids = set()
        for projection_key, leaf_shape in keyed_shapes:
            previous_leaf = previous_by_key.get(projection_key)
            binding_id = previous_leaf.binding_id if previous_leaf is not None else identities.reserve_structural(name, projection_key)
            active_ids.add(binding_id)
            clear_binding_object(active, binding_id)
            if leaf_shape.typ is TYPE_OBJECT:
                active.object_ids_by_binding[binding_id] = leaf_shape.object_id
            leaves.append(StructuralLeafBinding(projection_key, binding_id, leaf_shape.typ))
        if previous is not None:
            for leaf in previous.leaves:
                if leaf.binding_id not in active_ids:
                    clear_binding_object(active, leaf.binding_id)
        symbol = StructuralBindingSymbol(kind, tuple(leaves))
        active.structural_bindings[name] = symbol
        return symbol

    def _clear_name_runtime_structural(active: _BodySemanticState, name: str) -> None:
        """Deactivate runtime/fixed-structural/extension ownership before another category publishes."""
        active.extension_bindings.pop(name, None)
        runtime = active.runtime_bindings.pop(name, None)
        if runtime is not None:
            clear_binding_object(active, runtime.binding_id)
            active.interface_input_origins.pop(runtime.binding_id, None)
        structural = active.structural_bindings.pop(name, None)
        if structural is not None:
            for leaf in structural.leaves:
                clear_binding_object(active, leaf.binding_id)

    def bind_array_alias(active: _BodySemanticState, name: str, array_id: StructuralArrayId) -> None:
        """Bind one source name to an existing body-owned structural-array identity."""
        if array_id not in active.array_states:
            raise CompileError("Internal error: structural array alias references missing state")
        _clear_name_runtime_structural(active, name)
        active.array_bindings[name] = array_id

    def _validate_array_graph(states, root_id: StructuralArrayId) -> None:
        """Reject direct or indirect structural-array cycles before state publication."""
        visiting = set()
        visited = set()

        def visit(array_id):
            if array_id in visiting:
                raise CompileError("recursive structural arrays are not supported")
            if array_id in visited:
                return
            state_record = states.get(array_id)
            if state_record is None:
                raise CompileError("Internal error: structural array references missing state")
            visiting.add(array_id)
            for item in state_record.items:
                if isinstance(item, StructuralArrayRef):
                    visit(item.array_id)
            visiting.remove(array_id)
            visited.add(array_id)

        visit(root_id)

    def _expression_fact(analyzed: _AnalyzedBodyExpression, node):
        """Return one analyzed expression fact when the AST node belongs to this expression."""
        analysis = analyzed.analysis
        if analysis is None:
            return None
        return analysis.facts.get(node)

    def _plan_persisted_item(
        node,
        result,
        shape,
        analyzed: _AnalyzedBodyExpression,
        planned_states: dict[StructuralArrayId, StructuralArrayState],
        object_updates: dict[BindingId, ObjectSemanticId],
    ):
        """Plan one persistent structural item and its leaf bindings without mutating body state."""
        node_fact = _expression_fact(analyzed, node) if node is not None else None
        if isinstance(shape, ArrayResultShape) and node_fact is not None and node_fact.array_id is not None:
            return StructuralArrayRef(node_fact.array_id), ()

        if isinstance(shape, RuntimeResultShape):
            if not isinstance(result, IRValue):
                raise CompileError("Internal error: runtime structural item has no IRValue")
            binding_id = identities.allocate_binding_id()
            leaf = StructuralRuntimeLeaf(binding_id, shape.typ)
            binding = IRLeafBinding(result, binding_id, shape.typ)
            if shape.typ is TYPE_OBJECT:
                if not isinstance(shape.object_id, ObjectSemanticId):
                    raise CompileError("Internal error: Object array leaf has no ObjectSemanticId")
                object_updates[binding_id] = shape.object_id
            return leaf, (binding,)

        if isinstance(shape, (TupleResultShape, NamedOutputsResultShape)):
            if isinstance(shape, TupleResultShape):
                if not isinstance(result, IRTuple):
                    raise CompileError("Internal error: tuple structural item has invalid IR result")
                kind = StructuralBindingKind.TUPLE
                keyed = [(("index", index), child_shape, child) for index, (child_shape, child) in enumerate(zip(shape.items, result.items))]
            else:
                if not isinstance(result, IRNamedOutputs):
                    raise CompileError("Internal error: named-output structural item has invalid IR result")
                result_by_name = dict(result.items)
                kind = StructuralBindingKind.NAMED_OUTPUTS
                keyed = []
                for item_name, child_shape in shape.items:
                    child = result_by_name.get(item_name)
                    if child is None:
                        raise CompileError("Internal error: named-output structural result is incomplete")
                    keyed.append((("name", item_name), child_shape, child))
            leaves = []
            bindings = []
            for projection_key, child_shape, child in keyed:
                binding_id = identities.allocate_binding_id()
                leaves.append(StructuralLeafBinding(projection_key, binding_id, child_shape.typ))
                bindings.append(IRLeafBinding(child, binding_id, child_shape.typ))
                if child_shape.typ is TYPE_OBJECT:
                    if not isinstance(child_shape.object_id, ObjectSemanticId):
                        raise CompileError("Internal error: Object structural leaf has no ObjectSemanticId")
                    object_updates[binding_id] = child_shape.object_id
            return StructuralBindingSymbol(kind, tuple(leaves)), tuple(bindings)

        if isinstance(shape, ArrayResultShape):
            if not isinstance(result, IRArray):
                raise CompileError("Internal error: array structural item has invalid IR result")
            if len(result.items) != len(shape.items):
                raise CompileError("Internal error: array IR/result-shape length mismatch")
            array_id = identities.allocate_array_id()
            child_nodes = ()
            if isinstance(node, (ast.List, ast.Tuple)):
                child_nodes = tuple(node.elts)
            if child_nodes and len(child_nodes) != len(shape.items):
                raise CompileError("Internal error: array AST/result-shape length mismatch")
            items = []
            bindings = []
            for index, (child_result, child_shape) in enumerate(zip(result.items, shape.items)):
                child_node = child_nodes[index] if child_nodes else None
                item, item_bindings = _plan_persisted_item(
                    child_node,
                    child_result,
                    child_shape,
                    analyzed,
                    planned_states,
                    object_updates,
                )
                items.append(item)
                bindings.extend(item_bindings)
            planned_states[array_id] = StructuralArrayState(tuple(items))
            return StructuralArrayRef(array_id), tuple(bindings)

        raise CompileError("Internal error: unsupported structural result shape")

    def _plan_array_result(expr, analyzed: _AnalyzedBodyExpression, active: _BodySemanticState):
        """Plan array identity/state plus exact IR leaf publications for one expression result."""
        if not isinstance(analyzed.result_shape, ArrayResultShape) or not isinstance(analyzed.program.result, IRArray):
            raise CompileError("Internal error: array persistence requires an IRArray result")
        root_fact = _expression_fact(analyzed, expr)
        if root_fact is not None and root_fact.array_id is not None:
            return root_fact.array_id, {}, None, {}

        planned_states: dict[StructuralArrayId, StructuralArrayState] = {}
        object_updates: dict[BindingId, ObjectSemanticId] = {}
        root_ref, bindings = _plan_persisted_item(
            expr,
            analyzed.program.result,
            analyzed.result_shape,
            analyzed,
            planned_states,
            object_updates,
        )
        if not isinstance(root_ref, StructuralArrayRef):
            raise CompileError("Internal error: array persistence did not produce an array identity")
        combined_states = dict(active.array_states)
        combined_states.update(planned_states)
        _validate_array_graph(combined_states, root_ref.array_id)
        # Construct the IR statement before any Object/body-state publication so constructor
        # invariants cannot require rollback inside one source statement.
        bind_statement = IRBindLeaves(analyzed.program, tuple(bindings)) if bindings else None
        return root_ref.array_id, planned_states, bind_statement, object_updates

    def _commit_array_plan(
        active: _BodySemanticState,
        analyzed: _AnalyzedBodyExpression,
        planned_states,
        object_updates,
    ) -> None:
        """Publish one fully validated structural-array plan atomically to frontend state."""
        active.adopt_object_snapshot(analyzed.object_semantics)
        active.array_states.update(planned_states)
        active.object_ids_by_binding.update(object_updates)

    def _snapshot_lexical_name(active: _BodySemanticState, active_compile_time: CompileTimeState, name: str):
        """Capture one loop-target name across all frontend ownership domains."""
        runtime = active.runtime_bindings.get(name)
        structural = active.structural_bindings.get(name)
        binding_ids = []
        if runtime is not None:
            binding_ids.append(runtime.binding_id)
        if structural is not None:
            binding_ids.extend(leaf.binding_id for leaf in structural.leaves)
        object_bindings = {
            binding_id: active.object_ids_by_binding[binding_id]
            for binding_id in binding_ids
            if binding_id in active.object_ids_by_binding
        }
        runtime_origin = (
            active.interface_input_origins.get(runtime.binding_id)
            if runtime is not None
            else None
        )
        return (
            runtime,
            structural,
            active.array_bindings.get(name),
            active.extension_bindings.get(name),
            active_compile_time.contains(name),
            active_compile_time.get(name),
            object_bindings,
            runtime_origin,
        )

    def _restore_lexical_name(active: _BodySemanticState, active_compile_time: CompileTimeState, name: str, snapshot) -> None:
        """Restore one loop-target name without disturbing non-target loop mutations."""
        runtime, structural, array_id, extension_binding, had_const, const_value, object_bindings, runtime_origin = snapshot
        current_runtime = active.runtime_bindings.get(name)
        if current_runtime is not None:
            active.interface_input_origins.pop(current_runtime.binding_id, None)
        active.runtime_bindings.pop(name, None)
        active.structural_bindings.pop(name, None)
        active.array_bindings.pop(name, None)
        active.extension_bindings.pop(name, None)
        if runtime is not None:
            active.runtime_bindings[name] = runtime
            if runtime_origin is not None:
                active.interface_input_origins[runtime.binding_id] = runtime_origin
            else:
                active.interface_input_origins.pop(runtime.binding_id, None)
        elif structural is not None:
            active.structural_bindings[name] = structural
        elif array_id is not None:
            active.array_bindings[name] = array_id
        elif extension_binding is not None:
            active.extension_bindings[name] = extension_binding
        active.object_ids_by_binding.update(object_bindings)
        if had_const:
            active_compile_time.bind(name, const_value)
        else:
            active_compile_time.discard(name)

    def _bind_loop_runtime_leaf(
        active: _BodySemanticState,
        active_compile_time: CompileTimeState,
        name: str,
        item: StructuralRuntimeLeaf,
        temp_binding_id: BindingId,
    ):
        """Bind one runtime structural leaf to a lexical loop-target slot without Blender work."""
        _clear_name_runtime_structural(active, name)
        active.array_bindings.pop(name, None)
        active_compile_time.discard(name)
        active.runtime_bindings[name] = RuntimeBindingSymbol(temp_binding_id, item.typ)
        source_origin = active.interface_input_origins.get(item.binding_id)
        if source_origin is not None:
            active.interface_input_origins[temp_binding_id] = source_origin
        else:
            active.interface_input_origins.pop(temp_binding_id, None)
        clear_binding_object(active, temp_binding_id)
        if item.typ is TYPE_OBJECT:
            object_id = active.object_ids_by_binding.get(item.binding_id)
            if not isinstance(object_id, ObjectSemanticId):
                raise CompileError("Internal error: Object array loop item has no ObjectSemanticId")
            active.object_ids_by_binding[temp_binding_id] = object_id
        source = IRValue(0, item.typ)
        program = IRProgram((IRBinding(source, 0, item.binding_id),), source)
        return IRBindLeaves(program, (IRLeafBinding(source, temp_binding_id, item.typ),))

    def _bind_loop_item(
        active: _BodySemanticState,
        active_compile_time: CompileTimeState,
        name: str,
        item,
        temp_binding_id: BindingId,
        *,
        materialize_literal: bool = False,
    ):
        """Bind one compile-time/structural iteration item and return optional runtime publication IR."""
        validate_runtime_binding_target(name, reserved_name_labels)
        if materialize_literal and isinstance(item, (bool, int, float, str)):
            # Builder-loop literal materialization follows the canonical numeric literal
            # contract while preserving the existing node-emission behavior of this narrow path.
            if type(item) is bool:
                typ = NFType.BOOL
                literal = item
            elif type(item) is int:
                typ = NFType.INT
                literal = normalize_int_constant(item)
            elif type(item) is float:
                typ = NFType.FLOAT
                literal = normalize_float_constant(item)
            else:
                typ = NFType.STRING
                literal = item
            _clear_name_runtime_structural(active, name)
            active.array_bindings.pop(name, None)
            active_compile_time.discard(name)
            active.runtime_bindings[name] = RuntimeBindingSymbol(temp_binding_id, typ)
            clear_binding_object(active, temp_binding_id)
            result = IRValue(0, typ)
            program = IRProgram((IRLiteral(result, 0, literal),), result)
            return IRBindLeaves(program, (IRLeafBinding(result, temp_binding_id, typ),))
        if isinstance(item, StructuralRuntimeLeaf):
            return _bind_loop_runtime_leaf(active, active_compile_time, name, item, temp_binding_id)
        if isinstance(item, StructuralBindingSymbol):
            active.runtime_bindings.pop(name, None)
            active.array_bindings.pop(name, None)
            active.extension_bindings.pop(name, None)
            active.structural_bindings[name] = item
            active_compile_time.discard(name)
            return None
        if isinstance(item, StructuralArrayRef):
            active_compile_time.discard(name)
            bind_array_alias(active, name, item.array_id)
            return None
        active.runtime_bindings.pop(name, None)
        active.structural_bindings.pop(name, None)
        active.array_bindings.pop(name, None)
        active.extension_bindings.pop(name, None)
        active_compile_time.bind(name, item)
        return None

    def _iterable_items(expr, active: _BodySemanticState, active_compile_time: CompileTimeState):
        """Resolve one ordinary compile-time/structural for iterable without backend materialization."""
        if isinstance(expr, ast.Name) and expr.id in active.array_bindings:
            array_id = active.array_bindings[expr.id]
            state_record = active.array_states.get(array_id)
            if state_record is None:
                raise CompileError("Internal error: structural array iterable references missing state")
            return tuple(state_record.items)
        try:
            raw = _const_eval(expr, active_compile_time.values)
        except ConstEvalUnavailable:
            raw = None
        if isinstance(raw, (list, tuple)):
            return tuple(raw)
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "range":
            raise CompileError("range(...) requires compile-time integer arguments; use repeat_range(...) for Repeat Zone loops")
        raise CompileError("for loop requires a compile-time iterable, an array, or repeat_range(...)")


    def analyze_runtime_expression(expr, active: _BodySemanticState, active_compile_time: CompileTimeState, statement_sink: list):
        for builder_name in _builder_read_names(expr, active):
            ensure_builder_current(active, builder_name, statement_sink)
        environment = build_semantic_environment(
            runtime_bindings=active.runtime_bindings,
            structural_bindings=active.structural_bindings,
            structural_arrays=active.array_snapshot(),
            builder_bindings={name: state_record.state_binding_id for name, state_record in active.builder_states.items()},
            extension_bindings=active.extension_bindings,
            compile_time=active_compile_time.snapshot(),
            reserved_name_labels=reserved_name_labels,
            callable_environment=callable_environment,
            object_semantics=active.object_snapshot(),
            available_group_context_slots=group_context_cursor.snapshot(),
            source_callable_session=source_callable_session,
            source_definition_owner=source_definition_owner or declaration_owner or owner_scope,
            source_owner_scope=owner_scope,
            source_call_site_allocator=identities.allocate_call_site_id,
            helper_namespace=helper_namespace,
            extension_registry=extension_registry,
        )
        analysis = analyze_expression(expr, environment)
        if analysis.object_semantics is None:
            raise CompileError("Internal error: body expression analysis lost Object semantic registry")
        if extension_registry is not None:
            for owner_key in analysis.used_extension_owners:
                fingerprint = extension_registry.owner_fingerprint(owner_key)
                previous = extension_dependency_sink.get(owner_key)
                if previous is not None and previous != fingerprint:
                    raise CompileError("Internal error: one extension owner produced conflicting fingerprints")
                extension_dependency_sink[owner_key] = fingerprint
        root_fact = analysis.facts[expr]
        group_context_cursor.replace(analysis.available_group_context_slots)
        if root_fact.semantic_payload is not None:
            return _AnalyzedBodyExpression(
                None,
                root_fact.result_shape,
                analysis.object_semantics,
                analysis,
                root_fact.semantic_payload,
            )
        program = lower_analyzed_expression(expr, analysis)
        return _AnalyzedBodyExpression(
            program,
            root_fact.result_shape,
            analysis.object_semantics,
            analysis,
        )

    def persist_extension_payload(
        analyzed: _AnalyzedBodyExpression,
        active: _BodySemanticState,
) -> tuple[ExtensionSemanticPayload, IRBindLeaves | None]:
        """Snapshot live transient semantic dependencies into fresh hidden body bindings."""
        payload = analyzed.semantic_payload
        if payload is None or analyzed.analysis is None:
            raise CompileError("Internal error: extension persistence requires analyzed semantic payload")
        transient = [
            dependency
            for dependency in payload.dependencies
            if not dependency.is_persistent
        ]
        carrier = None
        carrier_values: tuple[IRValue, ...] = ()
        if transient:
            carrier, carrier_values = lower_analyzed_dependency_sources(
                analyzed.analysis,
                tuple((dependency.source, dependency.typ) for dependency in transient),
            )
            if len(carrier_values) != len(transient):
                raise CompileError("Internal error: extension dependency carrier result count changed")

        transient_values = iter(carrier_values)
        normalized: list[ExtensionDependencySource] = []
        leaf_bindings: list[IRLeafBinding] = []
        for dependency in payload.dependencies:
            if dependency.is_persistent:
                normalized.append(dependency)
                continue
            source_value = next(transient_values)
            binding_id = identities.allocate_binding_id()
            normalized.append(ExtensionDependencySource(binding_id, dependency.typ))
            leaf_bindings.append(IRLeafBinding(source_value, binding_id, dependency.typ))

        persistent_payload = ExtensionSemanticPayload(
            payload.type_spec,
            payload.value,
            tuple(normalized),
        )
        statement = None
        if leaf_bindings:
            if carrier is None:
                raise CompileError("Internal error: extension persistence lost its dependency carrier")
            statement = IRBindLeaves(carrier, tuple(leaf_bindings))
        return persistent_payload, statement

    def accept_expression(expr, active: _BodySemanticState, active_compile_time: CompileTimeState, statement_sink: list):
        analyzed = analyze_runtime_expression(expr, active, active_compile_time, statement_sink)
        if analyzed.semantic_payload is not None:
            raise CompileError("Package-defined semantic value cannot be used where a runtime value is required")
        active.adopt_object_snapshot(analyzed.object_semantics)
        return analyzed

    def build_contextual_call_program(call, active, active_compile_time, statement_sink, *, builtin_name):
        """Build one contextual geometry statement from existing typed builtin semantics."""
        runtime_sources = []

        def add_runtime(child, parameter_name, context):
            if isinstance(child, ast.Name) and child.id == "__nodeforge_current_geometry__":
                runtime_sources.append(None)
                return NFType.GEOMETRY
            analyzed = analyze_runtime_expression(child, active, active_compile_time, statement_sink)
            if not isinstance(analyzed.result_shape, RuntimeResultShape) or not isinstance(analyzed.program.result, IRValue):
                if isinstance(analyzed.result_shape, TupleResultShape):
                    raise CompileError(
                        f"{context} received a tuple of {len(analyzed.result_shape.items)} values; "
                        "unpack it or select an element by a compile-time index"
                    )
                if isinstance(analyzed.result_shape, NamedOutputsResultShape):
                    raise CompileError(f"Named multi-output results are structural and cannot be used in {context}")
                if context == "store() value" and isinstance(analyzed.result_shape, ArrayResultShape):
                    raise CompileError("store() value cannot be an array")
                raise CompileError(f"{context} requires a runtime value")
            active.adopt_object_snapshot(analyzed.object_semantics)
            runtime_sources.append(analyzed.program)
            return analyzed.result_shape.typ

        if builtin_name == "store_named_attribute":
            semantics = analyze_contextual_store_call(call, active_compile_time.values, add_runtime)
        else:
            semantics = analyze_contextual_set_position_call(call, active_compile_time.values, add_runtime)
        operations = []
        arguments = []
        next_id = 0
        for operand_meta, source in zip(semantics.operands, runtime_sources):
            if source is None:
                value = IRValue(next_id, NFType.GEOMETRY)
                next_id += 1
                operations.append(IRContextRead(value, 1, GroupContextSlot.CURRENT_GEOMETRY))
            else:
                rebased = _rebase_ir_data(source, next_id)
                operations.extend(rebased.operations)
                value = rebased.result
                if not isinstance(value, IRValue):
                    raise CompileError("Internal error: contextual builtin operand lowered to structural result")
                next_id += _program_value_extent(source)
            if value.typ is not operand_meta.typ:
                raise CompileError("Internal error: contextual builtin operand type changed during lowering")
            arguments.append(IRCallArgument(operand_meta.parameter_name, value))
        if not hasattr(semantics.result, "typ") or semantics.result.typ is not NFType.GEOMETRY:
            raise CompileError("Internal error: contextual geometry builtin must return Geometry")
        result = IRValue(next_id, NFType.GEOMETRY)
        operations.append(
            IRCall(
                (result,),
                0,
                IRCallableTarget(IRCallableKind.BUILTIN, builtin_name),
                tuple(arguments),
                tuple(semantics.options),
            )
        )
        operations.append(IRContextWrite(0, GroupContextSlot.CURRENT_GEOMETRY, result))
        return IRProgram(tuple(operations), result)

    def lower_statements(source_stmts, active: _BodySemanticState, active_compile_time: CompileTimeState, *, control_policy=None, repeat_merge_ids=None, repeat_merge_symbols=(), runtime_if_builder_baseline=None, root=False):
        statements = []

        repeat_type_by_binding_id = (
            {symbol.binding_id: symbol.typ for symbol in repeat_merge_symbols}
            if control_policy is BranchMergePolicy.REPEAT
            else {}
        )

        def validate_repeat_state_rebinding(
            name: str,
            assigned: NFType | SemanticResultShape,
        ) -> None:
            """Reject type or binding-category changes before mutating a Repeat carried state."""
            current = active.runtime_bindings.get(name)
            if current is None:
                return
            expected_type = repeat_type_by_binding_id.get(current.binding_id)
            if expected_type is None:
                return
            if isinstance(assigned, NFType):
                require_repeat_state_assignment(name, expected_type, assigned)
                return
            if isinstance(assigned, RuntimeResultShape):
                require_repeat_state_assignment(name, expected_type, assigned.typ)
                return
            if isinstance(assigned, ArrayResultShape):
                actual = "ARRAY"
            elif isinstance(assigned, TupleResultShape):
                actual = "TUPLE"
            elif isinstance(assigned, NamedOutputsResultShape):
                actual = "NAMED_OUTPUTS"
            else:
                raise CompileError("Internal error: unsupported semantic assignment result shape")
            raise CompileError(f"repeat_range state {name!r} changed type from {expected_type} to {actual}")

        def validate_repeat_extension_rebinding(
            name: str, analyzed: _AnalyzedBodyExpression
        ) -> None:
            """Reject runtime-carried Repeat state changing into extension semantic ownership."""
            if analyzed.semantic_payload is None:
                return
            current = active.runtime_bindings.get(name)
            if current is None or current.binding_id not in repeat_type_by_binding_id:
                return
            raise CompileError(
                f"repeat_range state {name!r} cannot change from runtime state to package semantic value"
            )

        def merge_runtime_if_extension_bindings(result) -> dict[str, ExtensionSemanticPayload]:
            """Join package semantic body state after the ordinary runtime-if merge is already valid."""
            base = active.extension_bindings
            true_state = result.true_state
            false_state = result.false_state

            def has_other_owner(state: _BodySemanticState, name: str) -> bool:
                """Return whether one branch owns *name* through a non-extension body category."""
                return any((
                    name in state.runtime_bindings,
                    name in state.structural_bindings,
                    name in state.array_bindings,
                    name in state.builder_states,
                ))

            merged = dict(base)
            names = set(base) | set(true_state.extension_bindings) | set(false_state.extension_bindings)
            for name in names:
                incoming = base.get(name)
                true_value = true_state.extension_bindings.get(name)
                false_value = false_state.extension_bindings.get(name)
                if true_value is not None and false_value is not None:
                    if true_value != false_value:
                        raise CompileError(
                            f"runtime if branches produce different package semantic values for {name!r}"
                        )
                    merged[name] = true_value
                    continue
                if true_value is None and false_value is None:
                    if incoming is not None:
                        raise CompileError(
                            f"runtime if branches disagree on package semantic ownership for {name!r}"
                        )
                    merged.pop(name, None)
                    continue
                present = true_value if true_value is not None else false_value
                missing_state = false_state if true_value is not None else true_state
                if incoming is not None or has_other_owner(missing_state, name):
                    raise CompileError(
                        f"runtime if branches disagree on package semantic ownership for {name!r}"
                    )
                # One-sided branch-local semantic values do not escape the runtime join.
                merged.pop(name, None)
            return merged

        def emit(statement):
            """Append executable IR and make its backend auto-output result authoritative."""
            statements.append(statement)
            active.clear_auto_final_output = False

        def emit_many(items):
            """Append executable IR while preserving a frontend-only clear across an empty batch."""
            items = tuple(items)
            statements.extend(items)
            if items:
                active.clear_auto_final_output = False

        for index, stmt in enumerate(source_stmts):
            if root:
                replay_compile_time_effects(compile_time_effects_before[index], active, active_compile_time)
            is_final = root and index == len(source_stmts) - 1

            if isinstance(stmt, ast.If):
                policy = control_policy or BranchMergePolicy.TOP_LEVEL
                if policy is BranchMergePolicy.TOP_LEVEL:
                    branch_region = tuple(stmt.body) + tuple(stmt.orelse)
                    replaced_names = _builder_constructor_targets(branch_region)
                    module = ast.Module(body=list(branch_region), type_ignores=[])
                    for builder_name in _builder_read_names(module, active):
                        if builder_name not in replaced_names:
                            ensure_builder_current(active, builder_name, statements)
                branch_builder_baseline = {
                    name: state_record.state_binding_id
                    for name, state_record in active.builder_states.items()
                }
                def branch_has_fresh_builder_identity(branch_state: _BodySemanticState) -> bool:
                    if policy is not BranchMergePolicy.TOP_LEVEL:
                        return False
                    for builder_name, state_record in branch_state.builder_states.items():
                        inherited_id = branch_builder_baseline.get(builder_name)
                        if inherited_id is None or state_record.state_binding_id != inherited_id:
                            return True
                    return False

                incoming_runtime_bindings = dict(active.runtime_bindings)
                result = lower_runtime_if(
                    stmt,
                    base_state=active,
                    compile_time=active_compile_time,
                    policy=policy,
                    analyze_condition=lambda expr, branch_state, branch_compile_time: accept_expression(
                        expr, branch_state, branch_compile_time, statements
                    ),
                    lower_branch=lambda branch, branch_state, branch_compile_time, branch_policy: lower_statements(
                        branch,
                        branch_state,
                        branch_compile_time,
                        control_policy=branch_policy,
                        repeat_merge_ids=repeat_merge_ids,
                        repeat_merge_symbols=repeat_merge_symbols,
                        runtime_if_builder_baseline=branch_builder_baseline,
                        root=False,
                    ),
                    merge_binding_ids=repeat_merge_ids,
                    merge_symbols=repeat_merge_symbols,
                    identity_assignment_merge_eligible=lambda true_state, false_state: (
                        branch_has_fresh_builder_identity(true_state)
                        or branch_has_fresh_builder_identity(false_state)
                    ),
                )

                # A builder identity created/replaced independently in both runtime branches is not
                # mergeable as one frontend identity. Preserve the diagnostic instead of silently
                # treating two fresh branch-local identities as equivalent. One-sided branch-local
                # construction is discarded with its branch state.
                if policy is BranchMergePolicy.TOP_LEVEL:
                    common_builder_names = set(result.true_state.builder_states) & set(result.false_state.builder_states)
                    for builder_name in sorted(common_builder_names):
                        inherited_id = branch_builder_baseline.get(builder_name)
                        true_id = result.true_state.builder_states[builder_name].state_binding_id
                        false_id = result.false_state.builder_states[builder_name].state_binding_id
                        true_is_local = inherited_id is None or true_id != inherited_id
                        false_is_local = inherited_id is None or false_id != inherited_id
                        if true_is_local and false_is_local:
                            raise CompileError("geometry_builder cannot escape script scope")

                merged_extension_bindings = merge_runtime_if_extension_bindings(result)
                emit(result.statement)
                active_compile_time.replace(result.merged_compile_time)
                active.extension_bindings = merged_extension_bindings

                invalidated_runtime_bindings = _runtime_if_invalidated_incoming_runtime_bindings(
                    incoming_runtime_bindings, result.true_state, result.false_state
                )
                for name, incoming_symbol in invalidated_runtime_bindings:
                    current = active.runtime_bindings.get(name)
                    if current is None or current.binding_id != incoming_symbol.binding_id:
                        raise CompileError(
                            "Internal error: runtime-if parent binding changed before convergence publication"
                        )
                    active.runtime_bindings.pop(name)
                    clear_binding_object(active, incoming_symbol.binding_id)
                    active.interface_input_origins.pop(incoming_symbol.binding_id, None)

                # Runtime merge publication is independent of compile-time state.
                for merge in result.statement.merges:
                    builder_state = active.builder_states.get(merge.source_name)
                    if builder_state is not None and builder_state.state_binding_id == merge.binding_id:
                        active.changed_runtime_ids.add(merge.binding_id)
                        continue
                    active.runtime_bindings[merge.source_name] = RuntimeBindingSymbol(merge.binding_id, merge.typ)
                    active.structural_bindings.pop(merge.source_name, None)
                    active.array_bindings.pop(merge.source_name, None)
                    active.extension_bindings.pop(merge.source_name, None)
                    active.changed_runtime_ids.add(merge.binding_id)
                    active.explicitly_assigned_runtime_ids.add(merge.binding_id)
                    clear_binding_object(active, merge.binding_id)
                    active.interface_input_origins.pop(merge.binding_id, None)
                continue

            if isinstance(stmt, ast.For):
                parsed = parse_repeat_range_for(stmt)
                if parsed is None:
                    unpacking_target_names = _unpacking_loop_target_names(
                        stmt.target, stmt.body,
                        structural_iterable=isinstance(stmt.iter, ast.Name) and stmt.iter.id in active.array_bindings,
                    )
                    iterable_items = _iterable_items(stmt.iter, active, active_compile_time)
                    target_names = unpacking_target_names or _ordinary_for_target_names(stmt.target)
                    for name in target_names:
                        validate_runtime_binding_target(name, reserved_name_labels)
                        if name in active.builder_states:
                            raise CompileError("Cannot assign over geometry_builder binding")

                    lexical_snapshots = {
                        name: _snapshot_lexical_name(active, active_compile_time, name)
                        for name in target_names
                    }
                    missing = object()
                    ordinary_reservations = {
                        name: identities.ordinary_reservations.get(name, missing)
                        for name in target_names
                    }
                    structural_reservations = {
                        name: {
                            key: value
                            for key, value in identities.structural_reservations.items()
                            if key[0] == name
                        }
                        for name in target_names
                    }
                    temp_binding_ids = {name: identities.allocate_binding_id() for name in target_names}
                    for name in target_names:
                        identities.ordinary_reservations[name] = temp_binding_ids[name]
                        for key in list(identities.structural_reservations):
                            if key[0] == name:
                                identities.structural_reservations.pop(key)

                    try:
                        for item in iterable_items:
                            if len(target_names) == 1:
                                assignments = (item,)
                            else:
                                if isinstance(item, StructuralArrayRef):
                                    item = active.array_states[item.array_id].items
                                if not isinstance(item, (list, tuple)):
                                    raise CompileError(f"Cannot unpack scalar compile-time loop item into {len(target_names)} names")
                                if len(item) != len(target_names):
                                    raise CompileError(f"Tuple unpacking expected {len(target_names)} values, got {len(item)}")
                                assignments = tuple(item)
                            prelude = []
                            for name, value in zip(target_names, assignments):
                                bound = _bind_loop_item(
                                    active,
                                    active_compile_time,
                                    name,
                                    value,
                                    temp_binding_ids[name],
                                    materialize_literal=unpacking_target_names is not None,
                                )
                                if bound is not None:
                                    prelude.append(bound)
                            emit_many(prelude)
                            lowered = lower_statements(
                                stmt.body,
                                active,
                                active_compile_time,
                                control_policy=control_policy,
                                repeat_merge_ids=repeat_merge_ids,
                                repeat_merge_symbols=repeat_merge_symbols,
                                runtime_if_builder_baseline=runtime_if_builder_baseline,
                                root=False,
                            )
                            statements.extend(lowered.statements)
                    finally:
                        for name in target_names:
                            active.changed_runtime_ids.discard(temp_binding_ids[name])
                            clear_binding_object(active, temp_binding_ids[name])
                            _restore_lexical_name(
                                active,
                                active_compile_time,
                                name,
                                lexical_snapshots[name],
                            )
                            for key in list(identities.structural_reservations):
                                if key[0] == name:
                                    temp_id = identities.structural_reservations.pop(key)
                                    active.changed_runtime_ids.discard(temp_id)
                                    clear_binding_object(active, temp_id)
                            identities.structural_reservations.update(structural_reservations[name])
                            previous = ordinary_reservations[name]
                            if previous is missing:
                                identities.ordinary_reservations.pop(name, None)
                            else:
                                identities.ordinary_reservations[name] = previous
                    continue
                iterations_expr, repeat_body, iteration_name = parsed
                validate_runtime_binding_target(iteration_name, reserved_name_labels)
                repeat_compile_time = active_compile_time.fork()
                try:
                    repeat_count_constant = _const_eval(iterations_expr, repeat_compile_time.values)
                except ConstEvalUnavailable:
                    repeat_count_constant = None
                if isinstance(repeat_count_constant, int) and not isinstance(repeat_count_constant, bool):
                    count_value = IRValue(0, NFType.INT)
                    iterations = _AnalyzedBodyExpression(
                        IRProgram((IRLiteral(count_value, 0, repeat_count_constant),), count_value),
                        RuntimeResultShape(NFType.INT),
                        active.object_snapshot(),
                    )
                else:
                    iterations = accept_expression(iterations_expr, active, active_compile_time, statements)
                    if not isinstance(iterations.result_shape, RuntimeResultShape) or iterations.result_shape.typ is not NFType.INT:
                        raise CompileError("repeat_range(n) expects an Int value")

                if repeat_body_has_nonruntime_for(repeat_body):
                    # Preserve the current Repeat grammar as a controlled frontend diagnostic.
                    repeat_mutation_names(repeat_body)
                    raise CompileError("repeat_range body supports assignments, builder methods, if blocks, and nested repeat_range loops")
                repeat_region = ast.Module(body=list(repeat_body), type_ignores=[])
                for builder_name in _builder_read_names(repeat_region, active):
                    ensure_builder_current(active, builder_name, statements)
                candidate_names = repeat_mutation_names(repeat_body)
                for name in candidate_names:
                    if name in active.extension_bindings:
                        raise CompileError(
                            f"repeat_range cannot carry or rebind package semantic value {name!r}"
                        )
                state_records = []
                entry_symbols = dict(active.runtime_bindings)
                builder_state_ids = set()
                for name in candidate_names:
                    builder_state = active.builder_states.get(name)
                    if builder_state is not None:
                        ensure_builder_current(active, name, statements)
                        builder_state = active.builder_states[name]
                        builder_state_ids.add(builder_state.state_binding_id)
                        state_records.append(
                            IRRepeatState(
                                binding_id=builder_state.state_binding_id,
                                source_name=name,
                                typ=NFType.GEOMETRY,
                                publish_to_parent=True,
                            )
                        )
                        continue
                    symbol = entry_symbols.get(name)
                    if symbol is None:
                        continue
                    if symbol.typ not in {NFType.GEOMETRY, NFType.VECTOR, NFType.FLOAT, NFType.INT, NFType.BOOL, NFType.BUNDLE}:
                        raise CompileError(f"repeat_range state {name!r} has unsupported type {symbol.typ}")
                    state_records.append(
                        IRRepeatState(
                            binding_id=symbol.binding_id,
                            source_name=name,
                            typ=symbol.typ,
                            publish_to_parent=symbol.binding_id not in active.lexical_iteration_ids,
                        )
                    )
                if not state_records:
                    raise CompileError("repeat_range loop must update at least one existing variable or geometry_builder")
                state_names = [record.source_name for record in state_records]
                if iteration_name in state_names:
                    raise CompileError("repeat_range loop index name cannot also be a state variable")

                iteration_binding_id = identities.allocate_binding_id()
                repeat_state = active.fork()
                repeat_state.runtime_bindings = dict(active.runtime_bindings)
                repeat_state.structural_bindings = dict(active.structural_bindings)
                repeat_state.object_ids_by_binding = dict(active.object_ids_by_binding)
                for record in state_records:
                    if record.binding_id in builder_state_ids:
                        repeat_state.builder_states[record.source_name] = GeometryBuilderState(record.binding_id, (), True)
                    else:
                        repeat_state.runtime_bindings[record.source_name] = RuntimeBindingSymbol(record.binding_id, record.typ)
                repeat_state.runtime_bindings[iteration_name] = RuntimeBindingSymbol(iteration_binding_id, NFType.INT)
                repeat_state.lexical_iteration_ids.add(iteration_binding_id)
                repeat_ir_body = lower_statements(
                    repeat_body,
                    repeat_state,
                    repeat_compile_time,
                    control_policy=BranchMergePolicy.REPEAT,
                    repeat_merge_ids=tuple(record.binding_id for record in state_records),
                    repeat_merge_symbols=tuple(
                        RuntimeMergeSymbol(record.binding_id, record.source_name, record.typ)
                        for record in state_records
                    ),
                    root=False,
                )
                for record in state_records:
                    if record.binding_id in builder_state_ids:
                        exit_builder = repeat_state.builder_states.get(record.source_name)
                        if exit_builder is None or exit_builder.state_binding_id != record.binding_id:
                            raise CompileError(f"Internal error: missing GeometryBuilder Repeat state {record.source_name!r} after semantic body")
                        continue
                    exit_symbol = repeat_state.runtime_bindings.get(record.source_name)
                    if exit_symbol is None:
                        raise CompileError(f"Internal error: missing Repeat state {record.source_name!r} after semantic body")
                    require_repeat_state_assignment(record.source_name, record.typ, exit_symbol.typ)
                repeat = IRRepeat(iterations.program, iteration_binding_id, iteration_name, tuple(state_records), repeat_ir_body)
                emit(repeat)
                active_compile_time.replace(repeat_compile_time)
                for record in state_records:
                    if not record.publish_to_parent:
                        continue
                    if record.binding_id in builder_state_ids:
                        active.builder_states[record.source_name] = GeometryBuilderState(record.binding_id, (), True)
                        active.changed_runtime_ids.add(record.binding_id)
                        continue
                    active.runtime_bindings[record.source_name] = RuntimeBindingSymbol(record.binding_id, record.typ)
                    active.changed_runtime_ids.add(record.binding_id)
                    clear_binding_object(active, record.binding_id)
                    active.interface_input_origins.pop(record.binding_id, None)
                continue

            if isinstance(stmt, ast.Assign):
                if len(stmt.targets) != 1:
                    raise CompileError("Assignment supports one name or one flat unpacking target")
                target_node = stmt.targets[0]
                if isinstance(target_node, (ast.Tuple, ast.List)):
                    names = _tuple_target_names(target_node)
                    for name in names:
                        validate_runtime_binding_target(name, reserved_name_labels)
                        if name in active.builder_states:
                            raise CompileError("Cannot assign over geometry_builder binding")
                    analyzed = accept_expression(stmt.value, active, active_compile_time, statements)
                    if not isinstance(analyzed.result_shape, TupleResultShape) or not isinstance(analyzed.program.result, IRTuple):
                        raise CompileError(f"Cannot unpack scalar result into {len(names)} names")
                    if len(analyzed.result_shape.items) != len(names):
                        raise CompileError(f"Tuple unpacking expected {len(names)} values, got {len(analyzed.result_shape.items)}")
                    bindings = []
                    for name, source, shape in zip(names, analyzed.program.result.items, analyzed.result_shape.items):
                        active_compile_time.discard(name)
                        validate_repeat_state_rebinding(name, shape)
                        symbol = bind_runtime(active, name, shape, changed=True)
                        bindings.append(IRLeafBinding(source, symbol.binding_id, shape.typ))
                    emit(IRBindLeaves(analyzed.program, tuple(bindings)))
                    continue
                if not isinstance(target_node, ast.Name):
                    raise CompileError("Only simple assignments like name = value are supported")
                target = target_node.id
                validate_runtime_binding_target(target, reserved_name_labels)
                if control_policy is not None and target in active.array_bindings:
                    raise CompileError("Structural array rebinding inside runtime control flow is not supported")

                is_builder_constructor = (
                    isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Name)
                    and stmt.value.func.id == "geometry_builder"
                )
                if target in active.builder_states and not is_builder_constructor:
                    raise CompileError("Cannot assign over geometry_builder binding")

                input_call = _direct_input_call(stmt.value)
                if input_call is not None:
                    active_compile_time.discard(target)
                    try:
                        input_semantics = analyze_input_declaration_call(input_call, active_compile_time.values)
                    except ValueError:
                        input_semantics = None
                    if input_semantics is not None:
                        validate_repeat_state_rebinding(target, input_semantics.typ)
                        symbol = bind_input(active, target, input_semantics.typ)
                        declaration_id = identities.allocate_input_declaration_id(target)
                        active.interface_input_origins[symbol.binding_id] = declaration_id
                        emit(
                            IRInputDeclaration(
                                target_binding_id=symbol.binding_id,
                                declaration_id=declaration_id,
                                target_name=target,
                                display_name=input_semantics.display_name,
                                typ=input_semantics.typ,
                                default=input_semantics.default,
                            )
                        )
                        continue

                if is_builder_constructor:
                    validate_geometry_builder_constructor(stmt.value)
                    if control_policy is BranchMergePolicy.REPEAT:
                        if target in active.builder_states:
                            raise CompileError("Cannot assign over geometry_builder binding")
                        raise CompileError("geometry_builder() must be assigned to a simple name")
                    _commit_builder_construction(active, active_compile_time, target)
                    # Constructor is frontend-only but historically clears implicit final-output selection.
                    active.clear_auto_final_output = True
                    continue
                compile_time_value = None
                has_compile_time_value = False
                if control_policy is BranchMergePolicy.REPEAT:
                    # Repeat assignments are runtime-state operations. Invalidate the assigned name in
                    # compile-time state instead of publishing a newly const-evaluated value so carried
                    # state cannot become a stale compile-time constant after Repeat construction.
                    pass
                elif isinstance(stmt.value, ast.List) and not stmt.value.elts:
                    # Empty-list declarations establish body-owned structural-array identity.
                    # They are deliberately residual even though CTFE can evaluate the literal.
                    pass
                else:
                    try:
                        compile_time_value = _const_eval(stmt.value, active_compile_time.values)
                    except ConstEvalUnavailable:
                        pass
                    except CompileError:
                        raise
                    else:
                        has_compile_time_value = True
                        if _is_compile_time_owned_assignment_rhs(stmt.value):
                            bind_compile_time_name(active, active_compile_time, target, compile_time_value)
                            continue
                analyzed = analyze_runtime_expression(stmt.value, active, active_compile_time, statements)
                validate_repeat_extension_rebinding(target, analyzed)
                if analyzed.semantic_payload is not None:
                    active.adopt_object_snapshot(analyzed.object_semantics)
                    persistent_payload, persistence_statement = persist_extension_payload(analyzed, active)
                    _clear_nonbuilder_name_ownership(active, target)
                    active_compile_time.discard(target)
                    active.extension_bindings[target] = persistent_payload
                    if persistence_statement is not None:
                        emit(persistence_statement)
                    continue
                validate_repeat_state_rebinding(target, analyzed.result_shape)
                if isinstance(analyzed.result_shape, ArrayResultShape):
                    array_id, planned_states, bind_statement, object_updates = _plan_array_result(
                        stmt.value,
                        analyzed,
                        active,
                    )
                    _commit_array_plan(active, analyzed, planned_states, object_updates)
                    active_compile_time.discard(target)
                    bind_array_alias(active, target, array_id)
                    if bind_statement is not None:
                        emit(bind_statement)
                    continue

                active.adopt_object_snapshot(analyzed.object_semantics)
                if isinstance(analyzed.result_shape, RuntimeResultShape) and isinstance(analyzed.program.result, IRValue):
                    existing = active.runtime_bindings.get(target)
                    source_binding_id = _program_binding_source(analyzed.program)
                    source_origin = active.interface_input_origins.get(source_binding_id) if source_binding_id is not None else None
                    unchanged = existing is not None and _program_is_binding_identity(analyzed.program, existing.binding_id)
                    symbol = bind_runtime(active, target, analyzed.result_shape, changed=not unchanged)
                    if source_origin is not None:
                        active.interface_input_origins[symbol.binding_id] = source_origin
                    active.explicitly_assigned_runtime_ids.add(symbol.binding_id)
                    emit(IRAssign(symbol.binding_id, target, analyzed.program))
                    if control_policy is BranchMergePolicy.REPEAT or isinstance(stmt.value, ast.List) and not stmt.value.elts:
                        active_compile_time.discard(target)
                    elif has_compile_time_value:
                        active_compile_time.bind(target, compile_time_value)
                    else:
                        active_compile_time.discard(target)
                    continue
                if isinstance(analyzed.result_shape, (TupleResultShape, NamedOutputsResultShape)) and isinstance(analyzed.program.result, (IRTuple, IRNamedOutputs)):
                    structural = bind_structural(active, target, analyzed.result_shape)
                    sources = analyzed.program.result.items if isinstance(analyzed.program.result, IRTuple) else tuple(value for _name, value in analyzed.program.result.items)
                    bindings = tuple(IRLeafBinding(source, leaf.binding_id, leaf.typ) for source, leaf in zip(sources, structural.leaves))
                    emit(IRBindLeaves(analyzed.program, bindings))
                    if has_compile_time_value:
                        active_compile_time.bind(target, compile_time_value)
                    else:
                        active_compile_time.discard(target)
                    continue
                raise CompileError("Internal error: unsupported semantic assignment result shape")

            if isinstance(stmt, ast.AugAssign):
                if not isinstance(stmt.target, ast.Name):
                    raise CompileError("Only simple augmented assignments like name += value are supported")
                target = stmt.target.id
                validate_runtime_binding_target(target, reserved_name_labels)
                current = active.runtime_bindings.get(target)
                if current is None and (target in active.structural_bindings or target in active.array_bindings):
                    bin_expr = ast.BinOp(left=ast.Name(id=target, ctx=ast.Load()), op=stmt.op, right=stmt.value)
                    accept_expression(bin_expr, active, active_compile_time, statements)
                    raise CompileError("Internal error: structural augmented assignment unexpectedly produced a runtime value")
                if current is None:
                    raise CompileError(f"Unknown name for augmented assignment: {target}")
                bin_expr = ast.BinOp(left=ast.Name(id=target, ctx=ast.Load()), op=stmt.op, right=stmt.value)
                analyzed = accept_expression(bin_expr, active, active_compile_time, statements)
                if not isinstance(analyzed.result_shape, RuntimeResultShape) or not isinstance(analyzed.program.result, IRValue):
                    raise CompileError("Internal error: augmented assignment lowered to a structural result")
                active_compile_time.discard(target)
                validate_repeat_state_rebinding(target, analyzed.result_shape)
                symbol = bind_runtime(active, target, analyzed.result_shape, changed=True)
                active.explicitly_assigned_runtime_ids.add(symbol.binding_id)
                emit(IRAssign(symbol.binding_id, target, analyzed.program))
                continue

            if isinstance(stmt, ast.Expr):
                call = _top_level_simple_call(stmt)
                if call is not None and call.func.id == "output":
                    if not root:
                        raise CompileError("output() is only supported as a top-level call")
                    if call.keywords:
                        kws = _kw_dict(call)
                        _check_extra(kws, {"name", "value"})
                        if call.args:
                            raise CompileError("output() cannot mix positional and keyword arguments")
                        if "value" not in kws:
                            raise CompileError('output(name="Name", value=value) expects value=...')
                        out_name = _unique_output_name(output_names, _literal_string(kws["name"], "output() name", active_compile_time.values)) if "name" in kws else _unique_output_name(output_names, "out")
                        value_expr = kws["value"]
                    elif len(call.args) == 1:
                        out_name = _unique_output_name(output_names, "out")
                        value_expr = call.args[0]
                    elif len(call.args) == 2:
                        out_name = _unique_output_name(output_names, _literal_string(call.args[0], "output() name", active_compile_time.values))
                        value_expr = call.args[1]
                    else:
                        raise CompileError('output(value), output("Name", value), or output(name="Name", value=value) expected')
                    analyzed = accept_expression(value_expr, active, active_compile_time, statements)
                    if isinstance(analyzed.result_shape, ArrayResultShape):
                        raise CompileError("output() cannot output an array directly; use join(array) or index it")
                    if not isinstance(analyzed.result_shape, RuntimeResultShape) or not isinstance(analyzed.program.result, IRValue):
                        if isinstance(analyzed.result_shape, TupleResultShape):
                            raise CompileError(
                                f"output() value received a tuple of {len(analyzed.result_shape.items)} values; "
                                "unpack it or select an element by a compile-time index"
                            )
                        if isinstance(analyzed.result_shape, NamedOutputsResultShape):
                            raise CompileError("Named multi-output results are structural and cannot be used in output() value")
                        raise CompileError("output() value requires a runtime value")
                    emit(IROutput(out_name, analyzed.program))
                    continue

                if (
                    isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Attribute)
                    and isinstance(stmt.value.func.value, ast.Name)
                    and stmt.value.func.value.id in active.builder_states
                ):
                    method_call = stmt.value
                    builder_name = method_call.func.value.id
                    method = method_call.func.attr
                    state_record = active.builder_states[builder_name]
                    if method not in {"add", "extend"}:
                        raise CompileError("geometry_builder supports only add(), extend(), and .geometry")
                    if control_policy is BranchMergePolicy.TOP_LEVEL and runtime_if_builder_baseline is not None:
                        inherited_id = runtime_if_builder_baseline.get(builder_name)
                        if inherited_id is not None and inherited_id == state_record.state_binding_id:
                            raise CompileError("geometry_builder mutations inside runtime if are supported only inside repeat_range(...)")
                    if len(method_call.args) != 1 or method_call.keywords:
                        if method == "add":
                            raise CompileError("builder.add(...) expects one positional Geometry argument")
                        raise CompileError("builder.extend(...) expects one positional array argument")
                    if method == "add":
                        analyzed = analyze_runtime_expression(method_call.args[0], active, active_compile_time, statements)
                        binding_id = _persist_builder_geometry_argument(
                            active, analyzed, statements, diagnostic="builder.add(...) expects Geometry"
                        )
                        _append_builder_binding(active, builder_name, binding_id, statements)
                    else:
                        analyzed = analyze_runtime_expression(method_call.args[0], active, active_compile_time, statements)
                        if not isinstance(analyzed.result_shape, ArrayResultShape) or not isinstance(analyzed.program.result, IRArray):
                            raise CompileError("builder.extend(...) expects an array of Geometry values")
                        if len(analyzed.result_shape.items) != len(analyzed.program.result.items):
                            raise CompileError("Internal error: builder.extend array shape mismatch")
                        for item_shape, item_result in zip(analyzed.result_shape.items, analyzed.program.result.items):
                            if not isinstance(item_shape, RuntimeResultShape) or item_shape.typ is not NFType.GEOMETRY or not isinstance(item_result, IRValue):
                                raise CompileError("builder.extend(...) expects an array of Geometry values")
                        # Validate the complete array before publishing any builder mutation.
                        bindings = []
                        for item_result in analyzed.program.result.items:
                            binding_id = identities.allocate_binding_id()
                            bindings.append((item_result, binding_id))
                        if bindings:
                            emit(
                                IRBindLeaves(
                                    analyzed.program,
                                    tuple(IRLeafBinding(result, binding_id, NFType.GEOMETRY) for result, binding_id in bindings),
                                )
                            )
                        active.adopt_object_snapshot(analyzed.object_semantics)
                        for _result, binding_id in bindings:
                            _append_builder_binding(active, builder_name, binding_id, statements)
                    # Builder mutation clears implicit final-output selection even when no executable
                    # IR is needed (for example, extend([])).
                    active.clear_auto_final_output = True
                    continue

                if call is not None and call.func.id == "panel":
                    if not root:
                        raise CompileError("panel() is a top-level interface declaration")
                    if len(call.args) != 1:
                        raise CompileError("panel() expects exactly one positional list or tuple of group inputs")
                    members_expr = call.args[0]
                    if not isinstance(members_expr, (ast.List, ast.Tuple)):
                        raise CompileError("panel() first argument must be a list or tuple of input variable names")
                    if not members_expr.elts:
                        raise CompileError("panel() requires at least one input")
                    if not all(isinstance(member, ast.Name) for member in members_expr.elts):
                        raise CompileError("panel() items must be simple input variable names")
                    kws = _kw_dict(call)
                    _check_extra(kws, {"name", "collapsed"})
                    if "name" not in kws:
                        raise CompileError("panel() requires name=")
                    panel_name = _literal_string(kws["name"], "panel() name", active_compile_time.values)
                    collapsed = False
                    if "collapsed" in kws:
                        try:
                            collapsed = _const_eval(kws["collapsed"], active_compile_time.values)
                        except ConstEvalUnavailable as exc:
                            raise CompileError("panel() collapsed= must be a compile-time bool") from exc
                        if type(collapsed) is not bool:
                            raise CompileError("panel() collapsed= must be a compile-time bool")
                    if panel_name in active.panel_names:
                        raise CompileError(f"panel() duplicate panel name: {panel_name}")
                    member_origins = []
                    seen_origins = set()
                    for member in members_expr.elts:
                        name = member.id
                        symbol = active.runtime_bindings.get(name)
                        origin = active.interface_input_origins.get(symbol.binding_id) if symbol is not None else None
                        if origin is None:
                            raise CompileError(f"panel() item {name} is not a group input")
                        if origin in seen_origins:
                            raise CompileError(f"panel() duplicate input: {name}")
                        existing_panel = active.panel_member_origins.get(origin)
                        if existing_panel is not None:
                            raise CompileError(f'panel() item {name} already belongs to panel "{existing_panel}"')
                        seen_origins.add(origin)
                        member_origins.append(origin)
                    active.panel_names.add(panel_name)
                    for origin in member_origins:
                        active.panel_member_origins[origin] = panel_name
                    emit(IRPanelDeclaration(tuple(member_origins), panel_name, collapsed))
                    active.clear_auto_final_output = True
                    continue
                if call is not None and call.func.id in {"store", "set_position"}:
                    if GroupContextSlot.CURRENT_GEOMETRY not in group_context_cursor.available_slots:
                        raise CompileError(f"Internal error: {call.func.id}() requires geometry mode")
                    builtin_name = "store_named_attribute" if call.func.id == "store" else "set_position"
                    program = build_contextual_call_program(
                        call, active, active_compile_time, statements, builtin_name=builtin_name
                    )
                    emit(IRDiscardExpression(program))
                    active.clear_auto_final_output = True
                    continue
                if (
                    isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Attribute)
                    and stmt.value.func.attr == "append"
                ):
                    append_call = stmt.value
                    if not isinstance(append_call.func.value, ast.Name) or len(append_call.args) != 1:
                        raise CompileError("append must look like items.append(value)")
                    array_name = append_call.func.value.id
                    array_id = active.array_bindings.get(array_name)
                    if array_id is None:
                        compile_time_value = active_compile_time.get(array_name)
                        if isinstance(compile_time_value, list):
                            raise CompileError("append() cannot promote a compile-time list to a runtime structural array")
                        raise CompileError(f"{array_name} is not an array")
                    if control_policy is not None:
                        raise CompileError("Structural array append inside runtime control flow is not supported")

                    analyzed = analyze_runtime_expression(append_call.args[0], active, active_compile_time, statements)
                    planned_states = {}
                    object_updates = {}
                    item, bindings = _plan_persisted_item(
                        append_call.args[0],
                        analyzed.program.result,
                        analyzed.result_shape,
                        analyzed,
                        planned_states,
                        object_updates,
                    )
                    current_state = active.array_states.get(array_id)
                    if current_state is None:
                        raise CompileError("Internal error: append receiver references missing structural array")
                    proposed = StructuralArrayState(current_state.items + (item,))
                    combined_states = dict(active.array_states)
                    combined_states.update(planned_states)
                    combined_states[array_id] = proposed
                    _validate_array_graph(combined_states, array_id)
                    bind_statement = IRBindLeaves(analyzed.program, tuple(bindings)) if bindings else None

                    # All recursive/cycle and IR leaf-binding checks are complete before Object or
                    # structural-array state is published for this source statement.
                    active.adopt_object_snapshot(analyzed.object_semantics)
                    active.array_states.update(planned_states)
                    active.array_states[array_id] = proposed
                    active.object_ids_by_binding.update(object_updates)
                    active_compile_time.discard(array_name)
                    if bind_statement is not None:
                        emit(bind_statement)
                    continue
                if isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Attribute):
                    if stmt.value.func.attr != "info":
                        raise CompileError("Object values support only the .info() method")
                    analyzed = accept_expression(stmt.value, active, active_compile_time, statements)
                    if not isinstance(analyzed.result_shape, RuntimeResultShape) or analyzed.result_shape.typ is not TYPE_OBJECT:
                        raise CompileError(".info() can only be used on Object values")
                    emit(IRDiscardExpression(analyzed.program))
                    continue
                if not is_final:
                    raise CompileError(
                        "Only assignments, array append, for/if blocks, store(), set_position() and output() may appear before the final expression"
                    )
                analyzed = accept_expression(stmt.value, active, active_compile_time, statements)
                if isinstance(analyzed.result_shape, ArrayResultShape):
                    raise CompileError("A final expression cannot be an array; use join(array) or index it")
                if not isinstance(analyzed.result_shape, RuntimeResultShape) or not isinstance(analyzed.program.result, IRValue):
                    if isinstance(analyzed.result_shape, TupleResultShape):
                        raise CompileError(
                            f"final expression received a tuple of {len(analyzed.result_shape.items)} values; "
                            "unpack it or select an element by a compile-time index"
                        )
                    if isinstance(analyzed.result_shape, NamedOutputsResultShape):
                        raise CompileError("Named multi-output results are structural and cannot be used in final expression")
                    raise CompileError("final expression requires a runtime value")
                emit(IRFinalExpression(analyzed.program))
                continue

            raise CompileError("Unsupported statement")
        if root:
            replay_compile_time_effects(trailing_compile_time_effects, active, active_compile_time)
        return IRBody(tuple(statements))

    body = lower_statements(stmts, state, compile_time, root=True)
    output_summary = _summarize_body_outputs(body, clear_auto_final_output=state.clear_auto_final_output)
    return BasicBodyCompilation(
        body,
        compile_time.snapshot(),
        state.array_snapshot(),
        output_summary,
        clear_auto_final_output=state.clear_auto_final_output,
    )


__all__ = ["BasicBodyCompilation", "BodyOutputSummary", "lower_basic_body", "validate_input_declaration_placement"]

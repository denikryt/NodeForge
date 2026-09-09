"""Pure Semantic IR construction for basic straight-line executable bodies.

This stage owns ordinary runtime assignments, fixed tuple/named-output
structural bindings, direct explicit inputs, explicit outputs, and migrated
Object semantics. Unsupported categories reject the complete body before
Blender lowering begins.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Mapping

from .builtin_call_semantics import (
    INPUT_DECLARATION_BUILTIN_NAMES,
    INPUT_DECLARATION_PLACEMENT_ERROR,
    analyze_input_declaration_call,
)
from .compiler_identities import BindingId, InputDeclarationId
from .constants import TYPE_OBJECT
from .consteval import _const_eval
from .compile_time import CompileTimeSnapshot, CompileTimeState
from .errors import CompileError
from .nf_types import NFType
from .parsing import _literal_string
from .runtime_bindings import RuntimeBindingSymbol, validate_runtime_binding_target
from .semantic_analysis import analyze_expression, build_semantic_environment
from .semantic_ir import (
    IRAssign,
    IRBindLeaves,
    IRBody,
    IRDiscardExpression,
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
from .semantic_lowering import lower_analyzed_expression
from .semantic_control_flow import (
    BranchMergePolicy,
    lower_runtime_if,
    parse_repeat_range_for,
    repeat_body_has_nonruntime_for,
    repeat_mutation_names,
    repeat_state_output_type,
    require_repeat_state_assignment,
)
from .semantic_values import (
    NamedOutputsResultShape,
    ObjectInfoState,
    ObjectSemanticId,
    ObjectSemanticSnapshot,
    RuntimeResultShape,
    StructuralBindingKind,
    StructuralBindingSymbol,
    StructuralLeafBinding,
    TupleResultShape,
)


@dataclass(frozen=True)
class _BodyUnsupported:
    """Sentinel proving the entire body must stay on the legacy statement route."""


BODY_UNSUPPORTED = _BodyUnsupported()


@dataclass(frozen=True)
class BasicBodyCompilation:
    """Return one accepted body plus its detached final compile-time state."""

    body: IRBody
    final_compile_time: CompileTimeSnapshot

    def __post_init__(self) -> None:
        """Require an immutable detached compile-time snapshot."""
        if not isinstance(self.final_compile_time, CompileTimeSnapshot):
            raise TypeError("final_compile_time must be a CompileTimeSnapshot")


@dataclass(frozen=True)
class _AnalyzedBodyExpression:
    """Pair one emitted expression program with semantic shape/state needed by body ownership."""

    program: IRProgram
    result_shape: object
    object_semantics: ObjectSemanticSnapshot


def _kw_dict(call: ast.Call) -> dict[str, ast.expr]:
    """Build the existing output keyword map with duplicate/**kwargs diagnostics."""
    result = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise CompileError("**kwargs are not supported")
        if kw.arg in result:
            raise CompileError(f"Duplicate keyword argument: {kw.arg}")
        result[kw.arg] = kw.value
    return result


def _check_no_extra_keywords(kws, allowed) -> None:
    """Apply the existing unsupported-keyword diagnostic."""
    extra = set(kws) - set(allowed)
    if extra:
        raise CompileError("Unsupported keyword argument(s): " + ", ".join(sorted(extra)))


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


def _reject_remaining_legacy_structural_binding():
    """Return whole-body fallback for intentionally out-of-scope structural categories."""
    # CONTROL_FLOW_IR_BODY_REMAINING_FALLBACK: IRBody now owns ordinary runtime if/repeat control flow,
    # fixed tuple/raw named-output bindings, and migrated Object semantics. Mutable source arrays,
    # GeometryBuilder state, compile-time iterable loops, stateful interface/geometry statements, and
    # dynamic callable/result categories remain whole-body fallback. Do not embed AST or backend objects
    # in control-flow IR. Remove this fallback when those remaining categories have explicit frontend
    # semantics and compile_statement() is no longer needed for supported bodies.
    return BODY_UNSUPPORTED


def _tuple_target_names(target_node: ast.Tuple | ast.List) -> list[str]:
    """Validate one legacy-compatible flat tuple/list unpack target and return its names."""
    if any(isinstance(item, ast.Starred) for item in target_node.elts):
        raise CompileError("Starred tuple unpacking is not supported")
    if not target_node.elts or not all(isinstance(item, ast.Name) for item in target_node.elts):
        raise CompileError("Tuple unpacking target must be a flat sequence of names")
    names = [item.id for item in target_node.elts]
    if len(set(names)) != len(names):
        raise CompileError("Tuple unpacking target names must be unique")
    return names


@dataclass
class _BodyIdentityAllocator:
    """Own non-rewinding identities shared by all semantic forks in one root body."""

    owner_scope: str
    declaration_owner: str
    next_local_id: int
    ordinary_reservations: dict[str, BindingId]
    structural_reservations: dict[tuple[str, tuple[str, int | str]], BindingId]
    input_declaration_ordinals: dict[str, int]
    next_object_id: int = 0

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

    def allocate_object_id(self) -> ObjectSemanticId:
        """Allocate one monotonic Object semantic identity."""
        object_id = ObjectSemanticId(self.next_object_id)
        self.next_object_id += 1
        return object_id


@dataclass
class _BodySemanticState:
    """Store forkable active frontend state for one structured body point."""

    runtime_bindings: dict[str, RuntimeBindingSymbol]
    structural_bindings: dict[str, StructuralBindingSymbol]
    object_ids_by_binding: dict[BindingId, ObjectSemanticId]
    object_states: dict[ObjectSemanticId, ObjectInfoState]
    identities: _BodyIdentityAllocator
    changed_runtime_ids: set[BindingId]
    lexical_iteration_ids: set[BindingId]

    def fork(self) -> "_BodySemanticState":
        """Copy active state while retaining the shared non-rewinding allocator."""
        return _BodySemanticState(
            dict(self.runtime_bindings),
            dict(self.structural_bindings),
            dict(self.object_ids_by_binding),
            dict(self.object_states),
            self.identities,
            set(),
            set(self.lexical_iteration_ids),
        )

    def adopt(self, other: "_BodySemanticState") -> None:
        """Replace active state from one successfully analyzed lexical path."""
        self.runtime_bindings = dict(other.runtime_bindings)
        self.structural_bindings = dict(other.structural_bindings)
        self.object_ids_by_binding = dict(other.object_ids_by_binding)
        self.object_states = dict(other.object_states)
        self.changed_runtime_ids.update(other.changed_runtime_ids)
        self.lexical_iteration_ids = set(other.lexical_iteration_ids)

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


def _program_is_binding_identity(program: IRProgram, binding_id: BindingId) -> bool:
    """Return whether lowering *program* preserves the exact backend binding identity."""
    if len(program.operations) != 1 or not isinstance(program.operations[0], IRBinding):
        return False
    operation = program.operations[0]
    return operation.binding_id == binding_id and program.result == operation.result



def lower_basic_body(
    stmts,
    *,
    initial_runtime_bindings: Mapping[str, RuntimeBindingSymbol],
    initial_compile_time: CompileTimeSnapshot,
    legacy_binding_names,
    reserved_name_labels: Mapping[str, str],
    callable_environment,
    owner_scope: str,
    declaration_owner: str | None = None,
):
    """Lower one whole eligible source body to compiler-owned structured Semantic IR."""
    validate_input_declaration_placement(stmts)
    if any(symbol.binding_id.owner_scope != owner_scope for symbol in initial_runtime_bindings.values()):
        raise CompileError("Internal error: body runtime binding owner scope mismatch")
    local_ids = [symbol.binding_id.local_id for symbol in initial_runtime_bindings.values()]
    if len(local_ids) != len(set(local_ids)):
        raise CompileError("Internal error: duplicate body-entry BindingId")

    identities = _BodyIdentityAllocator(
        owner_scope=owner_scope,
        declaration_owner=declaration_owner or owner_scope,
        next_local_id=max(local_ids, default=-1) + 1,
        ordinary_reservations={name: symbol.binding_id for name, symbol in initial_runtime_bindings.items()},
        structural_reservations={},
        input_declaration_ordinals={},
    )
    if not isinstance(initial_compile_time, CompileTimeSnapshot):
        raise TypeError("initial_compile_time must be a CompileTimeSnapshot")
    compile_time = CompileTimeState(initial_compile_time.values)
    state = _BodySemanticState(
        dict(initial_runtime_bindings), {}, {}, {}, identities, set(), set()
    )
    for symbol in state.runtime_bindings.values():
        if symbol.typ is TYPE_OBJECT:
            object_id = identities.allocate_object_id()
            state.object_ids_by_binding[symbol.binding_id] = object_id
            state.object_states[object_id] = ObjectInfoState()

    output_names: set[str] = set()

    def clear_binding_object(active: _BodySemanticState, binding_id: BindingId) -> None:
        active.object_ids_by_binding.pop(binding_id, None)

    def bind_runtime(active: _BodySemanticState, name: str, shape: RuntimeResultShape, *, changed=True) -> RuntimeBindingSymbol:
        current = active.runtime_bindings.get(name)
        binding_id = current.binding_id if current is not None else identities.reserve_ordinary(name)
        old_structural = active.structural_bindings.pop(name, None)
        if old_structural is not None:
            for leaf in old_structural.leaves:
                clear_binding_object(active, leaf.binding_id)
        symbol = RuntimeBindingSymbol(binding_id, shape.typ)
        active.runtime_bindings[name] = symbol
        clear_binding_object(active, binding_id)
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
        runtime = active.runtime_bindings.pop(name, None)
        if runtime is not None:
            clear_binding_object(active, runtime.binding_id)
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

    def analyze_runtime_expression(expr, active: _BodySemanticState, active_compile_time: CompileTimeState):
        environment = build_semantic_environment(
            runtime_bindings=active.runtime_bindings,
            structural_bindings=active.structural_bindings,
            legacy_binding_names=legacy_binding_names,
            compile_time=active_compile_time.snapshot(),
            reserved_name_labels=reserved_name_labels,
            callable_environment=callable_environment,
            object_semantics=active.object_snapshot(),
        )
        analysis = analyze_expression(expr, environment)
        if analysis is None:
            # BASIC_BODY_IR_EXPRESSION_FALLBACK: Body IR accepts only expressions already fully owned
            # by the semantic expression pipeline. A dynamic/stateful call or legacy structural binding
            # therefore rejects the whole body instead of embedding AST/backend payloads or lowering one
            # statement early. Remove this branch when expression semantic analysis has no supported
            # production fallback categories left.
            return BODY_UNSUPPORTED
        if analysis.object_semantics is None:
            raise CompileError("Internal error: body expression analysis lost Object semantic registry")
        program = lower_analyzed_expression(expr, analysis)
        return _AnalyzedBodyExpression(program, analysis.facts[expr].result_shape, analysis.object_semantics)

    def accept_expression(expr, active: _BodySemanticState, active_compile_time: CompileTimeState):
        analyzed = analyze_runtime_expression(expr, active, active_compile_time)
        if analyzed is BODY_UNSUPPORTED:
            return BODY_UNSUPPORTED
        active.adopt_object_snapshot(analyzed.object_semantics)
        return analyzed

    def nested_control_flow_fallback():
        """Reject one unsupported nested control-flow region atomically at the root body."""
        # CONTROL_FLOW_IR_NESTED_ATOMIC_FALLBACK: A runtime if/repeat is Semantic IR only when every nested
        # statement and expression needed by that control-flow region is frontend-owned. If a nested builder,
        # mutable array, stateful side-effect statement, or dynamic callable still requires legacy semantics,
        # reject the complete root body before Blender lowering. Never splice compile_statement() into an
        # already lowered IRIf/IRRepeat. Remove this fallback when all supported nested body categories are IR.
        return BODY_UNSUPPORTED

    def lower_statements(source_stmts, active: _BodySemanticState, active_compile_time: CompileTimeState, *, control_policy=None, repeat_merge_ids=None, root=False):
        statements = []
        for index, stmt in enumerate(source_stmts):
            is_final = root and index == len(source_stmts) - 1

            if isinstance(stmt, ast.If):
                # Top-level statement compilation historically const-folds if before runtime lowering.
                # Repeat's dedicated legacy compile_if() does not: even a constant Bool is a Repeat-local
                # runtime branch and can affect carried-state topology. Preserve that contextual difference.
                if control_policy is not BranchMergePolicy.REPEAT:
                    try:
                        const_branch = stmt.body if bool(_const_eval(stmt.test, active_compile_time.values)) else stmt.orelse
                        trial = active.fork()
                        trial_compile_time = active_compile_time.fork()
                        folded = lower_statements(
                            const_branch,
                            trial,
                            trial_compile_time,
                            control_policy=control_policy,
                            root=False,
                        )
                        if folded is not BODY_UNSUPPORTED:
                            active.adopt(trial)
                            active_compile_time.replace(trial_compile_time)
                            statements.extend(folded.statements)
                            continue
                    except CompileError:
                        pass

                policy = control_policy or BranchMergePolicy.TOP_LEVEL
                result = lower_runtime_if(
                    stmt,
                    base_state=active,
                    compile_time=active_compile_time,
                    policy=policy,
                    analyze_condition=lambda expr, branch_state, branch_compile_time: accept_expression(
                        expr, branch_state, branch_compile_time
                    ),
                    lower_branch=lambda branch, branch_state, branch_compile_time, branch_policy: lower_statements(
                        branch,
                        branch_state,
                        branch_compile_time,
                        control_policy=branch_policy,
                        repeat_merge_ids=repeat_merge_ids,
                        root=False,
                    ),
                    unsupported_sentinel=BODY_UNSUPPORTED,
                    merge_binding_ids=repeat_merge_ids,
                )
                if result is BODY_UNSUPPORTED:
                    return nested_control_flow_fallback()
                statements.append(result.statement)
                active_compile_time.replace(result.false_compile_time)
                # Runtime merge publication is independent of compile-time state.
                for merge in result.statement.merges:
                    active.runtime_bindings[merge.source_name] = RuntimeBindingSymbol(merge.binding_id, merge.typ)
                    active.structural_bindings.pop(merge.source_name, None)
                    active.changed_runtime_ids.add(merge.binding_id)
                    clear_binding_object(active, merge.binding_id)
                continue

            if isinstance(stmt, ast.For):
                parsed = parse_repeat_range_for(stmt)
                if parsed is None:
                    return BODY_UNSUPPORTED
                iterations_expr, repeat_body, iteration_name = parsed
                if iteration_name in legacy_binding_names:
                    return BODY_UNSUPPORTED
                validate_runtime_binding_target(iteration_name, reserved_name_labels)
                repeat_compile_time = active_compile_time.fork()
                try:
                    repeat_count_constant = _const_eval(iterations_expr, repeat_compile_time.values)
                except CompileError:
                    repeat_count_constant = None
                if isinstance(repeat_count_constant, int) and not isinstance(repeat_count_constant, bool):
                    count_value = IRValue(0, NFType.INT)
                    iterations = _AnalyzedBodyExpression(
                        IRProgram((IRLiteral(count_value, 0, repeat_count_constant),), count_value),
                        RuntimeResultShape(NFType.INT),
                        active.object_snapshot(),
                    )
                else:
                    iterations = accept_expression(iterations_expr, active, active_compile_time)
                    if iterations is BODY_UNSUPPORTED:
                        return BODY_UNSUPPORTED
                    if not isinstance(iterations.result_shape, RuntimeResultShape) or iterations.result_shape.typ is not NFType.INT:
                        raise CompileError("repeat_range(n) expects an Int value")

                if repeat_body_has_nonruntime_for(repeat_body):
                    return BODY_UNSUPPORTED
                candidate_names = repeat_mutation_names(repeat_body)
                if any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"add", "extend"}
                    for sub in repeat_body
                    for node in ast.walk(sub)
                ):
                    return BODY_UNSUPPORTED
                state_records = []
                entry_symbols = dict(active.runtime_bindings)
                for name in candidate_names:
                    symbol = entry_symbols.get(name)
                    if symbol is None:
                        continue
                    if symbol.typ not in {NFType.GEOMETRY, NFType.VECTOR, NFType.FLOAT, NFType.INT, NFType.BOOL, NFType.BUNDLE}:
                        raise CompileError(f"repeat_range state {name!r} has unsupported type {symbol.typ}")
                    state_records.append(
                        IRRepeatState(
                            symbol.binding_id,
                            name,
                            symbol.typ,
                            repeat_state_output_type(symbol.typ),
                            len(state_records),
                            symbol.binding_id not in active.lexical_iteration_ids,
                        )
                    )
                if not state_records:
                    if any(name in legacy_binding_names for name in candidate_names):
                        return BODY_UNSUPPORTED
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
                    repeat_state.runtime_bindings[record.source_name] = RuntimeBindingSymbol(record.binding_id, record.input_type)
                repeat_state.runtime_bindings[iteration_name] = RuntimeBindingSymbol(iteration_binding_id, NFType.INT)
                repeat_state.lexical_iteration_ids.add(iteration_binding_id)
                repeat_ir_body = lower_statements(
                    repeat_body,
                    repeat_state,
                    repeat_compile_time,
                    control_policy=BranchMergePolicy.REPEAT,
                    repeat_merge_ids=tuple(record.binding_id for record in state_records),
                    root=False,
                )
                if repeat_ir_body is BODY_UNSUPPORTED:
                    return nested_control_flow_fallback()
                for record in state_records:
                    exit_symbol = repeat_state.runtime_bindings.get(record.source_name)
                    if exit_symbol is None:
                        raise CompileError(f"Internal error: missing Repeat state {record.source_name!r} after semantic body")
                    require_repeat_state_assignment(record.source_name, record.input_type, exit_symbol.typ)
                repeat = IRRepeat(iterations.program, iteration_binding_id, iteration_name, tuple(state_records), repeat_ir_body)
                statements.append(repeat)
                active_compile_time.replace(repeat_compile_time)
                for record in state_records:
                    if not record.publish_to_parent:
                        continue
                    active.runtime_bindings[record.source_name] = RuntimeBindingSymbol(record.binding_id, record.output_type)
                    active.changed_runtime_ids.add(record.binding_id)
                    clear_binding_object(active, record.binding_id)
                continue

            if isinstance(stmt, ast.Assign):
                if len(stmt.targets) != 1:
                    raise CompileError("Assignment supports one name or one flat unpacking target")
                target_node = stmt.targets[0]
                if isinstance(target_node, (ast.Tuple, ast.List)):
                    names = _tuple_target_names(target_node)
                    if any(name in legacy_binding_names for name in names):
                        return BODY_UNSUPPORTED
                    for name in names:
                        validate_runtime_binding_target(name, reserved_name_labels)
                    analyzed = accept_expression(stmt.value, active, active_compile_time)
                    if analyzed is BODY_UNSUPPORTED:
                        return BODY_UNSUPPORTED
                    if not isinstance(analyzed.result_shape, TupleResultShape) or not isinstance(analyzed.program.result, IRTuple):
                        if isinstance(analyzed.result_shape, (NamedOutputsResultShape, RuntimeResultShape)):
                            raise CompileError(f"Cannot unpack scalar result into {len(names)} names")
                        return _reject_remaining_legacy_structural_binding()
                    if len(analyzed.result_shape.items) != len(names):
                        raise CompileError(f"Tuple unpacking expected {len(names)} values, got {len(analyzed.result_shape.items)}")
                    bindings = []
                    for name, source, shape in zip(names, analyzed.program.result.items, analyzed.result_shape.items):
                        active_compile_time.discard(name)
                        symbol = bind_runtime(active, name, shape, changed=True)
                        bindings.append(IRLeafBinding(source, symbol.binding_id, shape.typ))
                    statements.append(IRBindLeaves(analyzed.program, tuple(bindings)))
                    continue
                if not isinstance(target_node, ast.Name):
                    raise CompileError("Only simple assignments like name = value are supported")
                target = target_node.id
                validate_runtime_binding_target(target, reserved_name_labels)
                if target in legacy_binding_names:
                    return BODY_UNSUPPORTED

                input_call = _direct_input_call(stmt.value)
                if input_call is not None:
                    active_compile_time.discard(target)
                    try:
                        input_semantics = analyze_input_declaration_call(input_call, active_compile_time.values)
                    except ValueError:
                        input_semantics = None
                    if input_semantics is not None:
                        symbol = bind_input(active, target, input_semantics.typ)
                        statements.append(
                            IRInputDeclaration(
                                target_binding_id=symbol.binding_id,
                                declaration_id=identities.allocate_input_declaration_id(target),
                                target_name=target,
                                display_name=input_semantics.display_name,
                                typ=input_semantics.typ,
                                default=input_semantics.default,
                            )
                        )
                        continue

                if isinstance(stmt.value, (ast.List, ast.Tuple)):
                    return _reject_remaining_legacy_structural_binding()
                if isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == "geometry_builder":
                    return BODY_UNSUPPORTED
                if control_policy is BranchMergePolicy.REPEAT:
                    # Legacy Repeat assignments are runtime-state operations: compile_runtime_stmt()
                    # always invalidates the assigned name in Compiler.compile_time instead of publishing a
                    # newly const-evaluated value. Preserve that contextual contract so a carried
                    # state cannot become a stale compile-time constant after Repeat construction.
                    active_compile_time.discard(target)
                else:
                    try:
                        active_compile_time.bind(target, _const_eval(stmt.value, active_compile_time.values))
                    except CompileError:
                        active_compile_time.discard(target)
                analyzed = accept_expression(stmt.value, active, active_compile_time)
                if analyzed is BODY_UNSUPPORTED:
                    return BODY_UNSUPPORTED
                if isinstance(analyzed.result_shape, RuntimeResultShape) and isinstance(analyzed.program.result, IRValue):
                    existing = active.runtime_bindings.get(target)
                    unchanged = existing is not None and _program_is_binding_identity(analyzed.program, existing.binding_id)
                    symbol = bind_runtime(active, target, analyzed.result_shape, changed=not unchanged)
                    statements.append(IRAssign(symbol.binding_id, target, analyzed.program))
                    continue
                if isinstance(analyzed.result_shape, (TupleResultShape, NamedOutputsResultShape)) and isinstance(analyzed.program.result, (IRTuple, IRNamedOutputs)):
                    structural = bind_structural(active, target, analyzed.result_shape)
                    sources = analyzed.program.result.items if isinstance(analyzed.program.result, IRTuple) else tuple(value for _name, value in analyzed.program.result.items)
                    bindings = tuple(IRLeafBinding(source, leaf.binding_id, leaf.typ) for source, leaf in zip(sources, structural.leaves))
                    statements.append(IRBindLeaves(analyzed.program, bindings))
                    continue
                return _reject_remaining_legacy_structural_binding()

            if isinstance(stmt, ast.AugAssign):
                if not isinstance(stmt.target, ast.Name):
                    raise CompileError("Only simple augmented assignments like name += value are supported")
                target = stmt.target.id
                validate_runtime_binding_target(target, reserved_name_labels)
                if target in legacy_binding_names or target in active.structural_bindings:
                    return BODY_UNSUPPORTED
                current = active.runtime_bindings.get(target)
                if current is None:
                    raise CompileError(f"Unknown name for augmented assignment: {target}")
                bin_expr = ast.BinOp(left=ast.Name(id=target, ctx=ast.Load()), op=stmt.op, right=stmt.value)
                analyzed = accept_expression(bin_expr, active, active_compile_time)
                if analyzed is BODY_UNSUPPORTED:
                    return BODY_UNSUPPORTED
                if not isinstance(analyzed.result_shape, RuntimeResultShape) or not isinstance(analyzed.program.result, IRValue):
                    return _reject_remaining_legacy_structural_binding()
                active_compile_time.discard(target)
                symbol = bind_runtime(active, target, analyzed.result_shape, changed=True)
                statements.append(IRAssign(symbol.binding_id, target, analyzed.program))
                continue

            if isinstance(stmt, ast.Expr):
                call = _top_level_simple_call(stmt)
                if call is not None and call.func.id == "output":
                    if not root:
                        return BODY_UNSUPPORTED
                    if call.keywords:
                        kws = _kw_dict(call)
                        _check_no_extra_keywords(kws, {"name", "value"})
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
                    analyzed = accept_expression(value_expr, active, active_compile_time)
                    if analyzed is BODY_UNSUPPORTED:
                        return BODY_UNSUPPORTED
                    if not isinstance(analyzed.result_shape, RuntimeResultShape) or not isinstance(analyzed.program.result, IRValue):
                        return _reject_remaining_legacy_structural_binding()
                    statements.append(IROutput(out_name, analyzed.program))
                    continue

                if call is not None and call.func.id in {"panel", "store", "set_position"}:
                    return BODY_UNSUPPORTED
                if call is not None and call.func.id.startswith("input_"):
                    return BODY_UNSUPPORTED
                if isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Attribute):
                    if stmt.value.func.attr != "info":
                        return BODY_UNSUPPORTED
                    analyzed = accept_expression(stmt.value, active, active_compile_time)
                    if analyzed is BODY_UNSUPPORTED:
                        return BODY_UNSUPPORTED
                    if not isinstance(analyzed.result_shape, RuntimeResultShape) or analyzed.result_shape.typ is not TYPE_OBJECT:
                        return BODY_UNSUPPORTED
                    statements.append(IRDiscardExpression(analyzed.program))
                    continue
                if not is_final:
                    return BODY_UNSUPPORTED
                analyzed = accept_expression(stmt.value, active, active_compile_time)
                if analyzed is BODY_UNSUPPORTED:
                    return BODY_UNSUPPORTED
                if not isinstance(analyzed.result_shape, RuntimeResultShape) or not isinstance(analyzed.program.result, IRValue):
                    return _reject_remaining_legacy_structural_binding()
                statements.append(IRFinalExpression(analyzed.program))
                continue

            return BODY_UNSUPPORTED
        return IRBody(tuple(statements))

    body = lower_statements(stmts, state, compile_time, root=True)
    if body is BODY_UNSUPPORTED:
        return BODY_UNSUPPORTED
    return BasicBodyCompilation(body, compile_time.snapshot())


__all__ = ["BODY_UNSUPPORTED", "BasicBodyCompilation", "lower_basic_body", "validate_input_declaration_placement"]

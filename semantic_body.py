"""Pure Semantic IR construction for basic straight-line executable bodies.

this migration owns ordinary runtime assignments, fixed tuple/named-output
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
    ArrayResultShape,
    NamedOutputsResultShape,
    ObjectInfoState,
    ObjectSemanticId,
    ObjectSemanticSnapshot,
    RuntimeResultShape,
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
class _BodyUnsupported:
    """Sentinel proving the entire body must stay on the legacy statement route."""


BODY_UNSUPPORTED = _BodyUnsupported()


@dataclass(frozen=True)
class BasicBodyCompilation:
    """Return one accepted body plus detached final frontend semantic state."""

    body: IRBody
    final_compile_time: CompileTimeSnapshot
    final_structural_arrays: StructuralArraySnapshot

    def __post_init__(self) -> None:
        """Require immutable detached frontend snapshots."""
        if not isinstance(self.final_compile_time, CompileTimeSnapshot):
            raise TypeError("final_compile_time must be a CompileTimeSnapshot")
        if not isinstance(self.final_structural_arrays, StructuralArraySnapshot):
            raise TypeError("final_structural_arrays must be a StructuralArraySnapshot")


@dataclass(frozen=True)
class _AnalyzedBodyExpression:
    """Pair one emitted expression program with semantic shape/state needed by body ownership."""

    program: IRProgram
    result_shape: object
    object_semantics: ObjectSemanticSnapshot
    analysis: object | None = None


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
    # STRUCTURAL_ARRAYS_REMAINING_BODY_FALLBACK: Mutable structural arrays and ordinary compile-time
    # iterable loops are frontend-owned after structural-array migration. GeometryBuilder state, contextual group
    # statements (panel/store/set_position), grid/grid_uv state, and dynamic callable/result categories
    # still reject the complete root body before Blender lowering. Do not put AST/backend objects into
    # Semantic IR to bypass this boundary. Remove this marker when those remaining core categories have
    # permanent frontend semantics and the core whole-body compile_statement() fallback is removed.
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


def _ordinary_for_target_names(target_node) -> list[str]:
    """Return the one simple-name target accepted by migrated ordinary-for semantics."""
    if isinstance(target_node, ast.Name):
        return [target_node.id]
    raise CompileError("Only simple compile-time for targets are supported")


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


@dataclass
class _BodySemanticState:
    """Store forkable active frontend state for one structured body point."""

    runtime_bindings: dict[str, RuntimeBindingSymbol]
    structural_bindings: dict[str, StructuralBindingSymbol]
    array_bindings: dict[str, StructuralArrayId]
    array_states: dict[StructuralArrayId, StructuralArrayState]
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
            dict(self.array_bindings),
            dict(self.array_states),
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
        self.array_bindings = dict(other.array_bindings)
        self.array_states = dict(other.array_states)
        self.object_ids_by_binding = dict(other.object_ids_by_binding)
        self.object_states = dict(other.object_states)
        self.changed_runtime_ids.update(other.changed_runtime_ids)
        self.lexical_iteration_ids = set(other.lexical_iteration_ids)

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
        dict(initial_runtime_bindings), {}, {}, {}, {}, {}, identities, set(), set()
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
        active.array_bindings.pop(name, None)
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
        active.array_bindings.pop(name, None)
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

    def _clear_name_runtime_structural(active: _BodySemanticState, name: str) -> None:
        """Deactivate runtime/fixed-structural ownership before publishing an array alias."""
        runtime = active.runtime_bindings.pop(name, None)
        if runtime is not None:
            clear_binding_object(active, runtime.binding_id)
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
        return (
            runtime,
            structural,
            active.array_bindings.get(name),
            active_compile_time.contains(name),
            active_compile_time.get(name),
            object_bindings,
        )

    def _restore_lexical_name(active: _BodySemanticState, active_compile_time: CompileTimeState, name: str, snapshot) -> None:
        """Restore one loop-target name without disturbing non-target loop mutations."""
        runtime, structural, array_id, had_const, const_value, object_bindings = snapshot
        active.runtime_bindings.pop(name, None)
        active.structural_bindings.pop(name, None)
        active.array_bindings.pop(name, None)
        if runtime is not None:
            active.runtime_bindings[name] = runtime
        elif structural is not None:
            active.structural_bindings[name] = structural
        elif array_id is not None:
            active.array_bindings[name] = array_id
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
    ):
        """Bind one compile-time/structural iteration item and return optional runtime publication IR."""
        validate_runtime_binding_target(name, reserved_name_labels)
        if isinstance(item, StructuralRuntimeLeaf):
            return _bind_loop_runtime_leaf(active, active_compile_time, name, item, temp_binding_id)
        if isinstance(item, StructuralBindingSymbol):
            active.runtime_bindings.pop(name, None)
            active.array_bindings.pop(name, None)
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
        except CompileError:
            raw = None
        if isinstance(raw, (list, tuple)):
            return tuple(raw)
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "range":
            raise CompileError("range(...) requires compile-time integer arguments; use repeat_range(...) for Repeat Zone loops")
        raise CompileError("for loop requires a compile-time iterable, an array, or repeat_range(...)")


    def analyze_runtime_expression(expr, active: _BodySemanticState, active_compile_time: CompileTimeState):
        environment = build_semantic_environment(
            runtime_bindings=active.runtime_bindings,
            structural_bindings=active.structural_bindings,
            structural_arrays=active.array_snapshot(),
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
        return _AnalyzedBodyExpression(
            program,
            analysis.facts[expr].result_shape,
            analysis.object_semantics,
            analysis,
        )

    def accept_expression(expr, active: _BodySemanticState, active_compile_time: CompileTimeState):
        analyzed = analyze_runtime_expression(expr, active, active_compile_time)
        if analyzed is BODY_UNSUPPORTED:
            return BODY_UNSUPPORTED
        active.adopt_object_snapshot(analyzed.object_semantics)
        return analyzed

    def nested_control_flow_fallback():
        """Reject one unsupported nested control-flow region atomically at the root body."""
        # STRUCTURAL_ARRAYS_NESTED_REMAINING_FALLBACK: IRIf may now contain body-owned array reads and
        # frontend-unrolled non-mutating structural loops where the current DSL already accepts them.
        # Structural-array mutation/rebinding under runtime control flow has its own explicit compatibility
        # markers; ordinary non-repeat for-loops inside repeat_range remain a controlled source error. Nested
        # GeometryBuilder/contextual/grid/dynamic categories still require atomic whole-body fallback. Never
        # splice compile_statement() into accepted IRIf/IRRepeat. Remove this marker when every supported
        # nested core-body category is frontend-owned.
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
                    if not isinstance(stmt.target, ast.Name):
                        # STRUCTURAL_ARRAYS_NON_NAME_FOR_BODY_FALLBACK_COMPAT: In v0.51.3, any ordinary-for
                        # non-name target that survived compile-time preprocessing was handled by the legacy
                        # statement compiler, including runtime-dependent tuple/list unpack and the direct-append
                        # retention case. Keep the root body atomic and return BODY_UNSUPPORTED before iterable
                        # analysis, target binding, IR publication, or structural mutation. Remove this marker when
                        # all previously accepted non-name ordinary-for targets have permanent frontend semantics
                        # or the legacy ordinary-for compatibility route is intentionally retired.
                        return BODY_UNSUPPORTED
                    iterable_items = _iterable_items(stmt.iter, active, active_compile_time)
                    target_names = _ordinary_for_target_names(stmt.target)
                    if any(name in legacy_binding_names for name in target_names):
                        return BODY_UNSUPPORTED
                    for name in target_names:
                        validate_runtime_binding_target(name, reserved_name_labels)

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
                            assignments = (item,)
                            prelude = []
                            for name, value in zip(target_names, assignments):
                                bound = _bind_loop_item(
                                    active,
                                    active_compile_time,
                                    name,
                                    value,
                                    temp_binding_ids[name],
                                )
                                if bound is not None:
                                    prelude.append(bound)
                            statements.extend(prelude)
                            lowered = lower_statements(
                                stmt.body,
                                active,
                                active_compile_time,
                                control_policy=control_policy,
                                repeat_merge_ids=repeat_merge_ids,
                                root=False,
                            )
                            if lowered is BODY_UNSUPPORTED:
                                return BODY_UNSUPPORTED
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
                    # Preserve the current Repeat grammar as a controlled frontend diagnostic.
                    repeat_mutation_names(repeat_body)
                    raise CompileError("repeat_range body supports assignments, builder methods, if blocks, and nested repeat_range loops")
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
                if control_policy is not None and target in active.array_bindings:
                    # STRUCTURAL_ARRAYS_RUNTIME_REBIND_COMPAT: Legacy runtime-control-flow snapshots can expose
                    # source-visible behavior when an already-existing mutable array name is rebound inside a runtime
                    # if/repeat region. The structural-array migration does not redefine that merge/discard contract. Route the complete body
                    # through the existing atomic compatibility path before mutating semantic array state. Remove this
                    # marker only after a dedicated control-flow structural-mutation plan defines and tests array-name
                    # rebinding semantics without adding arrays to IRBranchMerge or IRRepeatState.
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
                analyzed = analyze_runtime_expression(stmt.value, active, active_compile_time)
                if analyzed is BODY_UNSUPPORTED:
                    return BODY_UNSUPPORTED
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
                        statements.append(bind_statement)
                    continue

                active.adopt_object_snapshot(analyzed.object_semantics)
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
                    if isinstance(analyzed.result_shape, ArrayResultShape):
                        raise CompileError("output() cannot output an array directly; use join(array) or index it")
                    if not isinstance(analyzed.result_shape, RuntimeResultShape) or not isinstance(analyzed.program.result, IRValue):
                        return _reject_remaining_legacy_structural_binding()
                    statements.append(IROutput(out_name, analyzed.program))
                    continue

                if call is not None and call.func.id in {"panel", "store", "set_position"}:
                    return BODY_UNSUPPORTED
                if call is not None and call.func.id.startswith("input_"):
                    return BODY_UNSUPPORTED
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
                            # STRUCTURAL_ARRAYS_COMPILETIME_PROMOTION_COMPAT: The structural-array migration does not implicitly promote a
                            # non-empty list/tuple that preprocessing already committed to CompileTimeState into the new
                            # mutable structural-array heap when a later append becomes runtime-dependent. Such promotion
                            # requires explicit alias/cycle semantics across CompileTimeState and StructuralArrayId ownership.
                            # Preserve the current whole-body compatibility route for this narrow case. Remove this marker
                            # only after a dedicated plan defines compile-time-container promotion or proves the source form
                            # is outside the supported DSL contract and replaces the fallback with a controlled diagnostic.
                            return BODY_UNSUPPORTED
                        raise CompileError(f"{array_name} is not an array")
                    if control_policy is not None:
                        # STRUCTURAL_ARRAYS_RUNTIME_APPEND_COMPAT: Legacy runtime-control-flow compilation uses shallow
                        # structural snapshots, so append mutation of an array can be observable through shared Python-list
                        # identity while branches are compiled. The structural-array migration must not approximate that behavior with new merge
                        # rules. Route the complete body through the existing atomic compatibility path before changing the
                        # semantic array heap. Remove this marker only after a dedicated control-flow structural-mutation
                        # plan defines source-order/branch visibility for append without runtime array sockets.
                        return BODY_UNSUPPORTED

                    analyzed = analyze_runtime_expression(append_call.args[0], active, active_compile_time)
                    if analyzed is BODY_UNSUPPORTED:
                        return BODY_UNSUPPORTED
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
                        statements.append(bind_statement)
                    continue
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
                if isinstance(analyzed.result_shape, ArrayResultShape):
                    raise CompileError("A final expression cannot be an array; use join(array) or index it")
                if not isinstance(analyzed.result_shape, RuntimeResultShape) or not isinstance(analyzed.program.result, IRValue):
                    return _reject_remaining_legacy_structural_binding()
                statements.append(IRFinalExpression(analyzed.program))
                continue

            return BODY_UNSUPPORTED
        return IRBody(tuple(statements))

    body = lower_statements(stmts, state, compile_time, root=True)
    if body is BODY_UNSUPPORTED:
        return BODY_UNSUPPORTED
    return BasicBodyCompilation(body, compile_time.snapshot(), state.array_snapshot())


__all__ = ["BODY_UNSUPPORTED", "BasicBodyCompilation", "lower_basic_body", "validate_input_declaration_placement"]

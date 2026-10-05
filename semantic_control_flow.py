"""Pure structured control-flow semantics for NodeForge body IR.

This module owns frontend-only branch policy, runtime-if construction, Repeat
mutation discovery, and Repeat type contracts. It never materializes Blender
objects and never stores source AST in final Semantic IR.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .compiler_identities import BindingId
from .compile_time import merge_runtime_if_compile_time
from .errors import CompileError
from .nf_types import NFType
from .semantic_ir import IRBranchMerge, IRIf


class BranchMergePolicy(str, Enum):
    """Select existing source-language merge behavior while constructing IRIf."""

    TOP_LEVEL = "TOP_LEVEL"
    REPEAT = "REPEAT"


_SWITCH_TYPES = frozenset(
    {NFType.FLOAT, NFType.INT, NFType.VECTOR, NFType.BOOL, NFType.GEOMETRY, NFType.STRING, NFType.BUNDLE}
)


def require_repeat_state_assignment(name: str, expected_type: NFType, assigned_type: NFType) -> None:
    """Require one Repeat carried state to preserve its exact semantic type."""
    if assigned_type is expected_type:
        return
    raise CompileError(f"repeat_range state {name!r} changed type from {expected_type} to {assigned_type}")



def parse_repeat_range_for(stmt: ast.For):
    """Return one validated runtime Repeat header or ``None`` for a non-Repeat loop."""
    call = stmt.iter
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "repeat_range"):
        return None
    if not isinstance(stmt.target, ast.Name):
        raise CompileError("repeat_range target must be a simple name, e.g. for i in repeat_range(n)")
    if len(call.args) != 1 or call.keywords or stmt.orelse:
        raise CompileError("repeat_range loop must look like: for i in repeat_range(n):")
    if not stmt.body:
        raise CompileError("repeat_range loop body cannot be empty")
    return call.args[0], stmt.body, stmt.target.id


def repeat_body_has_nonruntime_for(stmts) -> bool:
    """Return whether a Repeat body contains a compile-time/non-repeat ``for`` category."""
    for stmt in stmts:
        if isinstance(stmt, ast.For):
            if parse_repeat_range_for(stmt) is None:
                return True
            if repeat_body_has_nonruntime_for(stmt.body):
                return True
        elif isinstance(stmt, ast.If):
            if repeat_body_has_nonruntime_for(stmt.body) or repeat_body_has_nonruntime_for(stmt.orelse):
                return True
    return False

def repeat_mutation_names(stmts) -> tuple[str, ...]:
    """Return assignment targets in recursive first-mutation traversal order.

    This is a classification pre-scan only. It allocates no compiler identities and
    does not inspect backend state.
    """
    names: list[str] = []
    seen: set[str] = set()

    def add_target(target) -> None:
        if isinstance(target, ast.Name):
            candidates = [target.id]
        elif isinstance(target, (ast.Tuple, ast.List)):
            if any(isinstance(item, ast.Starred) for item in target.elts):
                raise CompileError("Starred tuple unpacking is not supported")
            if not target.elts or not all(isinstance(item, ast.Name) for item in target.elts):
                raise CompileError("Tuple unpacking target must be a flat sequence of names")
            candidates = [item.id for item in target.elts]
        else:
            return
        for name in candidates:
            if name not in seen:
                seen.add(name)
                names.append(name)

    def visit(stmt) -> None:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            add_target(stmt.targets[0])
            return
        if isinstance(stmt, ast.AugAssign):
            add_target(stmt.target)
            return
        if isinstance(stmt, ast.Expr):
            call = stmt.value
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in {"add", "extend"}
                and isinstance(call.func.value, ast.Name)
            ):
                name = call.func.value.id
                if name not in seen:
                    seen.add(name)
                    names.append(name)
                return
        if isinstance(stmt, ast.If):
            for child in list(stmt.body) + list(stmt.orelse):
                visit(child)
            return
        if isinstance(stmt, ast.For):
            parsed = parse_repeat_range_for(stmt)
            if parsed is None:
                raise CompileError("repeat_range body supports assignments, builder methods, if blocks, and nested repeat_range loops")
            for child in parsed[1]:
                visit(child)
            return
        # Other statement categories are classified by recursive body lowering.

    for stmt in stmts:
        visit(stmt)
    return tuple(names)


@dataclass(frozen=True)
class RuntimeMergeSymbol:
    """Describe one runtime slot eligible for control-flow merging."""

    binding_id: BindingId
    source_name: str
    typ: NFType


@dataclass(frozen=True)
class RuntimeIfResult:
    """Return one IRIf plus runtime exits and the authoritative merged CT exit."""

    statement: IRIf
    true_state: object
    false_state: object
    merged_compile_time: object


def _repeat_merge_candidate_ids(carried_ids, true_state, false_state) -> tuple[BindingId, ...]:
    """Return changed Repeat states followed by convergent ordinary runtime locals.

    Repeat-carried identities keep their established state order. Ordinary locals
    are eligible only when both exits still expose the same runtime identity, and
    are ordered by compiler allocation identity rather than source spelling.
    """
    carried_ids = tuple(carried_ids or ())
    carried_set = set(carried_ids)
    changed_ids = true_state.changed_runtime_ids | false_state.changed_runtime_ids
    true_runtime_ids = {symbol.binding_id for symbol in true_state.runtime_bindings.values()}
    false_runtime_ids = {symbol.binding_id for symbol in false_state.runtime_bindings.values()}

    changed_carried_ids = tuple(binding_id for binding_id in carried_ids if binding_id in changed_ids)
    local_ids = sorted(
        (changed_ids & true_runtime_ids & false_runtime_ids) - carried_set,
        key=lambda binding_id: binding_id.local_id,
    )
    return changed_carried_ids + tuple(local_ids)


def lower_runtime_if(
    stmt: ast.If,
    *,
    base_state,
    compile_time,
    policy: BranchMergePolicy,
    analyze_condition: Callable,
    lower_branch: Callable,
    merge_binding_ids=None,
    merge_symbols=(),
    identity_assignment_merge_eligible: Callable[[object, object], bool] | None = None,
):
    """Construct one runtime IRIf using caller-owned semantic state operations.

    Runtime semantic state and compile-time state are intentionally separate.
    This helper orchestrates their existing branch propagation policy without
    making either state domain own the other.
    """
    if policy is BranchMergePolicy.TOP_LEVEL and not stmt.orelse:
        raise CompileError("runtime if currently requires an else branch")

    analyzed_condition = analyze_condition(stmt.test, base_state, compile_time)
    if analyzed_condition.program.result.typ is not NFType.BOOL:
        message = "repeat_range if condition must be Bool" if policy is BranchMergePolicy.REPEAT else "select(cond, true, false): cond must be Bool"
        raise CompileError(message)

    base_compile_time = compile_time.snapshot()
    true_state = base_state.fork()
    true_compile_time = compile_time.fork()
    true_body = lower_branch(stmt.body, true_state, true_compile_time, policy)

    false_state = base_state.fork()
    false_compile_time = compile_time.fork()
    false_body = (
        lower_branch(stmt.orelse, false_state, false_compile_time, policy)
        if stmt.orelse
        else lower_branch((), false_state, false_compile_time, policy)
    )

    repeat_candidate_ids = ()
    if policy is BranchMergePolicy.TOP_LEVEL:
        changed_ids = true_state.changed_runtime_ids & false_state.changed_runtime_ids
        if identity_assignment_merge_eligible is not None and identity_assignment_merge_eligible(true_state, false_state):
            # A fresh branch-local GeometryBuilder identity can make an explicit identity assignment to a
            # common runtime target relevant to branch merging. Include explicitly assigned common runtime
            # bindings only after the caller confirms that a fresh/replaced builder survives a branch.
            changed_ids |= (
                true_state.explicitly_assigned_runtime_ids
                & false_state.explicitly_assigned_runtime_ids
            )
        if not changed_ids:
            raise CompileError("runtime if branches must assign at least one common variable")
    else:
        repeat_candidate_ids = _repeat_merge_candidate_ids(
            merge_binding_ids, true_state, false_state
        )
        changed_ids = set(repeat_candidate_ids)

    symbol_by_id = {symbol.binding_id: symbol for symbol in merge_symbols}
    by_name = {}
    for state in (true_state, false_state):
        for name, symbol in state.runtime_bindings.items():
            if symbol.binding_id in changed_ids:
                by_name.setdefault(name, symbol.binding_id)
    for binding_id in changed_ids:
        merge_symbol = symbol_by_id.get(binding_id)
        if merge_symbol is not None:
            by_name.setdefault(merge_symbol.source_name, binding_id)

    if policy is BranchMergePolicy.REPEAT:
        name_by_binding_id = {binding_id: name for name, binding_id in by_name.items()}
        ordered_names = [
            name_by_binding_id[binding_id]
            for binding_id in repeat_candidate_ids
            if binding_id in name_by_binding_id
        ]
    else:
        ordered_names = sorted(by_name)

    merges = []
    for name in ordered_names:
        binding_id = by_name[name]
        true_symbol = next((symbol for symbol in true_state.runtime_bindings.values() if symbol.binding_id == binding_id), None)
        false_symbol = next((symbol for symbol in false_state.runtime_bindings.values() if symbol.binding_id == binding_id), None)
        merge_symbol = symbol_by_id.get(binding_id)
        if true_symbol is None and merge_symbol is None:
            continue
        if false_symbol is None and merge_symbol is None:
            continue
        true_type = true_symbol.typ if true_symbol is not None else merge_symbol.typ
        false_type = false_symbol.typ if false_symbol is not None else merge_symbol.typ
        if true_type is not false_type:
            prefix = "repeat_range if" if policy is BranchMergePolicy.REPEAT else "runtime if"
            raise CompileError(f"{prefix} branch values for {name} have different types")
        typ = true_type
        if typ not in _SWITCH_TYPES:
            raise CompileError("runtime if branches must assign node values")
        merges.append(IRBranchMerge(binding_id, name, typ))

    if policy is BranchMergePolicy.TOP_LEVEL and not merges:
        raise CompileError("runtime if branches must assign at least one common variable")

    merged_compile_time = merge_runtime_if_compile_time(
        base_compile_time,
        true_compile_time,
        false_compile_time,
    )
    return RuntimeIfResult(
        IRIf(analyzed_condition.program, true_body, false_body, tuple(merges)),
        true_state,
        false_state,
        merged_compile_time,
    )


__all__ = [
    "BranchMergePolicy",
    "RuntimeIfResult",
    "RuntimeMergeSymbol",
    "lower_runtime_if",
    "parse_repeat_range_for",
    "repeat_body_has_nonruntime_for",
    "repeat_mutation_names",
    "require_repeat_state_assignment",
]

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

from .errors import CompileError
from .nf_types import NFType
from .semantic_ir import IRBranchMerge, IRIf


class BranchMergePolicy(str, Enum):
    """Select existing source-language merge behavior while constructing IRIf."""

    TOP_LEVEL = "TOP_LEVEL"
    REPEAT = "REPEAT"


_REPEAT_STATE_TYPES = frozenset(
    {NFType.GEOMETRY, NFType.VECTOR, NFType.FLOAT, NFType.INT, NFType.BOOL, NFType.BUNDLE}
)
_SWITCH_TYPES = frozenset(
    {NFType.FLOAT, NFType.INT, NFType.VECTOR, NFType.BOOL, NFType.GEOMETRY, NFType.STRING, NFType.BUNDLE}
)


def repeat_state_output_type(input_type: NFType) -> NFType:
    """Return the legacy logical post-Repeat state type for one physical input type."""
    if input_type not in _REPEAT_STATE_TYPES:
        raise CompileError(f"repeat_range state has unsupported type {input_type}")
    return NFType.FLOAT if input_type is NFType.INT else input_type


def require_repeat_state_assignment(name: str, input_type: NFType, assigned_type: NFType) -> None:
    """Validate one Repeat state assignment against the existing Int/Float contract."""
    if assigned_type is input_type:
        return
    if {input_type, assigned_type} <= {NFType.INT, NFType.FLOAT}:
        return
    raise CompileError(f"repeat_range state {name!r} changed type from {input_type} to {assigned_type}")



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
    """Return whether a Repeat body contains a compile-time/legacy ``for`` category."""
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
    """Return assignment targets in the legacy recursive first-mutation traversal order.

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
class RuntimeIfResult:
    """Return one constructed IRIf plus the two analyzed branch states."""

    statement: IRIf
    true_state: object
    false_state: object


def lower_runtime_if(
    stmt: ast.If,
    *,
    base_state,
    policy: BranchMergePolicy,
    analyze_condition: Callable,
    lower_branch: Callable,
    unsupported_sentinel,
    merge_binding_ids=None,
):
    """Construct one runtime IRIf using caller-owned semantic state operations.

    The state object is intentionally private to ``semantic_body``. This helper
    only requires ``fork()``, ``constants``, ``runtime_bindings``, and
    ``changed_runtime_ids`` so the control-flow policy stays separate from body
    statement mechanics.
    """
    if policy is BranchMergePolicy.TOP_LEVEL and not stmt.orelse:
        raise CompileError("runtime if currently requires an else branch")

    analyzed_condition = analyze_condition(stmt.test, base_state)
    if analyzed_condition is unsupported_sentinel:
        return unsupported_sentinel
    if analyzed_condition.program.result.typ is not NFType.BOOL:
        message = "repeat_range if condition must be Bool" if policy is BranchMergePolicy.REPEAT else "select(cond, true, false): cond must be Bool"
        raise CompileError(message)

    true_state = base_state.fork(constants=dict(base_state.constants))

    # CONTROL_FLOW_IR_LEGACY_CONSTANT_THREADING_COMPAT: Legacy runtime-if snapshots/restores active
    # runtime/structural bindings but not comp.consts, so true-branch constant mutations are visible while
    # compiling the false branch and false-exit constants become the post-if constant state. Preserve that
    # deterministic true-then-false threading here; do not independently fork/restore constants with branch
    # bindings. Remove this compatibility rule only when compile-time/runtime state separation defines and
    # migrates a new explicit branch-constant contract with its own behavior/update compatibility coverage.
    true_body = lower_branch(stmt.body, true_state, policy)
    if true_body is unsupported_sentinel:
        return unsupported_sentinel

    false_state = base_state.fork(constants=dict(true_state.constants))
    false_body = lower_branch(stmt.orelse, false_state, policy) if stmt.orelse else lower_branch((), false_state, policy)
    if false_body is unsupported_sentinel:
        return unsupported_sentinel

    if policy is BranchMergePolicy.TOP_LEVEL:
        changed_ids = true_state.changed_runtime_ids & false_state.changed_runtime_ids
        if not changed_ids:
            raise CompileError("runtime if branches must assign at least one common variable")
    else:
        changed_ids = true_state.changed_runtime_ids | false_state.changed_runtime_ids
        if merge_binding_ids is not None:
            changed_ids &= set(merge_binding_ids)

    by_name = {}
    for state in (true_state, false_state):
        for name, symbol in state.runtime_bindings.items():
            if symbol.binding_id in changed_ids:
                by_name.setdefault(name, symbol.binding_id)

    if policy is BranchMergePolicy.REPEAT and merge_binding_ids is not None:
        name_by_binding_id = {binding_id: name for name, binding_id in by_name.items()}
        ordered_names = [
            name_by_binding_id[binding_id]
            for binding_id in merge_binding_ids
            if binding_id in changed_ids and binding_id in name_by_binding_id
        ]
    else:
        ordered_names = sorted(by_name)

    merges = []
    for name in ordered_names:
        binding_id = by_name[name]
        true_symbol = next((symbol for symbol in true_state.runtime_bindings.values() if symbol.binding_id == binding_id), None)
        false_symbol = next((symbol for symbol in false_state.runtime_bindings.values() if symbol.binding_id == binding_id), None)
        if true_symbol is None or false_symbol is None:
            continue
        true_type = true_symbol.typ
        false_type = false_symbol.typ
        false_coerce = None
        true_coerce = None
        if true_type is not false_type:
            if policy is BranchMergePolicy.REPEAT and {true_type, false_type} <= {NFType.INT, NFType.FLOAT}:
                typ = NFType.FLOAT
                false_coerce = typ if false_type is NFType.INT else None
                true_coerce = typ if true_type is NFType.INT else None
            else:
                prefix = "repeat_range if" if policy is BranchMergePolicy.REPEAT else "runtime if"
                raise CompileError(f"{prefix} branch values for {name} have different types")
        else:
            typ = true_type
        if typ not in _SWITCH_TYPES:
            raise CompileError("runtime if branches must assign node values")
        merges.append(IRBranchMerge(binding_id, name, typ, false_coerce, true_coerce))

    if policy is BranchMergePolicy.TOP_LEVEL and not merges:
        raise CompileError("runtime if branches must assign at least one common variable")

    return RuntimeIfResult(IRIf(analyzed_condition.program, true_body, false_body, tuple(merges)), true_state, false_state)


__all__ = [
    "BranchMergePolicy",
    "RuntimeIfResult",
    "lower_runtime_if",
    "parse_repeat_range_for",
    "repeat_body_has_nonruntime_for",
    "repeat_mutation_names",
    "repeat_state_output_type",
    "require_repeat_state_assignment",
]

"""Compile-time residualization records and source preprocessing.

This module owns source retention/erasure decisions.  It consumes compile-time
evaluation but does not redefine expression semantics or runtime-fold policy.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .constants import TYPE_INT
from ..errors import CompileError
from .compile_time import CompileTimeState
from .consteval import (
    NOT_FOLDABLE,
    ConstEvalUnavailable,
    _const_eval,
    _is_compile_time_owned_assignment_rhs,
    try_runtime_fold,
)

@dataclass(frozen=True)
class CompileTimeBindExpression:
    """Replay one erased source assignment against the current CT state."""

    name: str
    expression: ast.expr


@dataclass(frozen=True)
class CompileTimeForEffect:
    """Replay one erased compile-time for-loop with replay-time alias identity."""

    target: str
    iterable_expression: ast.expr
    iteration_effects: tuple[tuple["CompileTimeEffect", ...], ...]


@dataclass(frozen=True)
class CompileTimeAppendExpression:
    """Replay one erased compile-time list append in source order."""

    name: str
    expression: ast.expr


CompileTimeEffect = (
    CompileTimeBindExpression
    | CompileTimeForEffect
    | CompileTimeAppendExpression
)


@dataclass(frozen=True)
class PreprocessedBody:
    """Carry residual source plus erased compile-time effects in source order."""

    statements: tuple[ast.stmt, ...]
    effects_before: tuple[tuple[CompileTimeEffect, ...], ...]
    trailing_effects: tuple[CompileTimeEffect, ...]
    initial_compile_time: object
    final_compile_time: object


class _PreprocessRecorder:
    """Collect retained statements and pending erased CT effects in source order."""

    def __init__(self):
        self.statements = []
        self.effects_before = []
        self.pending_effects = []

    def effect(self, effect: CompileTimeEffect) -> None:
        """Record one erased CT action before the next retained statement."""
        self.pending_effects.append(effect)

    def retain(self, stmt: ast.stmt) -> None:
        """Retain one source statement and attach all earlier erased CT work."""
        self.statements.append(stmt)
        self.effects_before.append(tuple(self.pending_effects))
        self.pending_effects.clear()

    def fork(self) -> "_PreprocessRecorder":
        """Return an empty recorder for speculative preprocessing."""
        return _PreprocessRecorder()

    def adopt(self, other: "_PreprocessRecorder") -> None:
        """Append a successful erased speculative sequence to this recorder."""
        if other.statements:
            raise ValueError("cannot adopt speculative preprocessing with retained statements")
        self.pending_effects.extend(other.pending_effects)


def _collect_preprocessing_written_names(stmts):
    """Return binding names that retained runtime control flow may write."""
    names = set()

    def add_target(target):
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                add_target(item)

    def visit(stmt):
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                add_target(target)
            return
        if isinstance(stmt, ast.AnnAssign):
            add_target(stmt.target)
            return
        if isinstance(stmt, ast.AugAssign):
            add_target(stmt.target)
            return
        if isinstance(stmt, ast.If):
            for child in (*stmt.body, *stmt.orelse):
                visit(child)
            return
        if isinstance(stmt, ast.For):
            add_target(stmt.target)
            for child in (*stmt.body, *stmt.orelse):
                visit(child)

    for stmt in stmts:
        visit(stmt)
    return names


def _contains_collection_mutation_stmt(stmts):
    """Detect builder or array mutation requiring runtime loop lowering."""
    for stmt in stmts:
        if isinstance(stmt, ast.Expr):
            call = stmt.value
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in {"add", "extend", "append"}
                and isinstance(call.func.value, ast.Name)
            ):
                return True
        if isinstance(stmt, ast.If):
            if _contains_collection_mutation_stmt(stmt.body) or _contains_collection_mutation_stmt(stmt.orelse):
                return True
        if isinstance(stmt, ast.For):
            if _contains_collection_mutation_stmt(stmt.body) or _contains_collection_mutation_stmt(stmt.orelse):
                return True
    return False

CompileTimeListAppendJournal = list[tuple[list, int]]


def _compile_time_list_append_savepoint(journal: CompileTimeListAppendJournal) -> int:
    """Return the current rollback boundary for speculative compile-time list appends."""
    return len(journal)


def _rollback_compile_time_list_appends_to(journal: CompileTimeListAppendJournal, savepoint: int) -> None:
    """Undo speculative list appends recorded after *savepoint* in reverse order."""
    while len(journal) > savepoint:
        target, old_len = journal.pop()
        del target[old_len:]


def _handle_compile_time_stmt(
    stmt,
    state: CompileTimeState,
    recorder: _PreprocessRecorder,
    preserve_names=None,
    *,
    list_append_journal: CompileTimeListAppendJournal | None = None,
):
    """Fold one statement into explicit compile-time state when current semantics allow it."""
    preserve_names = preserve_names or set()
    env = state.values
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target = stmt.targets[0]
        if isinstance(target, (ast.Tuple, ast.List)):
            for nested in target.elts:
                if isinstance(nested, ast.Name):
                    state.discard(nested.id)
            recorder.retain(stmt)
            return
        if not isinstance(target, ast.Name):
            recorder.retain(stmt)
            return
        if target.id in preserve_names:
            state.discard(target.id)
            recorder.retain(stmt)
            return
        if isinstance(stmt.value, ast.List) and not stmt.value.elts:
            state.discard(target.id)
            recorder.retain(stmt)
            return
        try:
            value = _const_eval(stmt.value, env)
        except ConstEvalUnavailable:
            state.discard(target.id)
            recorder.retain(stmt)
            return
        except CompileError:
            raise
        state.bind(target.id, value)
        if _is_compile_time_owned_assignment_rhs(stmt.value):
            recorder.effect(CompileTimeBindExpression(target.id, stmt.value))
            return
        replacement = try_runtime_fold(stmt.value, env)
        if replacement is not NOT_FOLDABLE:
            state.bind(target.id, replacement)
            recorder.effect(CompileTimeBindExpression(target.id, stmt.value))
            return
        # A known compile-time value does not authorize removal of a runtime-capable assignment.
        # Keep the source residual unless the explicit runtime-fold policy proves replacement safe;
        # compile-time-only consumers may still use the independently known value.
        recorder.retain(stmt)
        return
    if isinstance(stmt, ast.AugAssign):
        if isinstance(stmt.target, ast.Name):
            state.discard(stmt.target.id)
        recorder.retain(stmt)
        return
    if isinstance(stmt, ast.AnnAssign):
        if isinstance(stmt.target, ast.Name):
            state.discard(stmt.target.id)
        recorder.retain(stmt)
        return
    if isinstance(stmt, ast.Expr):
        call = stmt.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "append":
            if not isinstance(call.func.value, ast.Name) or len(call.args) != 1:
                raise CompileError("append must look like items.append(value)")
            list_name = call.func.value.id
            current = state.get(list_name)
            if isinstance(current, list):
                try:
                    append_value = _const_eval(call.args[0], env)
                except ConstEvalUnavailable:
                    pass
                except CompileError:
                    raise
                else:
                    if list_append_journal is not None:
                        list_append_journal.append((current, len(current)))
                    current.append(append_value)
                    recorder.effect(CompileTimeAppendExpression(list_name, call.args[0]))
                    return
            recorder.retain(stmt)
            return
        recorder.retain(stmt)
        return
    if isinstance(stmt, ast.If):
        written_names = _collect_preprocessing_written_names((*stmt.body, *stmt.orelse))
        recorder.retain(stmt)
        # Ordinary if remains runtime control flow. Discard compile-time facts for every name
        # either retained branch may write so later preprocessing cannot observe a stale pre-branch value.
        for name in written_names:
            state.discard(name)
        return
    if isinstance(stmt, ast.For):
        try:
            iterable = _const_eval(stmt.iter, env)
        except ConstEvalUnavailable:
            recorder.retain(stmt)
            return
        except CompileError:
            raise
        if _contains_collection_mutation_stmt(stmt.body) and not isinstance(stmt.target, ast.Name):
            # Collection loops retain their flat targets for semantic lowering.
            recorder.retain(stmt)
            return
        if not isinstance(iterable, (list, tuple)):
            recorder.retain(stmt)
            return
        if not isinstance(stmt.target, ast.Name):
            raise CompileError("Only simple compile-time for targets are supported")

        owns_journal = list_append_journal is None
        journal = [] if owns_journal else list_append_journal
        savepoint = _compile_time_list_append_savepoint(journal)
        trial_state = state.fork()
        had_old = trial_state.contains(stmt.target.id)
        old = trial_state.get(stmt.target.id)
        iteration_effects = []
        try:
            for item in iterable:
                trial_state.bind(stmt.target.id, item)
                iteration_recorder = recorder.fork()
                for sub in stmt.body:
                    _handle_compile_time_stmt(
                        sub,
                        trial_state,
                        iteration_recorder,
                        preserve_names,
                        list_append_journal=journal,
                    )
                if iteration_recorder.statements:
                    _rollback_compile_time_list_appends_to(journal, savepoint)
                    recorder.retain(stmt)
                    return
                iteration_effects.append(tuple(iteration_recorder.pending_effects))
            if had_old:
                trial_state.bind(stmt.target.id, old)
            else:
                trial_state.discard(stmt.target.id)
        except Exception:
            _rollback_compile_time_list_appends_to(journal, savepoint)
            raise

        state.replace(trial_state)
        recorder.effect(
            CompileTimeForEffect(
                target=stmt.target.id,
                iterable_expression=stmt.iter,
                iteration_effects=tuple(iteration_effects),
            )
        )
        if owns_journal:
            journal.clear()
        return
    recorder.retain(stmt)

def _is_repeat_range_for(stmt):
    """Return True for the runtime repeat_range(...) for-loop shape."""
    return (
        isinstance(stmt, ast.For)
        and isinstance(stmt.target, ast.Name)
        and isinstance(stmt.iter, ast.Call)
        and isinstance(stmt.iter.func, ast.Name)
        and stmt.iter.func.id == "repeat_range"
        and len(stmt.iter.args) == 1
    )


def _flat_assignment_names(target):
    """Return simple names from a flat assignment target used by repeat state."""
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, (ast.Tuple, ast.List)) and all(isinstance(item, ast.Name) for item in target.elts):
        return tuple(item.id for item in target.elts)
    return ()


def _repeat_range_state_names(stmts):
    """Names initialized before repeat_range loops that must remain GN values."""
    preserve = set()

    def assigned_names(sub_stmts):
        """Collect repeat-visible assignment targets through valid nested control flow."""
        names = set()
        for sub in sub_stmts:
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1:
                names.update(_flat_assignment_names(sub.targets[0]))
            elif isinstance(sub, ast.If):
                names |= assigned_names(sub.body)
                names |= assigned_names(sub.orelse)
            elif _is_repeat_range_for(sub):
                names |= assigned_names(sub.body)
        return names

    before = set()
    for stmt in stmts:
        if _is_repeat_range_for(stmt):
            preserve |= (assigned_names(stmt.body) & before)
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            before.update(_flat_assignment_names(stmt.targets[0]))
    return preserve


def _preprocess_compile_time(stmts):
    """Return residual source plus erased compile-time effects in source order."""
    state = CompileTimeState()
    initial = state.snapshot()
    recorder = _PreprocessRecorder()
    preserve = _repeat_range_state_names(stmts)
    for stmt in stmts:
        _handle_compile_time_stmt(
            stmt,
            state,
            recorder,
            preserve,
        )
    return PreprocessedBody(
        statements=tuple(recorder.statements),
        effects_before=tuple(recorder.effects_before),
        trailing_effects=tuple(recorder.pending_effects),
        initial_compile_time=initial,
        final_compile_time=state.snapshot(),
    )


def _infer_input_types(stmts):
    """Infer implicit input types required by runtime repeat counts at any depth."""
    result = {}

    def visit(sub_stmts):
        for stmt in sub_stmts:
            if _is_repeat_range_for(stmt):
                arg = stmt.iter.args[0]
                if isinstance(arg, ast.Name):
                    result[arg.id] = TYPE_INT
                visit(stmt.body)
            elif isinstance(stmt, ast.If):
                visit(stmt.body)
                visit(stmt.orelse)

    visit(stmts)
    return result



__all__ = [
    "CompileTimeAppendExpression",
    "CompileTimeBindExpression",
    "CompileTimeForEffect",
    "PreprocessedBody",
    "_PreprocessRecorder",
    "_handle_compile_time_stmt",
    "_preprocess_compile_time",
    "_infer_input_types",
]

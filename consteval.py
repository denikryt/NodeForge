"""Compile-time expression evaluation and preprocessing helpers."""

import ast
from dataclasses import dataclass
from .constants import TYPE_INT, _ALLOWED_CONSTS
from .errors import CompileError
from .compile_time import CompileTimeState, ConstVector


class ConstEvalUnavailable(Exception):
    """Signal that current compile-time evaluation cannot provide a value."""


NOT_FOLDABLE = object()


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

def _is_const_vector(v):
    """Function `_is_const_vector` used by the NodeForge addon."""
    return isinstance(v, ConstVector) and len(v) == 3

def _as_float_const(v, context="value"):
    """Function `_as_float_const` used by the NodeForge addon."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    raise CompileError(f"Expected numeric compile-time {context}")

def _const_len(value):
    """Return the length of a compile-time sequence."""
    if isinstance(value, (list, tuple, str)):
        return len(value)
    raise CompileError("len() expects a compile-time list/tuple/string")


def _is_compile_time_int(value):
    """Return True for integer compile-time range bounds, excluding booleans."""
    return isinstance(value, int) and not isinstance(value, bool)


def _const_range(args):
    """Evaluate range() for compile-time integer arguments only."""
    if len(args) not in {1, 2, 3}:
        raise CompileError("range() expects 1-3 arguments")
    if not all(_is_compile_time_int(arg) for arg in args):
        raise CompileError("range() arguments must be compile-time integers")
    if len(args) == 3 and args[2] == 0:
        raise CompileError("range() step must not be zero")
    return list(range(*args))


def _is_num(v):
    """Return True for compile-time scalar numbers, excluding booleans."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _vec(v):
    """Normalize a compile-time vector-like value to ConstVector."""
    if _is_const_vector(v):
        return v
    if isinstance(v, (tuple, list)) and len(v) == 3 and all(_is_num(c) for c in v):
        return ConstVector((float(v[0]), float(v[1]), float(v[2])))
    return None


def _bin_add(a, b):
    """Evaluate compile-time addition, including vector addition."""
    av = _vec(a); bv = _vec(b)
    if av is not None and bv is not None:
        return ConstVector((av[0] + bv[0], av[1] + bv[1], av[2] + bv[2]))
    return a + b


def _bin_sub(a, b):
    """Evaluate compile-time subtraction, including vector subtraction."""
    av = _vec(a); bv = _vec(b)
    if av is not None and bv is not None:
        return ConstVector((av[0] - bv[0], av[1] - bv[1], av[2] - bv[2]))
    return a - b


def _bin_mul(a, b):
    """Evaluate compile-time multiplication, including vector-scalar multiplication."""
    av = _vec(a); bv = _vec(b)
    if av is not None and _is_num(b):
        return ConstVector((av[0] * b, av[1] * b, av[2] * b))
    if bv is not None and _is_num(a):
        return ConstVector((a * bv[0], a * bv[1], a * bv[2]))
    return a * b


def _bin_div(a, b):
    """Evaluate compile-time division, including vector-scalar division."""
    av = _vec(a)
    if av is not None and _is_num(b):
        return ConstVector((av[0] / b, av[1] / b, av[2] / b))
    return a / b



def _eval_joined_string(expr, env):
    """Evaluate an f-string whose interpolations are compile-time strings."""
    parts = []
    for item in expr.values:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            parts.append(item.value)
            continue
        if isinstance(item, ast.FormattedValue):
            if item.conversion != -1 or item.format_spec is not None:
                raise CompileError("compile-time f-strings support only plain string interpolation")
            value = _const_eval(item.value, env)
            if not isinstance(value, str):
                raise CompileError("compile-time f-string interpolations must be strings")
            parts.append(value)
            continue
        raise CompileError("Unsupported compile-time f-string element")
    return "".join(parts)


def _require_compile_time_bool(value, message):
    """Return an exact compile-time Bool or raise the matching semantic error."""
    if type(value) is not bool:
        raise CompileError(message)
    return value


def _is_compile_time_owned_assignment_rhs(expr: ast.AST) -> bool:
    """Return whether an assignment root belongs to the compile-time metalayer."""
    if isinstance(expr, (ast.List, ast.Tuple, ast.JoinedStr)):
        return True
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id in {"range", "len", "sum"}
    )


def try_runtime_fold(expr, env):
    """Return a proven runtime replacement or ``NOT_FOLDABLE``.

    The initial policy is intentionally closed-world.  Compile-time value
    availability alone never authorizes removing Geometry Nodes computation.
    """
    if isinstance(expr, ast.Constant) and type(expr.value) in {bool, int, float, str}:
        return expr.value
    if isinstance(expr, ast.Name) and expr.id in _ALLOWED_CONSTS:
        return _ALLOWED_CONSTS[expr.id]
    if isinstance(expr, (ast.BinOp, ast.Compare)) or (
        isinstance(expr, ast.UnaryOp) and isinstance(expr.op, (ast.UAdd, ast.USub))
    ):
        # TODO(nodeforge-migration): Scalar numeric unary/binary/comparison expressions remain
        # non-foldable until type-directed numeric semantics defines the shared Int/Float result
        # and Blender-equivalence policy. Compile-time-only consumers may still use _const_eval();
        # remove this branch when numeric_semantics is the fold-policy authority.
        return NOT_FOLDABLE
    return NOT_FOLDABLE

def _const_eval(expr, env):
    """Evaluate one expression when the compiler-owned compile-time layer can."""
    if isinstance(expr, ast.Constant):
        if type(expr.value) in {int, float, bool, str}:
            return expr.value
        raise ConstEvalUnavailable(f"Unsupported compile-time constant: {type(expr.value).__name__}")
    if isinstance(expr, ast.JoinedStr):
        return _eval_joined_string(expr, env)
    if isinstance(expr, ast.Name):
        if expr.id in env:
            return env[expr.id]
        if expr.id in _ALLOWED_CONSTS:
            return _ALLOWED_CONSTS[expr.id]
        raise ConstEvalUnavailable(f"No compile-time value for name: {expr.id}")
    if isinstance(expr, ast.List):
        return [_const_eval(e, env) for e in expr.elts]
    if isinstance(expr, ast.Tuple):
        return tuple(_const_eval(e, env) for e in expr.elts)
    if isinstance(expr, ast.Subscript):
        seq = _const_eval(expr.value, env)
        idx = _const_eval(expr.slice, env)
        if type(idx) is not int:
            raise CompileError("compile-time list indexing requires an integer index")
        try:
            return seq[idx]
        except (IndexError, KeyError, TypeError) as exc:
            raise CompileError("compile-time list indexing failed") from exc
    if isinstance(expr, ast.Attribute):
        base = _const_eval(expr.value, env)
        v = _vec(base)
        if v is not None and expr.attr in {"x", "y", "z"}:
            return v[{"x": 0, "y": 1, "z": 2}[expr.attr]]
        raise CompileError(f"Unsupported compile-time attribute .{expr.attr}")
    if isinstance(expr, ast.UnaryOp):
        v = _const_eval(expr.operand, env)
        vv = _vec(v)
        if isinstance(expr.op, ast.USub):
            if vv is not None:
                return ConstVector((-vv[0], -vv[1], -vv[2]))
            if _is_compile_time_int(v):
                return -v
            return -_as_float_const(v)
        if isinstance(expr.op, ast.UAdd):
            if vv is not None:
                return vv
            if _is_compile_time_int(v):
                return v
            try:
                return _as_float_const(v)
            except CompileError as exc:
                raise ConstEvalUnavailable("Compile-time unary plus is unavailable") from exc
        if isinstance(expr.op, ast.Not):
            return not _require_compile_time_bool(v, "not expects Bool")
        raise ConstEvalUnavailable(f"Unsupported compile-time unary operator: {type(expr.op).__name__}")
    if isinstance(expr, ast.BinOp):
        a = _const_eval(expr.left, env); b = _const_eval(expr.right, env)
        try:
            if isinstance(expr.op, ast.Add): return _bin_add(a, b)
            if isinstance(expr.op, ast.Sub): return _bin_sub(a, b)
            if isinstance(expr.op, ast.Mult): return _bin_mul(a, b)
            if isinstance(expr.op, ast.Div): return _bin_div(a, b)
            if isinstance(expr.op, ast.Pow): return a ** b
            if isinstance(expr.op, ast.Mod): return a % b
        except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
            raise ConstEvalUnavailable("Compile-time numeric operation is unavailable") from exc
        raise ConstEvalUnavailable(f"Unsupported compile-time binary operator: {type(expr.op).__name__}")
    if isinstance(expr, ast.BoolOp):
        vals = [_const_eval(v, env) for v in expr.values]
        if not all(type(value) is bool for value in vals):
            raise CompileError("Boolean operations expect Bool values")
        if isinstance(expr.op, ast.And): return all(vals)
        if isinstance(expr.op, ast.Or): return any(vals)
        raise ConstEvalUnavailable(f"Unsupported compile-time Boolean operator: {type(expr.op).__name__}")
    if isinstance(expr, ast.Compare):
        if len(expr.ops) != 1 or len(expr.comparators) != 1:
            # Chained comparisons are a supported runtime-language form.  CTFE
            # does not own their pairwise/rematerialization semantics, so lack
            # of a compile-time evaluator must not turn accepted runtime source
            # into a hard language error.
            raise ConstEvalUnavailable("Compile-time chained comparison evaluation is unavailable")
        a = _const_eval(expr.left, env); b = _const_eval(expr.comparators[0], env); op = expr.ops[0]
        try:
            if isinstance(op, ast.Lt): return a < b
            if isinstance(op, ast.LtE): return a <= b
            if isinstance(op, ast.Gt): return a > b
            if isinstance(op, ast.GtE): return a >= b
            if isinstance(op, ast.Eq): return a == b
            if isinstance(op, ast.NotEq): return a != b
        except (TypeError, ValueError, OverflowError) as exc:
            raise ConstEvalUnavailable("Compile-time comparison is unavailable") from exc
        raise ConstEvalUnavailable(f"Unsupported compile-time comparison: {type(op).__name__}")
    if isinstance(expr, ast.Call):
        if not isinstance(expr.func, ast.Name):
            raise ConstEvalUnavailable("Compile-time evaluation does not own this callable")
        name = expr.func.id
        if name not in {"vector", "range", "len", "sum"}:
            raise ConstEvalUnavailable(f"Compile-time evaluation does not own callable: {name}")
        if expr.keywords:
            if name == "vector":
                raise ConstEvalUnavailable("Compile-time vector evaluation supports positional arguments only")
            raise CompileError("compile-time calls do not support keyword arguments")
        args = [_const_eval(a, env) for a in expr.args]
        if name == "vector":
            if len(args) != 3:
                raise CompileError("compile-time vector(x,y,z) expects 3 arguments")
            return ConstVector((_as_float_const(args[0]), _as_float_const(args[1]), _as_float_const(args[2])))
        if name == "range":
            return _const_range(args)
        if name == "len":
            if len(args) != 1:
                raise CompileError("len() expects one argument")
            return _const_len(args[0])
        if name == "sum":
            if len(args) != 1:
                raise CompileError("sum() expects one argument")
        try:
            return sum(args[0])
        except (TypeError, ValueError) as exc:
            raise CompileError("sum() expects one compile-time sequence") from exc
    raise ConstEvalUnavailable(f"Unsupported compile-time expression: {type(expr).__name__}")


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


def _contains_builder_method_stmt(stmts):
    """Return True if a statement list contains geometry_builder mutation syntax."""
    for stmt in stmts:
        if isinstance(stmt, ast.Expr):
            call = stmt.value
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in {"add", "extend"}
                and isinstance(call.func.value, ast.Name)
            ):
                return True
        if isinstance(stmt, ast.If):
            if _contains_builder_method_stmt(stmt.body) or _contains_builder_method_stmt(stmt.orelse):
                return True
        if isinstance(stmt, ast.For):
            if _contains_builder_method_stmt(stmt.body) or _contains_builder_method_stmt(stmt.orelse):
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
        # TODO(nodeforge-migration): Known non-foldable runtime-capable RHS values are
        # conservatively retained because this stage has no binding-level runtime-demand analysis.
        # Consumer contracts and typed numeric materialization land first; after those stages,
        # audit whether eager assignment lowering still forces unnecessary runtime form and,
        # if so, replace it with a narrow demand-driven materialization mechanism.
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
        # TODO(nodeforge-migration): Ordinary if still keeps the pre-existing implicit compile-time
        # branch-selection shortcut while CTFE and residualization boundaries are being separated.
        # The runtime-only ordinary-if migration removes only this compile-time selection behavior;
        # retained runtime ifs continue to require a sound preprocessing state barrier.
        try:
            branch = stmt.body if bool(_const_eval(stmt.test, env)) else stmt.orelse
        except (ConstEvalUnavailable, CompileError):
            written_names = _collect_preprocessing_written_names((*stmt.body, *stmt.orelse))
            recorder.retain(stmt)
            # TODO(nodeforge-migration): Preprocessing still carries CompileTimeState across residual
            # runtime control flow, so every retained ordinary if must conservatively discard names
            # that either branch may write before later source transformation. Remove this syntactic
            # barrier only when preprocessing no longer propagates pre-if facts across runtime control
            # flow, or an equally sound replacement owns that boundary.
            for name in written_names:
                state.discard(name)
            return
        for sub in branch:
            _handle_compile_time_stmt(
                sub,
                state,
                recorder,
                preserve_names,
                list_append_journal=list_append_journal,
            )
        return
    if isinstance(stmt, ast.For):
        try:
            iterable = _const_eval(stmt.iter, env)
        except ConstEvalUnavailable:
            recorder.retain(stmt)
            return
        except CompileError:
            raise
        if _contains_builder_method_stmt(stmt.body) and not isinstance(stmt.target, ast.Name):
            # Preserve the historical flat builder-loop target for Semantic Body's explicit frontend rule.
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
    'ConstEvalUnavailable',
    'ConstVector',
    'NOT_FOLDABLE',
    '_is_const_vector',
    '_as_float_const',
    '_is_compile_time_int',
    '_const_range',
    '_const_eval',
    '_is_compile_time_owned_assignment_rhs',
    'CompileTimeAppendExpression',
    'CompileTimeBindExpression',
    'CompileTimeForEffect',
    'PreprocessedBody',
    'try_runtime_fold',
    '_handle_compile_time_stmt',
    '_preprocess_compile_time',
    '_infer_input_types',
]

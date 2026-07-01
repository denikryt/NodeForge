"""Compile-time expression evaluation and preprocessing helpers."""

import ast
from .constants import *
from .errors import CompileError

_ALLOWED_MATH_FUNCS = {
    "sin": __import__("math").sin,
    "cos": __import__("math").cos,
    "tan": __import__("math").tan,
    "asin": __import__("math").asin,
    "acos": __import__("math").acos,
    "atan": __import__("math").atan,
    "sqrt": __import__("math").sqrt,
    "floor": __import__("math").floor,
    "ceil": __import__("math").ceil,
    "round": round,
    "abs": abs,
    "radians": __import__("math").radians,
    "degrees": __import__("math").degrees,
    "exp": __import__("math").exp,
    "ln": __import__("math").log,
}


class ConstVector(tuple):
    """Class `ConstVector` used by the NodeForge addon."""
    pass

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

def _const_eval(expr, env):
    """Evaluate a supported compile-time AST expression."""
    if isinstance(expr, ast.Constant):
        if isinstance(expr.value, (int, float, bool, str)):
            return expr.value
        raise CompileError("Unsupported compile-time constant")
    if isinstance(expr, ast.JoinedStr):
        return _eval_joined_string(expr, env)
    if isinstance(expr, ast.Name):
        if expr.id in env:
            return env[expr.id]
        if expr.id in _ALLOWED_CONSTS:
            return _ALLOWED_CONSTS[expr.id]
        raise CompileError(f"Unknown compile-time name: {expr.id}")
    if isinstance(expr, ast.List):
        return [_const_eval(e, env) for e in expr.elts]
    if isinstance(expr, ast.Tuple):
        return tuple(_const_eval(e, env) for e in expr.elts)
    if isinstance(expr, ast.Subscript):
        seq = _const_eval(expr.value, env)
        idx = _const_eval(expr.slice, env)
        if not isinstance(idx, int):
            idx = int(idx)
        try:
            return seq[idx]
        except Exception as exc:
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
            return _as_float_const(v)
        if isinstance(expr.op, ast.Not):
            return not bool(v)
    if isinstance(expr, ast.BinOp):
        a = _const_eval(expr.left, env); b = _const_eval(expr.right, env)
        if isinstance(expr.op, ast.Add): return _bin_add(a, b)
        if isinstance(expr.op, ast.Sub): return _bin_sub(a, b)
        if isinstance(expr.op, ast.Mult): return _bin_mul(a, b)
        if isinstance(expr.op, ast.Div): return _bin_div(a, b)
        if isinstance(expr.op, ast.Pow): return a ** b
        if isinstance(expr.op, ast.Mod): return a % b
    if isinstance(expr, ast.BoolOp):
        vals = [_const_eval(v, env) for v in expr.values]
        if isinstance(expr.op, ast.And): return all(bool(v) for v in vals)
        if isinstance(expr.op, ast.Or): return any(bool(v) for v in vals)
    if isinstance(expr, ast.Compare):
        if len(expr.ops) != 1 or len(expr.comparators) != 1:
            raise CompileError("Compile-time chained comparisons are not supported")
        a = _const_eval(expr.left, env); b = _const_eval(expr.comparators[0], env); op = expr.ops[0]
        if isinstance(op, ast.Lt): return a < b
        if isinstance(op, ast.LtE): return a <= b
        if isinstance(op, ast.Gt): return a > b
        if isinstance(op, ast.GtE): return a >= b
        if isinstance(op, ast.Eq): return a == b
        if isinstance(op, ast.NotEq): return a != b
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        name = expr.func.id
        args = [_const_eval(a, env) for a in expr.args]
        if name == "vector":
            if len(args) != 3:
                raise CompileError("compile-time vector(x,y,z) expects 3 arguments")
            return ConstVector((_as_float_const(args[0]), _as_float_const(args[1]), _as_float_const(args[2])))
        if name == "range":
            return _const_range(args)
        if name == "count_zero":
            return sum(1 for a in args if a == 0)
        if name == "len":
            if len(args) != 1:
                raise CompileError("len() expects one argument")
            return _const_len(args[0])
        if name == "sum":
            if len(args) != 1:
                raise CompileError("sum() expects one argument")
            return sum(args[0])
        if name in _ALLOWED_MATH_FUNCS:
            if len(args) != 1:
                raise CompileError(f"{name}() expects one argument")
            return _ALLOWED_MATH_FUNCS[name](args[0])
    raise CompileError(f"Unsupported compile-time expression: {type(expr).__name__}")



def _can_defer_range_error(expr, env):
    """Return True when a for iterable error should be handled by runtime lowering."""
    if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "range"):
        return True
    for arg in expr.args:
        try:
            _const_eval(arg, env)
        except CompileError:
            return True
    return False

def _handle_compile_time_stmt(stmt, env, out_stmts, preserve_names=None):
    """Function `_handle_compile_time_stmt` used by the NodeForge addon."""
    preserve_names = preserve_names or set()
    if isinstance(stmt, ast.Assign):
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            raise CompileError("Assignment target must be a simple name")
        # Preserve initial values for runtime_range state variables; they must become GN values.
        if target.id in preserve_names:
            env.pop(target.id, None)
            out_stmts.append(stmt)
            return
        # Empty lists are script-level runtime arrays: keep them for the compiler
        # so later `items.append(dynamic_value)` can collect node Values.
        if isinstance(stmt.value, ast.List) and not stmt.value.elts:
            env.pop(target.id, None)
            out_stmts.append(stmt)
            return
        # Treat non-empty fully-constant assignments as compile-time only.
        # Plain scalar assignments are preserved as node values so `a = 1; output(a)` works.
        try:
            val = _const_eval(stmt.value, env)
            if isinstance(stmt.value, (ast.List, ast.Tuple)) or isinstance(val, (list, tuple, ConstVector, str, bool)):
                env[target.id] = val
                return
            if isinstance(val, int) and not isinstance(val, bool):
                env[target.id] = val
            else:
                env.pop(target.id, None)
        except CompileError:
            env.pop(target.id, None)
        out_stmts.append(stmt)
        return
    if isinstance(stmt, ast.AugAssign):
        if isinstance(stmt.target, ast.Name):
            env.pop(stmt.target.id, None)
        out_stmts.append(stmt)
        return
    if isinstance(stmt, ast.AnnAssign):
        if isinstance(stmt.target, ast.Name):
            env.pop(stmt.target.id, None)
        out_stmts.append(stmt)
        return
    if isinstance(stmt, ast.Expr):
        call = stmt.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "append":
            if not isinstance(call.func.value, ast.Name) or len(call.args) != 1:
                raise CompileError("append must look like items.append(value)")
            list_name = call.func.value.id
            if list_name in env and isinstance(env[list_name], list):
                try:
                    env[list_name].append(_const_eval(call.args[0], env))
                    return
                except CompileError:
                    pass
            # Non-constant append is a script-level runtime array operation.
            out_stmts.append(stmt)
            return
        out_stmts.append(stmt)
        return
    if isinstance(stmt, ast.If):
        try:
            branch = stmt.body if bool(_const_eval(stmt.test, env)) else stmt.orelse
        except CompileError:
            out_stmts.append(stmt)
            return
        for sub in branch:
            _handle_compile_time_stmt(sub, env, out_stmts, preserve_names)
        return
    if isinstance(stmt, ast.For):
        # Runtime for range(input) is preserved; compile-time for requires a const iterable.
        try:
            iterable = _const_eval(stmt.iter, env)
        except CompileError:
            if not _can_defer_range_error(stmt.iter, env):
                raise
            out_stmts.append(stmt)
            return
        # Keep loops with array append for the main compiler; it can unroll them
        # while preserving dynamic node Values inside the array.
        for sub in stmt.body:
            if isinstance(sub, ast.Expr) and isinstance(sub.value, ast.Call) and isinstance(sub.value.func, ast.Attribute) and sub.value.func.attr == "append":
                out_stmts.append(stmt)
                return
        if not isinstance(stmt.target, ast.Name):
            raise CompileError("Only simple compile-time for targets are supported")
        old = env.get(stmt.target.id, None); had_old = stmt.target.id in env
        for item in iterable:
            env[stmt.target.id] = item
            for sub in stmt.body:
                _handle_compile_time_stmt(sub, env, out_stmts, preserve_names)
        if had_old: env[stmt.target.id] = old
        else: env.pop(stmt.target.id, None)
        return
    out_stmts.append(stmt)

def _runtime_range_state_names(stmts):
    """Names initialized before runtime_range loops that must remain GN values."""
    preserve = set()

    def assigned_names(sub_stmts):
        """Collect simple assignment targets from runtime statements recursively."""
        names = set()
        for sub in sub_stmts:
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
                names.add(sub.targets[0].id)
            elif isinstance(sub, ast.If):
                names |= assigned_names(sub.body)
                names |= assigned_names(sub.orelse)
        return names

    before = set()
    for stmt in stmts:
        if isinstance(stmt, ast.For) and isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id == "runtime_range":
            preserve |= (assigned_names(stmt.body) & before)
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            before.add(stmt.targets[0].id)
    return preserve

def _preprocess_compile_time(stmts):
    """Function `_preprocess_compile_time` used by the NodeForge addon."""
    env = {}
    out = []
    preserve = _runtime_range_state_names(stmts)
    for stmt in stmts:
        _handle_compile_time_stmt(stmt, env, out, preserve)
    return out, env

def _infer_input_types(stmts):
    """Function `_infer_input_types` used by the NodeForge addon."""
    result = {}
    for stmt in stmts:
        if isinstance(stmt, ast.For) and isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id in {"range", "runtime_range"}:
            for arg in stmt.iter.args:
                if isinstance(arg, ast.Name):
                    result[arg.id] = TYPE_INT
    return result

__all__ = ['ConstVector', '_is_const_vector', '_as_float_const', '_is_compile_time_int', '_const_range', '_const_eval', '_handle_compile_time_stmt', '_preprocess_compile_time', '_infer_input_types']

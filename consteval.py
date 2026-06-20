"""Compile-time expression evaluation and preprocessing helpers."""

import ast
from .constants import *
from .errors import CompileError



class ConstVector(tuple):
    """Class `ConstVector` used by the GN Script MVP addon."""
    pass

def _is_const_vector(v):
    """Function `_is_const_vector` used by the GN Script MVP addon."""
    return isinstance(v, ConstVector) and len(v) == 3

def _as_float_const(v, context="value"):
    """Function `_as_float_const` used by the GN Script MVP addon."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    raise CompileError(f"Expected numeric compile-time {context}")

def _const_eval(expr, env):
    """Function `_const_eval` used by the GN Script MVP addon."""
    if isinstance(expr, ast.Constant):
        if isinstance(expr.value, (int, float, bool, str)):
            return expr.value
        raise CompileError("Unsupported compile-time constant")
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
    if isinstance(expr, ast.UnaryOp):
        v = _const_eval(expr.operand, env)
        if isinstance(expr.op, ast.USub):
            return -_as_float_const(v)
        if isinstance(expr.op, ast.UAdd):
            return _as_float_const(v)
        if isinstance(expr.op, ast.Not):
            return not bool(v)
    if isinstance(expr, ast.BinOp):
        a = _const_eval(expr.left, env); b = _const_eval(expr.right, env)
        if isinstance(expr.op, ast.Add): return a + b
        if isinstance(expr.op, ast.Sub): return a - b
        if isinstance(expr.op, ast.Mult): return a * b
        if isinstance(expr.op, ast.Div): return a / b
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
            if len(args) == 1: return list(range(int(args[0])))
            if len(args) == 2: return list(range(int(args[0]), int(args[1])))
            if len(args) == 3: return list(range(int(args[0]), int(args[1]), int(args[2])))
            raise CompileError("range() expects 1-3 arguments")
        if name == "count_zero":
            return sum(1 for a in args if a == 0)
        if name in {"len", "sum"}:
            return getattr(__builtins__, name)(args[0]) if len(args) == 1 else None
    raise CompileError(f"Unsupported compile-time expression: {type(expr).__name__}")

def _handle_compile_time_stmt(stmt, env, out_stmts):
    """Function `_handle_compile_time_stmt` used by the GN Script MVP addon."""
    if isinstance(stmt, ast.Assign):
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            raise CompileError("Assignment target must be a simple name")
        # Treat [] and any fully constant assignment as compile-time only.
        try:
            val = _const_eval(stmt.value, env)
            # Do not swallow expressions that are meant to become GN values, except lists/vectors/strings/bools/int literals used by compile-time code.
            if isinstance(stmt.value, (ast.List, ast.Tuple)) or isinstance(val, (list, tuple, ConstVector, str, bool, int)):
                env[target.id] = val
                return
        except CompileError:
            pass
        out_stmts.append(stmt)
        return
    if isinstance(stmt, ast.Expr):
        call = stmt.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "append":
            if not isinstance(call.func.value, ast.Name) or len(call.args) != 1:
                raise CompileError("compile-time append must look like offsets.append(value)")
            list_name = call.func.value.id
            if list_name not in env or not isinstance(env[list_name], list):
                raise CompileError(f"{list_name} is not a compile-time list")
            env[list_name].append(_const_eval(call.args[0], env))
            return
        out_stmts.append(stmt)
        return
    if isinstance(stmt, ast.If):
        branch = stmt.body if bool(_const_eval(stmt.test, env)) else stmt.orelse
        for sub in branch:
            _handle_compile_time_stmt(sub, env, out_stmts)
        return
    if isinstance(stmt, ast.For):
        # Runtime for range(input) is preserved; compile-time for requires a const iterable.
        try:
            iterable = _const_eval(stmt.iter, env)
        except CompileError:
            out_stmts.append(stmt)
            return
        if not isinstance(stmt.target, ast.Name):
            raise CompileError("Only simple compile-time for targets are supported")
        old = env.get(stmt.target.id, None); had_old = stmt.target.id in env
        for item in iterable:
            env[stmt.target.id] = item
            for sub in stmt.body:
                _handle_compile_time_stmt(sub, env, out_stmts)
        if had_old: env[stmt.target.id] = old
        else: env.pop(stmt.target.id, None)
        return
    out_stmts.append(stmt)

def _preprocess_compile_time(stmts):
    """Function `_preprocess_compile_time` used by the GN Script MVP addon."""
    env = {}
    out = []
    for stmt in stmts:
        _handle_compile_time_stmt(stmt, env, out)
    return out, env

def _infer_input_types(stmts):
    """Function `_infer_input_types` used by the GN Script MVP addon."""
    result = {}
    for stmt in stmts:
        if isinstance(stmt, ast.For) and isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id == "range":
            for arg in stmt.iter.args:
                if isinstance(arg, ast.Name):
                    result[arg.id] = TYPE_INT
    return result

__all__ = ['ConstVector', '_is_const_vector', '_as_float_const', '_const_eval', '_handle_compile_time_stmt', '_preprocess_compile_time', '_infer_input_types']

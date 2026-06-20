"""AST parsing and lightweight script inspection helpers."""

import ast
from .constants import _BUILTIN_NAMES, _ALLOWED_CONSTS
from .errors import CompileError



def _parse_source(source: str):
    """Function `_parse_source` used by the GN Script MVP addon."""
    source = (source or "").strip()
    if not source:
        raise CompileError("Script is empty")
    tree = ast.parse(source, mode="exec")
    allowed = (ast.Assign, ast.Expr, ast.For, ast.If)
    if not tree.body or any(not isinstance(stmt, allowed) for stmt in tree.body):
        raise CompileError("Only assignments, for/if blocks, and expression/call statements are supported")
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                raise CompileError("Assignment target must be a simple name, e.g. out = sin(x)")
    return tree.body

def _assigned_names(stmts):
    """Function `_assigned_names` used by the GN Script MVP addon."""
    return {stmt.targets[0].id for stmt in stmts if isinstance(stmt, ast.Assign)}

def _collect_external_names(node, assigned, names):
    """Function `_collect_external_names` used by the GN Script MVP addon."""
    if isinstance(node, ast.Name):
        if isinstance(node.ctx, ast.Load) and node.id not in assigned and node.id not in _BUILTIN_NAMES and node.id not in _ALLOWED_CONSTS:
            names.add(node.id)
        return
    for child in ast.iter_child_nodes(node):
        _collect_external_names(child, assigned, names)

def _collect_inputs(stmts):
    """Function `_collect_inputs` used by the GN Script MVP addon."""
    assigned = _assigned_names(stmts)
    names = set()
    for stmt in stmts:
        _collect_external_names(stmt, assigned, names)
    return sorted(names)

def _literal_string(expr, context="argument"):
    """Function `_literal_string` used by the GN Script MVP addon."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str) and expr.value:
        return expr.value
    raise CompileError(f"Expected a non-empty string literal for {context}")

def _is_top_level_call(stmt, names=None):
    """Function `_is_top_level_call` used by the GN Script MVP addon."""
    if not isinstance(stmt, ast.Expr):
        return None
    call = stmt.value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
        return None
    if names is not None and call.func.id not in names:
        return None
    return call

def _top_level_call_name(stmt):
    """Function `_top_level_call_name` used by the GN Script MVP addon."""
    call = _is_top_level_call(stmt)
    return call.func.id if call else None

def _needs_geometry_io(stmts):
    """Function `_needs_geometry_io` used by the GN Script MVP addon."""
    return any(_top_level_call_name(stmt) in {"set_position", "store"} for stmt in stmts)

__all__ = ['_parse_source', '_assigned_names', '_collect_external_names', '_collect_inputs', '_literal_string', '_is_top_level_call', '_top_level_call_name', '_needs_geometry_io']

"""AST parsing and lightweight script inspection helpers."""

import ast
from .constants import _ALLOWED_CONSTS, TYPE_TOKEN_NAMES
from .errors import CompileError



def _binding_names(target):
    """Yield simple names bound by a supported assignment/loop target."""
    if isinstance(target, ast.Name):
        yield target.id
        return
    if isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            yield from _binding_names(item)


def _reject_type_token_binding(name, context):
    if name in TYPE_TOKEN_NAMES:
        raise CompileError(f"Type token {name} is reserved and may only be used in node(...) type declarations")


def _validate_no_type_token_bindings(stmts):
    """Reject bindings that would shadow raw-node type tokens."""
    def visit_stmt(stmt):
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                for name in _binding_names(target):
                    _reject_type_token_binding(name, "assignment")
            return
        if isinstance(stmt, ast.AugAssign):
            for name in _binding_names(stmt.target):
                _reject_type_token_binding(name, "augmented assignment")
            return
        if isinstance(stmt, ast.For):
            for name in _binding_names(stmt.target):
                _reject_type_token_binding(name, "for target")
            for sub in stmt.body:
                visit_stmt(sub)
            for sub in stmt.orelse:
                visit_stmt(sub)
            return
        if isinstance(stmt, ast.If):
            for sub in stmt.body:
                visit_stmt(sub)
            for sub in stmt.orelse:
                visit_stmt(sub)
            return
        if isinstance(stmt, ast.FunctionDef):
            _reject_type_token_binding(stmt.name, "function name")
            args = stmt.args
            for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
                _reject_type_token_binding(arg.arg, "function parameter")
            if args.vararg is not None:
                _reject_type_token_binding(args.vararg.arg, "function parameter")
            if args.kwarg is not None:
                _reject_type_token_binding(args.kwarg.arg, "function parameter")
            # Local function bodies are parsed and compiled later, but accepted binding
            # positions inside them must obey the same global reservation contract.
            for sub in stmt.body:
                visit_stmt(sub)

    for stmt in stmts:
        visit_stmt(stmt)


def _parse_source(source: str):
    """Function `_parse_source` used by the NodeForge addon."""
    source = (source or "").strip()
    if not source:
        raise CompileError("Script is empty")
    tree = ast.parse(source, mode="exec")
    allowed = (ast.Assign, ast.AugAssign, ast.Expr, ast.For, ast.If, ast.FunctionDef)
    if not tree.body or any(not isinstance(stmt, allowed) for stmt in tree.body):
        raise CompileError("Only assignments, function definitions, for/if blocks, and expression/call statements are supported")
    _validate_no_type_token_bindings(tree.body)
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                raise CompileError("Assignment target must be a simple name, e.g. out = sin(x)")
    return tree.body

def _assigned_names(stmts):
    """Return names assigned by statements, including if/for bodies.

    This is used only to avoid treating local variables as implicit group
    inputs. It deliberately does not descend into nested function definitions;
    those are compiled as separate node groups with their own input scan.
    """
    names = set()

    def visit_stmt(stmt):
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
            return
        if isinstance(stmt, ast.AugAssign):
            if isinstance(stmt.target, ast.Name):
                names.add(stmt.target.id)
            return
        if isinstance(stmt, ast.For):
            if isinstance(stmt.target, ast.Name):
                names.add(stmt.target.id)
            elif isinstance(stmt.target, (ast.Tuple, ast.List)):
                for elt in stmt.target.elts:
                    if isinstance(elt, ast.Name):
                        names.add(elt.id)
            for sub in stmt.body:
                visit_stmt(sub)
            for sub in stmt.orelse:
                visit_stmt(sub)
            return
        if isinstance(stmt, ast.If):
            for sub in stmt.body:
                visit_stmt(sub)
            for sub in stmt.orelse:
                visit_stmt(sub)
            return
        if isinstance(stmt, ast.FunctionDef):
            return

    for stmt in stmts:
        visit_stmt(stmt)
    return names

def _builtin_names():
    from .builtins import registry as builtin_registry
    return set(builtin_registry.BUILTIN_NAMES) | {"output", "store"}


def _collect_external_names(node, assigned, names, extra_builtin_names=None):
    """Collect names that should become implicit numeric inputs.

    User library function names are passed in as extra builtins so calls such as
    my_function(...) are not mistaken for external input variables.
    """
    builtin_names = _builtin_names()
    extra_builtin_names = extra_builtin_names or set()
    if isinstance(node, ast.Name):
        if (
            isinstance(node.ctx, ast.Load)
            and node.id not in assigned
            and node.id not in builtin_names
            and node.id not in _ALLOWED_CONSTS
            and node.id not in TYPE_TOKEN_NAMES
            and node.id not in extra_builtin_names
        ):
            names.add(node.id)
        return
    for child in ast.iter_child_nodes(node):
        _collect_external_names(child, assigned, names, extra_builtin_names)

def _collect_inputs(stmts, extra_builtin_names=None):
    """Return implicit external numeric input names used by the script."""
    assigned = _assigned_names(stmts)
    names = set()
    for stmt in stmts:
        _collect_external_names(stmt, assigned, names, extra_builtin_names or set())
    return sorted(names)

def _literal_string(expr, context="argument"):
    """Function `_literal_string` used by the NodeForge addon."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str) and expr.value:
        return expr.value
    raise CompileError(f"Expected a non-empty string literal for {context}")

def _is_top_level_call(stmt, names=None):
    """Function `_is_top_level_call` used by the NodeForge addon."""
    if not isinstance(stmt, ast.Expr):
        return None
    call = stmt.value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
        return None
    if names is not None and call.func.id not in names:
        return None
    return call

def _top_level_call_name(stmt):
    """Function `_top_level_call_name` used by the NodeForge addon."""
    call = _is_top_level_call(stmt)
    return call.func.id if call else None

def _needs_geometry_io(stmts):
    """Function `_needs_geometry_io` used by the NodeForge addon."""
    return any(_top_level_call_name(stmt) in {"set_position", "store"} for stmt in stmts)

__all__ = ['_parse_source', '_validate_no_type_token_bindings', '_assigned_names', '_collect_external_names', '_collect_inputs', '_literal_string', '_is_top_level_call', '_top_level_call_name', '_needs_geometry_io']

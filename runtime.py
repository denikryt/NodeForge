"""Repeat Zone and runtime for-loop graph construction helpers."""

import ast
from .constants import *
from .errors import CompileError
from .values import Value
from .compile_time import reject_compile_time_object
from .nodes import _new_node, _switch


def _is_range_call(stmt, name):
    """Return whether a for-loop iterates over a named one-argument range call."""
    return isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id == name


def _parse_repeat_range_for(stmt):
    """Parse scalar runtime loops: for i in repeat_range(n): ..."""
    if not isinstance(stmt.target, ast.Name):
        raise CompileError("repeat_range target must be a simple name, e.g. for i in repeat_range(n)")
    if not (_is_range_call(stmt, "repeat_range") and len(stmt.iter.args) == 1):
        raise CompileError("repeat_range loop must look like: for i in repeat_range(n):")
    if not stmt.body:
        raise CompileError("repeat_range loop body cannot be empty")

    def validate(sub):
        """Validate statements accepted inside repeat_range loop bodies."""
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
            return
        if isinstance(sub, ast.If):
            for branch_sub in list(sub.body) + list(sub.orelse):
                validate(branch_sub)
            return
        raise CompileError("repeat_range body supports assignments and if blocks with assignments")

    for sub in stmt.body:
        validate(sub)
    return stmt.iter.args[0], stmt.body


def _repeat_item_type_for_value(value):
    """Map a NodeForge value type to a Blender Repeat Zone item type."""
    if value.typ == TYPE_GEOMETRY:
        return "GEOMETRY"
    if value.typ == TYPE_VECTOR:
        return "VECTOR"
    if value.typ == TYPE_BOOL:
        return "BOOLEAN"
    if value.typ == TYPE_INT:
        return "INT"
    return "FLOAT"


def _socket_by_name(sockets, name):
    """Return the socket with the given display name or raise a compiler error."""
    for sock in sockets:
        if sock.name == name:
            return sock
    raise CompileError(f"Internal error: missing Repeat Zone socket {name!r}")


def _remove_default_repeat_items(repeat_output):
    """Remove Blender's default Repeat Zone items before adding explicit state."""
    # New Repeat Zones start with a Geometry item. Remove it when building scalar loops.
    try:
        for item in list(repeat_output.repeat_items):
            repeat_output.repeat_items.remove(item)
    except Exception:
        pass


def _assigned_names_in_runtime_body(stmts):
    """Return assignment targets from a repeat_range body in first-seen order."""
    names = []

    def add(name):
        """Append a name once while preserving runtime body order."""
        if name not in names:
            names.append(name)

    def visit(sub):
        """Collect assignment targets from assignments and nested if blocks."""
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
            add(sub.targets[0].id)
            return
        if isinstance(sub, ast.If):
            for branch_sub in list(sub.body) + list(sub.orelse):
                visit(branch_sub)
            return
        raise CompileError("repeat_range body supports assignments and if blocks with assignments")

    for sub in stmts:
        visit(sub)
    return names


def _repeat_state_assignments(group, comp, iterations, body_stmts, index_name=None, x=0, y=0):
    """Compile a repeat_range loop with type-generic Repeat Zone state.

    Existing variables assigned anywhere in the loop body become Repeat Zone state.
    Assignments to new names are iteration-local temporaries. An if block without an
    else conditionally updates state variables and preserves their previous values
    when the condition is false. Geometry, Vector, Float, Int, and Bool states share
    the same Repeat Zone lifecycle and branch-merge path.
    """
    reject_compile_time_object(iterations, "repeat_range iteration count")
    if iterations.typ != TYPE_INT:
        raise CompileError("repeat_range(n) expects an Int value")

    assigned_names = _assigned_names_in_runtime_body(body_stmts)
    state_names = []
    for name in assigned_names:
        if name in comp.vars and name not in state_names:
            state_names.append(name)
    if not state_names:
        raise CompileError("repeat_range loop must update at least one existing variable")

    supported_state_types = {TYPE_GEOMETRY, TYPE_VECTOR, TYPE_FLOAT, TYPE_INT, TYPE_BOOL}
    for name in state_names:
        value = comp.vars[name]
        reject_compile_time_object(value, "repeat_range state")
        if isinstance(value, list) or not isinstance(value, Value):
            raise CompileError("repeat_range state must be a node value")
        if value.typ not in supported_state_types:
            raise CompileError(f"repeat_range state {name!r} has unsupported type {value.typ}")

    if index_name in state_names:
        raise CompileError("repeat_range loop index name cannot also be a state variable")

    ri = _new_node(group, "GeometryNodeRepeatInput", x, y)
    ro = _new_node(group, "GeometryNodeRepeatOutput", x + 1120, y)
    if not ri.pair_with_output(ro):
        raise CompileError("Could not pair Repeat Zone nodes")

    _remove_default_repeat_items(ro)

    system_socket_names = {"Iterations", "Iteration"}
    for sockets in (ri.inputs, ri.outputs, ro.inputs, ro.outputs):
        for socket in sockets:
            system_socket_names.add(socket.name)
    if index_name in system_socket_names:
        raise CompileError(f"repeat_range loop index name {index_name!r} conflicts with Repeat Zone socket name")
    for name in state_names:
        if name in system_socket_names:
            raise CompileError(f"repeat_range state name {name!r} conflicts with Repeat Zone socket name")

    for name in state_names:
        ro.repeat_items.new(_repeat_item_type_for_value(comp.vars[name]), name)

    group.links.new(iterations.socket, ri.inputs[0])
    for name in state_names:
        group.links.new(comp.vars[name].socket, _socket_by_name(ri.inputs, name))

    old_vars = dict(comp.vars)
    state_set = set(state_names)

    def compatible_state_type(name, val):
        """Return whether a Repeat Zone state update preserves its declared type."""
        old_type = old_vars[name].typ
        if val.typ == old_type:
            return True
        return {old_type, val.typ} <= {TYPE_INT, TYPE_FLOAT}

    def compile_assign(sub):
        """Compile a repeat_range assignment and update the current variable frame."""
        target = sub.targets[0].id
        val = comp.compile(sub.value)
        reject_compile_time_object(val, "repeat_range assignment")
        if isinstance(val, list):
            raise CompileError("repeat_range assignments cannot assign arrays")
        if target in state_set and not compatible_state_type(target, val):
            raise CompileError(f"repeat_range state {target!r} changed type from {old_vars[target].typ} to {val.typ}")
        comp.vars[target] = val

    def compile_if(sub, depth=0):
        """Compile a runtime if block and merge changed state with Switch nodes."""
        cond = comp.compile(sub.test)
        reject_compile_time_object(cond, "repeat_range if condition")
        if cond.typ != TYPE_BOOL:
            raise CompileError("repeat_range if condition must be Bool")
        base_vars = dict(comp.vars)

        def run_branch(branch):
            """Compile one conditional branch from the same base variable frame."""
            comp.vars.clear()
            comp.vars.update(base_vars)
            for branch_sub in branch:
                compile_runtime_stmt(branch_sub, depth + 1)
            out = dict(comp.vars)
            comp.vars.clear()
            comp.vars.update(base_vars)
            return out

        true_vars = run_branch(sub.body)
        false_vars = run_branch(sub.orelse) if sub.orelse else dict(base_vars)
        changed = []
        for name in state_names:
            if true_vars.get(name) is not base_vars.get(name) or false_vars.get(name) is not base_vars.get(name):
                changed.append(name)
        for name in changed:
            true_val = true_vars.get(name, base_vars[name])
            false_val = false_vars.get(name, base_vars[name])
            reject_compile_time_object(true_val, "repeat_range if branch merge")
            reject_compile_time_object(false_val, "repeat_range if branch merge")
            if not isinstance(true_val, Value) or not isinstance(false_val, Value):
                raise CompileError("repeat_range if can only merge node state values")
            if true_val.typ != false_val.typ:
                if false_val.typ == TYPE_INT and true_val.typ == TYPE_FLOAT:
                    false_val = Value(false_val.socket, TYPE_FLOAT)
                elif false_val.typ == TYPE_FLOAT and true_val.typ == TYPE_INT:
                    true_val = Value(true_val.socket, TYPE_FLOAT)
                else:
                    raise CompileError(f"repeat_range if branch values for {name} have different types")
            merged = _switch(group, cond, false_val, true_val, x + 680 + depth * 120, y - 220 - len(changed) * 40)
            if name in state_set and not compatible_state_type(name, merged):
                raise CompileError(f"repeat_range state {name!r} changed type from {old_vars[name].typ} to {merged.typ}")
            comp.vars[name] = merged

    def compile_runtime_stmt(sub, depth=0):
        """Compile one statement accepted by a repeat_range loop body."""
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
            compile_assign(sub)
            return
        if isinstance(sub, ast.If):
            compile_if(sub, depth)
            return
        raise CompileError("repeat_range body supports assignments and if blocks with assignments")

    try:
        for name in state_names:
            comp.vars[name] = Value(_socket_by_name(ri.outputs, name), old_vars[name].typ)
        if index_name:
            comp.vars[index_name] = Value(ri.outputs[0], TYPE_INT)
        for sub in body_stmts:
            compile_runtime_stmt(sub)
        for name in state_names:
            val = comp.vars.get(name)
            if val is None:
                raise CompileError(f"repeat_range state {name!r} was not assigned")
            reject_compile_time_object(val, "repeat_range state output")
            if not compatible_state_type(name, val):
                raise CompileError(f"repeat_range state {name!r} changed type from {old_vars[name].typ} to {val.typ}")
            group.links.new(val.socket, _socket_by_name(ro.inputs, name))
    finally:
        comp.vars.clear()
        comp.vars.update(old_vars)

    result = {}
    for name in state_names:
        old_type = old_vars[name].typ
        out_type = TYPE_FLOAT if old_type == TYPE_INT else old_type
        result[name] = Value(_socket_by_name(ro.outputs, name), out_type)
        comp.vars[name] = result[name]
    return result

__all__ = [
    '_parse_repeat_range_for', '_repeat_state_assignments'
]

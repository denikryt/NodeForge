"""Repeat Zone and runtime for-loop graph construction helpers."""

import ast
from .constants import *
from .errors import CompileError
from .values import Value
from .compile_time import reject_compile_time_object
from .nodes import _new_node, _switch
from .geometry import _join_geometry
from .geometry_builder import GeometryBuilder


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
        if isinstance(sub, ast.Expr):
            call = sub.value
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in {"add", "extend"}
                and isinstance(call.func.value, ast.Name)
            ):
                return
        if isinstance(sub, ast.If):
            for branch_sub in list(sub.body) + list(sub.orelse):
                validate(branch_sub)
            return
        raise CompileError("repeat_range body supports assignments, builder methods, and if blocks")

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



class RuntimeStateFrame:
    """Branch-local Repeat Zone state values used while lowering a loop body."""

    def __init__(self, descriptors, current_values=None):
        self.descriptors = tuple(descriptors)
        self.current_values = dict(current_values or {})

    def copy(self):
        """Return an isolated shallow frame copy for speculative branch lowering."""
        return RuntimeStateFrame(self.descriptors, self.current_values)

    def descriptor_for_builder(self, builder):
        """Return the descriptor for *builder* when it belongs to this frame."""
        for descriptor in self.descriptors:
            if isinstance(descriptor, BuilderStateDescriptor) and descriptor.builder is builder:
                return descriptor
        return None


class OrdinaryStateDescriptor:
    """Repeat Zone state descriptor for an existing source variable."""

    def __init__(self, name, initial_value, order):
        self.display_name = name
        self.initial_value = initial_value
        self.old_type = initial_value.typ
        self.source_order_key = order

    def current_get(self, frame):
        return frame.current_values.get(self, self.initial_value)

    def current_set(self, frame, value):
        frame.current_values[self] = value

    def commit_after_repeat(self, comp, value):
        comp.vars[self.display_name] = value

    def repeat_socket_type(self):
        return _repeat_item_type_for_value(self.initial_value)

    def compatible(self, value):
        if value.typ == self.old_type:
            return True
        return {self.old_type, value.typ} <= {TYPE_INT, TYPE_FLOAT}

    def output_type(self):
        return TYPE_FLOAT if self.old_type == TYPE_INT else self.old_type


class BuilderStateDescriptor:
    """Repeat Zone Geometry state descriptor owned by one GeometryBuilder binding."""

    old_type = TYPE_GEOMETRY

    def __init__(self, builder, initial_value, order):
        self.builder = builder
        self.display_name = builder.binding_name
        self.initial_value = initial_value
        self.source_order_key = order

    def current_get(self, frame):
        return frame.current_values.get(self, self.initial_value)

    def current_set(self, frame, value):
        frame.current_values[self] = value

    def commit_after_repeat(self, comp, value):
        self.builder.set_runtime_value(value)

    def repeat_socket_type(self):
        return "GEOMETRY"

    def compatible(self, value):
        return isinstance(value, Value) and value.typ == TYPE_GEOMETRY

    def output_type(self):
        return TYPE_GEOMETRY


def _builder_method_info(comp, sub):
    """Return builder method info for a runtime statement or None."""
    if not isinstance(sub, ast.Expr):
        return None
    call = sub.value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
        return None
    if call.func.attr not in {"add", "extend"} or not isinstance(call.func.value, ast.Name):
        return None
    receiver = call.func.value.id
    builder = comp.vars.get(receiver)
    if not isinstance(builder, GeometryBuilder):
        raise CompileError("repeat_range builder method receiver must be a geometry_builder")
    return builder, call.func.attr, call


def _runtime_state_descriptors(comp, stmts):
    """Collect ordinary and builder Repeat Zone states in first mutation order."""
    descriptors = []
    ordinary_by_name = {}
    builder_by_obj = {}
    order = 0

    def add_ordinary(name):
        nonlocal order
        if name in comp.vars and name not in ordinary_by_name:
            value = comp.vars[name]
            if isinstance(value, GeometryBuilder):
                raise CompileError("Cannot assign over geometry_builder binding")
            reject_compile_time_object(value, "repeat_range state")
            if isinstance(value, list) or not isinstance(value, Value):
                raise CompileError("repeat_range state must be a node value")
            if value.typ not in {TYPE_GEOMETRY, TYPE_VECTOR, TYPE_FLOAT, TYPE_INT, TYPE_BOOL}:
                raise CompileError(f"repeat_range state {name!r} has unsupported type {value.typ}")
            desc = OrdinaryStateDescriptor(name, value, order)
            ordinary_by_name[name] = desc
            descriptors.append(desc)
        order += 1

    def add_builder(builder):
        nonlocal order
        if builder not in builder_by_obj:
            initial = builder.snapshot_for_runtime(comp)
            desc = BuilderStateDescriptor(builder, initial, order)
            builder_by_obj[builder] = desc
            descriptors.append(desc)
        order += 1

    def visit(sub):
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
            add_ordinary(sub.targets[0].id)
            return
        info = _builder_method_info(comp, sub)
        if info is not None:
            add_builder(info[0])
            return
        if isinstance(sub, ast.If):
            for branch_sub in list(sub.body) + list(sub.orelse):
                visit(branch_sub)
            return
        raise CompileError("repeat_range body supports assignments, builder methods, and if blocks")

    for sub in stmts:
        visit(sub)
    descriptors.sort(key=lambda desc: desc.source_order_key)
    return descriptors


def _repeat_state_assignments(group, comp, iterations, body_stmts, index_name=None, x=0, y=0):
    """Compile a repeat_range loop with descriptor-backed Repeat Zone state."""
    reject_compile_time_object(iterations, "repeat_range iteration count")
    if iterations.typ != TYPE_INT:
        raise CompileError("repeat_range(n) expects an Int value")

    descriptors = _runtime_state_descriptors(comp, body_stmts)
    if not descriptors:
        raise CompileError("repeat_range loop must update at least one existing variable or geometry_builder")

    state_names = [desc.display_name for desc in descriptors]
    if len(state_names) != len(set(state_names)):
        raise CompileError("repeat_range state names must be unique")
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

    for desc in descriptors:
        ro.repeat_items.new(desc.repeat_socket_type(), desc.display_name)

    group.links.new(iterations.socket, ri.inputs[0])
    for desc in descriptors:
        group.links.new(desc.initial_value.socket, _socket_by_name(ri.inputs, desc.display_name))

    old_vars = dict(comp.vars)
    descriptor_by_name = {desc.display_name: desc for desc in descriptors if isinstance(desc, OrdinaryStateDescriptor)}
    descriptor_by_builder = {desc.builder: desc for desc in descriptors if isinstance(desc, BuilderStateDescriptor)}
    frame = RuntimeStateFrame(descriptors)

    def set_active_frame(active_frame):
        comp.runtime_state_frame = active_frame

    def clear_active_frame():
        if hasattr(comp, "runtime_state_frame"):
            delattr(comp, "runtime_state_frame")

    def set_comp_state_from_frame(active_frame):
        for desc in descriptors:
            if isinstance(desc, OrdinaryStateDescriptor):
                comp.vars[desc.display_name] = desc.current_get(active_frame)

    def compatible_or_raise(desc, val):
        reject_compile_time_object(val, "repeat_range state")
        if isinstance(val, list) or not isinstance(val, Value):
            raise CompileError("repeat_range state must be a node value")
        if not desc.compatible(val):
            raise CompileError(f"repeat_range state {desc.display_name!r} changed type from {desc.old_type} to {val.typ}")

    def compile_assign(sub, active_frame):
        target = sub.targets[0].id
        set_active_frame(active_frame)
        val = comp.compile(sub.value)
        reject_compile_time_object(val, "repeat_range assignment")
        if isinstance(val, list):
            raise CompileError("repeat_range assignments cannot assign arrays")
        desc = descriptor_by_name.get(target)
        if desc is not None:
            compatible_or_raise(desc, val)
            desc.current_set(active_frame, val)
        comp.vars[target] = val

    def compile_builder_method(sub, active_frame):
        info = _builder_method_info(comp, sub)
        if info is None:
            raise CompileError("repeat_range body supports assignments, builder methods, and if blocks")
        builder, method, call = info
        desc = descriptor_by_builder.get(builder)
        if desc is None:
            raise CompileError("Internal error: missing geometry_builder Repeat Zone state")
        if method == "add":
            if len(call.args) != 1 or call.keywords:
                raise CompileError("builder.add(...) expects one positional Geometry argument")
            set_active_frame(active_frame)
            value = comp.compile(call.args[0])
            reject_compile_time_object(value, "builder.add(...) argument")
            if isinstance(value, list) or not isinstance(value, Value) or value.typ != TYPE_GEOMETRY:
                raise CompileError("builder.add(...) expects Geometry")
            current = desc.current_get(active_frame)
            desc.current_set(active_frame, _join_geometry(group, [current, value], x + 520, y - 160))
            return
        if method == "extend":
            if len(call.args) != 1 or call.keywords:
                raise CompileError("builder.extend(...) expects one positional array argument")
            set_active_frame(active_frame)
            values = comp.compile(call.args[0])
            reject_compile_time_object(values, "builder.extend(...) argument")
            if not isinstance(values, list):
                raise CompileError("builder.extend(...) expects an array of Geometry values")
            checked = []
            for value in values:
                if isinstance(value, list) or not isinstance(value, Value) or value.typ != TYPE_GEOMETRY:
                    raise CompileError("builder.extend(...) expects an array of Geometry values")
                checked.append(value)
            for value in checked:
                current = desc.current_get(active_frame)
                desc.current_set(active_frame, _join_geometry(group, [current, value], x + 520, y - 160))
            return
        raise CompileError("geometry_builder supports only add(), extend(), and .geometry")

    def compile_if(sub, active_frame, depth=0):
        set_active_frame(active_frame)
        cond = comp.compile(sub.test)
        reject_compile_time_object(cond, "repeat_range if condition")
        if cond.typ != TYPE_BOOL:
            raise CompileError("repeat_range if condition must be Bool")
        base_vars = dict(comp.vars)
        base_frame = active_frame.copy()

        def run_branch(branch):
            branch_frame = base_frame.copy()
            comp.vars.clear()
            comp.vars.update(base_vars)
            set_comp_state_from_frame(branch_frame)
            set_active_frame(branch_frame)
            for branch_sub in branch:
                compile_runtime_stmt(branch_sub, branch_frame, depth + 1)
            out_vars = dict(comp.vars)
            comp.vars.clear()
            comp.vars.update(base_vars)
            set_comp_state_from_frame(active_frame)
            set_active_frame(active_frame)
            return branch_frame, out_vars

        true_frame, true_vars = run_branch(sub.body)
        if sub.orelse:
            false_frame, false_vars = run_branch(sub.orelse)
        else:
            false_frame, false_vars = base_frame.copy(), dict(base_vars)

        for desc in descriptors:
            base_val = desc.current_get(base_frame)
            true_val = desc.current_get(true_frame)
            false_val = desc.current_get(false_frame)
            if true_val is base_val and false_val is base_val:
                continue
            reject_compile_time_object(true_val, "repeat_range if branch merge")
            reject_compile_time_object(false_val, "repeat_range if branch merge")
            if not isinstance(true_val, Value) or not isinstance(false_val, Value):
                raise CompileError("repeat_range if can only merge node state values")
            if true_val.typ != false_val.typ:
                if isinstance(desc, OrdinaryStateDescriptor) and false_val.typ == TYPE_INT and true_val.typ == TYPE_FLOAT:
                    false_val = Value(false_val.socket, TYPE_FLOAT)
                elif isinstance(desc, OrdinaryStateDescriptor) and false_val.typ == TYPE_FLOAT and true_val.typ == TYPE_INT:
                    true_val = Value(true_val.socket, TYPE_FLOAT)
                else:
                    raise CompileError(f"repeat_range if branch values for {desc.display_name} have different types")
            merged = _switch(group, cond, false_val, true_val, x + 680 + depth * 120, y - 220)
            compatible_or_raise(desc, merged)
            desc.current_set(active_frame, merged)
            if isinstance(desc, OrdinaryStateDescriptor):
                comp.vars[desc.display_name] = merged

        # Restore branch-local temporaries from the parent frame and keep only
        # descriptor merges. New branch temporaries intentionally do not escape.
        comp.vars.clear()
        comp.vars.update(base_vars)
        set_comp_state_from_frame(active_frame)
        set_active_frame(active_frame)

    def compile_runtime_stmt(sub, active_frame, depth=0):
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
            compile_assign(sub, active_frame)
            return
        if _builder_method_info(comp, sub) is not None:
            compile_builder_method(sub, active_frame)
            return
        if isinstance(sub, ast.If):
            compile_if(sub, active_frame, depth)
            return
        raise CompileError("repeat_range body supports assignments, builder methods, and if blocks")

    try:
        for desc in descriptors:
            repeat_value = Value(_socket_by_name(ri.outputs, desc.display_name), desc.old_type)
            desc.current_set(frame, repeat_value)
            if isinstance(desc, OrdinaryStateDescriptor):
                comp.vars[desc.display_name] = repeat_value
        if index_name:
            comp.vars[index_name] = Value(ri.outputs[0], TYPE_INT)
        set_active_frame(frame)
        for sub in body_stmts:
            compile_runtime_stmt(sub, frame)
        for desc in descriptors:
            val = desc.current_get(frame)
            compatible_or_raise(desc, val)
            group.links.new(val.socket, _socket_by_name(ro.inputs, desc.display_name))
    finally:
        clear_active_frame()
        comp.vars.clear()
        comp.vars.update(old_vars)

    result = {}
    for desc in descriptors:
        out_type = desc.output_type()
        output_value = Value(_socket_by_name(ro.outputs, desc.display_name), out_type)
        desc.commit_after_repeat(comp, output_value)
        result[desc.display_name] = output_value
    return result

__all__ = [
    '_parse_repeat_range_for', '_repeat_state_assignments'
]

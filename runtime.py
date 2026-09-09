"""Repeat Zone and runtime for-loop graph construction helpers."""

import ast
from .constants import *
from .errors import CompileError
from .values import TupleValue, Value
from .compile_time import reject_compile_time_object
from .nodes import _int_value, _new_node, _switch
from .geometry import _join_geometry
from .geometry_builder import GeometryBuilder
from .consteval import _const_eval
from .nf_types import NFType


def _is_range_call(stmt, name):
    """Return whether a for-loop iterates over a named one-argument range call."""
    return isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id == name



def _flat_assignment_target_names(target):
    """Return flat assignment names accepted inside repeat_range, or None."""
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, (ast.Tuple, ast.List)):
        names = []
        for item in target.elts:
            if not isinstance(item, ast.Name):
                return None
            names.append(item.id)
        return tuple(names)
    return None


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
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1:
            names = _flat_assignment_target_names(sub.targets[0])
            if names is not None and len(names) == len(set(names)):
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
        if isinstance(sub, ast.For):
            _parse_repeat_range_for(sub)
            return
        raise CompileError("repeat_range body supports assignments, builder methods, if blocks, and nested repeat_range loops")

    for sub in stmt.body:
        validate(sub)
    return stmt.iter.args[0], stmt.body


def _repeat_item_type_for_nf_type(typ: NFType) -> str:
    """Map one supported semantic type to the exact Blender Repeat item token."""
    mapping = {
        NFType.GEOMETRY: "GEOMETRY",
        NFType.VECTOR: "VECTOR",
        NFType.BOOL: "BOOLEAN",
        NFType.INT: "INT",
        NFType.BUNDLE: "BUNDLE",
        NFType.FLOAT: "FLOAT",
    }
    try:
        return mapping[typ]
    except KeyError as exc:
        raise CompileError(f"repeat_range state has unsupported type {typ}") from exc


def _repeat_item_type_for_value(value):
    """Map a legacy backend Value to a Repeat token, retaining old Float defaulting."""
    try:
        return _repeat_item_type_for_nf_type(value.typ)
    except CompileError:
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



def _create_repeat_zone(group, state_specs, index_name, x=0, y=0):
    """Create one physical Repeat Zone and its compiler-specified state items."""
    ri = _new_node(group, "GeometryNodeRepeatInput", x, y)
    ro = _new_node(group, "GeometryNodeRepeatOutput", x + 1120, y)
    if not ri.pair_with_output(ro):
        raise CompileError("Could not pair Repeat Zone nodes")
    _remove_default_repeat_items(ro)

    # CONTROL_FLOW_IR_BLENDER_REPEAT_SOCKET_COMPAT: Semantic analysis validates compiler-known Repeat
    # names, but Blender may expose version-specific system socket names only after the physical zone is
    # created. Keep this final backend collision check centralized here and treat failure as the existing
    # controlled DSL error. Remove only if NodeForge later owns a versioned authoritative Blender Repeat
    # socket schema that makes the runtime probe unnecessary across all supported Blender versions.
    system_socket_names = {"Iterations", "Iteration"}
    for sockets in (ri.inputs, ri.outputs, ro.inputs, ro.outputs):
        for socket in sockets:
            system_socket_names.add(socket.name)
    if index_name in system_socket_names:
        raise CompileError(f"repeat_range loop index name {index_name!r} conflicts with Repeat Zone socket name")
    names = [name for _typ, name in state_specs]
    if len(names) != len(set(names)):
        raise CompileError("repeat_range state names must be unique")
    for name in names:
        if name in system_socket_names:
            raise CompileError(f"repeat_range state name {name!r} conflicts with Repeat Zone socket name")
    for typ, name in state_specs:
        ro.repeat_items.new(_repeat_item_type_for_nf_type(typ), name)
    return ri, ro


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
        comp.bind_runtime_value(self.display_name, value)

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
        frame, descriptor = comp.runtime_frame_for_builder(self.builder)
        if descriptor is not None:
            descriptor.current_set(frame, value)
            return
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
    builder = comp.legacy_structural_binding(receiver)
    if not isinstance(builder, GeometryBuilder):
        raise CompileError("repeat_range builder method receiver must be a geometry_builder")
    return builder, call.func.attr, call


# CONTROL_FLOW_IR_LEGACY_REPEAT_ENGINE_COMPAT: Migrated ordinary repeat_range() semantics are owned by
# typed IRRepeat and recursive Blender IR lowering. Keep this AST/Compiler/RuntimeStateFrame engine only
# for whole-body fallback categories such as GeometryBuilder and dynamic legacy expressions. New IR
# lowering may reuse backend-only Repeat Zone helpers from this module but must not call this legacy
# semantic engine. Remove it when every supported Repeat body has frontend-owned state semantics.
def _runtime_state_descriptors(comp, stmts):
    """Collect ordinary and builder Repeat Zone states in first mutation order."""
    descriptors = []
    ordinary_by_name = {}
    builder_by_obj = {}
    order = 0

    def add_ordinary(name):
        nonlocal order
        if name in ordinary_by_name:
            order += 1
            return
        symbol = comp.runtime_binding(name)
        if symbol is not None:
            value = comp.runtime_value(name)
        elif comp.has_legacy_structural_binding(name):
            value = comp.legacy_structural_binding(name)
            if isinstance(value, GeometryBuilder):
                raise CompileError("Cannot assign over geometry_builder binding")
        else:
            order += 1
            return
        reject_compile_time_object(value, "repeat_range state")
        if isinstance(value, list) or not isinstance(value, Value):
            raise CompileError("repeat_range state must be a node value")
        if value.typ not in {TYPE_GEOMETRY, TYPE_VECTOR, TYPE_FLOAT, TYPE_INT, TYPE_BOOL, TYPE_BUNDLE}:
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
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1:
            names = _flat_assignment_target_names(sub.targets[0])
            if names is not None:
                for name in names:
                    add_ordinary(name)
                return
        info = _builder_method_info(comp, sub)
        if info is not None:
            add_builder(info[0])
            return
        if isinstance(sub, ast.If):
            for branch_sub in list(sub.body) + list(sub.orelse):
                visit(branch_sub)
            return
        if isinstance(sub, ast.For):
            _iterations_expr, nested_body = _parse_repeat_range_for(sub)
            for nested_sub in nested_body:
                visit(nested_sub)
            return
        raise CompileError("repeat_range body supports assignments, builder methods, if blocks, and nested repeat_range loops")

    for sub in stmts:
        visit(sub)
    descriptors.sort(key=lambda desc: desc.source_order_key)
    return descriptors


def _compile_repeat_iteration_count(group, comp, expr, x=0, y=0):
    """Compile a repeat count while preserving integer constants as Int sockets."""
    try:
        value = _const_eval(expr, comp.consts)
    except CompileError:
        value = None
    if isinstance(value, int) and not isinstance(value, bool):
        return _int_value(group, value, x, y)
    return comp.compile(expr)


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

    old_binding_state = comp._snapshot_binding_state()
    descriptor_by_name = {desc.display_name: desc for desc in descriptors if isinstance(desc, OrdinaryStateDescriptor)}
    descriptor_by_builder = {desc.builder: desc for desc in descriptors if isinstance(desc, BuilderStateDescriptor)}
    frame = RuntimeStateFrame(descriptors)

    def set_active_frame(active_frame):
        """Select a branch-local copy at the current Repeat nesting depth."""
        comp.replace_active_runtime_frame(active_frame)

    def set_comp_state_from_frame(active_frame):
        for desc in descriptors:
            if isinstance(desc, OrdinaryStateDescriptor):
                comp.bind_runtime_value(desc.display_name, desc.current_get(active_frame))

    def compatible_or_raise(desc, val):
        reject_compile_time_object(val, "repeat_range state")
        if isinstance(val, list) or not isinstance(val, Value):
            raise CompileError("repeat_range state must be a node value")
        if not desc.compatible(val):
            raise CompileError(f"repeat_range state {desc.display_name!r} changed type from {desc.old_type} to {val.typ}")

    def compile_assign(sub, active_frame):
        target_node = sub.targets[0]
        names = _flat_assignment_target_names(target_node)
        if names is None:
            raise CompileError("repeat_range assignment target must be a name or flat sequence of names")
        if len(names) != len(set(names)):
            raise CompileError("Tuple unpacking target names must be unique")

        set_active_frame(active_frame)
        val = comp.compile(sub.value)
        reject_compile_time_object(val, "repeat_range assignment")
        if isinstance(target_node, ast.Name):
            values = (val,)
        else:
            if not isinstance(val, TupleValue):
                raise CompileError("Cannot unpack scalar result inside repeat_range")
            if len(names) != len(val):
                raise CompileError(f"Tuple unpacking expected {len(names)} values, got {len(val)}")
            values = val.values

        for item in values:
            reject_compile_time_object(item, "repeat_range assignment")
            if isinstance(item, list):
                raise CompileError("repeat_range assignments cannot assign arrays")

        for name, item in zip(names, values):
            desc = descriptor_by_name.get(name)
            if desc is not None:
                compatible_or_raise(desc, item)

        for name, item in zip(names, values):
            desc = descriptor_by_name.get(name)
            if desc is not None:
                desc.current_set(active_frame, item)
            if isinstance(item, Value):
                comp.bind_runtime_value(name, item)
            else:
                comp.bind_legacy_structural(name, item)
            comp.consts.pop(name, None)

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
        base_binding_state = comp._snapshot_binding_state()
        base_frame = active_frame.copy()

        def run_branch(branch):
            branch_frame = base_frame.copy()
            comp._restore_binding_state(base_binding_state)
            set_comp_state_from_frame(branch_frame)
            set_active_frame(branch_frame)
            try:
                for branch_sub in branch:
                    compile_runtime_stmt(branch_sub, branch_frame, depth + 1)
                return branch_frame, comp._snapshot_binding_state()
            finally:
                comp._restore_binding_state(base_binding_state)
                set_comp_state_from_frame(active_frame)
                set_active_frame(active_frame)

        true_frame, true_binding_state = run_branch(sub.body)
        if sub.orelse:
            false_frame, false_binding_state = run_branch(sub.orelse)
        else:
            false_frame, false_binding_state = base_frame.copy(), base_binding_state

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
                comp.bind_runtime_value(desc.display_name, merged)

        # Restore branch-local temporaries from the parent frame and keep only
        # descriptor merges. New branch temporaries intentionally do not escape.
        comp._restore_binding_state(base_binding_state)
        set_comp_state_from_frame(active_frame)
        set_active_frame(active_frame)

    def compile_runtime_stmt(sub, active_frame, depth=0):
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and _flat_assignment_target_names(sub.targets[0]) is not None:
            compile_assign(sub, active_frame)
            return
        if _builder_method_info(comp, sub) is not None:
            compile_builder_method(sub, active_frame)
            return
        if isinstance(sub, ast.If):
            compile_if(sub, active_frame, depth)
            return
        if isinstance(sub, ast.For):
            iterations_expr, nested_body = _parse_repeat_range_for(sub)
            set_active_frame(active_frame)

            # A nested Repeat may assign the enclosing loop-index name and carry
            # it as ordinary inner state. That state is local to the inner loop:
            # subsequent statements in this lexical Repeat must still see this
            # Repeat's own Iteration socket. Preserve the exact branch-local
            # binding because compile_runtime_stmt() is also used inside runtime
            # if branch frames.
            enclosing_index_state = comp._snapshot_binding_name_state(index_name) if index_name else None
            try:
                nested_iterations = _compile_repeat_iteration_count(
                    group, comp, iterations_expr, x + 300 + depth * 140, y - 360 - depth * 220
                )
                nested_results = _repeat_state_assignments(
                    group,
                    comp,
                    nested_iterations,
                    nested_body,
                    index_name=sub.target.id,
                    x=x + 360 + depth * 180,
                    y=y - 500 - depth * 280,
                )
                # The recursive Repeat commits ordinary variables through Compiler binding APIs.
                # Mirror every state shared with this enclosing Repeat into its current frame.
                for desc in descriptors:
                    value = nested_results.get(desc.display_name)
                    if value is None:
                        continue
                    compatible_or_raise(desc, value)
                    desc.current_set(active_frame, value)
                    if isinstance(desc, OrdinaryStateDescriptor):
                        comp.bind_runtime_value(desc.display_name, value)
            finally:
                if index_name:
                    comp._restore_binding_name_state(index_name, enclosing_index_state)
                set_active_frame(active_frame)
            return
        raise CompileError("repeat_range body supports assignments, builder methods, if blocks, and nested repeat_range loops")

    comp.push_runtime_frame(frame)
    try:
        for desc in descriptors:
            repeat_value = Value(_socket_by_name(ri.outputs, desc.display_name), desc.old_type)
            desc.current_set(frame, repeat_value)
            if isinstance(desc, OrdinaryStateDescriptor):
                comp.bind_runtime_value(desc.display_name, repeat_value)
        if index_name:
            comp.bind_runtime_value(index_name, Value(ri.outputs[0], TYPE_INT))
        for sub in body_stmts:
            compile_runtime_stmt(sub, frame)
        for desc in descriptors:
            val = desc.current_get(frame)
            compatible_or_raise(desc, val)
            group.links.new(val.socket, _socket_by_name(ro.inputs, desc.display_name))
    finally:
        comp.pop_runtime_frame(frame)
        comp._restore_binding_state(old_binding_state)

    result = {}
    for desc in descriptors:
        out_type = desc.output_type()
        output_value = Value(_socket_by_name(ro.outputs, desc.display_name), out_type)
        desc.commit_after_repeat(comp, output_value)
        result[desc.display_name] = output_value
    return result

__all__ = [
    '_compile_repeat_iteration_count', '_parse_repeat_range_for', '_repeat_state_assignments',
    '_repeat_item_type_for_nf_type', '_create_repeat_zone', '_socket_by_name'
]

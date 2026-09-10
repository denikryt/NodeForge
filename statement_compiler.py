"""Top-level statement lowering for NodeForge group compilation."""

import ast
from dataclasses import dataclass, field

from .constants import *
from .errors import CompileError
from .values import Value, TupleValue, reject_tuple_value
from .nodes import _switch
from .parsing import _literal_string, _is_top_level_call
from .statements import (
    _kw_dict,
    _optional_string_kw,
    _selection_kw,
    _string_value_or_literal,
    _check_no_extra_keywords,
    _store_named_attribute,
    _set_position_node,
    _unique_output_name,
)
from .consteval import _const_eval
from .compile_time import reject_compile_time_object
from .interface import _create_interface_panel
from .geometry_builder import GeometryBuilder, validate_geometry_builder_constructor
from .call_resolution import CallableEnvironment
from .builtin_call_semantics import (
    IR_CAPABLE_BUILTIN_NAMES,
    STATEFUL_FALLBACK_BUILTIN_NAMES,
    analyze_input_declaration_call,
)
from .compiler_identities import InputDeclarationId
from .semantic_body import BODY_UNSUPPORTED, lower_basic_body
from .blender_ir_lowering import BlenderIRLoweringContext, lower_body as lower_ir_body
from .runtime_bindings import validate_runtime_binding_target
from .runtime import (
    _compile_repeat_iteration_count,
    _parse_repeat_range_for,
    _repeat_state_assignments,
)


@dataclass
class GroupBuildContext:
    """Mutable statement-compilation state for one group build."""

    group: object
    comp: object
    geometry_mode: bool
    geometry_socket: object = None
    explicit_outputs: list = field(default_factory=list)
    auto_final_output: object = None
    output_names: set = field(default_factory=set)
    input_declaration_ordinals: dict[str, int] = field(default_factory=dict)


def _as_array_iter_value(value):
    """Return a script-level list value when a for-loop can be unrolled."""
    if isinstance(value, list):
        return value
    return None


def _target_names(target):
    """Return simple variable names bound by an unrolled array for-loop target."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)) and all(isinstance(e, ast.Name) for e in target.elts):
        return [e.id for e in target.elts]
    raise CompileError("array for target must be a simple name or tuple of names")


def _check_runtime_binding(comp, name):
    """Defensively apply the canonical compiler-owned runtime binding target rule."""
    validate_runtime_binding_target(name, getattr(comp, "reserved_name_labels", {}))


# GEOMETRY_BUILDER_LEGACY_STATEMENT_COMPAT: Semantic Body owns geometry_builder construction,
# add()/extend(), .geometry snapshots, compile-time loops, and structured runtime control flow for
# accepted core bodies. Keep these AST/Compiler/backend helpers only when another compatibility
# category has already routed the complete original body through compile_statement(). Do not call
# them from Semantic IR construction or Blender IR lowering. Remove them when supported fallback
# bodies no longer require legacy execution of geometry_builder syntax.
def _is_geometry_builder_constructor(expr):
    """Return True for a direct geometry_builder(...) constructor call."""
    return isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "geometry_builder"


def _builder_method_call(comp, stmt):
    """Return (builder, method, call) for a supported builder method statement."""
    if not isinstance(stmt, ast.Expr):
        return None
    call = stmt.value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
        return None
    if not isinstance(call.func.value, ast.Name):
        return None
    receiver = call.func.value.id
    value = comp.legacy_structural_binding(receiver)
    if isinstance(value, GeometryBuilder):
        return value, call.func.attr, call
    return None


def _compile_builder_method(comp, builder, method, call, x=0, y=0):
    """Compile one compile-time builder mutation statement."""
    if method == "add":
        if len(call.args) != 1 or call.keywords:
            raise CompileError("builder.add(...) expects one positional Geometry argument")
        value = comp.compile(call.args[0])
        reject_compile_time_object(value, "builder.add(...) argument")
        builder.add_value(comp, value, x, y)
        return
    if method == "extend":
        if len(call.args) != 1 or call.keywords:
            raise CompileError("builder.extend(...) expects one positional array argument")
        values = comp.compile(call.args[0])
        reject_compile_time_object(values, "builder.extend(...) argument")
        if not isinstance(values, list):
            raise CompileError("builder.extend(...) expects an array of Geometry values")
        builder.extend_values(comp, values, x, y)
        return
    raise CompileError("geometry_builder supports only add(), extend(), and .geometry")


def _compile_panel_statement(ctx, call):
    """Validate and lower one root-level panel() interface declaration."""
    comp = ctx.comp
    if len(call.args) != 1:
        raise CompileError("panel() expects exactly one positional list or tuple of group inputs")
    members_expr = call.args[0]
    if not isinstance(members_expr, (ast.List, ast.Tuple)):
        raise CompileError("panel() first argument must be a list or tuple of input variable names")
    if not members_expr.elts:
        raise CompileError("panel() requires at least one input")
    if not all(isinstance(member, ast.Name) for member in members_expr.elts):
        raise CompileError("panel() items must be simple input variable names")

    kws = _kw_dict(call)
    _check_no_extra_keywords(kws, {"name", "collapsed"})
    if "name" not in kws:
        raise CompileError("panel() requires name=")
    panel_name = _literal_string(kws["name"], "panel() name", comp.compile_time.values)
    collapsed = False
    if "collapsed" in kws:
        try:
            collapsed = _const_eval(kws["collapsed"], comp.compile_time.values)
        except CompileError as exc:
            raise CompileError("panel() collapsed= must be a compile-time bool") from exc
        if not isinstance(collapsed, bool):
            raise CompileError("panel() collapsed= must be a compile-time bool")

    resolved = []
    identities = []
    seen = set()
    for member in members_expr.elts:
        name = member.id
        value = comp.runtime_value(name)
        iface_item = comp.interface_input_for_value(value) if value is not None else None
        identity = comp.interface_input_identity_for_value(value) if value is not None else None
        if iface_item is None or identity is None:
            raise CompileError(f"panel() item {name} is not a group input")
        if identity in seen:
            raise CompileError(f"panel() duplicate input: {name}")
        seen.add(identity)
        existing_panel = comp._panel_input_memberships.get(identity)
        if existing_panel is not None:
            raise CompileError(f'panel() item {name} already belongs to panel "{existing_panel}"')
        resolved.append(iface_item)
        identities.append(identity)

    _create_interface_panel(ctx.group, resolved, panel_name, collapsed=collapsed)
    for identity in identities:
        comp._panel_input_memberships[identity] = panel_name
    ctx.auto_final_output = None


def compile_statement(
    ctx,
    stmt,
    idx=0,
    allow_final_expr=False,
    *,
    allow_interface_directives=False,
):
    """Compile one statement, optionally allowing root-only interface directives."""
    comp = ctx.comp
    group = ctx.group
    call = _is_top_level_call(stmt)

    if call and call.func.id == "panel":
        if not allow_interface_directives:
            raise CompileError("panel() is a top-level interface declaration")
        _compile_panel_statement(ctx, call)
        return

    if isinstance(stmt, ast.Assign):
        if len(stmt.targets) != 1:
            raise CompileError("Assignment supports one name or one flat unpacking target")
        assignment_target = stmt.targets[0]
        if isinstance(assignment_target, (ast.Tuple, ast.List)):
            if any(isinstance(item, ast.Starred) for item in assignment_target.elts):
                raise CompileError("Starred tuple unpacking is not supported")
            if not assignment_target.elts or not all(isinstance(item, ast.Name) for item in assignment_target.elts):
                raise CompileError("Tuple unpacking target must be a flat sequence of names")
            names = [item.id for item in assignment_target.elts]
            if len(set(names)) != len(names):
                raise CompileError("Tuple unpacking target names must be unique")
            for name in names:
                _check_runtime_binding(comp, name)
                if isinstance(comp.legacy_structural_binding(name), GeometryBuilder):
                    raise CompileError("Cannot assign over geometry_builder binding")
            value = comp.compile(stmt.value)
            if not isinstance(value, TupleValue):
                raise CompileError(f"Cannot unpack scalar result into {len(names)} names")
            if len(value) != len(names):
                raise CompileError(f"Tuple unpacking expected {len(names)} values, got {len(value)}")
            for name, item in zip(names, value.values):
                comp.compile_time.discard(name)
                comp.bind_runtime_value(name, item)
            ctx.auto_final_output = None
            return
        if not isinstance(assignment_target, ast.Name):
            raise CompileError("Only simple assignments like name = value are supported")
        target = assignment_target.id
        _check_runtime_binding(comp, target)
        if isinstance(comp.legacy_structural_binding(target), GeometryBuilder) and not _is_geometry_builder_constructor(stmt.value):
            raise CompileError("Cannot assign over geometry_builder binding")
        if _is_geometry_builder_constructor(stmt.value):
            validate_geometry_builder_constructor(stmt.value)
            comp.compile_time.discard(target)
            comp.bind_legacy_structural(target, GeometryBuilder(binding_name=target))
            ctx.auto_final_output = None
            return
        if (
            isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name)
            and stmt.value.func.id.startswith("input_")
        ):
            try:
                semantics = analyze_input_declaration_call(stmt.value, comp.compile_time.values)
            except ValueError:
                semantics = None
            if semantics is not None:
                comp.compile_time.discard(target)
                ordinal = ctx.input_declaration_ordinals.get(target, 0)
                ctx.input_declaration_ordinals[target] = ordinal + 1
                declaration_id = InputDeclarationId(comp.input_declaration_owner, target, ordinal)
                value = comp._create_input_socket_value(
                    semantics.display_name,
                    semantics.typ,
                    semantics.default,
                    declaration_id=declaration_id,
                )
                comp.bind_runtime_value(target, value)
                ctx.auto_final_output = (target, value)
                return
        try:
            comp.compile_time.bind(target, _const_eval(stmt.value, comp.compile_time.values))
        except CompileError:
            comp.compile_time.discard(target)
        # STRUCTURAL_ARRAYS_LEGACY_ASSIGNMENT_COMPAT: Accepted Semantic Body IR now owns source array
        # construction, persistent leaves, and alias identity. Keep this Python-list/backend construction
        # only when a separately marked non-array category has already routed the complete body through the
        # legacy statement engine. Do not call this branch from Semantic IR lowering. Remove it when no
        # supported compatibility body containing array assignment is compiled by compile_statement().
        if isinstance(stmt.value, (ast.List, ast.Tuple)):
            if isinstance(stmt.value, ast.List) and not stmt.value.elts:
                comp.compile_time.discard(target)
            values = [comp.compile(e) for e in stmt.value.elts]
            reject_compile_time_object(values, "array literal")
            comp.bind_legacy_structural(target, values)
            ctx.auto_final_output = None
            return
        value = comp.compile(stmt.value)
        if isinstance(value, GeometryBuilder):
            reject_compile_time_object(value, "assignment")
        if isinstance(value, Value):
            comp.bind_runtime_value(target, value)
        else:
            comp.bind_legacy_structural(target, value)
        if isinstance(value, (list, TupleValue)):
            ctx.auto_final_output = None
        else:
            try:
                reject_compile_time_object(value, "final auto-output")
            except CompileError:
                ctx.auto_final_output = None
            else:
                ctx.auto_final_output = (target, value)
        return

    if isinstance(stmt, ast.AugAssign):
        if not isinstance(stmt.target, ast.Name):
            raise CompileError("Only simple augmented assignments like name += value are supported")
        target = stmt.target.id
        _check_runtime_binding(comp, target)
        if comp.runtime_value(target) is None:
            raise CompileError(f"Unknown name for augmented assignment: {target}")
        bin_expr = ast.BinOp(left=ast.Name(id=target, ctx=ast.Load()), op=stmt.op, right=stmt.value)
        value = comp.compile(bin_expr)
        reject_compile_time_object(value, "augmented assignment")
        comp.bind_runtime_value(target, value)
        comp.compile_time.discard(target)
        if isinstance(value, list):
            ctx.auto_final_output = None
        else:
            ctx.auto_final_output = (target, value)
        return

    if isinstance(stmt, ast.Expr):
        expr = stmt.value
        builder_call = _builder_method_call(comp, stmt)
        if builder_call is not None:
            builder, method, method_call = builder_call
            _compile_builder_method(comp, builder, method, method_call, 360 + idx * 120, -120 - idx * 50)
            ctx.auto_final_output = None
            return
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "info":
            comp.compile(expr)
            ctx.auto_final_output = None
            return
        # STRUCTURAL_ARRAYS_LEGACY_APPEND_COMPAT: Accepted Semantic Body IR now owns append mutation through
        # StructuralArrayId/state. Keep this in-place Python-list append only for complete legacy compatibility
        # bodies selected because of another unmigrated category. Do not share these list objects with the new
        # semantic array heap. Remove this branch when no supported compatibility body containing append is
        # compiled by compile_statement().
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "append":
            if not isinstance(expr.func.value, ast.Name) or len(expr.args) != 1:
                raise CompileError("append must look like items.append(value)")
            list_name = expr.func.value.id
            arr = comp.legacy_structural_binding(list_name)
            if not isinstance(arr, list):
                raise CompileError(f"{list_name} is not an array")
            value = comp.compile(expr.args[0])
            reject_compile_time_object(value, "array append")
            reject_tuple_value(value, "array append")
            arr.append(value)
            comp.compile_time.discard(list_name)
            ctx.auto_final_output = None
            return
        if not (call and call.func.id in {"store", "set_position", "output"}):
            if not allow_final_expr:
                raise CompileError("Only assignments, array append, for/if blocks, store(), set_position() and output() may appear before the final expression")
            value = comp.compile(expr)
            reject_compile_time_object(value, "final expression")
            reject_tuple_value(value, "final expression")
            if isinstance(value, list):
                raise CompileError("A final expression cannot be an array; use join(array) or index it")
            ctx.auto_final_output = ("out", value)
            return

    if isinstance(stmt, ast.For):
        # CONTROL_FLOW_IR_LEGACY_REPEAT_DISPATCH_COMPAT: Ordinary repeat_range() is now represented by
        # IRRepeat. Keep this AST dispatch only for whole-body legacy fallback, including GeometryBuilder and
        # dynamic-call bodies not yet representable in Semantic IR. Do not invoke it after IRBody lowering has
        # started. Remove it when no supported repeat_range() body depends on legacy statement compilation.
        if isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id == "repeat_range":
            for target_name in _target_names(stmt.target):
                _check_runtime_binding(comp, target_name)
            iterations_expr, body = _parse_repeat_range_for(stmt)
            iterations = _compile_repeat_iteration_count(group, comp, iterations_expr, 240 + idx * 120, -260 - idx * 50)
            reject_compile_time_object(iterations, "repeat_range iteration count")
            reject_tuple_value(iterations, "repeat_range iteration count")
            if iterations.typ != TYPE_INT:
                raise CompileError("repeat_range(n) expects an Int input or integer value")
            results = _repeat_state_assignments(group, comp, iterations, body, index_name=stmt.target.id, x=300 + idx * 160, y=-380 - idx * 70)
            if results:
                last_name = list(results.keys())[-1]
                ctx.auto_final_output = (last_name, results[last_name])
            return

        # STRUCTURAL_ARRAYS_LEGACY_FOR_COMPAT: Accepted Semantic Body IR now unrolls ordinary for-loops over
        # body-owned arrays and compile-time list/tuple/range iterables before Blender lowering. Keep this
        # Compiler/backend unroller only for complete legacy compatibility bodies selected by another
        # unmigrated category. It must never be entered after IRBody acceptance. Remove it when no supported
        # compatibility body containing an ordinary structural for-loop uses compile_statement().
        iter_values = None
        if isinstance(stmt.iter, ast.Name):
            iter_values = _as_array_iter_value(comp.legacy_structural_binding(stmt.iter.id))
        if iter_values is None:
            try:
                raw_iter = _const_eval(stmt.iter, comp.compile_time.values)
                if isinstance(raw_iter, (list, tuple)):
                    iter_values = [comp._compile_const_value(v, 260 + idx * 120, -220 - idx * 50) for v in raw_iter]
            except CompileError:
                iter_values = None
        if iter_values is not None:
            target_names = _target_names(stmt.target)
            for target_name in target_names:
                _check_runtime_binding(comp, target_name)
            old_states = {name: comp._snapshot_binding_name_state(name) for name in target_names}
            try:
                for item in iter_values:
                    assignments = (item,) if len(target_names) == 1 else item
                    if len(target_names) != 1 and (not isinstance(item, list) or len(item) != len(target_names)):
                        raise CompileError("tuple unpack in for loop needs matching tuple/list item length")
                    for name, val in zip(target_names, assignments):
                        if isinstance(val, Value):
                            comp.bind_runtime_value(name, val)
                        else:
                            comp.bind_legacy_structural(name, val)
                    for sub in stmt.body:
                        compile_statement(ctx, sub, idx, allow_final_expr=False, allow_interface_directives=False)
            finally:
                for name in target_names:
                    comp._restore_binding_name_state(name, old_states[name])
            return

        if isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id == "range":
            raise CompileError("range(...) requires compile-time integer arguments; use repeat_range(...) for Repeat Zone loops")
        raise CompileError("for loop requires a compile-time iterable, an array, or repeat_range(...)")

    # CONTROL_FLOW_IR_LEGACY_IF_COMPAT: Ordinary runtime if is now represented by IRIf and lowered by
    # blender_ir_lowering. Keep this AST/Compiler/backend implementation only because remaining whole-body
    # fallback categories can still contain legacy if statements. New Semantic IR paths must never call
    # this branch. Remove it when compile_statement() is no longer a production path for supported bodies.
    if isinstance(stmt, ast.If):
        try:
            branch = stmt.body if bool(_const_eval(stmt.test, comp.compile_time.values)) else stmt.orelse
            for sub in branch:
                compile_statement(ctx, sub, idx, allow_final_expr=False, allow_interface_directives=False)
            return
        except CompileError:
            pass
        if not stmt.orelse:
            raise CompileError("runtime if currently requires an else branch")

        def _contains_builder_method(stmts):
            for sub in stmts:
                if _builder_method_call(comp, sub) is not None:
                    return True
                if isinstance(sub, ast.If) and _contains_builder_method(list(sub.body) + list(sub.orelse)):
                    return True
            return False
        if _contains_builder_method(list(stmt.body) + list(stmt.orelse)):
            raise CompileError("geometry_builder mutations inside runtime if are supported only inside repeat_range(...)")
        cond = comp.compile(stmt.test)
        reject_compile_time_object(cond, "runtime if condition")
        base_state = comp._snapshot_binding_state()
        base_vars = comp._binding_identity_view(base_state)
        saved_auto = ctx.auto_final_output

        def _compile_runtime_if_branch(branch_stmts):
            """Compile one dynamic if branch and report variables changed by the branch."""
            comp._restore_binding_state(base_state)
            ctx.auto_final_output = saved_auto
            for sub in branch_stmts:
                compile_statement(ctx, sub, idx, allow_final_expr=False, allow_interface_directives=False)
            branch_state = comp._snapshot_binding_state()
            branch_vars = comp._binding_identity_view(branch_state)
            changed = {
                name for name, value in branch_vars.items()
                if name not in base_vars or base_vars.get(name) is not value
            }
            comp._restore_binding_state(base_state)
            ctx.auto_final_output = saved_auto
            return branch_state, branch_vars, changed

        true_state, true_vars, true_changed = _compile_runtime_if_branch(stmt.body)
        false_state, false_vars, false_changed = _compile_runtime_if_branch(stmt.orelse)
        common_changed = sorted(true_changed & false_changed)
        if not common_changed:
            raise CompileError("runtime if branches must assign at least one common variable")

        last_target = None
        for target in common_changed:
            true_val = true_vars[target]
            false_val = false_vars[target]
            if isinstance(true_val, list) or isinstance(false_val, list):
                raise CompileError("runtime if cannot assign arrays")
            reject_compile_time_object(true_val, "runtime if branch merge")
            reject_tuple_value(true_val, "runtime if branch merge")
            reject_compile_time_object(false_val, "runtime if branch merge")
            reject_tuple_value(false_val, "runtime if branch merge")
            if not isinstance(true_val, Value) or not isinstance(false_val, Value):
                raise CompileError("runtime if branches must assign node values")
            if true_val.typ != false_val.typ:
                raise CompileError(f"runtime if branch values for {target} have different types")
            merged = _switch(group, cond, false_val, true_val, 360 + idx * 160, -220 - idx * 70)
            comp.bind_runtime_value(target, merged)
            last_target = target

        if last_target is not None:
            ctx.auto_final_output = (last_target, comp.runtime_value(last_target))
        return

    if call and call.func.id == "store":
        if ctx.geometry_socket is None:
            raise CompileError("Internal error: store() requires geometry mode")
        if len(call.args) != 2:
            raise CompileError('store(attribute_name, value, selection=..., domain="POINT", type="FLOAT") expects 2 positional arguments')
        kws = _kw_dict(call)
        _check_no_extra_keywords(kws, {"selection", "domain", "type"})
        attr_name = _string_value_or_literal(comp, call.args[0], "store() attribute name")
        value = comp.compile(call.args[1])
        reject_compile_time_object(value, "store() value")
        reject_tuple_value(value, "store() value")
        if isinstance(value, list):
            raise CompileError("store() value cannot be an array")
        selection = _selection_kw(comp, kws)
        domain = _optional_string_kw(kws, "domain", "POINT", comp.compile_time.values)
        data_type_override = _optional_string_kw(kws, "type", None, comp.compile_time.values)
        ctx.geometry_socket = _store_named_attribute(group, ctx.geometry_socket, attr_name, value, selection, domain, data_type_override, 520 + idx * 130, -260 - idx * 60)
        ctx.auto_final_output = None
        return

    if call and call.func.id == "set_position":
        if ctx.geometry_socket is None:
            raise CompileError("Internal error: set_position() requires geometry mode")
        if len(call.args) != 1:
            raise CompileError("set_position(position_vector, selection=...) expects exactly one positional argument")
        kws = _kw_dict(call)
        _check_no_extra_keywords(kws, {"selection"})
        pos = comp.compile(call.args[0])
        reject_compile_time_object(pos, "set_position() position")
        reject_tuple_value(pos, "set_position() position")
        selection = _selection_kw(comp, kws)
        ctx.geometry_socket = _set_position_node(group, ctx.geometry_socket, pos, selection, 520 + idx * 130, -40 - idx * 60)
        ctx.auto_final_output = None
        return

    if call and call.func.id == "output":
        if call.keywords:
            kws = _kw_dict(call)
            _check_no_extra_keywords(kws, {"name", "value"})
            if call.args:
                raise CompileError('output() cannot mix positional and keyword arguments')
            if "value" not in kws:
                raise CompileError('output(name="Name", value=value) expects value=...')
            if "name" in kws:
                out_name = _unique_output_name(ctx.output_names, _literal_string(kws["name"], "output() name", comp.compile_time.values))
            else:
                out_name = _unique_output_name(ctx.output_names, "out")
            value_expr = kws["value"]
        elif len(call.args) == 1:
            out_name = _unique_output_name(ctx.output_names, "out")
            value_expr = call.args[0]
        elif len(call.args) == 2:
            out_name = _unique_output_name(ctx.output_names, _literal_string(call.args[0], "output() name", comp.compile_time.values))
            value_expr = call.args[1]
        else:
            raise CompileError('output(value), output("Name", value), or output(name="Name", value=value) expected')
        value = comp.compile(value_expr)
        reject_compile_time_object(value, "output() value")
        reject_tuple_value(value, "output() value")
        if isinstance(value, list):
            raise CompileError("output() cannot output an array directly; use join(array) or index it")
        ctx.explicit_outputs.append((out_name, value))
        ctx.auto_final_output = None
        return

    raise CompileError("Unsupported statement")


def compile_statements(ctx, stmts):
    """Compile one body through Semantic Body IR or one whole legacy fallback route."""
    comp = ctx.comp
    runtime_bindings = comp.runtime_bindings_snapshot()
    callable_environment = CallableEnvironment(
        callable_builtins=frozenset(IR_CAPABLE_BUILTIN_NAMES | STATEFUL_FALLBACK_BUILTIN_NAMES),
        system_constructors=comp.resolved_environment.system_constructors,
        local_functions=comp.local_functions,
        backend_helper_names=frozenset(comp.backend_builtins),
        imported_functions=comp.imported_library_functions,
    )
    root_compile_time = comp.compile_time.snapshot()
    body_compilation = lower_basic_body(
        stmts,
        initial_runtime_bindings=runtime_bindings,
        initial_compile_time=root_compile_time,
        legacy_binding_names=comp.legacy_structural_binding_names_snapshot(),
        reserved_name_labels=getattr(comp, "reserved_name_labels", {}),
        callable_environment=callable_environment,
        owner_scope=comp.function_group_owner_scope,
        declaration_owner=comp.input_declaration_owner,
    )
    if body_compilation is BODY_UNSUPPORTED:
        # COMPILE_TIME_STATE_LEGACY_STATEMENT_PATH_COMPAT: Whole-body fallback still executes the legacy
        # AST/Compiler statement engine for arrays, GeometryBuilder, stateful statements, and dynamic
        # result categories. The preceding Semantic Body attempt runs on a detached speculative
        # CompileTimeState, so BODY_UNSUPPORTED reaches this branch with Compiler.compile_time unchanged.
        # This legacy engine then mutates only the committed Compiler.compile_time owner; it must not
        # resurrect comp.consts as a second state owner. Remove this marker with the whole-body legacy
        # statement path when all supported statement categories are Semantic-IR owned.
        for idx, stmt in enumerate(stmts):
            compile_statement(
                ctx,
                stmt,
                idx,
                allow_final_expr=(idx == len(stmts) - 1),
                allow_interface_directives=True,
            )
        return ctx

    comp.compile_time.replace(body_compilation.final_compile_time)
    backend_context = BlenderIRLoweringContext(
        group=ctx.group,
        runtime_bindings=comp.backend_runtime_values_snapshot(),
    )
    result = lower_ir_body(
        backend_context,
        body_compilation.body,
        comp.backend_runtime_values_snapshot(),
        base_depth=1,
        group_input=comp.group_input,
    )
    ctx.explicit_outputs.extend(result.explicit_outputs)
    ctx.output_names.update(name for name, _ in result.explicit_outputs)
    ctx.auto_final_output = None if body_compilation.clear_auto_final_output else result.auto_output
    return ctx


__all__ = ["GroupBuildContext", "compile_statement", "compile_statements"]

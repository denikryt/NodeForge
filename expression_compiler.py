"""AST expression lowering into Geometry Nodes."""

import ast

from .constants import *
from .errors import CompileError
from .values import Value, ObjectValue, TupleValue, NodeResult, reject_tuple_value
from .nodes import *
from .consteval import _const_eval
from .builtins import registry as builtin_registry
from .compile_time import reject_compile_time_object
from .geometry_builder import GeometryBuilder
from .systems import registry as systems_registry
from . import local_functions
from . import library_calls
from .function_instances import extract_function_call_modifiers, unsupported_unique
from .call_resolution import CallableEnvironment, CallableKind, UNRESOLVED, resolve_simple_callable
from .builtin_call_semantics import (
    INPUT_DECLARATION_BUILTIN_NAMES,
    INPUT_DECLARATION_PLACEMENT_ERROR,
    IR_CAPABLE_BUILTIN_NAMES,
    STATEFUL_FALLBACK_BUILTIN_NAMES,
)
from .semantic_analysis import analyze_expression, build_semantic_environment
from .semantic_lowering import lower_analyzed_expression
from .blender_ir_lowering import BlenderIRLoweringContext, lower_expression as lower_ir_expression


def _contains_resolved_core_grid_call(expr, callable_environment):
    """Return whether *expr* contains a resolved core grid/grid_uv builtin call."""
    for node in ast.walk(expr):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id not in {"grid", "grid_uv"}:
            continue
        resolved = resolve_simple_callable(node.func.id, callable_environment)
        if resolved is not UNRESOLVED and resolved.kind is CallableKind.BUILTIN:
            return True
    return False


def compile_expr(comp, expr, depth=0):
    """Compile one AST expression into the active node group context."""
    runtime_bindings = comp.runtime_bindings_snapshot()
    # STRUCTURAL_SEMANTICS_REMAINING_LEGACY_NAME_FALLBACK: Fixed tuple and raw named-output bindings
    # now have frontend semantic descriptors, but arrays, GeometryBuilder/CompileTimeObject state,
    # and structural values created only by whole-body dynamic-call fallback still expose names only.
    # Pass those names without backend objects so semantic analysis can reject the complete expression
    # or body before Blender effects. Remove this fallback when every remaining source-visible binding
    # category has frontend-owned semantic metadata and no name-only legacy structural set is needed.
    legacy_binding_names = comp.legacy_structural_binding_names_snapshot()
    callable_environment = CallableEnvironment(
        callable_builtins=frozenset(IR_CAPABLE_BUILTIN_NAMES | STATEFUL_FALLBACK_BUILTIN_NAMES),
        system_constructors=comp.resolved_environment.system_constructors,
        local_functions=comp.local_functions,
        backend_helper_names=frozenset(comp.backend_builtins),
        imported_functions=comp.imported_library_functions,
    )
    environment = build_semantic_environment(
        runtime_bindings=runtime_bindings,
        legacy_binding_names=legacy_binding_names,
        compile_time=comp.compile_time.snapshot(),
        reserved_name_labels=getattr(comp, "reserved_name_labels", {}),
        callable_environment=callable_environment,
    )
    # CONTEXTUAL_GROUP_LEGACY_GRID_EXPRESSION_ROUTE_COMPAT: A whole-body compatibility route
    # compiles statements one by one, but migrated grid()/grid_uv() normally keep GRID_UV only in
    # one Semantic IR lowering context. When compile_statements() has selected legacy whole-body
    # execution, route only expressions containing resolved core grid/grid_uv builtins through the
    # existing legacy dispatcher so they continue sharing comp.grid_context. Other expressions in
    # the same fallback body remain semantic-first. Remove this branch together with
    # CONTEXTUAL_GROUP_LEGACY_GRID_CONTEXT_COMPAT when legacy fallback bodies no longer need it.
    force_legacy_grid_expression = (
        getattr(comp, "_legacy_contextual_grid_expression_routing_active", False)
        and _contains_resolved_core_grid_call(expr, callable_environment)
    )
    analysis = None if force_legacy_grid_expression else analyze_expression(expr, environment)
    # SEMANTIC_CALL_IR_FALLBACK: Stateless compiler-owned calls now lower through Semantic IR,
    # but resolved dynamic extension calls, explicitly stateful compiler-owned builtins, and
    # legacy non-Value compiler bindings can still make analysis unsupported. Preserve legacy
    # whole-expression dispatch only for those marked categories; do not add opaque backend
    # leaves or a temporary lowering-session protocol. Remove this branch when all three
    # fallback sources have permanent frontend-owned semantic/runtime contracts.
    if analysis is None:
        x = depth * 240
        y = -depth * 90
    else:
        ir = lower_analyzed_expression(expr, analysis)
        context = BlenderIRLoweringContext(
            group=comp.group,
            runtime_bindings=comp.backend_runtime_values_snapshot(),
        )
        return lower_ir_expression(context, ir, depth)

    if isinstance(expr, ast.Constant):
        if isinstance(expr.value, bool):
            val = _value(comp.group, 1.0 if expr.value else 0.0, x, y)
            zero = _value(comp.group, 0.0, x + 20, y - 40)
            return _compare(comp.group, "NOT_EQUAL", val, zero, x, y)
        if isinstance(expr.value, (int, float)):
            return _value(comp.group, expr.value, x, y)
        if isinstance(expr.value, str):
            return _string_value(comp.group, expr.value, x, y)
        raise CompileError("Only numeric, boolean and string constants are supported")

    if isinstance(expr, ast.Name):
        if expr.id in TYPE_TOKEN_NAMES:
            raise CompileError(f"Type token {expr.id} may only be used in node(...) type declarations")
        runtime_value = comp.runtime_value(expr.id)
        if runtime_value is not None:
            return runtime_value
        if comp.has_legacy_structural_binding(expr.id):
            return comp.legacy_structural_binding(expr.id)
        if comp.compile_time.contains(expr.id):
            return comp._compile_const_value(comp.compile_time.get(expr.id), x, y)
        if expr.id in _ALLOWED_CONSTS:
            return _value(comp.group, _ALLOWED_CONSTS[expr.id], x, y)
        label = getattr(comp, "reserved_name_labels", {}).get(expr.id)
        if label in {"DSL builtin", "imported function", "local function", "backend helper", "embedded-system constructor", "reserved helper"}:
            raise CompileError(f"Name {expr.id} is registered as {label} and cannot be used as a value")
        raise CompileError(f"Unknown name: {expr.id}")

    if isinstance(expr, ast.Attribute):
        base = compile_expr(comp, expr.value, depth + 1)
        # GEOMETRY_BUILDER_LEGACY_EXPRESSION_COMPAT: Accepted Semantic Body paths resolve builder.geometry
        # from frontend builder identity/state and lower only ordinary Geometry BindingIds. Keep this
        # GeometryBuilder object branch solely for complete bodies already routed through legacy statement
        # compatibility because of another unsupported category. Remove it when legacy whole-body execution
        # can no longer produce GeometryBuilder objects.
        if isinstance(base, GeometryBuilder):
            if expr.attr == "geometry":
                return base.geometry_value(comp, x, y)
            raise CompileError("geometry_builder supports only .geometry")
        if isinstance(base, NodeResult):
            return base.get_output(expr.attr)
        if isinstance(base, ObjectValue):
            return base.resolve_property(expr.attr, comp.group, x=x, y=y)
        if expr.attr in {"x", "y", "z"}:
            reject_compile_time_object(base, f".{expr.attr} attribute access")
            return _separate_xyz(comp.group, base, expr.attr, x, y)
        raise CompileError("Only .x, .y and .z vector attributes are supported")

    if isinstance(expr, ast.BinOp):
        left = compile_expr(comp, expr.left, depth + 1)
        right = compile_expr(comp, expr.right, depth + 1)
        reject_compile_time_object(left, "binary expression")
        reject_tuple_value(left, "binary expression")
        reject_compile_time_object(right, "binary expression")
        reject_tuple_value(right, "binary expression")
        op_type = type(expr.op)
        if op_type not in _BIN_OPS:
            raise CompileError(f"Unsupported binary operator: {op_type.__name__}")
        if _is_number_type(left.typ) and _is_number_type(right.typ):
            return _math(comp.group, _BIN_OPS[op_type], [left, right], x, y)
        if op_type in {ast.Add, ast.Sub} and left.typ == TYPE_VECTOR and right.typ == TYPE_VECTOR:
            return _vector_math(comp.group, _BIN_OPS[op_type], [left, right], TYPE_VECTOR, x, y)
        if op_type is ast.Mult:
            if left.typ == TYPE_VECTOR and right.typ == TYPE_FLOAT:
                return _vector_math(comp.group, "SCALE", [left, right], TYPE_VECTOR, x, y)
            if left.typ == TYPE_FLOAT and right.typ == TYPE_VECTOR:
                return _vector_math(comp.group, "SCALE", [right, left], TYPE_VECTOR, x, y)
            if left.typ == TYPE_VECTOR and right.typ == TYPE_VECTOR:
                return _vector_math(comp.group, "MULTIPLY", [left, right], TYPE_VECTOR, x, y)
        if op_type is ast.Div and left.typ == TYPE_VECTOR and right.typ == TYPE_FLOAT:
            inv = _math(comp.group, "DIVIDE", [_value(comp.group, 1.0, x, y - 40), right], x, y)
            return _vector_math(comp.group, "SCALE", [left, inv], TYPE_VECTOR, x, y)
        raise CompileError(f"Unsupported operation between {left.typ} and {right.typ}")

    if isinstance(expr, ast.UnaryOp):
        val = compile_expr(comp, expr.operand, depth + 1)
        reject_compile_time_object(val, "unary expression")
        reject_tuple_value(val, "unary expression")
        if isinstance(expr.op, ast.UAdd):
            return val
        if isinstance(expr.op, ast.USub):
            if _is_number_type(val.typ):
                return _math(comp.group, "SUBTRACT", [_value(comp.group, 0.0, x, y - 40), val], x, y)
            if val.typ == TYPE_VECTOR:
                return _vector_math(comp.group, "SCALE", [val, _value(comp.group, -1.0, x, y - 40)], TYPE_VECTOR, x, y)
        if isinstance(expr.op, ast.Not):
            if val.typ != TYPE_BOOL:
                raise CompileError("not expects Bool")
            node = _new_node(comp.group, "FunctionNodeBooleanMath", x, y)
            node.operation = "NOT"
            comp.group.links.new(val.socket, node.inputs[0])
            return Value(node.outputs[0], TYPE_BOOL)
        raise CompileError(f"Unsupported unary operator: {type(expr.op).__name__}")

    if isinstance(expr, ast.BoolOp):
        if len(expr.values) < 2:
            return compile_expr(comp, expr.values[0], depth + 1)
        op = _BOOLEAN_OPS.get(type(expr.op))
        if not op:
            raise CompileError("Unsupported boolean operator")
        current = compile_expr(comp, expr.values[0], depth + 1)
        reject_compile_time_object(current, "boolean expression")
        reject_tuple_value(current, "boolean expression")
        for nxt_expr in expr.values[1:]:
            nxt = compile_expr(comp, nxt_expr, depth + 1)
            reject_compile_time_object(nxt, "boolean expression")
            reject_tuple_value(nxt, "boolean expression")
            current = _boolean_math(comp.group, op, [current, nxt], x, y)
        return current

    if isinstance(expr, ast.Compare):
        if len(expr.ops) < 1 or len(expr.comparators) < 1:
            raise CompileError("Invalid comparison")
        comparisons = []
        left_expr = expr.left
        for op_node, right_expr in zip(expr.ops, expr.comparators):
            left = compile_expr(comp, left_expr, depth + 1)
            right = compile_expr(comp, right_expr, depth + 1)
            reject_compile_time_object(left, "comparison")
            reject_tuple_value(left, "comparison")
            reject_compile_time_object(right, "comparison")
            reject_tuple_value(right, "comparison")
            op = _COMPARE_OPS.get(type(op_node))
            if not op:
                raise CompileError("Unsupported comparison operator")
            comparisons.append(_compare(comp.group, op, left, right, x, y))
            left_expr = right_expr
        current = comparisons[0]
        for nxt in comparisons[1:]:
            current = _boolean_math(comp.group, "AND", [current, nxt], x, y)
        return current

    if isinstance(expr, ast.IfExp):
        cond = compile_expr(comp, expr.test, depth + 1)
        true_val = compile_expr(comp, expr.body, depth + 1)
        false_val = compile_expr(comp, expr.orelse, depth + 1)
        reject_compile_time_object(cond, "if-expression condition")
        reject_tuple_value(cond, "if-expression condition")
        reject_compile_time_object(true_val, "if-expression result")
        reject_tuple_value(true_val, "if-expression result")
        reject_compile_time_object(false_val, "if-expression result")
        reject_tuple_value(false_val, "if-expression result")
        if isinstance(true_val, list) or isinstance(false_val, list):
            raise CompileError("if-expression cannot return arrays")
        return _switch(comp.group, cond, false_val, true_val, x, y)

    if isinstance(expr, (ast.List, ast.Tuple)):
        values = [compile_expr(comp, e, depth + 1) for e in expr.elts]
        reject_compile_time_object(values, "array literal")
        return values

    if isinstance(expr, ast.Subscript):
        base = compile_expr(comp, expr.value, depth + 1)
        if isinstance(base, NodeResult):
            try:
                key = _const_eval(expr.slice, comp.compile_time.values)
            except CompileError as exc:
                raise CompileError("raw node output lookup requires a compile-time string key") from exc
            if not isinstance(key, str) or not key:
                raise CompileError("raw node output lookup requires a non-empty string key")
            return base.get_output(key)
        if isinstance(base, TupleValue):
            try:
                index = _const_eval(expr.slice, comp.compile_time.values)
            except CompileError as exc:
                raise CompileError("tuple result indexing requires a compile-time integer index") from exc
            if not isinstance(index, int) or isinstance(index, bool):
                raise CompileError("tuple result indexing requires a compile-time integer index")
            return base.get_item(index)
        try:
            idx = int(_const_eval(expr.slice, comp.compile_time.values))
        except CompileError as exc:
            raise CompileError("array/vector indexing currently requires a compile-time integer index") from exc
        reject_compile_time_object(base, "subscript")
        if isinstance(base, list):
            try:
                return base[idx]
            except Exception as exc:
                raise CompileError("array index out of range") from exc
        if isinstance(base, Value) and base.typ == TYPE_VECTOR:
            if idx not in (0, 1, 2):
                raise CompileError("vector index must be 0, 1 or 2")
            return _separate_xyz(comp.group, base, ("x", "y", "z")[idx], x, y)
        raise CompileError("indexing is supported for arrays and Vector values only")

    if isinstance(expr, ast.Call):
        if isinstance(expr.func, ast.Attribute):
            # Object.info() is Semantic-IR owned. Reaching the legacy branch means the
            # receiver itself forced whole-expression fallback, so preserve its old path.
            if expr.func.attr != "info":
                raise CompileError("Object values support only the .info() method")
            receiver = compile_expr(comp, expr.func.value, depth + 1)
            if not isinstance(receiver, ObjectValue):
                raise CompileError(".info() can only be used on Object values")
            if expr.args:
                raise CompileError("Object.info() accepts only keyword arguments")
            if any(kw.arg is None for kw in expr.keywords):
                raise CompileError("Object.info() does not support **kwargs")
            kws = {kw.arg: kw.value for kw in expr.keywords}
            extra = set(kws) - {"transform_space", "as_instance"}
            if extra:
                raise CompileError("Object.info() accepts only transform_space= and as_instance=")
            options = {}
            if "transform_space" in kws:
                try:
                    value = _const_eval(kws["transform_space"], comp.compile_time.values)
                except CompileError as exc:
                    raise CompileError("Object.info() transform_space must be 'ORIGINAL' or 'RELATIVE'") from exc
                if value not in {"ORIGINAL", "RELATIVE"}:
                    raise CompileError("Object.info() transform_space must be 'ORIGINAL' or 'RELATIVE'")
                options["transform_space"] = value
            if "as_instance" in kws:
                try:
                    value = _const_eval(kws["as_instance"], comp.compile_time.values)
                except CompileError as exc:
                    raise CompileError("Object.info() as_instance must be a compile-time Bool") from exc
                if not isinstance(value, bool):
                    raise CompileError("Object.info() as_instance must be a compile-time Bool")
                options["as_instance"] = value
            return receiver.configure_info(**options)
        if not isinstance(expr.func, ast.Name):
            raise CompileError("Only simple function calls are supported")
        name = expr.func.id
        resolved = resolve_simple_callable(name, callable_environment)
        cleaned_expr, function_modifiers = extract_function_call_modifiers(expr, name, environment.const_eval_values)
        if resolved is UNRESOLVED:
            if cleaned_expr.keywords:
                raise CompileError(
                    f"Keyword arguments are only supported for builtins, library functions, local functions or local backend helpers; {name} is not registered as one"
                )
            if function_modifiers.unique_was_explicit:
                raise unsupported_unique(name)
            raise CompileError(f"Unsupported function: {name}")
        if resolved.kind is CallableKind.TOP_LEVEL_ONLY:
            if cleaned_expr.keywords:
                raise CompileError(
                    f"Keyword arguments are only supported for builtins, library functions, local functions or local backend helpers; {name} is not registered as one"
                )
            raise CompileError(f"{name}() is only supported as a top-level call")

        # SEMANTIC_CALL_IR_LEGACY_DISPATCH: Remaining dynamic extension calls, explicitly stateful
        # builtins, and IR-capable wrapper builtins whose nested operand forced the already-active
        # whole-expression fallback still consume ast.Call and compiler/backend state here. Dispatch
        # only the already-resolved callable category; an IR-capable BUILTIN is permitted here only
        # because this branch is unreachable unless semantic analysis returned unsupported for the
        # enclosing expression. Do not repeat source-name precedence. Remove this branch when dynamic
        # extensions, stateful builtins, and legacy non-Value operands all have permanent frontend-owned
        # typed/runtime contracts and whole-expression fallback is gone.
        if resolved.kind is CallableKind.BUILTIN:
            if name in INPUT_DECLARATION_BUILTIN_NAMES:
                raise CompileError(INPUT_DECLARATION_PLACEMENT_ERROR)
            if name not in STATEFUL_FALLBACK_BUILTIN_NAMES and name not in IR_CAPABLE_BUILTIN_NAMES:
                raise CompileError(f"Internal error: unclassified builtin {name!r} reached legacy call dispatch")
            if function_modifiers.unique_was_explicit:
                raise unsupported_unique(name)
            return builtin_registry.compile_call(comp, cleaned_expr, depth)
        if resolved.kind is CallableKind.SYSTEM:
            if function_modifiers.unique_was_explicit:
                raise unsupported_unique(name)
            return systems_registry.compile_resolved_call(comp, cleaned_expr, resolved.target, depth)
        if resolved.kind is CallableKind.LOCAL_FUNCTION:
            return local_functions.compile_local_function_call(comp, cleaned_expr, depth, modifiers=function_modifiers)
        if resolved.kind is CallableKind.BACKEND_HELPER:
            if function_modifiers.unique_was_explicit:
                raise unsupported_unique(name)
            return local_functions.compile_backend_builtin_call(comp, cleaned_expr, depth)
        if resolved.kind is CallableKind.LIBRARY:
            return library_calls.compile_library_function_call(
                comp,
                cleaned_expr,
                depth,
                binding=resolved.target,
                function_id=resolved.library_function_id,
                modifiers=function_modifiers,
            )
        raise CompileError(f"Internal error: unsupported resolved callable category {resolved.kind}")

    raise CompileError(f"Unsupported expression element: {type(expr).__name__}")


__all__ = ["compile_expr"]

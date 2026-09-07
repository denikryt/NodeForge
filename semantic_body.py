"""Pure Semantic IR construction for basic straight-line executable bodies.

This stage owns assignments, direct explicit input declarations, augmented
assignments, explicit outputs, and one eligible final expression.  It does not
materialize Blender nodes or synchronize with the legacy statement compiler.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .builtin_call_semantics import (
    INPUT_DECLARATION_BUILTIN_NAMES,
    INPUT_DECLARATION_PLACEMENT_ERROR,
    analyze_input_declaration_call,
)
from .compiler_identities import BindingId, InputDeclarationId
from .consteval import _const_eval
from .errors import CompileError
from .parsing import _literal_string
from .runtime_bindings import RuntimeBindingSymbol, validate_runtime_binding_target
from .semantic_analysis import analyze_expression, build_semantic_environment
from .semantic_ir import IRAssign, IRBody, IRFinalExpression, IRInputDeclaration, IROutput, IRValue
from .semantic_lowering import lower_analyzed_expression


@dataclass(frozen=True)
class _BodyUnsupported:
    """Sentinel proving the entire body must stay on the legacy statement route."""


BODY_UNSUPPORTED = _BodyUnsupported()


@dataclass(frozen=True)
class BasicBodyCompilation:
    """Return one accepted body plus its detached final compile-time constant state."""

    body: IRBody
    final_constants: Mapping[str, object]

    def __post_init__(self) -> None:
        """Freeze the final constant mapping exposed to orchestration."""
        object.__setattr__(self, "final_constants", MappingProxyType(dict(self.final_constants)))


def _kw_dict(call: ast.Call) -> dict[str, ast.expr]:
    """Build the existing output keyword map with duplicate/**kwargs diagnostics."""
    result = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise CompileError("**kwargs are not supported")
        if kw.arg in result:
            raise CompileError(f"Duplicate keyword argument: {kw.arg}")
        result[kw.arg] = kw.value
    return result


def _check_no_extra_keywords(kws, allowed) -> None:
    """Apply the existing unsupported-keyword diagnostic."""
    extra = set(kws) - set(allowed)
    if extra:
        raise CompileError("Unsupported keyword argument(s): " + ", ".join(sorted(extra)))


def _unique_output_name(existing: set[str], requested: str) -> str:
    """Return the existing deterministic uniqued output display name."""
    base = requested or "out"
    name = base
    index = 2
    while name in existing:
        name = f"{base}_{index}"
        index += 1
    existing.add(name)
    return name


def _top_level_simple_call(stmt, name: str | None = None):
    """Return a direct ``name(...)`` expression call when present."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return None
    call = stmt.value
    if not isinstance(call.func, ast.Name):
        return None
    if name is not None and call.func.id != name:
        return None
    return call


def _direct_input_call(expr):
    """Return a direct explicit-input call or None."""
    if not isinstance(expr, ast.Call) or not isinstance(expr.func, ast.Name):
        return None
    return expr if expr.func.id in INPUT_DECLARATION_BUILTIN_NAMES else None


def validate_input_declaration_placement(stmts) -> None:
    """Reject input builtins outside the complete RHS of a simple assignment."""
    module = ast.Module(body=list(stmts), type_ignores=[])
    allowed_call_ids = set()
    for node in ast.walk(module):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        if not isinstance(node.targets[0], ast.Name):
            continue
        call = _direct_input_call(node.value)
        if call is not None:
            allowed_call_ids.add(id(call))

    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id in INPUT_DECLARATION_BUILTIN_NAMES and id(node) not in allowed_call_ids:
            raise CompileError(INPUT_DECLARATION_PLACEMENT_ERROR)


def _analyze_runtime_expression(expr, *, runtime_bindings, legacy_binding_names, constants, reserved_name_labels, callable_environment):
    """Analyze and emit one ordinary runtime expression program or return fallback."""
    environment = build_semantic_environment(
        runtime_bindings=runtime_bindings,
        legacy_binding_names=legacy_binding_names,
        constants=constants,
        reserved_name_labels=reserved_name_labels,
        callable_environment=callable_environment,
    )
    analysis = analyze_expression(expr, environment)
    if analysis is None:
        # BASIC_BODY_IR_EXPRESSION_FALLBACK: Body IR accepts only expressions already fully owned
        # by the semantic expression pipeline. A dynamic/stateful call or legacy structural binding
        # therefore rejects the whole body instead of embedding AST/backend payloads or lowering one
        # statement early. Remove this branch when expression semantic analysis has no supported
        # production fallback categories left.
        return BODY_UNSUPPORTED
    return lower_analyzed_expression(expr, analysis)


def _reject_structural_binding():
    """Return the whole-body fallback sentinel for structural body bindings."""
    # BASIC_BODY_IR_STRUCTURAL_BINDING_FALLBACK: Expression IR can represent arrays, tuples,
    # and named outputs, but source-name ownership/mutation for those structures is not yet a
    # permanent frontend binding model. Keep any body that assigns/unpacks a structural result
    # on the whole-body legacy path; do not add fake NFType variants or store backend containers
    # in IRBody. Remove this fallback when structural body bindings have compiler-owned identity,
    # shape, assignment, and mutation semantics.
    return BODY_UNSUPPORTED


def lower_basic_body(
    stmts,
    *,
    initial_runtime_bindings: Mapping[str, RuntimeBindingSymbol],
    initial_constants: Mapping[str, object],
    legacy_binding_names,
    reserved_name_labels: Mapping[str, str],
    callable_environment,
    owner_scope: str,
    declaration_owner: str | None = None,
):
    """Lower one whole eligible straight-line source body to compiler-owned IR.

    The function is side-effect free.  Any unsupported category rejects the
    complete body before Blender lowering begins.
    """
    validate_input_declaration_placement(stmts)
    runtime_bindings = dict(initial_runtime_bindings)
    constants = dict(initial_constants)
    statements = []
    output_names: set[str] = set()

    if any(symbol.binding_id.owner_scope != owner_scope for symbol in runtime_bindings.values()):
        raise CompileError("Internal error: body runtime binding owner scope mismatch")
    local_ids = [symbol.binding_id.local_id for symbol in runtime_bindings.values()]
    if len(local_ids) != len(set(local_ids)):
        raise CompileError("Internal error: duplicate body-entry BindingId")
    next_local_id = max(local_ids, default=-1) + 1
    declaration_owner = declaration_owner or owner_scope
    input_declaration_ordinals: dict[str, int] = {}

    def bind(name: str, typ):
        nonlocal next_local_id
        current = runtime_bindings.get(name)
        if current is None:
            current = RuntimeBindingSymbol(BindingId(owner_scope, next_local_id), typ)
            next_local_id += 1
        else:
            current = RuntimeBindingSymbol(current.binding_id, typ)
        runtime_bindings[name] = current
        return current

    for index, stmt in enumerate(stmts):
        is_final = index == len(stmts) - 1

        if isinstance(stmt, (ast.For, ast.If)):
            return BODY_UNSUPPORTED

        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1:
                raise CompileError("Assignment supports one name or one flat unpacking target")
            target_node = stmt.targets[0]
            if isinstance(target_node, (ast.Tuple, ast.List)):
                return _reject_structural_binding()
            if not isinstance(target_node, ast.Name):
                raise CompileError("Only simple assignments like name = value are supported")
            target = target_node.id
            validate_runtime_binding_target(target, reserved_name_labels)
            if target in legacy_binding_names:
                return BODY_UNSUPPORTED

            input_call = _direct_input_call(stmt.value)
            if input_call is not None:
                # Legacy assignment const-eval fails for runtime input calls and removes the
                # assignment target before the builtin's compile-time label/default arguments
                # are evaluated. Preserve that ordering while changing only input identity.
                constants.pop(target, None)
                try:
                    input_semantics = analyze_input_declaration_call(input_call, constants)
                except ValueError:
                    input_semantics = None
                if input_semantics is not None:
                    symbol = bind(target, input_semantics.typ)
                    declaration_ordinal = input_declaration_ordinals.get(target, 0)
                    input_declaration_ordinals[target] = declaration_ordinal + 1
                    statements.append(
                        IRInputDeclaration(
                            target_binding_id=symbol.binding_id,
                            declaration_id=InputDeclarationId(declaration_owner, target, declaration_ordinal),
                            target_name=target,
                            display_name=input_semantics.display_name,
                            typ=input_semantics.typ,
                            default=input_semantics.default,
                        )
                    )
                    continue

            if isinstance(stmt.value, (ast.List, ast.Tuple)):
                return _reject_structural_binding()
            if (
                isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
                and stmt.value.func.id == "geometry_builder"
            ):
                return BODY_UNSUPPORTED
            try:
                constants[target] = _const_eval(stmt.value, constants)
            except CompileError:
                constants.pop(target, None)
            program = _analyze_runtime_expression(
                stmt.value,
                runtime_bindings=runtime_bindings,
                legacy_binding_names=legacy_binding_names,
                constants=constants,
                reserved_name_labels=reserved_name_labels,
                callable_environment=callable_environment,
            )
            if program is BODY_UNSUPPORTED:
                return BODY_UNSUPPORTED
            if not isinstance(program.result, IRValue):
                return _reject_structural_binding()
            symbol = bind(target, program.result.typ)
            statements.append(IRAssign(symbol.binding_id, target, program))
            continue

        if isinstance(stmt, ast.AugAssign):
            if not isinstance(stmt.target, ast.Name):
                raise CompileError("Only simple augmented assignments like name += value are supported")
            target = stmt.target.id
            validate_runtime_binding_target(target, reserved_name_labels)
            if target in legacy_binding_names:
                return BODY_UNSUPPORTED
            current = runtime_bindings.get(target)
            if current is None:
                raise CompileError(f"Unknown name for augmented assignment: {target}")
            bin_expr = ast.BinOp(left=ast.Name(id=target, ctx=ast.Load()), op=stmt.op, right=stmt.value)
            program = _analyze_runtime_expression(
                bin_expr,
                runtime_bindings=runtime_bindings,
                legacy_binding_names=legacy_binding_names,
                constants=constants,
                reserved_name_labels=reserved_name_labels,
                callable_environment=callable_environment,
            )
            if program is BODY_UNSUPPORTED:
                return BODY_UNSUPPORTED
            if not isinstance(program.result, IRValue):
                return _reject_structural_binding()
            constants.pop(target, None)
            symbol = bind(target, program.result.typ)
            statements.append(IRAssign(symbol.binding_id, target, program))
            continue

        if isinstance(stmt, ast.Expr):
            call = _top_level_simple_call(stmt)
            if call is not None and call.func.id == "output":
                if call.keywords:
                    kws = _kw_dict(call)
                    _check_no_extra_keywords(kws, {"name", "value"})
                    if call.args:
                        raise CompileError('output() cannot mix positional and keyword arguments')
                    if "value" not in kws:
                        raise CompileError('output(name="Name", value=value) expects value=...')
                    if "name" in kws:
                        out_name = _unique_output_name(output_names, _literal_string(kws["name"], "output() name", constants))
                    else:
                        out_name = _unique_output_name(output_names, "out")
                    value_expr = kws["value"]
                elif len(call.args) == 1:
                    out_name = _unique_output_name(output_names, "out")
                    value_expr = call.args[0]
                elif len(call.args) == 2:
                    out_name = _unique_output_name(output_names, _literal_string(call.args[0], "output() name", constants))
                    value_expr = call.args[1]
                else:
                    raise CompileError('output(value), output("Name", value), or output(name="Name", value=value) expected')
                program = _analyze_runtime_expression(
                    value_expr,
                    runtime_bindings=runtime_bindings,
                    legacy_binding_names=legacy_binding_names,
                    constants=constants,
                    reserved_name_labels=reserved_name_labels,
                    callable_environment=callable_environment,
                )
                if program is BODY_UNSUPPORTED:
                    return BODY_UNSUPPORTED
                if not isinstance(program.result, IRValue):
                    return _reject_structural_binding()
                statements.append(IROutput(out_name, program))
                continue

            if call is not None and call.func.id in {"panel", "store", "set_position"}:
                return BODY_UNSUPPORTED
            if call is not None and call.func.id.startswith("input_"):
                return BODY_UNSUPPORTED
            if isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Attribute):
                return BODY_UNSUPPORTED
            if not is_final:
                return BODY_UNSUPPORTED
            program = _analyze_runtime_expression(
                stmt.value,
                runtime_bindings=runtime_bindings,
                legacy_binding_names=legacy_binding_names,
                constants=constants,
                reserved_name_labels=reserved_name_labels,
                callable_environment=callable_environment,
            )
            if program is BODY_UNSUPPORTED:
                return BODY_UNSUPPORTED
            if not isinstance(program.result, IRValue):
                return _reject_structural_binding()
            statements.append(IRFinalExpression(program))
            continue

        return BODY_UNSUPPORTED

    return BasicBodyCompilation(IRBody(tuple(statements)), constants)


__all__ = ["BODY_UNSUPPORTED", "BasicBodyCompilation", "lower_basic_body"]

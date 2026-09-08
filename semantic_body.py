"""Pure Semantic IR construction for basic straight-line executable bodies.

This stage owns ordinary runtime assignments, fixed tuple/named-output
structural bindings, direct explicit inputs, explicit outputs, and migrated
Object semantics. Unsupported categories reject the complete body before
Blender lowering begins.
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
from .constants import TYPE_OBJECT
from .consteval import _const_eval
from .errors import CompileError
from .parsing import _literal_string
from .runtime_bindings import RuntimeBindingSymbol, validate_runtime_binding_target
from .semantic_analysis import analyze_expression, build_semantic_environment
from .semantic_ir import (
    IRAssign,
    IRBindLeaves,
    IRBody,
    IRDiscardExpression,
    IRFinalExpression,
    IRInputDeclaration,
    IRLeafBinding,
    IRNamedOutputs,
    IROutput,
    IRProgram,
    IRTuple,
    IRValue,
)
from .semantic_lowering import lower_analyzed_expression
from .semantic_values import (
    NamedOutputsResultShape,
    ObjectInfoState,
    ObjectSemanticId,
    ObjectSemanticSnapshot,
    RuntimeResultShape,
    StructuralBindingKind,
    StructuralBindingSymbol,
    StructuralLeafBinding,
    TupleResultShape,
)


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


@dataclass(frozen=True)
class _AnalyzedBodyExpression:
    """Pair one emitted expression program with semantic shape/state needed by body ownership."""

    program: IRProgram
    result_shape: object
    object_semantics: ObjectSemanticSnapshot


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


def _reject_remaining_legacy_structural_binding():
    """Return whole-body fallback for intentionally out-of-scope structural categories."""
    # STRUCTURAL_SEMANTICS_BODY_REMAINING_FALLBACK: IRBody now owns fixed tuple and raw named-output
    # source bindings/unpacking, but mutable script arrays, GeometryBuilder state, and structural
    # results whose callable signatures are still legacy-only remain whole-body fallback categories.
    # Do not embed their Python/compiler/backend containers in IRBody. Remove this fallback when those
    # categories have explicit frontend semantics and whole-body lowering can represent them without
    # compile_statement().
    return BODY_UNSUPPORTED


def _tuple_target_names(target_node: ast.Tuple | ast.List) -> list[str]:
    """Validate one legacy-compatible flat tuple/list unpack target and return its names."""
    if any(isinstance(item, ast.Starred) for item in target_node.elts):
        raise CompileError("Starred tuple unpacking is not supported")
    if not target_node.elts or not all(isinstance(item, ast.Name) for item in target_node.elts):
        raise CompileError("Tuple unpacking target must be a flat sequence of names")
    names = [item.id for item in target_node.elts]
    if len(set(names)) != len(names):
        raise CompileError("Tuple unpacking target names must be unique")
    return names


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
    """Lower one whole eligible straight-line source body to compiler-owned IR."""
    validate_input_declaration_placement(stmts)
    runtime_bindings = dict(initial_runtime_bindings)
    structural_bindings: dict[str, StructuralBindingSymbol] = {}
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
    ordinary_reservations = {name: symbol.binding_id for name, symbol in runtime_bindings.items()}
    structural_reservations: dict[tuple[str, tuple[str, int | str]], BindingId] = {}

    object_ids_by_binding: dict[BindingId, ObjectSemanticId] = {}
    object_states: dict[ObjectSemanticId, ObjectInfoState] = {}
    next_object_id = 0

    def allocate_binding_id() -> BindingId:
        nonlocal next_local_id
        binding_id = BindingId(owner_scope, next_local_id)
        next_local_id += 1
        return binding_id

    def allocate_object_id() -> ObjectSemanticId:
        nonlocal next_object_id
        object_id = ObjectSemanticId(next_object_id)
        next_object_id += 1
        object_states[object_id] = ObjectInfoState()
        return object_id

    for symbol in runtime_bindings.values():
        if symbol.typ is TYPE_OBJECT:
            object_ids_by_binding[symbol.binding_id] = allocate_object_id()

    def object_snapshot() -> ObjectSemanticSnapshot:
        return ObjectSemanticSnapshot(object_ids_by_binding, object_states, next_object_id)

    def adopt_object_snapshot(snapshot: ObjectSemanticSnapshot) -> None:
        nonlocal object_ids_by_binding, object_states, next_object_id
        object_ids_by_binding = dict(snapshot.object_ids_by_binding)
        object_states = dict(snapshot.states)
        next_object_id = snapshot.next_object_id

    def clear_binding_object(binding_id: BindingId) -> None:
        object_ids_by_binding.pop(binding_id, None)

    def bind_runtime(name: str, shape: RuntimeResultShape) -> RuntimeBindingSymbol:
        current = runtime_bindings.get(name)
        if current is not None:
            binding_id = current.binding_id
        else:
            binding_id = ordinary_reservations.get(name)
            if binding_id is None:
                binding_id = allocate_binding_id()
                ordinary_reservations[name] = binding_id
        old_structural = structural_bindings.pop(name, None)
        if old_structural is not None:
            for leaf in old_structural.leaves:
                clear_binding_object(leaf.binding_id)
        symbol = RuntimeBindingSymbol(binding_id, shape.typ)
        runtime_bindings[name] = symbol
        clear_binding_object(binding_id)
        if shape.typ is TYPE_OBJECT:
            object_ids_by_binding[binding_id] = shape.object_id
        return symbol

    def bind_input(name: str, typ) -> RuntimeBindingSymbol:
        object_id = allocate_object_id() if typ is TYPE_OBJECT else None
        return bind_runtime(name, RuntimeResultShape(typ, object_id))

    def bind_structural(name: str, shape) -> StructuralBindingSymbol:
        runtime = runtime_bindings.pop(name, None)
        if runtime is not None:
            clear_binding_object(runtime.binding_id)
        previous = structural_bindings.get(name)
        previous_by_key = {} if previous is None else {leaf.projection_key: leaf for leaf in previous.leaves}
        if isinstance(shape, TupleResultShape):
            kind = StructuralBindingKind.TUPLE
            keyed_shapes = [(('index', index), item) for index, item in enumerate(shape.items)]
        elif isinstance(shape, NamedOutputsResultShape):
            kind = StructuralBindingKind.NAMED_OUTPUTS
            keyed_shapes = [(('name', item_name), item_shape) for item_name, item_shape in shape.items]
        else:
            raise TypeError("fixed structural binding requires tuple or named-output shape")
        leaves = []
        active_ids = set()
        for projection_key, leaf_shape in keyed_shapes:
            previous_leaf = previous_by_key.get(projection_key)
            if previous_leaf is not None:
                binding_id = previous_leaf.binding_id
            else:
                reservation_key = (name, projection_key)
                binding_id = structural_reservations.get(reservation_key)
                if binding_id is None:
                    binding_id = allocate_binding_id()
                    structural_reservations[reservation_key] = binding_id
            active_ids.add(binding_id)
            clear_binding_object(binding_id)
            if leaf_shape.typ is TYPE_OBJECT:
                object_ids_by_binding[binding_id] = leaf_shape.object_id
            leaves.append(StructuralLeafBinding(projection_key, binding_id, leaf_shape.typ))
        if previous is not None:
            for leaf in previous.leaves:
                if leaf.binding_id not in active_ids:
                    clear_binding_object(leaf.binding_id)
        symbol = StructuralBindingSymbol(kind, tuple(leaves))
        structural_bindings[name] = symbol
        return symbol

    def analyze_runtime_expression(expr):
        environment = build_semantic_environment(
            runtime_bindings=runtime_bindings,
            structural_bindings=structural_bindings,
            legacy_binding_names=legacy_binding_names,
            constants=constants,
            reserved_name_labels=reserved_name_labels,
            callable_environment=callable_environment,
            object_semantics=object_snapshot(),
        )
        analysis = analyze_expression(expr, environment)
        if analysis is None:
            # BASIC_BODY_IR_EXPRESSION_FALLBACK: Body IR accepts only expressions already fully owned
            # by the semantic expression pipeline. A dynamic/stateful call or legacy structural binding
            # therefore rejects the whole body instead of embedding AST/backend payloads or lowering one
            # statement early. Remove this branch when expression semantic analysis has no supported
            # production fallback categories left.
            return BODY_UNSUPPORTED
        if analysis.object_semantics is None:
            raise CompileError("Internal error: body expression analysis lost Object semantic registry")
        program = lower_analyzed_expression(expr, analysis)
        return _AnalyzedBodyExpression(program, analysis.facts[expr].result_shape, analysis.object_semantics)

    def accept_expression(expr):
        analyzed = analyze_runtime_expression(expr)
        if analyzed is BODY_UNSUPPORTED:
            return BODY_UNSUPPORTED
        adopt_object_snapshot(analyzed.object_semantics)
        return analyzed

    for index, stmt in enumerate(stmts):
        is_final = index == len(stmts) - 1

        if isinstance(stmt, (ast.For, ast.If)):
            return BODY_UNSUPPORTED

        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1:
                raise CompileError("Assignment supports one name or one flat unpacking target")
            target_node = stmt.targets[0]
            if isinstance(target_node, (ast.Tuple, ast.List)):
                names = _tuple_target_names(target_node)
                if any(name in legacy_binding_names for name in names):
                    return BODY_UNSUPPORTED
                for name in names:
                    validate_runtime_binding_target(name, reserved_name_labels)
                analyzed = accept_expression(stmt.value)
                if analyzed is BODY_UNSUPPORTED:
                    return BODY_UNSUPPORTED
                if not isinstance(analyzed.result_shape, TupleResultShape) or not isinstance(analyzed.program.result, IRTuple):
                    if isinstance(analyzed.result_shape, (NamedOutputsResultShape, RuntimeResultShape)):
                        raise CompileError(f"Cannot unpack scalar result into {len(names)} names")
                    return _reject_remaining_legacy_structural_binding()
                if len(analyzed.result_shape.items) != len(names):
                    raise CompileError(f"Tuple unpacking expected {len(names)} values, got {len(analyzed.result_shape.items)}")
                bindings = []
                for name, source, shape in zip(names, analyzed.program.result.items, analyzed.result_shape.items):
                    constants.pop(name, None)
                    symbol = bind_runtime(name, shape)
                    bindings.append(IRLeafBinding(source, symbol.binding_id, shape.typ))
                statements.append(IRBindLeaves(analyzed.program, tuple(bindings)))
                continue
            if not isinstance(target_node, ast.Name):
                raise CompileError("Only simple assignments like name = value are supported")
            target = target_node.id
            validate_runtime_binding_target(target, reserved_name_labels)
            if target in legacy_binding_names:
                return BODY_UNSUPPORTED

            input_call = _direct_input_call(stmt.value)
            if input_call is not None:
                constants.pop(target, None)
                try:
                    input_semantics = analyze_input_declaration_call(input_call, constants)
                except ValueError:
                    input_semantics = None
                if input_semantics is not None:
                    symbol = bind_input(target, input_semantics.typ)
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
                return _reject_remaining_legacy_structural_binding()
            if isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == "geometry_builder":
                return BODY_UNSUPPORTED
            try:
                constants[target] = _const_eval(stmt.value, constants)
            except CompileError:
                constants.pop(target, None)
            analyzed = accept_expression(stmt.value)
            if analyzed is BODY_UNSUPPORTED:
                return BODY_UNSUPPORTED
            if isinstance(analyzed.result_shape, RuntimeResultShape) and isinstance(analyzed.program.result, IRValue):
                symbol = bind_runtime(target, analyzed.result_shape)
                statements.append(IRAssign(symbol.binding_id, target, analyzed.program))
                continue
            if isinstance(analyzed.result_shape, (TupleResultShape, NamedOutputsResultShape)) and isinstance(analyzed.program.result, (IRTuple, IRNamedOutputs)):
                structural = bind_structural(target, analyzed.result_shape)
                sources = analyzed.program.result.items if isinstance(analyzed.program.result, IRTuple) else tuple(value for _name, value in analyzed.program.result.items)
                bindings = tuple(
                    IRLeafBinding(source, leaf.binding_id, leaf.typ)
                    for source, leaf in zip(sources, structural.leaves)
                )
                statements.append(IRBindLeaves(analyzed.program, bindings))
                continue
            return _reject_remaining_legacy_structural_binding()

        if isinstance(stmt, ast.AugAssign):
            if not isinstance(stmt.target, ast.Name):
                raise CompileError("Only simple augmented assignments like name += value are supported")
            target = stmt.target.id
            validate_runtime_binding_target(target, reserved_name_labels)
            if target in legacy_binding_names or target in structural_bindings:
                return BODY_UNSUPPORTED
            current = runtime_bindings.get(target)
            if current is None:
                raise CompileError(f"Unknown name for augmented assignment: {target}")
            bin_expr = ast.BinOp(left=ast.Name(id=target, ctx=ast.Load()), op=stmt.op, right=stmt.value)
            analyzed = accept_expression(bin_expr)
            if analyzed is BODY_UNSUPPORTED:
                return BODY_UNSUPPORTED
            if not isinstance(analyzed.result_shape, RuntimeResultShape) or not isinstance(analyzed.program.result, IRValue):
                return _reject_remaining_legacy_structural_binding()
            constants.pop(target, None)
            symbol = bind_runtime(target, analyzed.result_shape)
            statements.append(IRAssign(symbol.binding_id, target, analyzed.program))
            continue

        if isinstance(stmt, ast.Expr):
            call = _top_level_simple_call(stmt)
            if call is not None and call.func.id == "output":
                if call.keywords:
                    kws = _kw_dict(call)
                    _check_no_extra_keywords(kws, {"name", "value"})
                    if call.args:
                        raise CompileError("output() cannot mix positional and keyword arguments")
                    if "value" not in kws:
                        raise CompileError('output(name="Name", value=value) expects value=...')
                    out_name = _unique_output_name(output_names, _literal_string(kws["name"], "output() name", constants)) if "name" in kws else _unique_output_name(output_names, "out")
                    value_expr = kws["value"]
                elif len(call.args) == 1:
                    out_name = _unique_output_name(output_names, "out")
                    value_expr = call.args[0]
                elif len(call.args) == 2:
                    out_name = _unique_output_name(output_names, _literal_string(call.args[0], "output() name", constants))
                    value_expr = call.args[1]
                else:
                    raise CompileError('output(value), output("Name", value), or output(name="Name", value=value) expected')
                analyzed = accept_expression(value_expr)
                if analyzed is BODY_UNSUPPORTED:
                    return BODY_UNSUPPORTED
                if not isinstance(analyzed.result_shape, RuntimeResultShape) or not isinstance(analyzed.program.result, IRValue):
                    return _reject_remaining_legacy_structural_binding()
                statements.append(IROutput(out_name, analyzed.program))
                continue

            if call is not None and call.func.id in {"panel", "store", "set_position"}:
                return BODY_UNSUPPORTED
            if call is not None and call.func.id.startswith("input_"):
                return BODY_UNSUPPORTED

            if isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Attribute):
                if stmt.value.func.attr != "info":
                    return BODY_UNSUPPORTED
                analyzed = accept_expression(stmt.value)
                if analyzed is BODY_UNSUPPORTED:
                    return BODY_UNSUPPORTED
                if not isinstance(analyzed.result_shape, RuntimeResultShape) or analyzed.result_shape.typ is not TYPE_OBJECT:
                    return BODY_UNSUPPORTED
                statements.append(IRDiscardExpression(analyzed.program))
                continue

            if not is_final:
                return BODY_UNSUPPORTED
            analyzed = accept_expression(stmt.value)
            if analyzed is BODY_UNSUPPORTED:
                return BODY_UNSUPPORTED
            if not isinstance(analyzed.result_shape, RuntimeResultShape) or not isinstance(analyzed.program.result, IRValue):
                return _reject_remaining_legacy_structural_binding()
            statements.append(IRFinalExpression(analyzed.program))
            continue

        return BODY_UNSUPPORTED

    return BasicBodyCompilation(IRBody(tuple(statements)), constants)


__all__ = ["BODY_UNSUPPORTED", "BasicBodyCompilation", "lower_basic_body", "validate_input_declaration_placement"]

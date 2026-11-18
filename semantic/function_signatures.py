"""Typed function parameter declarations and initial interface-panel projection.

Compile declarations from their original FunctionDef AST. This module owns
source-facing names, declared-default presence, and stable input provenance;
Blender projection is supplied by the existing callable interface machinery.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Mapping

from ..compiler_identities import BindingId, InputDeclarationId
from ..errors import CompileError
from ..nf_types import NFType
from .callable_contracts import GroupInputContract, canonicalize_group_input_default, project_group_input_layout
from .constants import TYPE_TOKEN_NAMES, _ALLOWED_CONSTS
from .builtin_registry import BUILTIN_NAMES
from .consteval import ConstEvalUnavailable, _const_eval
from .ir import IRInputDeclaration, IRPanelDeclaration


@dataclass(frozen=True)
class FunctionSignature:
    """Logical parameter declarations and their projected Blender input layout.

    ``logical_inputs`` always follows the FunctionDef argument order, while
    ``physical_inputs`` follows the actual interface/panel socket order.
    """

    declarations: tuple[IRInputDeclaration, ...]
    logical_inputs: tuple[GroupInputContract, ...]
    panels: tuple[IRPanelDeclaration, ...]
    physical_inputs: tuple[GroupInputContract, ...]
    inline_default: bool = False

    def __post_init__(self) -> None:
        """Verify logical and projected inputs have exactly the same origins."""
        if len(self.declarations) != len(self.logical_inputs) or len(self.logical_inputs) != len(self.physical_inputs):
            raise ValueError("Function signature declaration/input count mismatch")
        origins = tuple(item.declaration_id for item in self.declarations)
        if origins != tuple(item.interface_origin for item in self.logical_inputs):
            raise ValueError("Function signature logical input origins must match declarations")
        if set(origins) != {item.interface_origin for item in self.physical_inputs}:
            raise ValueError("Function signature panel projection changed interface identities")


def resolve_parameter_annotation(annotation: ast.expr | None) -> tuple[NFType | None, str | None]:
    """Read ``Type`` or ``Type[\"Display Label\"]`` without evaluating Python code."""
    if annotation is None:
        return None, None
    label = None
    token = annotation
    if isinstance(annotation, ast.Subscript):
        token = annotation.value
        label_node = annotation.slice
        if not isinstance(label_node, ast.Constant) or not isinstance(label_node.value, str) or not label_node.value.strip():
            raise CompileError(f"Type display label must be one non-empty string literal at line {annotation.lineno}")
        label = label_node.value
    if not isinstance(token, ast.Name) or token.id not in TYPE_TOKEN_NAMES:
        raise CompileError(f"Unsupported function parameter annotation: {ast.unparse(annotation)}")
    return TYPE_TOKEN_NAMES[token.id], label


def _panel_declaration(call: ast.Call, origins: Mapping[str, InputDeclarationId], values: Mapping[str, object]) -> IRPanelDeclaration:
    """Validate one prefix panel declaration against the function's parameter IDs."""
    if len(call.args) != 1 or not isinstance(call.args[0], (ast.List, ast.Tuple)):
        raise CompileError("panel() expects exactly one positional list or tuple of parameters")
    members = call.args[0].elts
    if not members or any(not isinstance(item, ast.Name) for item in members):
        raise CompileError("panel() requires one or more simple parameter names")
    names = [item.id for item in members]
    if len(set(names)) != len(names):
        raise CompileError("panel() cannot contain the same parameter twice")
    unknown = [name for name in names if name not in origins]
    if unknown:
        raise CompileError(f"panel() item {unknown[0]!r} is not a function parameter")
    keywords = {}
    for item in call.keywords:
        if item.arg not in {"name", "collapsed"}:
            raise CompileError(f"panel() unknown keyword {item.arg!r}")
        if item.arg in keywords:
            raise CompileError(f"panel() duplicate keyword {item.arg!r}")
        keywords[item.arg] = item.value
    if "name" not in keywords:
        raise CompileError("panel() requires name=")
    try:
        name = _const_eval(keywords["name"], values)
        collapsed = _const_eval(keywords.get("collapsed", ast.Constant(False)), values)
    except ConstEvalUnavailable as exc:
        raise CompileError("panel() name and collapsed must be compile-time values") from exc
    if not isinstance(name, str) or not name:
        raise CompileError("panel() name must be a non-empty string")
    if type(collapsed) is not bool:
        raise CompileError("panel() collapsed must be Bool")
    return IRPanelDeclaration(tuple(origins[item] for item in names), name, collapsed)


def validate_function_parameters(fn: ast.FunctionDef) -> None:
    """Reject unsupported parameter declarations at the source AST boundary.

    Every declaration is checked, including definitions that are never called
    or physically materialized. Defaults are evaluated later in their module's
    compile-time environment, after this structural check.
    """
    if fn.args.posonlyargs or fn.args.kwonlyargs or fn.args.vararg or fn.args.kwarg or fn.args.kw_defaults:
        raise CompileError(f"{fn.name}() accepts only ordinary named positional-or-keyword parameters")
    for index, arg in enumerate(fn.args.args):
        name = arg.arg
        if name == "__inline__":
            location = f"line {arg.lineno}, column {arg.col_offset + 1}"
            if index != len(fn.args.args) - 1:
                raise CompileError(f"{fn.name}() __inline__ must be the final parameter at {location}")
            if (arg.annotation is not None
                    and not (isinstance(arg.annotation, ast.Name) and arg.annotation.id == "Bool")):
                raise CompileError(f"{fn.name}() __inline__ requires Bool annotation at {location}")
            if not fn.args.defaults:
                raise CompileError(f"{fn.name}() __inline__ requires a compile-time Bool default at {location}")
            continue
        location = f"line {arg.lineno}, column {arg.col_offset + 1}"
        if name == "__unique__":
            raise CompileError(f"{fn.name}() cannot declare compiler-reserved __unique__ at {location}")
        if name in TYPE_TOKEN_NAMES or name in _ALLOWED_CONSTS or name in BUILTIN_NAMES or name in {"output", "store", "panel"}:
            raise CompileError(f"{fn.name}() parameter {name!r} conflicts with a reserved source name at {location}")
        if arg.annotation is None:
            raise CompileError(f"{fn.name}(): parameter {name!r} requires an explicit type annotation at {location}")
        try:
            resolve_parameter_annotation(arg.annotation)
        except CompileError as exc:
            raise CompileError(f"{fn.name}() parameter {name!r} at {location}: {exc}") from exc


def resolve_declared_inline_default(
    fn: ast.FunctionDef, compile_time_values: Mapping[str, object] | None = None,
) -> bool:
    """Resolve a function-level compile policy from its defining final CT state."""
    validate_function_parameters(fn)
    if not fn.args.args or fn.args.args[-1].arg != "__inline__":
        return False
    expression = fn.args.defaults[-1]
    parameter_names = {arg.arg for arg in fn.args.args[:-1]}
    if any(isinstance(node, ast.Name) and node.id in parameter_names for node in ast.walk(expression)):
        raise CompileError(f"{fn.name}() __inline__ default cannot reference a runtime parameter")
    try:
        value = _const_eval(expression, compile_time_values or {})
    except ConstEvalUnavailable as exc:
        raise CompileError(f"{fn.name}() __inline__ requires a compile-time Bool default") from exc
    if type(value) is not bool:
        raise CompileError(f"{fn.name}() __inline__ requires a compile-time Bool default")
    return value


def compile_function_signature(
    fn: ast.FunctionDef,
    *,
    owner_scope: str,
    declaration_owner: str,
    compile_time_values: Mapping[str, object] | None = None,
) -> FunctionSignature:
    """Compile typed parameters directly from a FunctionDef into existing IR.

    Parameter types come only from source annotations. Defaults are evaluated
    within the defining module, never used to select a parameter type.
    """
    if not isinstance(fn, ast.FunctionDef):
        raise TypeError("Function signature source must be ast.FunctionDef")
    validate_function_parameters(fn)
    values = dict(compile_time_values or {})
    inline_default = resolve_declared_inline_default(fn, values)
    arguments = tuple(arg for arg in fn.args.args if arg.arg != "__inline__")
    default_offset = len(fn.args.args) - len(fn.args.defaults)
    declarations = []
    inputs = []
    origins = {}
    for index, arg in enumerate(arguments):
        name = arg.arg
        typ, label = resolve_parameter_annotation(arg.annotation)
        has_default = index >= default_offset
        default = None
        if has_default:
            expression = fn.args.defaults[index - default_offset]
            # Parameters are runtime inputs, even when an identically named
            # module compile-time constant exists in the source snapshot.
            parameters = {parameter.arg for parameter in arguments}
            if any(isinstance(node, ast.Name) and node.id in parameters
                   for node in ast.walk(expression)):
                raise CompileError(f"{fn.name}() parameter {name!r} default cannot reference a runtime parameter")
            try:
                evaluated = _const_eval(expression, values)
            except ConstEvalUnavailable as exc:
                detail = "Rotation default " if typ is NFType.ROTATION else ""
                raise CompileError(f"{fn.name}() parameter {name!r} {detail}requires a compile-time default") from exc
            except CompileError as exc:
                if typ is NFType.ROTATION:
                    raise CompileError(f"{fn.name}() parameter {name!r} Rotation default: {exc}") from exc
                raise
            try:
                default = canonicalize_group_input_default(typ, evaluated)
            except CompileError as exc:
                raise CompileError(f"{fn.name}() parameter {name!r} has an invalid default: {exc}") from exc
        binding = BindingId(owner_scope, index)
        origin = InputDeclarationId(declaration_owner, name, 0)
        origins[name] = origin
        declarations.append(IRInputDeclaration(binding, origin, name, label or name, typ, default, has_default))
        inputs.append(GroupInputContract(name, label or name, typ, default, has_default, origin))
    panels = []
    panel_names = set()
    panel_prefix = True
    for statement in fn.body:
        is_panel = (
            isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name) and statement.value.func.id == "panel"
        )
        if is_panel and not panel_prefix:
            raise CompileError(f"{fn.name}() panel() declarations must precede executable statements")
        if is_panel:
            panel = _panel_declaration(statement.value, origins, values)
            if panel.name in panel_names:
                raise CompileError(f"{fn.name}() duplicate panel name: {panel.name}")
            panel_names.add(panel.name)
            panels.append(panel)
        else:
            panel_prefix = False
            if any(
                isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "panel"
                for node in ast.walk(statement)
            ):
                raise CompileError(f"{fn.name}() panel() is permitted only in the initial declarative prefix")
    return FunctionSignature(
        tuple(declarations), tuple(inputs), tuple(panels),
        project_group_input_layout(tuple(inputs), tuple(panels)), inline_default,
    )


__all__ = ["FunctionSignature", "compile_function_signature", "resolve_parameter_annotation", "validate_function_parameters"]

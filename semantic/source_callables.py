"""Pure source-backed callable analysis and semantic preparation records."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Mapping

from .call_resolution import (
    CallableEnvironment,
    non_callable_source_binding_error,
    package_candidates_for_unqualified,
)
from .callable_contracts import (
    SourceCallableContract,
)
from ..compiler_identities import FunctionId, GroupCompilationIdentity, normalize_library_package_id
from .constants import TYPE_TOKEN_NAMES, _ALLOWED_CONSTS
from .compile_time import _is_const_vector
from ..errors import CompileError
from ..nf_types import NFType, serialize_nf_type


@dataclass(frozen=True)
class LocalReturnElement:
    """Describe one ordered output synthesized from a local ``return`` expression."""

    index: int
    socket_name: str
    key: str
    expression: ast.expr


@dataclass(frozen=True)
class LocalReturnShape:
    """Describe the fixed scalar/tuple return surface of one local function."""

    elements: tuple[LocalReturnElement, ...]

@dataclass(frozen=True)
class LocalCapture:
    """Describe one frontend-resolved hidden local-function capture."""

    name: str
    typ: NFType
    runtime: bool
    compile_time_value: object | None = None


@dataclass(frozen=True)
class SourceCallablePreparationKey:
    """Key one owner-specific semantic preparation for a canonical function."""

    function_id: FunctionId
    owner_scope: str

    def __post_init__(self) -> None:
        """Require canonical function identity and final physical owner scope."""
        if not isinstance(self.function_id, FunctionId):
            raise TypeError("SourceCallablePreparationKey.function_id must be FunctionId")
        if not isinstance(self.owner_scope, str) or not self.owner_scope:
            raise ValueError("SourceCallablePreparationKey.owner_scope must be non-empty")


@dataclass(frozen=True)
class PreparedSourceCallable:
    """Pair one source-call contract with its exact semantic artifact and source owner record."""

    contract: SourceCallableContract
    group: "SemanticGroupCompilation"
    source_record: object | None = None

    def __post_init__(self) -> None:
        """Validate final compiler identity and exact library-record ownership."""
        identity = getattr(self.group, "identity", None)
        if not isinstance(identity, GroupCompilationIdentity):
            raise TypeError("prepared source callable group must expose GroupCompilationIdentity")
        function_id = self.contract.function_id
        if function_id.kind == "LOCAL_DEF":
            if self.source_record is not None:
                raise TypeError("prepared local callable must not carry a library source record")
            return
        if self.source_record is None:
            raise TypeError("prepared library callable must carry its exact source record")
        if (
            getattr(self.source_record, "namespace", None) != function_id.namespace
            or getattr(self.source_record, "name", None) != function_id.name
            or normalize_library_package_id(getattr(self.source_record, "package_id", None)) != function_id.package_id
        ):
            raise TypeError("prepared library callable source record does not match FunctionId")


def value_type_for_const(value) -> NFType:
    """Infer the canonical NodeForge type represented by one materializable constant."""
    from .compile_time import ConstVector
    from ..nf_types import NFType

    if isinstance(value, ConstVector) or _is_const_vector(value) or (
        isinstance(value, (tuple, list))
        and len(value) == 3
        and all(type(component) in {int, float} for component in value)
    ):
        return NFType.VECTOR
    if type(value) is bool:
        return NFType.BOOL
    if type(value) is int:
        return NFType.INT
    if type(value) is float:
        return NFType.FLOAT
    if isinstance(value, str):
        return NFType.STRING
    raise CompileError("Local function constant arguments must be numbers, booleans, strings or vectors")


def input_call_for_type(param_name: str, typ: NFType) -> str:
    """Return DSL source declaring one generated local-helper group input."""
    constructors = {
        NFType.GEOMETRY: "input_geometry",
        NFType.MATERIAL: "input_material",
        NFType.OBJECT: "input_object",
        NFType.VECTOR: "input_vector",
        NFType.BOOL: "input_bool",
        NFType.INT: "input_int",
        NFType.FLOAT: "input_float",
        NFType.STRING: "input_string",
        NFType.BUNDLE: "input_bundle",
    }
    constructor = constructors.get(typ)
    if constructor is None:
        raise CompileError(f"Local function input type {typ!r} has no supported input constructor")
    return f"{param_name} = {constructor}({param_name!r})"


def _display_name(name: str) -> str:
    """Convert one Python binding name to the existing socket display convention."""
    return " ".join(part.capitalize() for part in name.split("_")) or "Value"


def analyze_local_return_shape(fn: ast.FunctionDef) -> LocalReturnShape:
    """Validate and describe the existing single-final-return local function contract."""
    returns = [stmt for stmt in fn.body if isinstance(stmt, ast.Return)]
    nested_returns = [node for stmt in fn.body for node in ast.walk(stmt) if isinstance(node, ast.Return)]
    if len(returns) != 1 or len(nested_returns) != 1 or fn.body[-1] is not returns[0]:
        raise CompileError(f"Local function {fn.name}() must have exactly one final top-level return")
    value = returns[0].value
    if value is None:
        raise CompileError(f"Local function {fn.name}() return must produce at least one value")
    if isinstance(value, ast.List):
        raise CompileError(f"Local function {fn.name}() cannot return a list; use a flat tuple return")
    expressions = list(value.elts) if isinstance(value, ast.Tuple) else [value]
    if not expressions:
        raise CompileError(f"Local function {fn.name}() cannot return an empty tuple")
    if any(isinstance(item, ast.Starred) for item in expressions):
        raise CompileError(f"Local function {fn.name}() does not support starred return elements")
    if any(isinstance(item, (ast.Tuple, ast.List)) for item in expressions):
        raise CompileError(f"Local function {fn.name}() does not support nested tuple returns")
    used: dict[str, int] = {}
    elements: list[LocalReturnElement] = []
    for index, expression in enumerate(expressions):
        base = _display_name(expression.id) if isinstance(expression, ast.Name) else f"Value {index + 1}"
        count = used.get(base, 0) + 1
        used[base] = count
        socket_name = base if count == 1 else f"{base} {count}"
        elements.append(LocalReturnElement(index, socket_name, f"return:{index}", expression))
    if len(elements) == 1:
        element = elements[0]
        elements[0] = LocalReturnElement(0, "Value", "return:0", element.expression)
    return LocalReturnShape(tuple(elements))


def resolve_local_parameter_annotation(annotation) -> NFType | None:
    """Resolve one simple local parameter annotation to canonical ``NFType``."""
    if annotation is None:
        return None
    if not isinstance(annotation, ast.Name) or annotation.id not in TYPE_TOKEN_NAMES:
        raise CompileError(f"Unsupported local function parameter annotation: {ast.unparse(annotation)}")
    return TYPE_TOKEN_NAMES[annotation.id]


def serialize_local_signature(signature_names, param_types: Mapping[str, NFType]) -> str:
    """Serialize local parameter/capture types to the established stable signature."""
    return ",".join(f"{name}:{serialize_nf_type(param_types[name])}" for name in signature_names)


def local_function_source(
    fn: ast.FunctionDef,
    param_types: Mapping[str, NFType],
    hidden_captures=(),
    return_shape: LocalReturnShape | None = None,
) -> str:
    """Normalize one local function into ordinary source accepted by group semantics."""
    shape = return_shape or analyze_local_return_shape(fn)
    if fn.args.posonlyargs or fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs or fn.args.defaults or fn.args.kw_defaults:
        raise CompileError("Local functions currently support only plain positional parameters without defaults")
    lines = [input_call_for_type(arg.arg, param_types[arg.arg]) for arg in fn.args.args]
    lines.extend(input_call_for_type(name, param_types[name]) for name in hidden_captures)
    for stmt in fn.body[:-1]:
        if isinstance(stmt, ast.FunctionDef):
            raise CompileError("Nested function definitions are not supported")
        lines.append(ast.unparse(stmt))
    for element in shape.elements:
        lines.append(f"output({element.socket_name!r}, {ast.unparse(element.expression)})")
    return "\n".join(lines)


def _binding_names_in_local_function(fn: ast.FunctionDef) -> set[str]:
    """Return names lexically local to one script-local function body."""
    names = {arg.arg for arg in fn.args.args}

    def add_target(target) -> None:
        """Collect names introduced by one assignment/loop target shape."""
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                add_target(item)

    for stmt in fn.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.FunctionDef):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    add_target(target)
            elif isinstance(node, ast.AugAssign):
                add_target(node.target)
            elif isinstance(node, ast.For):
                add_target(node.target)
    return names


def local_binding_names(fn: ast.FunctionDef) -> frozenset[str]:
    """Return lexical names owned by one local function body/parameter scope."""
    return frozenset(_binding_names_in_local_function(fn))


@dataclass(frozen=True)
class _LocalNameAnalysis:
    """Invocation-local facts shared by capture and callable validation filters."""

    free_names: tuple[str, ...]
    callee_names: tuple[str, ...]
    local_bindings: frozenset[str]


class _LocalNameVisitor(ast.NodeVisitor):
    """Collect free value names and all simple callees in source order."""

    def __init__(self, local_names, package_namespaces=None) -> None:
        """Initialize lexical facts for one function and inherited package scope."""
        self.local_names = set(local_names)
        self.package_namespaces = dict(package_namespaces or {})
        self.names: list[str] = []
        self._seen: set[str] = set()
        self.callee_names: list[str] = []
        self._seen_callees: set[str] = set()

    def _add(self, name: str) -> None:
        """Record one unseen free value name in stable source order."""
        if name not in self.local_names and name not in self._seen:
            self._seen.add(name)
            self.names.append(name)

    def _add_callee(self, name: str) -> None:
        """Record a simple callee even when its spelling is lexically bound."""
        if name not in self._seen_callees:
            self._seen_callees.add(name)
            self.callee_names.append(name)

    def visit_Call(self, node) -> None:
        """Classify callable positions while traversing every ordinary operand."""
        if isinstance(node.func, ast.Name):
            self._add_callee(node.func.id)
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id not in self.local_names
            and node.func.value.id in self.package_namespaces
        ):
            # Package alias is resolver syntax only in this exact qualified-callee
            # position. Arguments remain ordinary value expressions/captures.
            pass
        else:
            self.visit(node.func)
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Name(self, node) -> None:
        """Record free loaded names except canonical type-token names."""
        if isinstance(node.ctx, ast.Load) and node.id not in TYPE_TOKEN_NAMES:
            self._add(node.id)

    def visit_FunctionDef(self, node) -> None:
        """Do not traverse nested function definitions."""
        return


def _analyze_local_names(fn: ast.FunctionDef, package_namespaces=None) -> _LocalNameAnalysis:
    """Collect lexical bindings once and share one call/value traversal."""
    local_bindings = local_binding_names(fn)
    visitor = _LocalNameVisitor(local_bindings, package_namespaces)
    for stmt in fn.body:
        visitor.visit(stmt)
    return _LocalNameAnalysis(tuple(visitor.names), tuple(visitor.callee_names), local_bindings)


def free_names(fn: ast.FunctionDef, package_namespaces=None) -> tuple[str, ...]:
    """Return free value-load names in stable first-required order."""
    return _analyze_local_names(fn, package_namespaces).free_names


def has_source_value_binding(
    name: str,
    *,
    runtime_bindings: Mapping[str, object],
    compile_time_values: Mapping[str, object],
    structural_binding_names=frozenset(),
    structural_array_names=frozenset(),
    builder_binding_names=frozenset(),
    extension_binding_names=frozenset(),
) -> bool:
    """Return whether an existing frontend value domain owns *name*."""
    if name in runtime_bindings:
        return True
    if name in structural_binding_names:
        return True
    if name in structural_array_names:
        return True
    if name in builder_binding_names:
        return True
    if name in extension_binding_names:
        return True
    return name in compile_time_values


def _validate_local_bare_package_calls(
    analysis: _LocalNameAnalysis,
    *,
    callable_environment: CallableEnvironment,
    runtime_bindings: Mapping[str, object],
    compile_time_values: Mapping[str, object],
    structural_binding_names=frozenset(),
    structural_array_names=frozenset(),
    builder_binding_names=frozenset(),
    extension_binding_names=frozenset(),
) -> None:
    """Reject outer source values that invalidate the function's bare package calls."""
    for name in analysis.callee_names:
        if name in analysis.local_bindings:
            continue
        if not package_candidates_for_unqualified(name, callable_environment):
            continue
        if has_source_value_binding(
            name,
            runtime_bindings=runtime_bindings,
            compile_time_values=compile_time_values,
            structural_binding_names=structural_binding_names,
            structural_array_names=structural_array_names,
            builder_binding_names=builder_binding_names,
            extension_binding_names=extension_binding_names,
        ):
            raise non_callable_source_binding_error(name, callable_environment)


def called_local_functions(fn: ast.FunctionDef, local_functions: Mapping[str, ast.FunctionDef]) -> tuple[str, ...]:
    """Return local callees used by *fn* in stable first-seen order."""
    analysis = _analyze_local_names(fn)
    return tuple(name for name in analysis.callee_names if name in local_functions)


def _reserved_capture_label(reserved_name_labels: Mapping[str, str], name: str) -> str | None:
    """Return the existing user-facing reservation wording for one capture name."""
    label = reserved_name_labels.get(name)
    if label == "DSL builtin":
        return "reserved by DSL builtin"
    if label == "imported function":
        return "already registered as imported function"
    if label == "local function":
        return "already registered as local function"
    if label == "type token":
        return "reserved by type token"
    return None if label is None else f"reserved by {label}"


def analyze_local_captures(
    fn: ast.FunctionDef,
    *,
    local_functions: Mapping[str, ast.FunctionDef],
    runtime_bindings: Mapping[str, object],
    compile_time_values: Mapping[str, object],
    reserved_name_labels: Mapping[str, str],
    structural_binding_names=frozenset(),
    structural_array_names=frozenset(),
    builder_binding_names=frozenset(),
    extension_binding_names=frozenset(),
    callable_environment: CallableEnvironment | None = None,
    _stack=(),
) -> tuple[LocalCapture, ...]:
    """Resolve direct/transitive captures and invocation-time package-call validity."""
    if fn.name in _stack:
        cycle = " -> ".join(_stack + (fn.name,))
        raise CompileError(f"Recursive local function calls are not supported: {cycle}")

    package_namespaces = {} if callable_environment is None else callable_environment.package_namespaces
    analysis = _analyze_local_names(fn, package_namespaces)
    if callable_environment is not None:
        _validate_local_bare_package_calls(
            analysis,
            callable_environment=callable_environment,
            runtime_bindings=runtime_bindings,
            compile_time_values=compile_time_values,
            structural_binding_names=structural_binding_names,
            structural_array_names=structural_array_names,
            builder_binding_names=builder_binding_names,
            extension_binding_names=extension_binding_names,
        )

    captures: list[LocalCapture] = []
    seen: set[str] = set()

    def append(capture: LocalCapture) -> None:
        """Append one capture once while retaining first-required ordering."""
        if capture.name not in seen:
            seen.add(capture.name)
            captures.append(capture)

    for name in analysis.free_names:
        if name in _ALLOWED_CONSTS:
            continue
        if name in package_namespaces:
            raise CompileError(f"Package namespace {name!r} cannot be used as a value")
        reserved = _reserved_capture_label(reserved_name_labels, name)
        if reserved is not None:
            raise CompileError(f"Local function {fn.name}() cannot capture {name}: name is {reserved}")
        symbol = runtime_bindings.get(name)
        if symbol is not None:
            typ = getattr(symbol, "typ", None)
            if not isinstance(typ, NFType):
                raise CompileError(f"Internal error: local function capture {name!r} has no frontend type")
            append(LocalCapture(name, typ, True))
            continue
        if name in extension_binding_names:
            raise CompileError(
                f"Local function {fn.name}() cannot capture {name}: package semantic values are not supported"
            )
        if name in structural_array_names:
            raise CompileError(f"Local function {fn.name}() cannot capture {name}: arrays are not supported")
        if name in structural_binding_names or name in builder_binding_names:
            raise CompileError(f"Local function {fn.name}() cannot capture {name}: unsupported binding")
        if name in compile_time_values:
            try:
                typ = value_type_for_const(compile_time_values[name])
            except CompileError as exc:
                raise CompileError(
                    f"Local function {fn.name}() cannot capture {name}: unsupported compile-time value"
                ) from exc
            append(LocalCapture(name, typ, False, compile_time_values[name]))
            continue
        raise CompileError(f"Local function {fn.name}() cannot capture {name}: name is not available in the outer scope")

    for called_name in analysis.callee_names:
        if called_name not in local_functions:
            continue
        if called_name == fn.name or called_name in _stack:
            cycle = " -> ".join(_stack + (fn.name, called_name))
            raise CompileError(f"Recursive local function calls are not supported: {cycle}")
        for capture in analyze_local_captures(
            local_functions[called_name],
            local_functions=local_functions,
            runtime_bindings=runtime_bindings,
            compile_time_values=compile_time_values,
            reserved_name_labels=reserved_name_labels,
            structural_binding_names=structural_binding_names,
            structural_array_names=structural_array_names,
            builder_binding_names=builder_binding_names,
            extension_binding_names=extension_binding_names,
            callable_environment=callable_environment,
            _stack=_stack + (fn.name,),
        ):
            if capture.name in analysis.local_bindings and capture.name not in seen:
                raise CompileError(
                    f"Local function {fn.name}() cannot forward capture {capture.name} "
                    f"required by {called_name}(): name is local to {fn.name}()"
                )
            append(capture)
    return tuple(captures)




__all__ = [
    "LocalCapture",
    "LocalReturnElement",
    "LocalReturnShape",
    "PreparedSourceCallable",
    "SourceCallablePreparationKey",
    "analyze_local_captures",
    "analyze_local_return_shape",
    "called_local_functions",
    "free_names",
    "input_call_for_type",
    "local_binding_names",
    "local_function_source",
    "resolve_local_parameter_annotation",
    "serialize_local_signature",
    "value_type_for_const",
]

"""Pure source-backed callable analysis, source snapshots, and preparation caching."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

from .callable_contracts import (
    SourceCallableContract,
    SourceCallableParameter,
    normalize_callable_keyword,
)
from .compiler_identities import FunctionId, GroupCompilationIdentity
from .constants import TYPE_TOKEN_NAMES, _ALLOWED_CONSTS
from .consteval import _is_const_vector
from .errors import CompileError
from .nf_types import NFType, serialize_nf_type

if TYPE_CHECKING:
    from .semantic_group import SemanticGroupCompilation


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

    @property
    def is_tuple(self) -> bool:
        """Return whether the source used tuple-return syntax."""
        return len(self.elements) > 1


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
    """Pair one source-call contract with the exact owner-specific group compilation."""

    contract: SourceCallableContract
    group: "SemanticGroupCompilation"

    def __post_init__(self) -> None:
        """Validate that the prepared group exposes final compiler identity."""
        identity = getattr(self.group, "identity", None)
        if not isinstance(identity, GroupCompilationIdentity):
            raise TypeError("prepared source callable group must expose GroupCompilationIdentity")


def value_type_for_const(value) -> NFType:
    """Infer the canonical NodeForge type represented by one materializable constant."""
    from .compile_time import ConstVector
    from .nf_types import NFType

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


class _FreeNameVisitor(ast.NodeVisitor):
    """Collect value-load free names while excluding simple callable-name positions."""

    def __init__(self, local_names) -> None:
        """Initialize free-name collection for one lexical local-name set."""
        self.local_names = set(local_names)
        self.names: list[str] = []
        self._seen: set[str] = set()

    def _add(self, name: str) -> None:
        """Record one unseen free name in stable source-discovery order."""
        if name not in self.local_names and name not in self._seen:
            self._seen.add(name)
            self.names.append(name)

    def visit_Call(self, node) -> None:
        """Visit call arguments while treating a simple callee name as callable identity."""
        if not isinstance(node.func, ast.Name):
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


def free_names(fn: ast.FunctionDef) -> tuple[str, ...]:
    """Return free value-load names in stable first-required order."""
    visitor = _FreeNameVisitor(_binding_names_in_local_function(fn))
    for stmt in fn.body:
        visitor.visit(stmt)
    return tuple(visitor.names)


class _LocalFunctionCallVisitor(ast.NodeVisitor):
    """Collect local function calls in stable first-seen order."""

    def __init__(self, local_function_names) -> None:
        """Initialize local-callee collection for the visible function names."""
        self.local_function_names = set(local_function_names)
        self.names: list[str] = []
        self._seen: set[str] = set()

    def visit_Call(self, node) -> None:
        """Record simple local callees and traverse ordinary operands."""
        if isinstance(node.func, ast.Name):
            if node.func.id in self.local_function_names and node.func.id not in self._seen:
                self._seen.add(node.func.id)
                self.names.append(node.func.id)
        else:
            self.visit(node.func)
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_FunctionDef(self, node) -> None:
        """Do not traverse nested function definitions."""
        return


def called_local_functions(fn: ast.FunctionDef, local_functions: Mapping[str, ast.FunctionDef]) -> tuple[str, ...]:
    """Return local callees used by *fn* in stable first-seen order."""
    visitor = _LocalFunctionCallVisitor(local_functions)
    for stmt in fn.body:
        visitor.visit(stmt)
    return tuple(visitor.names)


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
    _stack=(),
) -> tuple[LocalCapture, ...]:
    """Resolve direct/transitive captures entirely from frontend-owned semantic state."""
    if fn.name in _stack:
        cycle = " -> ".join(_stack + (fn.name,))
        raise CompileError(f"Recursive local function calls are not supported: {cycle}")

    captures: list[LocalCapture] = []
    seen: set[str] = set()

    def append(capture: LocalCapture) -> None:
        """Append one capture once while retaining first-required ordering."""
        if capture.name not in seen:
            seen.add(capture.name)
            captures.append(capture)

    for name in free_names(fn):
        if name in _ALLOWED_CONSTS:
            continue
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

    local_names = _binding_names_in_local_function(fn)
    for called_name in called_local_functions(fn, local_functions):
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
            _stack=_stack + (fn.name,),
        ):
            if capture.name in local_names and capture.name not in seen:
                raise CompileError(
                    f"Local function {fn.name}() cannot forward capture {capture.name} "
                    f"required by {called_name}(): name is local to {fn.name}()"
                )
            append(capture)
    return tuple(captures)


class SourceCallableSession:
    """Own one root-attempt source snapshot and owner-specific preparation cache."""

    def __init__(self, *, resolved_environment) -> None:
        """Start a root-scoped source/preparation session with no persistent cache."""
        self.resolved_environment = resolved_environment
        self.source_snapshots: dict[FunctionId, str] = {}
        self.prepared: dict[SourceCallablePreparationKey, PreparedSourceCallable] = {}
        self._active_stack: list[FunctionId] = []

    def _source_snapshot(self, function_id: FunctionId, source_supplier) -> str:
        """Capture one coherent definition source snapshot for this root attempt."""
        source = self.source_snapshots.get(function_id)
        if source is None:
            source = source_supplier()
            if not isinstance(source, str):
                raise CompileError("Internal error: source-backed callable supplier did not return source text")
            self.source_snapshots[function_id] = source
        return source

    def _prepare(
        self,
        *,
        function_id: FunctionId,
        identity: GroupCompilationIdentity,
        source_supplier,
        local_functions,
        imported_library_functions,
        backend_builtins,
        helper_namespace: str,
        local_public_names: tuple[str, ...] | None = None,
        local_hidden_names: tuple[str, ...] = (),
        local_output_count: int | None = None,
    ) -> PreparedSourceCallable:
        """Prepare/cache one exact owner-specific semantic group and callable contract."""
        key = SourceCallablePreparationKey(function_id, identity.owner_scope)
        cached = self.prepared.get(key)
        if cached is not None:
            return cached
        if function_id in self._active_stack:
            chain = " -> ".join(item.name for item in self._active_stack + [function_id])
            raise CompileError(f"Recursive source function calls are not supported: {chain}")
        source = self._source_snapshot(function_id, source_supplier)
        self._active_stack.append(function_id)
        try:
            from .semantic_group import analyze_group_source

            group = analyze_group_source(
                source,
                compilation_identity=identity,
                resolved_environment=self.resolved_environment,
                inherited_local_functions=local_functions,
                inherited_imported_library_functions=imported_library_functions,
                backend_builtins=backend_builtins,
                helper_namespace=helper_namespace,
                source_callable_session=self,
            )
        finally:
            popped = self._active_stack.pop()
            if popped != function_id:
                raise CompileError("Internal error: source-call semantic activity stack was corrupted")

        if group.source != source:
            raise CompileError("Internal error: prepared source callable changed its captured source snapshot")
        if local_public_names is None:
            parameters = tuple(
                SourceCallableParameter(
                    input_index=input_index,
                    source_name=item.source_name,
                    display_name=item.display_name,
                    keyword_key=normalize_callable_keyword(item.display_name),
                    typ=item.typ,
                    public=True,
                )
                for input_index, item in enumerate(group.interface.inputs)
            )
        else:
            inputs_by_name = {
                item.source_name: (input_index, item)
                for input_index, item in enumerate(group.interface.inputs)
                if item.source_name is not None
            }
            parameters_list: list[SourceCallableParameter] = []
            for name in local_public_names + local_hidden_names:
                indexed_item = inputs_by_name.get(name)
                if indexed_item is None:
                    raise CompileError(f"Internal error: local helper semantic interface lost input {name!r}")
                input_index, item = indexed_item
                public = name in local_public_names
                parameters_list.append(
                    SourceCallableParameter(
                        input_index=input_index,
                        source_name=name,
                        display_name=item.display_name,
                        keyword_key=name if public else None,
                        typ=item.typ,
                        public=public,
                    )
                )
            parameters = tuple(parameters_list)
        outputs = group.interface.outputs
        if local_output_count is not None:
            if local_output_count <= 0 or local_output_count > len(outputs):
                raise CompileError("Internal error: local helper semantic output count does not match return shape")
            outputs = outputs[-local_output_count:]
        contract = SourceCallableContract(function_id, parameters, outputs)
        if contract.function_id != key.function_id or group.identity.owner_scope != key.owner_scope:
            raise CompileError("Internal error: prepared source callable cache key does not match semantic result")
        prepared = PreparedSourceCallable(contract, group)
        self.prepared[key] = prepared
        return prepared

    def prepare_local(
        self,
        *,
        function_id: FunctionId,
        identity: GroupCompilationIdentity,
        generated_source: str,
        explicit_parameter_names: tuple[str, ...],
        hidden_capture_names: tuple[str, ...],
        local_functions,
        imported_library_functions,
        backend_builtins,
        helper_namespace: str,
        return_shape: LocalReturnShape,
    ) -> PreparedSourceCallable:
        """Prepare one local specialization under its final shared/unique physical owner."""
        return self._prepare(
            function_id=function_id,
            identity=identity,
            source_supplier=lambda: generated_source,
            local_functions=local_functions,
            imported_library_functions=imported_library_functions,
            backend_builtins=backend_builtins,
            helper_namespace=helper_namespace,
            local_public_names=tuple(explicit_parameter_names),
            local_hidden_names=tuple(hidden_capture_names),
            local_output_count=len(return_shape.elements),
        )

    def prepare_library(
        self,
        *,
        function_id: FunctionId,
        identity: GroupCompilationIdentity,
        record,
        backend_builtins,
    ) -> PreparedSourceCallable:
        """Prepare one pure source-only catalog entry without importing Python modules."""
        source_path = getattr(record, "source_path", None)
        if source_path is None or getattr(record, "module_path", None) is not None:
            raise CompileError("Internal error: prepare_library requires a pure source-only record")
        return self._prepare(
            function_id=function_id,
            identity=identity,
            source_supplier=lambda: source_path.read_text(encoding="utf-8"),
            local_functions={},
            imported_library_functions={},
            backend_builtins=backend_builtins,
            helper_namespace=getattr(record, "name", function_id.name),
        )

    def get_prepared(self, key: SourceCallablePreparationKey) -> PreparedSourceCallable:
        """Return the exact cached owner-specific artifact required by backend lowering."""
        try:
            return self.prepared[key]
        except KeyError as exc:
            raise CompileError("Internal error: source-call backend requested an unprepared callable") from exc

    def snapshot_view(self) -> Mapping[FunctionId, str]:
        """Return a read-only view useful for deterministic unit assertions."""
        return MappingProxyType(dict(self.source_snapshots))


__all__ = [
    "LocalCapture",
    "LocalReturnElement",
    "LocalReturnShape",
    "PreparedSourceCallable",
    "SourceCallablePreparationKey",
    "SourceCallableSession",
    "analyze_local_captures",
    "analyze_local_return_shape",
    "called_local_functions",
    "free_names",
    "input_call_for_type",
    "local_function_source",
    "resolve_local_parameter_annotation",
    "serialize_local_signature",
    "value_type_for_const",
]

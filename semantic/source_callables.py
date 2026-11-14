"""Pure source-backed callable analysis and semantic preparation records."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Mapping

from .callable_contracts import SourceCallableContract
from ..compiler_identities import FunctionId, GroupCompilationIdentity, normalize_library_package_id
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
        if function_id.kind == "MODULE_DEF":
            if self.source_record is not None and (
                    getattr(self.source_record, "source_path", None) is None
                    or function_id.module.kind not in {"LOCAL", "PACKAGE", "EXAMPLES"}):
                raise TypeError("Module definition source record does not match its file owner")
            return
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


def _display_name(name: str) -> str:
    """Convert one Python binding name to the existing socket display convention."""
    return " ".join(part.capitalize() for part in name.split("_")) or "Value"


def analyze_local_return_shape(fn: ast.FunctionDef, *, output_label: str | None = None) -> LocalReturnShape:
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
    if output_label is not None and len(expressions) != 1:
        raise CompileError(f"Local function {fn.name}() cannot label a tuple return")
    used: dict[str, int] = {}
    elements: list[LocalReturnElement] = []
    for index, expression in enumerate(expressions):
        base = (_display_name(expression.id) if isinstance(expression, ast.Name)
                else "Value" if len(expressions) == 1 else f"Value {index + 1}")
        # The old scalar contract used Value. Preserve its compilability when
        # an inferred variable label exceeds Blender's physical socket limit.
        if len(expressions) == 1 and len(base.encode("utf-8")) > 63:
            base = "Value"
        count = used.get(base, 0) + 1
        used[base] = count
        socket_name = base if count == 1 else f"{base} {count}"
        elements.append(LocalReturnElement(index, socket_name, f"return:{index}", expression))
    if output_label is not None:
        element = elements[0]
        elements[0] = LocalReturnElement(0, output_label, "return:0", element.expression)
    # Blender's interface item keeps long names, but its instantiated node
    # sockets truncate names beyond 63 UTF-8 bytes. Reject such names here
    # instead of failing with an uncontrolled KeyError during graph assembly.
    for element in elements:
        if len(element.socket_name.encode("utf-8")) > 63:
            raise CompileError(
                f"Local function {fn.name}() output socket label "
                f"{element.socket_name!r} exceeds Blender's 63-byte UTF-8 limit"
            )
    return LocalReturnShape(tuple(elements))


def serialize_local_signature(signature_names, param_types: Mapping[str, NFType]) -> str:
    """Serialize typed function parameters to the established stable signature."""
    return ",".join(f"{name}:{serialize_nf_type(param_types[name])}" for name in signature_names)


def deserialize_module_parameter_signature(signature: str) -> tuple[tuple[str, NFType], ...]:
    """Decode the exact specialization types of one persisted module definition."""
    from ..nf_types import deserialize_nf_type

    if not isinstance(signature, str):
        raise CompileError('Invalid module specialization signature')
    if not signature:
        return ()
    result = []
    seen = set()
    for entry in signature.split(','):
        name, separator, token = entry.partition(':')
        if not separator or not name.isidentifier() or name in seen:
            raise CompileError('Invalid module specialization signature')
        try:
            typ = deserialize_nf_type(token)
        except (TypeError, ValueError) as exc:
            raise CompileError('Invalid module specialization type') from exc
        seen.add(name)
        result.append((name, typ))
    return tuple(result)


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


__all__ = [
    "LocalReturnElement",
    "LocalReturnShape",
    "PreparedSourceCallable",
    "SourceCallablePreparationKey",
    "analyze_local_return_shape",
    "serialize_local_signature",
    "deserialize_module_parameter_signature",
]

"""Pure callable-name resolution records for NodeForge semantic analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType
from typing import Mapping

from .compiler_identities import FunctionId, library_function_id
from .group_context import GROUP_CONTEXT_SPECS, GroupContextSlot
from .nf_types import NFType


class CallableKind(Enum):
    """Identify the compiler-owned category selected for one source call."""

    BUILTIN = auto()
    SYSTEM = auto()
    LOCAL_FUNCTION = auto()
    BACKEND_HELPER = auto()
    LIBRARY = auto()
    TOP_LEVEL_ONLY = auto()
    OBJECT_INFO = auto()


@dataclass(frozen=True)
class ResolvedCallable:
    """Describe one selected callable without backend implementation objects."""

    kind: CallableKind
    source_name: str
    target: object | None = None
    library_function_id: FunctionId | None = None

    def __post_init__(self) -> None:
        """Validate category-specific immutable identity fields."""
        if not isinstance(self.kind, CallableKind):
            raise TypeError("kind must be a CallableKind")
        if not isinstance(self.source_name, str) or not self.source_name:
            raise ValueError("source_name must be a non-empty string")
        if self.kind is CallableKind.LIBRARY:
            if self.target is None or not isinstance(self.library_function_id, FunctionId):
                raise ValueError("library call resolution requires binding and FunctionId")
        elif self.library_function_id is not None:
            raise ValueError("only library call resolution may carry library_function_id")


@dataclass(frozen=True)
class CallableEnvironment:
    """Immutable namespace snapshot used to resolve simple expression calls."""

    callable_builtins: frozenset[str]
    system_constructors: Mapping[str, object]
    local_functions: Mapping[str, object]
    backend_helper_names: frozenset[str]
    imported_functions: Mapping[str, object]

    def __post_init__(self) -> None:
        """Defensively freeze all name collections for one analysis invocation."""
        object.__setattr__(self, "callable_builtins", frozenset(self.callable_builtins))
        object.__setattr__(self, "system_constructors", MappingProxyType(dict(self.system_constructors)))
        object.__setattr__(self, "local_functions", MappingProxyType(dict(self.local_functions)))
        object.__setattr__(self, "backend_helper_names", frozenset(self.backend_helper_names))
        object.__setattr__(self, "imported_functions", MappingProxyType(dict(self.imported_functions)))


@dataclass(frozen=True)
class AnalyzedCallOperand:
    """Describe one normalized runtime operand without retaining its AST node."""

    parameter_name: str | None
    typ: NFType

    def __post_init__(self) -> None:
        """Require canonical runtime type identity."""
        if self.parameter_name is not None and not isinstance(self.parameter_name, str):
            raise TypeError("parameter_name must be a string or None")
        if not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType")


@dataclass(frozen=True)
class RuntimeCallResult:
    """Describe one ordinary runtime result of a normalized call."""

    typ: NFType

    def __post_init__(self) -> None:
        """Require canonical runtime type identity."""
        if not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType")


@dataclass(frozen=True)
class TupleCallResult:
    """Describe one fixed structural tuple returned by a normalized call."""

    types: tuple[NFType, ...]

    def __post_init__(self) -> None:
        """Require at least one canonically typed member."""
        object.__setattr__(self, "types", tuple(self.types))
        if not self.types or not all(isinstance(typ, NFType) for typ in self.types):
            raise TypeError("tuple call results require one or more NFType members")


@dataclass(frozen=True)
class ProjectedCallResult:
    """Describe a physical multi-result call with one source-visible projection."""

    types: tuple[NFType, ...]
    exposed_index: int
    context_writes: tuple[tuple[GroupContextSlot, int], ...] = ()

    def __post_init__(self) -> None:
        """Validate projected result indices and contextual result types."""
        object.__setattr__(self, "types", tuple(self.types))
        object.__setattr__(self, "context_writes", tuple(self.context_writes))
        if not self.types or not all(isinstance(typ, NFType) for typ in self.types):
            raise TypeError("projected call results require one or more NFType members")
        if not isinstance(self.exposed_index, int) or isinstance(self.exposed_index, bool):
            raise TypeError("exposed_index must be an integer")
        if not 0 <= self.exposed_index < len(self.types):
            raise ValueError("exposed_index is out of range")
        slots = []
        for item in self.context_writes:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("context_writes must contain (GroupContextSlot, result_index) pairs")
            slot, result_index = item
            if not isinstance(slot, GroupContextSlot):
                raise TypeError("context write slot must be a GroupContextSlot")
            if not isinstance(result_index, int) or isinstance(result_index, bool) or not 0 <= result_index < len(self.types):
                raise ValueError("context write result index is out of range")
            if self.types[result_index] is not GROUP_CONTEXT_SPECS[slot].typ:
                raise TypeError("context write result type does not match slot type")
            slots.append(slot)
        if len(slots) != len(set(slots)):
            raise ValueError("one context slot may be written at most once per projected call")


@dataclass(frozen=True)
class ContextReadCallResult:
    """Describe a source call that reads one compiler-owned group-context slot."""

    slot: GroupContextSlot
    typ: NFType

    def __post_init__(self) -> None:
        """Require the result type to match the contextual slot contract."""
        if not isinstance(self.slot, GroupContextSlot):
            raise TypeError("slot must be a GroupContextSlot")
        if not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType")
        if self.typ is not GROUP_CONTEXT_SPECS[self.slot].typ:
            raise TypeError("context read type does not match slot type")


@dataclass(frozen=True)
class NamedOutputsCallResult:
    """Describe raw ``outputs=`` structural results in declared source order."""

    items: tuple[tuple[str, NFType], ...]

    def __post_init__(self) -> None:
        """Require one or more unique named typed outputs."""
        object.__setattr__(self, "items", tuple(self.items))
        names = [name for name, _ in self.items]
        if not self.items or len(names) != len(set(names)):
            raise ValueError("named outputs require one or more unique names")
        if any(not isinstance(name, str) or not name for name in names):
            raise ValueError("named output names must be non-empty strings")
        if any(not isinstance(typ, NFType) for _, typ in self.items):
            raise TypeError("named output types must be NFType members")


CallResultSpec = RuntimeCallResult | TupleCallResult | NamedOutputsCallResult | ProjectedCallResult | ContextReadCallResult


@dataclass(frozen=True)
class AnalyzedCall:
    """AST-free normalized semantic meaning for one IR-capable call."""

    target: ResolvedCallable
    runtime_operands: tuple[AnalyzedCallOperand, ...]
    options: tuple[tuple[str, object], ...]
    result: CallResultSpec

    def __post_init__(self) -> None:
        """Freeze normalized fields and reject non-core call targets."""
        if not isinstance(self.target, ResolvedCallable):
            raise TypeError("target must be a ResolvedCallable")
        object.__setattr__(self, "runtime_operands", tuple(self.runtime_operands))
        object.__setattr__(self, "options", tuple(self.options))
        if not all(isinstance(item, AnalyzedCallOperand) for item in self.runtime_operands):
            raise TypeError("runtime_operands must contain AnalyzedCallOperand records")
        if not isinstance(self.result, (RuntimeCallResult, TupleCallResult, NamedOutputsCallResult, ProjectedCallResult, ContextReadCallResult)):
            raise TypeError("result must be a call result specification")

    def option_map(self) -> Mapping[str, object]:
        """Return a read-only mapping view of normalized call options."""
        return MappingProxyType(dict(self.options))


@dataclass(frozen=True)
class _UnresolvedCallable:
    """Sentinel preserving legacy diagnostics after pure name resolution."""


UNRESOLVED = _UnresolvedCallable()


def resolve_simple_callable(name: str, environment: CallableEnvironment):
    """Resolve *name* with the exact legacy callable precedence."""
    if name in environment.callable_builtins:
        return ResolvedCallable(CallableKind.BUILTIN, name, target=name)
    system = environment.system_constructors.get(name)
    if system is not None:
        return ResolvedCallable(CallableKind.SYSTEM, name, target=system)
    if name in environment.local_functions:
        return ResolvedCallable(CallableKind.LOCAL_FUNCTION, name, target=name)
    if name in environment.backend_helper_names:
        return ResolvedCallable(CallableKind.BACKEND_HELPER, name, target=name)
    binding = environment.imported_functions.get(name)
    if binding is not None:
        record = binding.record
        function_id = library_function_id(binding.namespace, record.package_id, binding.canonical_name)
        return ResolvedCallable(
            CallableKind.LIBRARY,
            name,
            target=binding,
            library_function_id=function_id,
        )
    if name in {"output", "store"}:
        return ResolvedCallable(CallableKind.TOP_LEVEL_ONLY, name, target=name)
    return UNRESOLVED


__all__ = [
    "AnalyzedCall",
    "AnalyzedCallOperand",
    "CallResultSpec",
    "CallableEnvironment",
    "CallableKind",
    "NamedOutputsCallResult",
    "ProjectedCallResult",
    "ContextReadCallResult",
    "ResolvedCallable",
    "RuntimeCallResult",
    "TupleCallResult",
    "UNRESOLVED",
    "resolve_simple_callable",
]

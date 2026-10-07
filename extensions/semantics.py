"""Expression-local semantic execution helpers for declarative Python extensions."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum

from ..compiler_identities import BindingId
from ..errors import CompileError
from .contracts import (
    ExtensionCallableSpec,
    TypeSpec,
    is_executable_result_type_spec,
    is_frontend_semantic_type_spec,
)
from ..extension_semantic_api import RuntimeRef
from .values import (
    ExtensionDependencySlot,
    pack_value,
    remap_dependency_slots,
    unpack_value,
)
from ..nf_types import NFType




class ExtensionExecutionForm(str, Enum):
    """Identify the normalized semantic/physical execution shape of one extension callable."""

    BACKEND_ONLY = "BACKEND_ONLY"
    SEMANTIC_ONLY = "SEMANTIC_ONLY"
    SEMANTIC_THEN_BACKEND = "SEMANTIC_THEN_BACKEND"


def classify_extension_execution(
    spec: ExtensionCallableSpec,
    semantic_return: TypeSpec | None,
    *,
    has_implementation: bool,
) -> ExtensionExecutionForm:
    """Classify one non-overloaded extension contract without introducing fallback behavior."""
    has_semantic_parameter = any(is_frontend_semantic_type_spec(parameter.type_spec) for parameter in spec.parameters)
    public_semantic = is_frontend_semantic_type_spec(spec.result)
    public_executable = is_executable_result_type_spec(spec.result)

    if semantic_return is None:
        if has_semantic_parameter or public_semantic:
            raise CompileError(f"Extension callable {spec.id.name!r} requires semantic.py implementation")
        if not public_executable:
            raise CompileError(f"Extension callable {spec.id.name!r} has unsupported public result contract")
        if not has_implementation:
            raise CompileError(f"Executable extension callable {spec.id.name!r} requires a physical implementation")
        return ExtensionExecutionForm.BACKEND_ONLY

    if public_semantic:
        if semantic_return != spec.result:
            raise CompileError(
                f"Semantic return contract for {spec.id.name!r} must exactly match its frontend-semantic public result"
            )
        if has_implementation:
            raise CompileError(
                f"Frontend-semantic extension callable {spec.id.name!r} cannot also declare a physical implementation"
            )
        return ExtensionExecutionForm.SEMANTIC_ONLY

    if not public_executable:
        raise CompileError(f"Extension callable {spec.id.name!r} has unsupported public result contract")
    if semantic_return == spec.result:
        raise CompileError(
            f"Executable extension callable {spec.id.name!r} cannot use its executable public result as semantic state"
        )
    if not has_implementation:
        raise CompileError(f"Semantic-then-backend extension callable {spec.id.name!r} requires a physical implementation")
    return ExtensionExecutionForm.SEMANTIC_THEN_BACKEND


@dataclass(frozen=True)
class ExtensionDependencySource:
    """Identify one transient AST occurrence or persistent body binding used by semantic state."""

    source: ast.expr | BindingId
    typ: NFType

    def __post_init__(self) -> None:
        """Require one compiler-owned dependency identity and its actual runtime type."""
        if not isinstance(self.source, (ast.expr, BindingId)):
            raise TypeError("ExtensionDependencySource.source must be ast.expr or BindingId")
        if not isinstance(self.typ, NFType):
            raise TypeError("ExtensionDependencySource.typ must be NFType")

    @property
    def is_persistent(self) -> bool:
        """Return whether this dependency already names a persistent body binding."""
        return isinstance(self.source, BindingId)



def _dependency_slot_indices(value: object):
    """Yield dependency slot indices in deterministic detached-storage traversal order."""
    if isinstance(value, ExtensionDependencySlot):
        yield value.dependency_index, value.typ
        return
    from .values import ExtensionValue
    if isinstance(value, ExtensionValue):
        yield from _dependency_slot_indices(value.storage)
        return
    if isinstance(value, tuple):
        for item in value:
            yield from _dependency_slot_indices(item)


@dataclass(frozen=True)
class ExtensionSemanticPayload:
    """Carry canonical detached package semantic state and exactly its live runtime dependencies."""

    type_spec: TypeSpec
    value: object
    dependencies: tuple[ExtensionDependencySource, ...]

    def __post_init__(self) -> None:
        """Require dependency-compact canonical storage ordered by first semantic use."""
        dependencies = tuple(self.dependencies)
        object.__setattr__(self, "dependencies", dependencies)
        if not isinstance(self.type_spec, TypeSpec) or not is_frontend_semantic_type_spec(self.type_spec):
            raise TypeError("ExtensionSemanticPayload requires RECORD or recursively semantic LIST TypeSpec")
        if not all(isinstance(item, ExtensionDependencySource) for item in dependencies):
            raise TypeError("ExtensionSemanticPayload.dependencies must contain ExtensionDependencySource records")
        first_seen: list[int] = []
        seen: set[int] = set()
        for index, slot_type in _dependency_slot_indices(self.value):
            if index < 0 or index >= len(dependencies):
                raise CompileError("Extension semantic dependency slot is out of range")
            if dependencies[index].typ is not slot_type:
                raise CompileError("Extension semantic dependency slot actual type does not match its source")
            if index not in seen:
                seen.add(index)
                first_seen.append(index)
        expected = list(range(len(dependencies)))
        if first_seen != expected or len(set(dependencies)) != len(dependencies):
            raise CompileError("Extension semantic payload dependencies are not canonical and dependency-compact")


def compact_extension_semantic_payload(
    type_spec: TypeSpec,
    value: object,
    dependencies: tuple[ExtensionDependencySource, ...] | list[ExtensionDependencySource],
) -> ExtensionSemanticPayload:
    """Canonicalize live dependencies by first occurrence in detached semantic storage."""
    dependencies = tuple(dependencies)
    mapping: dict[int, int] = {}
    compact: list[ExtensionDependencySource] = []
    canonical_by_source: dict[ExtensionDependencySource, int] = {}
    for old_index, slot_type in _dependency_slot_indices(value):
        if old_index < 0 or old_index >= len(dependencies):
            raise CompileError("Extension semantic dependency slot is out of range")
        source = dependencies[old_index]
        if source.typ is not slot_type:
            raise CompileError("Extension semantic dependency slot actual type does not match its source")
        if old_index in mapping:
            continue
        canonical_index = canonical_by_source.get(source)
        if canonical_index is None:
            canonical_index = len(compact)
            canonical_by_source[source] = canonical_index
            compact.append(source)
        mapping[old_index] = canonical_index
    remapped = remap_dependency_slots(value, mapping) if mapping else value
    return ExtensionSemanticPayload(type_spec, remapped, tuple(compact))


class SemanticInvocation:
    """Own one semantic call's runtime dependencies, RuntimeRefs, and reconstruction."""

    def __init__(self, registry):
        """Create an empty invocation-local dependency table."""
        self.registry = registry
        self._sources: list[ExtensionDependencySource] = []
        self._indices: dict[ExtensionDependencySource, int] = {}
        self._refs: list[RuntimeRef] = []
        self._active: dict[object, tuple[int, NFType]] = {}

    def _add_source(self, source: ExtensionDependencySource) -> int:
        """Return the stable invocation index for one exact analyzed source occurrence."""
        index = self._indices.get(source)
        if index is not None:
            return index
        index = len(self._sources)
        token = object()
        ref = RuntimeRef(token, source.typ)
        self._sources.append(source)
        self._indices[source] = index
        self._refs.append(ref)
        self._active[token] = (index, source.typ)
        return index

    def runtime_ref_for_source(self, source: ExtensionDependencySource) -> RuntimeRef:
        """Return the invocation-owned RuntimeRef for one runtime source."""
        return self._refs[self._add_source(source)]

    def runtime_ref(self, index: int, expected_type: NFType | None = None) -> RuntimeRef:
        """Return one active RuntimeRef and verify its actual type when requested."""
        try:
            ref = self._refs[index]
        except IndexError as exc:
            raise CompileError("Extension semantic dependency index is out of range") from exc
        if expected_type is not None and ref.typ is not expected_type:
            raise CompileError("Extension semantic dependency actual type changed during reconstruction")
        return ref

    def import_payload_value(self, payload: ExtensionSemanticPayload) -> object:
        """Return invocation-local remapped storage without constructing a noncanonical payload."""
        mapping = {
            old_index: self._add_source(source)
            for old_index, source in enumerate(payload.dependencies)
        }
        return remap_dependency_slots(payload.value, mapping) if mapping else payload.value

    def reconstruct_payload(self, payload: ExtensionSemanticPayload) -> object:
        """Rebuild package data from invocation-local remapped detached storage."""
        imported_value = self.import_payload_value(payload)

        def resolve_runtime_leaf(stored: object):
            """Resolve one invocation-local dependency slot to its active RuntimeRef."""
            if not isinstance(stored, ExtensionDependencySlot):
                return None
            return stored.typ, self.runtime_ref(stored.dependency_index, stored.typ)

        return unpack_value(
            payload.type_spec,
            imported_value,
            registry=self.registry,
            runtime_leaf_resolver=resolve_runtime_leaf,
        )

    def pack_result(self, return_type: TypeSpec, value: object) -> object:
        """Validate and detach one semantic function result against its declared TypeSpec."""
        return pack_value(
            return_type,
            value,
            registry=self.registry,
            active_runtime_refs=self._active,
        )

    @property
    def dependencies(self) -> tuple[ExtensionDependencySource, ...]:
        """Return invocation acquisition state; canonical payload reachability owns runtime demand."""
        return tuple(self._sources)


def semantic_type_compatible(actual: TypeSpec, expected: TypeSpec, registry) -> bool:
    """Return structural/nominal compatibility for frontend semantic RECORD/LIST contracts."""
    if actual.kind != expected.kind:
        return False
    if actual.kind == "RECORD":
        return registry.is_nominal_subtype(actual.record_type, expected.record_type)
    if actual.kind == "LIST":
        return semantic_type_compatible(actual.item, expected.item, registry)
    return actual == expected



__all__ = [
    "ExtensionExecutionForm",
    "classify_extension_execution",
    "ExtensionDependencySource",
    "ExtensionSemanticPayload",
    "SemanticInvocation",
    "compact_extension_semantic_payload",
    "semantic_type_compatible",
]

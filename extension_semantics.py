"""Expression-local semantic execution helpers for declarative Python extensions."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum

from .errors import CompileError
from .extension_contracts import (
    ExtensionCallableSpec,
    TypeSpec,
    is_executable_result_type_spec,
    is_frontend_semantic_type_spec,
)
from .extension_semantic_api import RuntimeRef
from .extension_values import (
    ExtensionDependencySlot,
    pack_value,
    remap_dependency_slots,
    unpack_value,
)
from .nf_types import NFType




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
    """Identify one already-analyzed runtime source occurrence within one expression analysis."""

    expression: ast.expr
    typ: NFType

    def __post_init__(self) -> None:
        """Require an expression occurrence and its actual canonical runtime type."""
        if not isinstance(self.expression, ast.expr):
            raise TypeError("ExtensionDependencySource.expression must be ast.expr")
        if not isinstance(self.typ, NFType):
            raise TypeError("ExtensionDependencySource.typ must be NFType")

    def __hash__(self) -> int:
        """Hash by exact source occurrence identity and actual type."""
        return hash((id(self.expression), self.typ))

    def __eq__(self, other: object) -> bool:
        """Deduplicate only the exact same analyzed source occurrence."""
        return (
            isinstance(other, ExtensionDependencySource)
            and self.expression is other.expression
            and self.typ is other.typ
        )


@dataclass(frozen=True)
class ExtensionSemanticPayload:
    """Carry one expression-local package semantic value and its runtime dependency sources."""

    type_spec: TypeSpec
    value: object
    dependencies: tuple[ExtensionDependencySource, ...]

    def __post_init__(self) -> None:
        """Freeze dependencies and require a frontend-semantic canonical type contract."""
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        if not isinstance(self.type_spec, TypeSpec) or not is_frontend_semantic_type_spec(self.type_spec):
            raise TypeError("ExtensionSemanticPayload requires RECORD or recursively semantic LIST TypeSpec")
        if not all(isinstance(item, ExtensionDependencySource) for item in self.dependencies):
            raise TypeError("ExtensionSemanticPayload.dependencies must contain ExtensionDependencySource records")


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

    def import_payload(self, payload: ExtensionSemanticPayload) -> ExtensionSemanticPayload:
        """Remap a nested payload once into this invocation's ordered dependency table."""
        mapping: dict[int, int] = {}
        for old_index, source in enumerate(payload.dependencies):
            mapping[old_index] = self._add_source(source)
        return ExtensionSemanticPayload(
            payload.type_spec,
            remap_dependency_slots(payload.value, mapping),
            self.dependencies,
        )

    def reconstruct_payload(self, payload: ExtensionSemanticPayload) -> object:
        """Rebuild package data while importing nested dependencies without rewriting storage."""
        mapping = {
            old_index: self._add_source(source)
            for old_index, source in enumerate(payload.dependencies)
        }

        def resolve_runtime_leaf(stored: object):
            """Resolve one frontend dependency slot through the invocation-local index map."""
            if not isinstance(stored, ExtensionDependencySlot):
                return None
            try:
                index = mapping[stored.dependency_index]
            except KeyError as exc:
                raise CompileError("Extension semantic dependency slot references an unknown payload dependency") from exc
            return stored.typ, self.runtime_ref(index, stored.typ)

        return unpack_value(
            payload.type_spec,
            payload.value,
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
        """Return the deterministic runtime dependency order accumulated by this invocation."""
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
    "semantic_type_compatible",
]

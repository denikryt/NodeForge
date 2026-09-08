"""Blender-independent semantic result, structural binding, and Object-state records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, TypeAlias

from .compiler_identities import BindingId
from .errors import CompileError
from .nf_types import NFType


@dataclass(frozen=True, order=True)
class ObjectSemanticId:
    """Identify one semantic Object value within a single body-analysis attempt."""

    local_id: int

    def __post_init__(self) -> None:
        """Require a non-negative monotonic local identifier."""
        if not isinstance(self.local_id, int) or isinstance(self.local_id, bool) or self.local_id < 0:
            raise ValueError("ObjectSemanticId.local_id must be a non-negative integer")


@dataclass(frozen=True)
class RuntimeResultShape:
    """Describe one runtime result leaf and its Object provenance when applicable."""

    typ: NFType
    object_id: ObjectSemanticId | None = None

    def __post_init__(self) -> None:
        """Enforce the bidirectional Object identity invariant."""
        if not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType")
        if self.typ is NFType.OBJECT:
            if not isinstance(self.object_id, ObjectSemanticId):
                raise ValueError("Object result requires object_id")
        elif self.object_id is not None:
            raise ValueError("object_id is only valid for Object")


@dataclass(frozen=True)
class ArrayResultShape:
    """Describe one compiler-structural array expression result."""

    items: tuple["SemanticResultShape", ...]

    def __post_init__(self) -> None:
        """Freeze recursive array members."""
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True)
class TupleResultShape:
    """Describe one fixed tuple returned by a compiler-owned runtime call."""

    items: tuple[RuntimeResultShape, ...]

    def __post_init__(self) -> None:
        """Require one or more runtime leaves."""
        object.__setattr__(self, "items", tuple(self.items))
        if not self.items or not all(isinstance(item, RuntimeResultShape) for item in self.items):
            raise TypeError("TupleResultShape requires one or more RuntimeResultShape members")


@dataclass(frozen=True)
class NamedOutputsResultShape:
    """Describe declared outputs returned by raw ``node(..., outputs=...)`` syntax."""

    items: tuple[tuple[str, RuntimeResultShape], ...]

    def __post_init__(self) -> None:
        """Require unique non-empty names and runtime leaves."""
        object.__setattr__(self, "items", tuple(self.items))
        names = [name for name, _ in self.items]
        if not self.items or any(not isinstance(name, str) or not name for name in names):
            raise ValueError("NamedOutputsResultShape requires one or more non-empty names")
        if len(names) != len(set(names)):
            raise ValueError("NamedOutputsResultShape requires unique names")
        if any(not isinstance(shape, RuntimeResultShape) for _, shape in self.items):
            raise TypeError("NamedOutputsResultShape members must be RuntimeResultShape records")

    @property
    def names(self) -> tuple[str, ...]:
        """Return declared output names in source order."""
        return tuple(name for name, _ in self.items)

    def get(self, name: str) -> RuntimeResultShape:
        """Return one declared output shape with the current controlled diagnostic."""
        for item_name, shape in self.items:
            if item_name == name:
                return shape
        known = ", ".join(repr(item_name) for item_name, _ in self.items) or "<none>"
        raise CompileError(f"Unknown raw node output {name!r}; declared outputs are: {known}")


SemanticResultShape: TypeAlias = RuntimeResultShape | ArrayResultShape | TupleResultShape | NamedOutputsResultShape


class StructuralBindingKind(str, Enum):
    """Identify one fixed frontend-owned structural source binding."""

    TUPLE = "TUPLE"
    NAMED_OUTPUTS = "NAMED_OUTPUTS"


@dataclass(frozen=True)
class StructuralLeafBinding:
    """Bind one fixed structural projection to a compiler-owned runtime leaf slot."""

    projection_key: tuple[str, int | str]
    binding_id: BindingId
    typ: NFType

    def __post_init__(self) -> None:
        """Validate detached structural leaf metadata."""
        if not isinstance(self.binding_id, BindingId):
            raise TypeError("binding_id must be a BindingId")
        if not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType")
        if not isinstance(self.projection_key, tuple) or len(self.projection_key) != 2:
            raise TypeError("projection_key must be a two-item tuple")
        kind, key = self.projection_key
        if kind == "index":
            if not isinstance(key, int) or isinstance(key, bool) or key < 0:
                raise ValueError("tuple projection key requires a non-negative integer")
        elif kind == "name":
            if not isinstance(key, str) or not key:
                raise ValueError("named-output projection key requires a non-empty string")
        else:
            raise ValueError("unsupported structural projection kind")


@dataclass(frozen=True)
class StructuralBindingSymbol:
    """Describe one fixed tuple or named-output source binding using runtime leaves only."""

    kind: StructuralBindingKind
    leaves: tuple[StructuralLeafBinding, ...]

    def __post_init__(self) -> None:
        """Validate aggregate kind, ordering, and duplicate projection keys."""
        if not isinstance(self.kind, StructuralBindingKind):
            raise TypeError("kind must be a StructuralBindingKind")
        object.__setattr__(self, "leaves", tuple(self.leaves))
        if not self.leaves or not all(isinstance(leaf, StructuralLeafBinding) for leaf in self.leaves):
            raise TypeError("StructuralBindingSymbol requires one or more StructuralLeafBinding members")
        keys = [leaf.projection_key for leaf in self.leaves]
        if len(keys) != len(set(keys)):
            raise ValueError("structural projection keys must be unique")
        expected_kind = "index" if self.kind is StructuralBindingKind.TUPLE else "name"
        if any(key[0] != expected_kind for key in keys):
            raise ValueError("structural leaf projection kind does not match aggregate kind")
        if self.kind is StructuralBindingKind.TUPLE:
            expected = [("index", index) for index in range(len(self.leaves))]
            if keys != expected:
                raise ValueError("tuple structural leaves must preserve contiguous source order")

    def result_shape(self, object_ids_by_binding: Mapping[BindingId, ObjectSemanticId]) -> SemanticResultShape:
        """Reconstruct the semantic structure using canonical Object identity by BindingId."""
        items: list[RuntimeResultShape] = []
        named_items: list[tuple[str, RuntimeResultShape]] = []
        for leaf in self.leaves:
            if leaf.typ is NFType.OBJECT:
                object_id = object_ids_by_binding.get(leaf.binding_id)
                if not isinstance(object_id, ObjectSemanticId):
                    raise CompileError("Internal error: Object structural leaf has no ObjectSemanticId")
                shape = RuntimeResultShape(leaf.typ, object_id)
            else:
                if leaf.binding_id in object_ids_by_binding:
                    raise CompileError("Internal error: non-Object structural leaf has ObjectSemanticId")
                shape = RuntimeResultShape(leaf.typ)
            if self.kind is StructuralBindingKind.TUPLE:
                items.append(shape)
            else:
                named_items.append((leaf.projection_key[1], shape))
        if self.kind is StructuralBindingKind.TUPLE:
            return TupleResultShape(tuple(items))
        return NamedOutputsResultShape(tuple(named_items))

    def leaf_by_index(self, index: int) -> StructuralLeafBinding:
        """Return one tuple leaf using Python negative-index semantics."""
        if self.kind is not StructuralBindingKind.TUPLE:
            raise TypeError("leaf_by_index is valid only for tuple structural bindings")
        try:
            return self.leaves[index]
        except IndexError as exc:
            raise CompileError(f"tuple result index {index} is out of range for {len(self.leaves)} values") from exc

    def leaf_by_name(self, name: str) -> StructuralLeafBinding:
        """Return one declared named-output leaf with the current controlled diagnostic."""
        if self.kind is not StructuralBindingKind.NAMED_OUTPUTS:
            raise TypeError("leaf_by_name is valid only for named-output structural bindings")
        for leaf in self.leaves:
            if leaf.projection_key[1] == name:
                return leaf
        known = ", ".join(repr(leaf.projection_key[1]) for leaf in self.leaves) or "<none>"
        raise CompileError(f"Unknown raw node output {name!r}; declared outputs are: {known}")


@dataclass(frozen=True)
class ObjectInfoState:
    """Store frontend-owned Object Info configuration and resolution lock state."""

    transform_space: str = "ORIGINAL"
    as_instance: bool = True
    resolved: bool = False

    def __post_init__(self) -> None:
        """Validate detached Object Info semantic state."""
        if self.transform_space not in {"ORIGINAL", "RELATIVE"}:
            raise ValueError("ObjectInfoState.transform_space must be ORIGINAL or RELATIVE")
        if not isinstance(self.as_instance, bool):
            raise TypeError("ObjectInfoState.as_instance must be bool")
        if not isinstance(self.resolved, bool):
            raise TypeError("ObjectInfoState.resolved must be bool")


@dataclass(frozen=True)
class ObjectSemanticSnapshot:
    """Provide one immutable body-owned Object identity/configuration snapshot to expression analysis."""

    object_ids_by_binding: Mapping[BindingId, ObjectSemanticId]
    states: Mapping[ObjectSemanticId, ObjectInfoState]
    next_object_id: int

    def __post_init__(self) -> None:
        """Freeze mappings and validate non-reusing allocator state."""
        object.__setattr__(self, "object_ids_by_binding", MappingProxyType(dict(self.object_ids_by_binding)))
        object.__setattr__(self, "states", MappingProxyType(dict(self.states)))
        if not isinstance(self.next_object_id, int) or isinstance(self.next_object_id, bool) or self.next_object_id < 0:
            raise ValueError("next_object_id must be a non-negative integer")
        if any(not isinstance(binding_id, BindingId) for binding_id in self.object_ids_by_binding):
            raise TypeError("Object identity table keys must be BindingId")
        if any(not isinstance(object_id, ObjectSemanticId) for object_id in self.object_ids_by_binding.values()):
            raise TypeError("Object identity table values must be ObjectSemanticId")
        if any(not isinstance(object_id, ObjectSemanticId) or not isinstance(state, ObjectInfoState) for object_id, state in self.states.items()):
            raise TypeError("Object state table contains invalid records")
        for object_id in self.object_ids_by_binding.values():
            if object_id not in self.states:
                raise ValueError("Object binding references missing ObjectInfoState")
        issued_max = max((object_id.local_id for object_id in self.states), default=-1)
        if self.next_object_id <= issued_max:
            raise ValueError("next_object_id must be greater than every issued ObjectSemanticId")


__all__ = [
    "ArrayResultShape",
    "NamedOutputsResultShape",
    "ObjectInfoState",
    "ObjectSemanticId",
    "ObjectSemanticSnapshot",
    "RuntimeResultShape",
    "SemanticResultShape",
    "StructuralBindingKind",
    "StructuralBindingSymbol",
    "StructuralLeafBinding",
    "TupleResultShape",
]

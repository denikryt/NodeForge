"""Blender-independent semantic result, structural binding, and Object-state records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, TypeAlias

from ..compiler_identities import BindingId
from ..errors import CompileError
from ..extensions.contracts import ExtensionTypeId
from ..nf_types import NFType


@dataclass(frozen=True, order=True)
class ObjectSemanticId:
    """Identify one semantic Object value within a single body-analysis attempt."""

    local_id: int

    def __post_init__(self) -> None:
        """Require a non-negative monotonic local identifier."""
        if not isinstance(self.local_id, int) or isinstance(self.local_id, bool) or self.local_id < 0:
            raise ValueError("ObjectSemanticId.local_id must be a non-negative integer")




@dataclass(frozen=True, order=True)
class StructuralArrayId:
    """Identify one mutable compiler-structural array within one body-analysis attempt."""

    local_id: int

    def __post_init__(self) -> None:
        """Require a non-negative monotonic local identifier."""
        if not isinstance(self.local_id, int) or isinstance(self.local_id, bool) or self.local_id < 0:
            raise ValueError("StructuralArrayId.local_id must be a non-negative integer")




@dataclass(frozen=True)
class StructuralRuntimeLeaf:
    """Store one persistent runtime leaf referenced by a structural array."""

    binding_id: BindingId
    typ: NFType

    def __post_init__(self) -> None:
        """Require canonical body binding and runtime type identities."""
        if not isinstance(self.binding_id, BindingId):
            raise TypeError("binding_id must be a BindingId")
        if not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType")


@dataclass(frozen=True)
class StructuralArrayRef:
    """Reference one body-owned nested structural-array identity."""

    array_id: StructuralArrayId

    def __post_init__(self) -> None:
        """Require a canonical structural-array identity."""
        if not isinstance(self.array_id, StructuralArrayId):
            raise TypeError("array_id must be a StructuralArrayId")


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
class ExtensionResultShape:
    """Describe one concrete owner-scoped package semantic record result leaf."""

    type_id: ExtensionTypeId

    def __post_init__(self) -> None:
        """Require canonical package semantic type identity."""
        if not isinstance(self.type_id, ExtensionTypeId):
            raise TypeError("ExtensionResultShape.type_id must be ExtensionTypeId")



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


SemanticResultShape: TypeAlias = RuntimeResultShape | ArrayResultShape | TupleResultShape | NamedOutputsResultShape | ExtensionResultShape


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


StructuralArrayItem: TypeAlias = StructuralRuntimeLeaf | StructuralBindingSymbol | StructuralArrayRef


@dataclass(frozen=True)
class StructuralArrayState:
    """Store one immutable body-owned structural array payload."""

    items: tuple[StructuralArrayItem, ...]

    def __post_init__(self) -> None:
        """Freeze and validate structural array members."""
        object.__setattr__(self, "items", tuple(self.items))
        allowed = (StructuralRuntimeLeaf, StructuralBindingSymbol, StructuralArrayRef)
        if not all(isinstance(item, allowed) for item in self.items):
            raise TypeError("StructuralArrayState contains an unsupported item")

    def result_shape(
        self,
        states: Mapping[StructuralArrayId, "StructuralArrayState"],
        object_ids_by_binding: Mapping[BindingId, ObjectSemanticId],
        *,
        active: frozenset[StructuralArrayId] = frozenset(),
    ) -> ArrayResultShape:
        """Reconstruct the recursive semantic result shape with cycle detection."""
        items: list[SemanticResultShape] = []
        for item in self.items:
            if isinstance(item, StructuralRuntimeLeaf):
                object_id = object_ids_by_binding.get(item.binding_id)
                if item.typ is NFType.OBJECT:
                    if not isinstance(object_id, ObjectSemanticId):
                        raise CompileError("Internal error: Object array leaf has no ObjectSemanticId")
                    items.append(RuntimeResultShape(item.typ, object_id))
                else:
                    if object_id is not None:
                        raise CompileError("Internal error: non-Object array leaf has ObjectSemanticId")
                    items.append(RuntimeResultShape(item.typ))
            elif isinstance(item, StructuralBindingSymbol):
                items.append(item.result_shape(object_ids_by_binding))
            else:
                if item.array_id in active:
                    raise CompileError("recursive structural arrays are not supported")
                nested = states.get(item.array_id)
                if nested is None:
                    raise CompileError("Internal error: structural array references missing state")
                items.append(
                    nested.result_shape(
                        states,
                        object_ids_by_binding,
                        active=active | frozenset({item.array_id}),
                    )
                )
        return ArrayResultShape(tuple(items))


@dataclass(frozen=True)
class StructuralArraySnapshot:
    """Provide an immutable detached view of body-owned structural arrays."""

    bindings: Mapping[str, StructuralArrayId]
    states: Mapping[StructuralArrayId, StructuralArrayState]

    def __post_init__(self) -> None:
        """Copy then freeze both mappings so the snapshot cannot observe later mutation."""
        bindings = dict(self.bindings)
        states = dict(self.states)
        if any(not isinstance(name, str) or not name for name in bindings):
            raise TypeError("structural array binding names must be non-empty strings")
        if any(not isinstance(array_id, StructuralArrayId) for array_id in bindings.values()):
            raise TypeError("structural array bindings must reference StructuralArrayId")
        if any(
            not isinstance(array_id, StructuralArrayId) or not isinstance(state, StructuralArrayState)
            for array_id, state in states.items()
        ):
            raise TypeError("structural array state table contains invalid records")
        if any(array_id not in states for array_id in bindings.values()):
            raise ValueError("structural array binding references missing state")
        object.__setattr__(self, "bindings", MappingProxyType(bindings))
        object.__setattr__(self, "states", MappingProxyType(states))

    def result_shape(
        self,
        array_id: StructuralArrayId,
        object_ids_by_binding: Mapping[BindingId, ObjectSemanticId],
    ) -> ArrayResultShape:
        """Reconstruct one array result shape without exposing mutable body state."""
        if not isinstance(array_id, StructuralArrayId):
            raise TypeError("array_id must be a StructuralArrayId")
        state = self.states.get(array_id)
        if state is None:
            raise CompileError("Internal error: unknown structural array identity")
        return state.result_shape(
            self.states,
            object_ids_by_binding,
            active=frozenset({array_id}),
        )


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
    "ExtensionResultShape",
    "NamedOutputsResultShape",
    "ObjectInfoState",
    "ObjectSemanticId",
    "ObjectSemanticSnapshot",
    "RuntimeResultShape",
    "SemanticResultShape",
    "StructuralArrayId",
    "StructuralArrayItem",
    "StructuralArrayRef",
    "StructuralArraySnapshot",
    "StructuralArrayState",
    "StructuralBindingKind",
    "StructuralBindingSymbol",
    "StructuralLeafBinding",
    "StructuralRuntimeLeaf",
    "TupleResultShape",
]

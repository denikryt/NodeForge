"""Detached compiler-owned storage for package-defined semantic values."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping

from .callable_contracts import canonicalize_group_input_default, source_argument_type_matches
from .compile_time import ConstVector
from .errors import CompileError
from .extension_contracts import (
    ExtensionTypeId,
    PythonScalarKind,
    TypeSpec,
)
from .extension_semantic_api import RuntimeRef
from .nf_types import NFType


@dataclass(frozen=True)
class ExtensionDependencySlot:
    """Reference one expression-local runtime dependency from detached semantic storage."""

    dependency_index: int
    typ: NFType

    def __post_init__(self) -> None:
        """Require a non-negative dependency index and canonical actual runtime type."""
        if not isinstance(self.dependency_index, int) or isinstance(self.dependency_index, bool) or self.dependency_index < 0:
            raise ValueError("ExtensionDependencySlot.dependency_index must be a non-negative integer")
        if not isinstance(self.typ, NFType):
            raise TypeError("ExtensionDependencySlot.typ must be an NFType")


@dataclass(frozen=True)
class ExtensionValue:
    """Store one detached nominal package record using compiler-owned schema storage."""

    type_id: ExtensionTypeId
    storage: object

    def __post_init__(self) -> None:
        """Require canonical nominal type identity."""
        if not isinstance(self.type_id, ExtensionTypeId):
            raise TypeError("ExtensionValue.type_id must be an ExtensionTypeId")


def static_nf_type(value: object) -> NFType:
    """Infer one detached compile-time NodeForge type for extension contract handling."""
    if type(value) is bool:
        return NFType.BOOL
    if type(value) is int:
        canonicalize_group_input_default(NFType.INT, value)
        return NFType.INT
    if type(value) is float:
        canonicalize_group_input_default(NFType.FLOAT, value)
        return NFType.FLOAT
    if type(value) is str:
        return NFType.STRING
    if isinstance(value, ConstVector):
        canonicalize_group_input_default(NFType.VECTOR, value)
        return NFType.VECTOR
    if isinstance(value, (tuple, list)):
        canonicalize_group_input_default(NFType.VECTOR, value)
        return NFType.VECTOR
    raise CompileError(f"Unsupported detached NodeForge value in extension contract: {value!r}")


def select_declared_nf_type(spec: TypeSpec, actual: NFType) -> NFType:
    """Select the unique declared NF type compatible with an actual runtime/static type."""
    if spec.kind != "NF_SET":
        raise CompileError("Internal error: NF compatibility requested for non-NF TypeSpec")
    if actual in spec.nf_types:
        return actual
    compatible = [
        expected
        for expected in sorted(spec.nf_types, key=lambda item: item.value)
        if source_argument_type_matches(expected, actual)
    ]
    if len(compatible) != 1:
        raise CompileError(
            f"Extension semantic value type {actual.value} is not uniquely accepted by "
            f"{sorted(item.value for item in spec.nf_types)}"
        )
    return compatible[0]


def type_spec_accepts_nf(spec: TypeSpec, actual: NFType, *, exact: bool = False) -> bool:
    """Return whether one NF_SET accepts the actual type under canonical source compatibility."""
    if spec.kind != "NF_SET":
        return False
    if exact:
        return actual in spec.nf_types
    return any(source_argument_type_matches(expected, actual) for expected in spec.nf_types)


def _canonical_static_nf(spec: TypeSpec, value: object) -> object:
    """Canonicalize one static NodeForge value according to its selected declared NF type."""
    actual = static_nf_type(value)
    selected = select_declared_nf_type(spec, actual)
    canonical = canonicalize_group_input_default(selected, value)
    if selected is NFType.VECTOR:
        return ConstVector(canonical)
    return canonical


def _require_python_scalar(kind: PythonScalarKind, value: object) -> object:
    """Validate and return one exact detached Python package-data scalar."""
    expected = {
        PythonScalarKind.BOOL: bool,
        PythonScalarKind.INT: int,
        PythonScalarKind.FLOAT: float,
        PythonScalarKind.STRING: str,
    }[kind]
    if type(value) is not expected:
        raise CompileError(f"Extension semantic field requires exact Python {expected.__name__}")
    return value


@contextmanager
def _guard_semantic_cycle(value: object, active_ids: set[int]) -> Iterator[None]:
    """Mark one container as active for recursive packing and always release it."""
    identity = id(value)
    if identity in active_ids:
        raise CompileError("Extension semantic value contains a cycle")
    active_ids.add(identity)
    try:
        yield
    finally:
        active_ids.remove(identity)


def pack_value(
    spec: TypeSpec,
    value: object,
    *,
    registry,
    active_runtime_refs: Mapping[object, tuple[int, NFType]],
    active_ids: set[int] | None = None,
) -> object:
    """Pack one package value recursively into detached compiler-owned semantic storage."""
    if active_ids is None:
        active_ids = set()
    if spec.kind == "OPTIONAL":
        return None if value is None else pack_value(
            spec.item,
            value,
            registry=registry,
            active_runtime_refs=active_runtime_refs,
            active_ids=active_ids,
        )
    if spec.kind == "PY_SCALAR":
        return _require_python_scalar(spec.python_scalar_kind, value)
    if spec.kind == "NF_SET":
        if isinstance(value, RuntimeRef):
            source = active_runtime_refs.get(value._token)
            if source is None or source[1] is not value.typ:
                raise CompileError("RuntimeRef is stale, fabricated, or belongs to another semantic invocation")
            dependency_index, actual = source
            select_declared_nf_type(spec, actual)
            return ExtensionDependencySlot(dependency_index, actual)
        return _canonical_static_nf(spec, value)
    if spec.kind == "RECORD":
        concrete = registry.type_spec_for_instance(value, owner=spec.record_type.owner)
        if not registry.is_nominal_subtype(concrete.id, spec.record_type):
            raise CompileError(
                f"Extension semantic record {concrete.id.name!r} is not a subtype of {spec.record_type.name!r}"
            )
        with _guard_semantic_cycle(value, active_ids):
            stored_fields = tuple(
                pack_value(
                    field_spec,
                    getattr(value, field_name),
                    registry=registry,
                    active_runtime_refs=active_runtime_refs,
                    active_ids=active_ids,
                )
                for field_name, field_spec in concrete.fields
            )
        return ExtensionValue(concrete.id, stored_fields)
    if spec.kind == "LIST":
        if type(value) is not list:
            raise CompileError("Extension semantic LIST value must be a Python list")
        with _guard_semantic_cycle(value, active_ids):
            return tuple(
                pack_value(spec.item, item, registry=registry, active_runtime_refs=active_runtime_refs, active_ids=active_ids)
                for item in value
            )
    if spec.kind == "TUPLE_FIXED":
        if type(value) is not tuple or len(value) != len(spec.items):
            raise CompileError(f"Extension semantic tuple must contain exactly {len(spec.items)} items")
        with _guard_semantic_cycle(value, active_ids):
            return tuple(
                pack_value(item_spec, item, registry=registry, active_runtime_refs=active_runtime_refs, active_ids=active_ids)
                for item_spec, item in zip(spec.items, value)
            )
    if spec.kind == "TUPLE_VAR":
        if type(value) is not tuple:
            raise CompileError("Extension semantic variadic tuple value must be a Python tuple")
        with _guard_semantic_cycle(value, active_ids):
            return tuple(
                pack_value(spec.item, item, registry=registry, active_runtime_refs=active_runtime_refs, active_ids=active_ids)
                for item in value
            )
    if spec.kind == "DICT_STR":
        if type(value) is not dict or any(type(key) is not str for key in value):
            raise CompileError("Extension semantic DICT_STR value must be dict[str, T]")
        with _guard_semantic_cycle(value, active_ids):
            return tuple(
                (
                    key,
                    pack_value(spec.item, item, registry=registry, active_runtime_refs=active_runtime_refs, active_ids=active_ids),
                )
                for key, item in value.items()
            )
    raise CompileError(f"Internal error: unsupported extension TypeSpec kind {spec.kind!r}")


def unpack_value(
    spec: TypeSpec,
    stored: object,
    *,
    registry,
    runtime_leaf_resolver: Callable[[object], tuple[NFType, object] | None],
) -> object:
    """Reconstruct one fresh package-facing value from detached semantic storage.

    ``runtime_leaf_resolver`` recognizes the lifecycle-specific runtime reference
    stored at an ``NF_SET`` leaf and returns its actual NFType together with the
    package-facing replacement. Returning ``None`` means the stored leaf is a
    detached static NodeForge value.
    """
    if spec.kind == "OPTIONAL":
        return None if stored is None else unpack_value(
            spec.item, stored, registry=registry, runtime_leaf_resolver=runtime_leaf_resolver
        )
    if spec.kind == "PY_SCALAR":
        return _require_python_scalar(spec.python_scalar_kind, stored)
    if spec.kind == "NF_SET":
        runtime_leaf = runtime_leaf_resolver(stored)
        if runtime_leaf is not None:
            actual_type, value = runtime_leaf
            if not isinstance(actual_type, NFType):
                raise TypeError("runtime_leaf_resolver must return an NFType for runtime leaves")
            select_declared_nf_type(spec, actual_type)
            return value
        # Revalidate detached static state on reconstruction.
        select_declared_nf_type(spec, static_nf_type(stored))
        return stored
    if spec.kind == "RECORD":
        if not isinstance(stored, ExtensionValue):
            raise CompileError("Extension semantic RECORD storage is malformed")
        concrete = registry.type_spec(stored.type_id)
        if not registry.is_nominal_subtype(concrete.id, spec.record_type):
            raise CompileError("Extension semantic record storage violates nominal TypeSpec")
        if not isinstance(stored.storage, tuple) or len(stored.storage) != len(concrete.fields):
            raise CompileError("Extension semantic record storage has invalid field shape")
        cls = registry.python_class_for(concrete.id)
        instance = object.__new__(cls)
        for (field_name, field_spec), field_value in zip(concrete.fields, stored.storage):
            object.__setattr__(
                instance,
                field_name,
                unpack_value(field_spec, field_value, registry=registry, runtime_leaf_resolver=runtime_leaf_resolver),
            )
        return instance
    if spec.kind == "LIST":
        if not isinstance(stored, tuple):
            raise CompileError("Extension semantic LIST storage is malformed")
        return [unpack_value(spec.item, item, registry=registry, runtime_leaf_resolver=runtime_leaf_resolver) for item in stored]
    if spec.kind == "TUPLE_FIXED":
        if not isinstance(stored, tuple) or len(stored) != len(spec.items):
            raise CompileError("Extension semantic fixed tuple storage is malformed")
        return tuple(
            unpack_value(item_spec, item, registry=registry, runtime_leaf_resolver=runtime_leaf_resolver)
            for item_spec, item in zip(spec.items, stored)
        )
    if spec.kind == "TUPLE_VAR":
        if not isinstance(stored, tuple):
            raise CompileError("Extension semantic variadic tuple storage is malformed")
        return tuple(unpack_value(spec.item, item, registry=registry, runtime_leaf_resolver=runtime_leaf_resolver) for item in stored)
    if spec.kind == "DICT_STR":
        if not isinstance(stored, tuple):
            raise CompileError("Extension semantic dict storage is malformed")
        result = {}
        for pair in stored:
            if not isinstance(pair, tuple) or len(pair) != 2 or type(pair[0]) is not str:
                raise CompileError("Extension semantic dict storage entry is malformed")
            key, item = pair
            result[key] = unpack_value(spec.item, item, registry=registry, runtime_leaf_resolver=runtime_leaf_resolver)
        return result
    raise CompileError(f"Internal error: unsupported extension TypeSpec kind {spec.kind!r}")


def remap_dependency_slots(value: object, index_map: Mapping[int, int]) -> object:
    """Return detached storage with every dependency slot remapped to a merged source table."""
    if isinstance(value, ExtensionDependencySlot):
        try:
            new_index = index_map[value.dependency_index]
        except KeyError as exc:
            raise CompileError("Internal error: semantic dependency remap is incomplete") from exc
        return ExtensionDependencySlot(new_index, value.typ)
    if isinstance(value, ExtensionValue):
        return ExtensionValue(value.type_id, remap_dependency_slots(value.storage, index_map))
    if isinstance(value, tuple):
        return tuple(remap_dependency_slots(item, index_map) for item in value)
    return value


__all__ = [
    "ExtensionDependencySlot",
    "ExtensionValue",
    "pack_value",
    "select_declared_nf_type",
    "static_nf_type",
    "remap_dependency_slots",
    "type_spec_accepts_nf",
    "unpack_value",
]

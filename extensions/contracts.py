"""Blender-independent contracts for declarative Python extensions."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from ..evaluation_modes import EvaluationMode
from ..nf_types import NFType
from ..result_keys import validate_named_result_key


@dataclass(frozen=True)
class ExtensionCallableId:
    """Identify one public extension callable independently from Python implementation layout."""

    owner: tuple[str, ...]
    name: str

    def __post_init__(self) -> None:
        """Validate the canonical owner tuple and public callable name."""
        object.__setattr__(self, "owner", tuple(self.owner))
        if not self.owner or not all(isinstance(part, str) and part for part in self.owner):
            raise ValueError("extension owner must contain non-empty string parts")
        if not isinstance(self.name, str) or not self.name or not self.name.isidentifier() or self.name.startswith("_"):
            raise ValueError("extension callable name must be a public identifier")


@dataclass(frozen=True)
class ExtensionTypeId:
    """Identify one owner-scoped package semantic record independent of Python class identity."""

    owner: tuple[str, ...]
    name: str

    def __post_init__(self) -> None:
        """Validate the canonical owner tuple and record identifier."""
        object.__setattr__(self, "owner", tuple(self.owner))
        if not self.owner or not all(isinstance(part, str) and part for part in self.owner):
            raise ValueError("extension type owner must contain non-empty string parts")
        if not isinstance(self.name, str) or not self.name or not self.name.isidentifier():
            raise ValueError("extension type name must be a Python identifier")


@dataclass(frozen=True)
class ExtensionImplementationRef:
    """Store one symbolic owner-relative physical implementation target."""

    module: str
    attribute: str

    def __post_init__(self) -> None:
        """Validate the normalized symbolic reference shape."""
        if not isinstance(self.module, str) or not self.module.startswith("."):
            raise ValueError("extension implementation module must be owner-relative")
        parts = self.module[1:].split(".")
        if not parts or any(not part or not part.isidentifier() for part in parts):
            raise ValueError("extension implementation module must contain Python identifiers")
        if not isinstance(self.attribute, str) or not self.attribute.isidentifier():
            raise ValueError("extension implementation attribute must be one Python identifier")


class PythonScalarKind(str, Enum):
    """Canonical detached Python scalar kinds allowed inside package semantic state."""

    BOOL = "BOOL"
    INT = "INT"
    FLOAT = "FLOAT"
    STRING = "STRING"


TypeSpecKind = Literal[
    "NF_SET",
    "DYNAMIC_RESULTS",
    "NAMED_RESULTS",
    "PY_SCALAR",
    "RECORD",
    "LIST",
    "TUPLE_FIXED",
    "TUPLE_VAR",
    "DICT_STR",
    "OPTIONAL",
]


@dataclass(frozen=True)
class TypeSpec:
    """Describe one canonical recursive extension type independent from Python typing objects."""

    kind: TypeSpecKind
    nf_types: frozenset[NFType] = frozenset()
    items: tuple["TypeSpec", ...] = ()
    python_scalar_kind: PythonScalarKind | None = None
    record_type: ExtensionTypeId | None = None
    item: "TypeSpec | None" = None
    named_items: tuple[tuple[str, NFType], ...] = ()

    def __post_init__(self) -> None:
        """Freeze recursive members and reject malformed canonical specifications."""
        object.__setattr__(self, "nf_types", frozenset(self.nf_types))
        object.__setattr__(self, "items", tuple(self.items))
        allowed = {
            "NF_SET", "DYNAMIC_RESULTS", "NAMED_RESULTS", "PY_SCALAR", "RECORD", "LIST",
            "TUPLE_FIXED", "TUPLE_VAR", "DICT_STR", "OPTIONAL",
        }
        if self.kind not in allowed:
            raise ValueError("unsupported extension TypeSpec kind")
        if not all(isinstance(value, NFType) for value in self.nf_types):
            raise TypeError("TypeSpec.nf_types must contain NFType members")
        if not all(isinstance(value, TypeSpec) for value in self.items):
            raise TypeError("TypeSpec.items must contain TypeSpec records")
        if self.python_scalar_kind is not None and not isinstance(self.python_scalar_kind, PythonScalarKind):
            raise TypeError("TypeSpec.python_scalar_kind must be PythonScalarKind or None")
        if self.record_type is not None and not isinstance(self.record_type, ExtensionTypeId):
            raise TypeError("TypeSpec.record_type must be ExtensionTypeId or None")
        if self.item is not None and not isinstance(self.item, TypeSpec):
            raise TypeError("TypeSpec.item must be TypeSpec or None")

        object.__setattr__(self, "named_items", tuple(self.named_items))
        if any(member.kind == "NAMED_RESULTS" for member in self.items) or (
                self.item is not None and self.item.kind == "NAMED_RESULTS"):
            raise ValueError("Named results cannot be nested in a TypeSpec")
        if self.kind != "NAMED_RESULTS" and self.named_items:
            raise ValueError("Only NAMED_RESULTS may carry named_items")
        populated = {
            "nf": bool(self.nf_types),
            "items": bool(self.items),
            "scalar": self.python_scalar_kind is not None,
            "record": self.record_type is not None,
            "item": self.item is not None,
        }
        if self.kind == "NAMED_RESULTS":
            if any(populated.values()) or not self.named_items:
                raise ValueError("NAMED_RESULTS requires only nonempty named_items")
            names = set()
            for row in self.named_items:
                if not isinstance(row, tuple) or len(row) != 2:
                    raise TypeError("NAMED_RESULTS items must be (name, NFType) pairs")
                name, typ = row
                validate_named_result_key(name)
                if name in names or type(typ) is not NFType:
                    raise ValueError("NAMED_RESULTS requires unique keys and exact NFTypes")
                names.add(name)
        elif self.kind == "DYNAMIC_RESULTS":
            if any(populated.values()):
                raise ValueError("DYNAMIC_RESULTS must carry no payload")
        elif self.kind == "NF_SET":
            if not populated["nf"] or any(populated[key] for key in ("items", "scalar", "record", "item")):
                raise ValueError("NF_SET requires one or more NFTypes and no other payload")
        elif self.kind == "PY_SCALAR":
            if not populated["scalar"] or any(populated[key] for key in ("nf", "items", "record", "item")):
                raise ValueError("PY_SCALAR requires exactly one PythonScalarKind")
        elif self.kind == "RECORD":
            if not populated["record"] or any(populated[key] for key in ("nf", "items", "scalar", "item")):
                raise ValueError("RECORD requires exactly one ExtensionTypeId")
        elif self.kind == "TUPLE_FIXED":
            if not populated["items"] or any(populated[key] for key in ("nf", "scalar", "record", "item")):
                raise ValueError("TUPLE_FIXED requires one or more items")
        else:
            if not populated["item"] or any(populated[key] for key in ("nf", "items", "scalar", "record")):
                raise ValueError(f"{self.kind} requires exactly one item TypeSpec")


@dataclass(frozen=True)
class ExtensionTypeSpec:
    """Store one normalized same-owner nominal package record schema."""

    id: ExtensionTypeId
    fields: tuple[tuple[str, TypeSpec], ...]
    bases: tuple[ExtensionTypeId, ...]
    is_public: bool

    def __post_init__(self) -> None:
        """Freeze ordered fields/bases and validate detached schema members."""
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "bases", tuple(self.bases))
        if not isinstance(self.id, ExtensionTypeId):
            raise TypeError("ExtensionTypeSpec.id must be ExtensionTypeId")
        names: list[str] = []
        for field in self.fields:
            if not isinstance(field, tuple) or len(field) != 2:
                raise TypeError("ExtensionTypeSpec.fields must contain (name, TypeSpec) pairs")
            name, spec = field
            if not isinstance(name, str) or not name.isidentifier():
                raise ValueError("ExtensionTypeSpec field names must be Python identifiers")
            if not isinstance(spec, TypeSpec):
                raise TypeError("ExtensionTypeSpec field types must be TypeSpec records")
            if spec.kind == "NAMED_RESULTS":
                raise ValueError("Named results cannot be semantic record fields")
            names.append(name)
        if len(names) != len(set(names)):
            raise ValueError("ExtensionTypeSpec field names must be unique")
        if not all(isinstance(base, ExtensionTypeId) for base in self.bases):
            raise TypeError("ExtensionTypeSpec.bases must contain ExtensionTypeId records")
        if any(base.owner != self.id.owner for base in self.bases):
            raise ValueError("ExtensionTypeSpec bases must belong to the same owner")
        if not isinstance(self.is_public, bool):
            raise TypeError("ExtensionTypeSpec.is_public must be bool")


def is_frontend_semantic_type_spec(spec: TypeSpec) -> bool:
    """Return whether *spec* denotes a frontend semantic record/list public value."""
    if spec.kind == "RECORD":
        return True
    return spec.kind == "LIST" and is_frontend_semantic_type_spec(spec.item)


def is_executable_result_type_spec(spec: TypeSpec) -> bool:
    """Return whether *spec* is an executable public result shape from the backend-only declarative extension contract."""
    if spec.kind in {"DYNAMIC_RESULTS", "NAMED_RESULTS"}:
        return True
    if spec.kind == "NF_SET":
        return len(spec.nf_types) == 1
    if spec.kind == "TUPLE_FIXED":
        return bool(spec.items) and all(item.kind == "NF_SET" and len(item.nf_types) == 1 for item in spec.items)
    return False



@dataclass(frozen=True)
class ExtensionParameterSpec:
    """Describe one normalized public Python parameter for an extension callable."""

    name: str
    kind: object
    type_spec: TypeSpec
    evaluation_mode: EvaluationMode | None
    default: object = inspect.Parameter.empty
    default_type: NFType | None = None

    def __post_init__(self) -> None:
        """Validate the public parameter contract and optional detached default."""
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("extension parameter name must be non-empty")
        if self.kind not in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.VAR_KEYWORD,
        }:
            raise TypeError("extension parameter kind must be inspect.Parameter.kind")
        if not isinstance(self.type_spec, TypeSpec):
            raise TypeError("extension parameter type_spec must be TypeSpec")
        if self.type_spec.kind == "NF_SET":
            if not isinstance(self.evaluation_mode, EvaluationMode):
                raise TypeError("direct NF_SET extension parameter requires EvaluationMode")
        elif is_frontend_semantic_type_spec(self.type_spec):
            if self.evaluation_mode is not None:
                raise ValueError("semantic RECORD/LIST extension parameter cannot carry EvaluationMode")
        else:
            raise TypeError("public extension parameters support only NF_SET or semantic RECORD/LIST")
        if self.kind is inspect.Parameter.VAR_KEYWORD:
            raise ValueError("extension parameters cannot use **kwargs")
        if self.kind is inspect.Parameter.VAR_POSITIONAL and self.default is not inspect.Parameter.empty:
            raise ValueError("variadic extension parameter cannot declare a default")
        if self.default is inspect.Parameter.empty or self.default is None:
            if self.default_type is not None:
                raise ValueError("extension parameter without concrete default cannot carry default_type")
            if self.default is None and self.type_spec.kind != "NF_SET":
                raise ValueError("semantic RECORD/LIST extension parameters cannot declare defaults")
        else:
            if self.type_spec.kind != "NF_SET":
                raise ValueError("semantic RECORD/LIST extension parameters cannot declare defaults")
            if self.evaluation_mode is EvaluationMode.RUNTIME_ONLY:
                raise ValueError("RUNTIME_ONLY extension parameter cannot declare a concrete default")
            if not isinstance(self.default_type, NFType):
                raise TypeError("extension parameter concrete default requires canonical NFType")
            if self.default_type not in self.type_spec.nf_types:
                raise ValueError("extension parameter default NFType is not declared")


@dataclass(frozen=True)
class ExtensionCallableSpec:
    """Describe one normalized semantic alternative for an extension callable family."""

    id: ExtensionCallableId
    parameters: tuple[ExtensionParameterSpec, ...]
    result: TypeSpec

    def __post_init__(self) -> None:
        """Freeze ordered parameters and validate supported public result shape."""
        object.__setattr__(self, "parameters", tuple(self.parameters))
        if not isinstance(self.id, ExtensionCallableId):
            raise TypeError("ExtensionCallableSpec.id must be ExtensionCallableId")
        if not all(isinstance(item, ExtensionParameterSpec) for item in self.parameters):
            raise TypeError("ExtensionCallableSpec.parameters must contain ExtensionParameterSpec records")
        if not isinstance(self.result, TypeSpec):
            raise TypeError("ExtensionCallableSpec.result must be TypeSpec")
        if self.result.kind == "NF_SET" and len(self.result.nf_types) != 1:
            raise ValueError("extension result NF_SET must contain exactly one NFType")
        if self.result.kind == "TUPLE_FIXED" and any(
            item.kind != "NF_SET" or len(item.nf_types) != 1 for item in self.result.items
        ):
            raise ValueError("extension fixed tuple result leaves must each contain exactly one NFType")
        if not (is_executable_result_type_spec(self.result) or is_frontend_semantic_type_spec(self.result)):
            raise ValueError("extension public result must be executable NF value/tuple or semantic RECORD/LIST")

    def python_signature(self) -> inspect.Signature:
        """Derive the canonical Python invocation signature for this callable alternative."""
        return inspect.Signature(
            inspect.Parameter(parameter.name, parameter.kind, default=parameter.default)
            for parameter in self.parameters
        )


def validate_extension_argument_positions(positions) -> None:
    """Validate canonical fixed/variadic positions carried across extension call layers."""
    combined = tuple(positions)
    if any(position is None for position, _variadic in combined):
        raise ValueError("extension operands require parameter positions")
    if len(combined) != len(set(combined)):
        raise ValueError("extension parameter/variadic positions must be unique")
    by_parameter: dict[int, list[int | None]] = {}
    for position, variadic in combined:
        by_parameter.setdefault(int(position), []).append(variadic)
    for values in by_parameter.values():
        if any(value is None for value in values):
            if values != [None]:
                raise ValueError("fixed extension parameter position may appear only once")
        else:
            ordered = sorted(int(value) for value in values)
            if ordered != list(range(len(ordered))):
                raise ValueError("extension variadic indices must be contiguous from zero")


__all__ = [
    "ExtensionCallableId",
    "ExtensionCallableSpec",
    "ExtensionImplementationRef",
    "ExtensionParameterSpec",
    "ExtensionTypeId",
    "ExtensionTypeSpec",
    "PythonScalarKind",
    "TypeSpec",
    "TypeSpecKind",
    "is_executable_result_type_spec",
    "is_frontend_semantic_type_spec",
    "validate_extension_argument_positions",
]

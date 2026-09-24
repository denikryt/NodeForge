"""Blender-independent contracts for declarative Python extensions."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Literal

from .evaluation_modes import EvaluationMode
from .nf_types import NFType


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


TypeSpecKind = Literal["NF_SET", "TUPLE_FIXED"]


@dataclass(frozen=True)
class TypeSpec:
    """Describe the deliberately small executable extension type grammar."""

    kind: TypeSpecKind
    nf_types: frozenset[NFType] = frozenset()
    items: tuple["TypeSpec", ...] = ()

    def __post_init__(self) -> None:
        """Freeze members and reject structurally malformed type specifications."""
        object.__setattr__(self, "nf_types", frozenset(self.nf_types))
        object.__setattr__(self, "items", tuple(self.items))
        if self.kind not in {"NF_SET", "TUPLE_FIXED"}:
            raise ValueError("unsupported extension TypeSpec kind")
        if not all(isinstance(item, NFType) for item in self.nf_types):
            raise TypeError("TypeSpec.nf_types must contain NFType members")
        if not all(isinstance(item, TypeSpec) for item in self.items):
            raise TypeError("TypeSpec.items must contain TypeSpec records")
        if self.kind == "NF_SET":
            if not self.nf_types or self.items:
                raise ValueError("NF_SET requires one or more NFTypes and no items")
        elif self.nf_types or not self.items:
            raise ValueError("TUPLE_FIXED requires items and no direct NFTypes")


@dataclass(frozen=True)
class ExtensionParameterSpec:
    """Describe one normalized public Python parameter for an extension callable."""

    name: str
    kind: object
    type_spec: TypeSpec
    evaluation_mode: EvaluationMode
    default: object = inspect.Parameter.empty
    default_type: NFType | None = None

    def __post_init__(self) -> None:
        """Validate the direct-NF parameter contract and optional detached default."""
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
        if not isinstance(self.type_spec, TypeSpec) or self.type_spec.kind != "NF_SET":
            raise TypeError("extension parameters require direct NF_SET TypeSpec")
        if not isinstance(self.evaluation_mode, EvaluationMode):
            raise TypeError("extension parameter evaluation_mode must be EvaluationMode")
        if self.default is inspect.Parameter.empty:
            if self.default_type is not None:
                raise ValueError("extension parameter without default cannot carry default_type")
        elif not isinstance(self.default_type, NFType):
            raise TypeError("extension parameter default requires canonical NFType")


@dataclass(frozen=True)
class ExtensionCallableSpec:
    """Describe one normalized semantic alternative for an extension callable family."""

    id: ExtensionCallableId
    parameters: tuple[ExtensionParameterSpec, ...]
    result: TypeSpec

    def __post_init__(self) -> None:
        """Freeze ordered parameters and validate exact executable result shape."""
        object.__setattr__(self, "parameters", tuple(self.parameters))
        if not isinstance(self.id, ExtensionCallableId):
            raise TypeError("ExtensionCallableSpec.id must be ExtensionCallableId")
        if not all(isinstance(item, ExtensionParameterSpec) for item in self.parameters):
            raise TypeError("ExtensionCallableSpec.parameters must contain ExtensionParameterSpec records")
        if not isinstance(self.result, TypeSpec):
            raise TypeError("ExtensionCallableSpec.result must be TypeSpec")
        if self.result.kind == "NF_SET":
            if len(self.result.nf_types) != 1:
                raise ValueError("extension result NF_SET must contain exactly one NFType")
        else:
            if any(item.kind != "NF_SET" or len(item.nf_types) != 1 for item in self.result.items):
                raise ValueError("extension fixed tuple result leaves must be singleton NF_SET")

    def python_signature(self) -> inspect.Signature:
        """Derive the canonical Python invocation signature for this callable alternative."""
        return inspect.Signature(
            inspect.Parameter(
                parameter.name,
                parameter.kind,
                default=parameter.default,
            )
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
    "TypeSpec",
    "TypeSpecKind",
    "validate_extension_argument_positions",
]

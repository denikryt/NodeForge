"""Canonical compiler-owned identities used across NodeForge compiler phases.

These records separate in-memory compiler identity from source spelling and
Blender persistence. ``BindingId`` and ``CallSiteId`` are compilation-local
scoped identities, while ``FunctionId.stable_key()`` preserves the existing
durable JSON identity contract for reusable function groups.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeAlias

CORE_PACKAGE_ID = "__nodeforge_core__"
_LOCAL_FUNCTION_KIND = "LOCAL_DEF"
_LIBRARY_FUNCTION_KIND = "LIBRARY"


def _canonical_json(value) -> str:
    """Serialize *value* with the existing deterministic metadata JSON rules."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_library_package_id(package_id: str | None) -> str:
    """Return the durable package identifier used by imported function identity."""
    return str(package_id or CORE_PACKAGE_ID)


@dataclass(frozen=True)
class BindingId:
    """Identify one mutable runtime binding slot within a compiler owner scope."""

    owner_scope: str
    local_id: int

    def __post_init__(self):
        """Reject invalid local binding indices."""
        if not isinstance(self.local_id, int) or isinstance(self.local_id, bool) or self.local_id < 0:
            raise ValueError("BindingId.local_id must be a non-negative integer")


@dataclass(frozen=True)
class InputDeclarationId:
    """Identify one direct explicit-input declaration across recompilations."""

    owner_scope: str
    target_name: str
    declaration_ordinal: int

    def __post_init__(self):
        """Reject malformed owner/target identities and declaration ordinals."""
        if not isinstance(self.owner_scope, str) or not self.owner_scope:
            raise ValueError("InputDeclarationId.owner_scope must be a non-empty string")
        if not isinstance(self.target_name, str) or not self.target_name:
            raise ValueError("InputDeclarationId.target_name must be a non-empty string")
        if (
            not isinstance(self.declaration_ordinal, int)
            or isinstance(self.declaration_ordinal, bool)
            or self.declaration_ordinal < 0
        ):
            raise ValueError("InputDeclarationId.declaration_ordinal must be a non-negative integer")

    def stable_key(self) -> str:
        """Return deterministic persisted identity independent of display metadata."""
        return _canonical_json({
            "owner_scope": self.owner_scope,
            "target_name": self.target_name,
            "declaration_ordinal": self.declaration_ordinal,
        })


InterfaceInputOrigin: TypeAlias = BindingId | InputDeclarationId


@dataclass(frozen=True)
class FunctionId:
    """Identify one reusable local or imported function specialization."""

    kind: str
    definition_owner: str = ""
    namespace: str = ""
    package_id: str = ""
    name: str = ""
    signature: str = ""

    def __post_init__(self):
        """Reject mixed field combinations outside the supported identity forms."""
        if self.kind == _LOCAL_FUNCTION_KIND:
            if not self.definition_owner or not self.name or self.namespace or self.package_id:
                raise ValueError("Invalid local FunctionId")
            return
        if self.kind == _LIBRARY_FUNCTION_KIND:
            if not self.namespace or not self.package_id or not self.name or self.definition_owner or self.signature:
                raise ValueError("Invalid library FunctionId")
            return
        raise ValueError(f"Unsupported FunctionId kind: {self.kind!r}")

    def stable_key(self) -> str:
        """Return the exact durable JSON identity used by existing metadata/hashes."""
        if self.kind == _LOCAL_FUNCTION_KIND:
            return _canonical_json({
                "kind": self.kind,
                "definition_owner": self.definition_owner,
                "name": self.name,
                "signature": self.signature,
            })
        return _canonical_json({
            "kind": self.kind,
            "namespace": self.namespace,
            "package_id": self.package_id,
            "name": self.name,
        })


@dataclass(frozen=True)
class CallSiteId:
    """Identify one owner-scoped reusable-function occurrence."""

    owner_scope: str
    callee: FunctionId
    ordinal: int

    def __post_init__(self):
        """Reject invalid occurrence indices or non-canonical callees."""
        if not isinstance(self.callee, FunctionId):
            raise TypeError("CallSiteId.callee must be a FunctionId")
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValueError("CallSiteId.ordinal must be a non-negative integer")


def local_function_id(definition_owner: str, name: str, signature: str) -> FunctionId:
    """Construct the canonical identity for one local-function specialization."""
    return FunctionId(
        kind=_LOCAL_FUNCTION_KIND,
        definition_owner=str(definition_owner),
        name=str(name),
        signature=str(signature),
    )


def library_function_id(namespace: str, package_id: str | None, name: str) -> FunctionId:
    """Construct the canonical identity for one reusable imported function."""
    return FunctionId(
        kind=_LIBRARY_FUNCTION_KIND,
        namespace=str(namespace),
        package_id=normalize_library_package_id(package_id),
        name=str(name),
    )


__all__ = [
    "BindingId",
    "CallSiteId",
    "CORE_PACKAGE_ID",
    "FunctionId",
    "InputDeclarationId",
    "InterfaceInputOrigin",
    "library_function_id",
    "local_function_id",
    "normalize_library_package_id",
]

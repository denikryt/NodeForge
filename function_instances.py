"""Shared ownership and freshness helpers for reusable function groups.

This module contains the compiler-level protocol used by calls that
materialize editable reusable ``GeometryNodeTree`` datablocks. It deliberately
does not resolve imports or predict dependencies. Actual local/library
materializers report canonical ``IRFunctionMaterialization`` identity and the
realized child fingerprint while normal compilation runs.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

from .errors import CompileError
from .compiler_identities import (
    CallSiteId,
    CORE_PACKAGE_ID,
)
from .semantic.ir import IRFunctionMaterialization, IRFunctionMaterializationMode

FUNCTION_INSTANCE_KEY_PROP = "nodeforge_function_instance_key"
FUNCTION_DEFINITION_OWNER_PROP = "nodeforge_function_definition_owner"
FUNCTION_INTERFACE_CONTRACT_PROP = "nodeforge_function_interface_contract"
FUNCTION_COMPILATION_FINGERPRINT_PROP = "nodeforge_function_compilation_fingerprint"
FUNCTION_ROOT_OWNER_ID_PROP = "nodeforge_function_root_owner_id"

FINGERPRINT_SCHEMA = 1
FUNCTION_COMPILER_VERSION: str | None = None
SHARED_INSTANCE_KEY = "SHARED"


def _canonical_json(value) -> str:
    """Serialize *value* deterministically for metadata and hashing."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest_payload(value) -> str:
    """Return a SHA-256 digest for a canonical JSON payload."""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def function_group_owner_scope(kind: str, *parts, instance_key: str | None = None) -> str:
    """Serialize the canonical owner scope for one physical reusable group."""

    payload = {
        "kind": str(kind),
        "parts": list(parts),
        "instance": instance_key or SHARED_INSTANCE_KEY,
    }
    return _canonical_json(payload)


def direct_library_owner_scope(namespace: str, package_id: str | None, name: str) -> str:
    """Return the established owner scope for standalone catalog materialization.

    Local catalog entries intentionally predate package-qualified reusable ownership
    and therefore keep an empty package slot. Other editable catalogs use the
    canonical package identifier carried by ``FunctionId``.
    """

    normalized_package_id = "" if namespace == "local" else str(package_id or CORE_PACKAGE_ID)
    return function_group_owner_scope(
        "LIBRARY",
        str(namespace),
        normalized_package_id,
        str(name),
    )


def instance_key_for(call_site: CallSiteId) -> str:
    """Return the existing deterministic compact key for one canonical call site."""

    return _digest_payload({
        "schema": 1,
        "owner_scope": call_site.owner_scope,
        "callee_identity": call_site.callee.stable_key(),
        "ordinal": call_site.ordinal,
    })[:32]


def instance_key_for_materialization(materialization: IRFunctionMaterialization) -> str:
    """Return the persisted instance key implied by reusable-call Semantic IR."""

    if not isinstance(materialization, IRFunctionMaterialization):
        raise TypeError("materialization must be an IRFunctionMaterialization")
    if materialization.mode is IRFunctionMaterializationMode.SHARED:
        return ""
    return instance_key_for(materialization.call_site)


def function_materialization_owner_scope(
    materialization: IRFunctionMaterialization,
) -> str:
    """Serialize reusable physical ownership from canonical materialization IR."""

    if not isinstance(materialization, IRFunctionMaterialization):
        raise TypeError("materialization must be an IRFunctionMaterialization")
    callee = materialization.callee
    instance_key = instance_key_for_materialization(materialization)
    if callee.kind == "LOCAL_DEF":
        return function_group_owner_scope(
            "LOCAL_DEF",
            callee.definition_owner,
            callee.name,
            callee.signature,
            instance_key=instance_key,
        )
    if callee.kind == "LIBRARY":
        return function_group_owner_scope(
            "LIBRARY",
            callee.namespace,
            callee.package_id,
            callee.name,
            instance_key=instance_key or None,
        )
    raise ValueError(f"Unsupported reusable FunctionId kind: {callee.kind!r}")


def new_root_owner_id() -> str:
    """Return a new lowercase UUID hex root owner id."""

    return uuid.uuid4().hex


def validate_root_owner_id(value) -> str:
    """Return a validated persistent root UUID or raise a controlled error."""

    if not isinstance(value, str) or not value:
        raise CompileError("NodeForge root owner ID is invalid")
    try:
        parsed = uuid.UUID(hex=value)
    except Exception as exc:
        raise CompileError("NodeForge root owner ID is invalid") from exc
    normalized = parsed.hex
    if normalized != value.lower():
        raise CompileError("NodeForge root owner ID is invalid")
    return normalized


def interface_contract(group) -> str:
    """Serialize ordered public sockets of one compiled group."""

    sockets = []
    for item in getattr(group.interface, "items_tree", []):
        if getattr(item, "item_type", None) != "SOCKET":
            continue
        sockets.append({
            "name": getattr(item, "name", ""),
            "in_out": getattr(item, "in_out", ""),
            "socket_type": getattr(item, "socket_type", "") or getattr(item, "bl_socket_idname", ""),
        })
    return _canonical_json({"schema": 1, "sockets": sockets})


def stored_interface_contract(group) -> str:
    """Return the persisted interface contract, or an empty string."""

    try:
        return str(group.get(FUNCTION_INTERFACE_CONTRACT_PROP) or "")
    except Exception:
        return ""


def stored_fingerprint(group) -> str:
    """Return the persisted effective compilation fingerprint, or empty."""

    try:
        return str(group.get(FUNCTION_COMPILATION_FINGERPRINT_PROP) or "")
    except Exception:
        return ""


def stamp_function_metadata(group, *, instance_key: str | None, definition_owner: str | None, fingerprint: str | None = None) -> None:
    """Persist generic function-instance metadata on a reusable group."""

    group[FUNCTION_INSTANCE_KEY_PROP] = str(instance_key or "")
    if definition_owner is not None:
        group[FUNCTION_DEFINITION_OWNER_PROP] = str(definition_owner)
    if fingerprint:
        group[FUNCTION_COMPILATION_FINGERPRINT_PROP] = str(fingerprint)
    group[FUNCTION_INTERFACE_CONTRACT_PROP] = interface_contract(group)


@dataclass
class FunctionCompilationResult:
    """Finalized freshness result for one actual reusable-group compilation."""

    fingerprint: str = ""
    freshness_unproven: bool = False


@dataclass
class FunctionCompilationFrame:
    """Mutable trace frame populated by real child materialization paths."""

    owner_identity: str
    own_inputs: dict
    child_rows: list = field(default_factory=list)
    freshness_unproven: bool = False
    result: FunctionCompilationResult | None = None

    def record_dependency_identity(self, owner_identity: str, fingerprint: str | None) -> None:
        """Record one realized dependency by its canonical physical owner identity."""
        if not isinstance(owner_identity, str) or not owner_identity:
            raise ValueError("owner_identity must be a non-empty string")
        if not fingerprint:
            self.freshness_unproven = True
            return
        self.child_rows.append({"owner": owner_identity, "fingerprint": str(fingerprint)})

    def record_dependency(
        self,
        materialization: IRFunctionMaterialization,
        fingerprint: str | None,
    ) -> None:
        """Record one reusable access through the generic identity helper."""
        if not isinstance(materialization, IRFunctionMaterialization):
            raise TypeError("materialization must be an IRFunctionMaterialization")
        self.record_dependency_identity(
            function_materialization_owner_scope(materialization),
            fingerprint,
        )

    def finish(self, contract: str) -> FunctionCompilationResult:
        """Finalize this frame's effective fingerprint."""

        if self.freshness_unproven:
            self.result = FunctionCompilationResult("", True)
            return self.result
        payload = {
            "schema": FINGERPRINT_SCHEMA,
            "owner": self.owner_identity,
            "own_inputs": self.own_inputs,
            "children": sorted(self.child_rows, key=lambda row: _canonical_json(row)),
            "interface": contract,
        }
        if FUNCTION_COMPILER_VERSION is not None:
            payload["compiler"] = FUNCTION_COMPILER_VERSION
        self.result = FunctionCompilationResult(_digest_payload(payload), False)
        return self.result


class FunctionCompilationTrace:
    """Build-local actual-compilation trace for reusable function groups."""

    def __init__(self):
        self._stack: list[FunctionCompilationFrame] = []

    @property
    def current(self) -> FunctionCompilationFrame | None:
        """Return the current active frame, if any."""

        return self._stack[-1] if self._stack else None

    @contextmanager
    def group(self, owner_identity: str, own_inputs: dict):
        """Open one actual compilation frame and guard against recursion."""

        if owner_identity in {frame.owner_identity for frame in self._stack}:
            chain = " -> ".join([frame.owner_identity for frame in self._stack] + [owner_identity])
            raise CompileError(f"Cyclic function-group dependency: {chain}")
        frame = FunctionCompilationFrame(owner_identity, dict(own_inputs or {}))
        self._stack.append(frame)
        try:
            yield frame
        finally:
            if self._stack and self._stack[-1] is frame:
                self._stack.pop()


__all__ = [
    "FUNCTION_INSTANCE_KEY_PROP",
    "FUNCTION_DEFINITION_OWNER_PROP",
    "FUNCTION_INTERFACE_CONTRACT_PROP",
    "FUNCTION_COMPILATION_FINGERPRINT_PROP",
    "FUNCTION_ROOT_OWNER_ID_PROP",
    "FunctionCompilationTrace",
    "function_group_owner_scope",
    "direct_library_owner_scope",
    "function_materialization_owner_scope",
    "instance_key_for",
    "instance_key_for_materialization",
    "new_root_owner_id",
    "validate_root_owner_id",
    "interface_contract",
    "stored_interface_contract",
    "stored_fingerprint",
    "stamp_function_metadata",
]

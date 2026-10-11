"""Shared authority eligibility for NodeForge GeometryNodeTree artifacts.

Transaction-private replacement/rollback groups carry a persistent Blender
custom property so they remain non-authoritative even if cleanup fails. Fresh
canonical groups use reload-stable process-local provisional state until the
outer build transaction commits.
"""

from __future__ import annotations

import bpy

TRANSACTION_PRIVATE_PROP = "nodeforge_transaction_private_v1"
PROVISIONAL_REGISTRY_KEY = "nodeforge.blender_group_backend.provisional_groups.v1"
BACKEND_PRIVATE_GROUP_PROPERTIES = frozenset({TRANSACTION_PRIVATE_PROP})


def _registry() -> dict[int, object]:
    """Return the reload-stable process-local provisional-group registry."""
    namespace = bpy.app.driver_namespace
    registry = namespace.get(PROVISIONAL_REGISTRY_KEY)
    if not isinstance(registry, dict):
        registry = {}
        namespace[PROVISIONAL_REGISTRY_KEY] = registry
    return registry


def _pointer(group) -> int:
    """Return one process-local lookup key for a Blender datablock."""
    try:
        return int(group.as_pointer())
    except Exception:
        return id(group)


def _is_live_exact(group) -> bool:
    """Return whether the exact RNA object still exists in node_groups."""
    if group is None:
        return False
    try:
        return any(candidate is group for candidate in bpy.data.node_groups)
    except Exception:
        return False


def mark_transaction_private(group) -> None:
    """Persistently mark a replacement/rollback group as authority-ineligible."""
    group[TRANSACTION_PRIVATE_PROP] = True


def is_transaction_private(group) -> bool:
    """Return whether *group* carries the persistent private-artifact marker."""
    try:
        return bool(group.get(TRANSACTION_PRIVATE_PROP))
    except Exception:
        return False


def mark_provisional(group) -> None:
    """Mark a fresh canonical group provisional until outer publication."""
    _registry()[_pointer(group)] = group


def is_provisional(group) -> bool:
    """Return whether the exact live group is still process-local provisional."""
    registry = _registry()
    stale = []
    for key, stored in list(registry.items()):
        if not _is_live_exact(stored):
            stale.append(key)
    for key in stale:
        registry.pop(key, None)
    key = _pointer(group)
    stored = registry.get(key)
    return stored is group and _is_live_exact(group)


def publish(groups) -> None:
    """Publish fresh groups through infallible process-local bookkeeping only."""
    registry = _registry()
    for group in groups:
        key = _pointer(group)
        if registry.get(key) is group:
            registry.pop(key, None)


def forget(group) -> None:
    """Forget one provisional group after confirmed physical removal."""
    registry = _registry()
    key = _pointer(group)
    if registry.get(key) is group:
        registry.pop(key, None)


def is_authority_ineligible_group(group) -> bool:
    """Return whether group metadata must not be accepted as ownership authority."""
    return is_transaction_private(group) or is_provisional(group)


__all__ = [
    "BACKEND_PRIVATE_GROUP_PROPERTIES",
    "PROVISIONAL_REGISTRY_KEY",
    "TRANSACTION_PRIVATE_PROP",
    "forget",
    "is_authority_ineligible_group",
    "is_provisional",
    "is_transaction_private",
    "mark_provisional",
    "mark_transaction_private",
    "publish",
]

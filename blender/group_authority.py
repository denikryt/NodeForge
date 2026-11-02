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


def find_unique_authoritative_group(
    matches_identity,
    *,
    description: str,
    selected=None,
    transaction=None,
):
    """Find exactly one eligible Blender owner for a complete canonical identity.

    This boundary is shared by automatic lookup and user-selected Reload. A
    copied ID duplicates custom properties, so neither Blender naming nor the
    selected pointer may decide which duplicate is authoritative.
    """
    from ..errors import CompileError

    matches = []
    for group in bpy.data.node_groups:
        if getattr(group, "bl_idname", None) != "GeometryNodeTree":
            continue
        if transaction is not None and hasattr(transaction, "owns_group") and transaction.owns_group(group):
            continue
        if is_authority_ineligible_group(group):
            continue
        if matches_identity(group):
            matches.append(group)
    if len(matches) > 1:
        owners = ", ".join(f"{item.name} [{item.as_pointer()}]" for item in matches)
        raise CompileError(
            f"Duplicate authoritative source owners for {description}: {owners}. "
            "Select the independent single-user copy and detach its NodeForge source ownership."
        )
    canonical = matches[0] if matches else None
    if selected is not None and canonical is not selected:
        raise CompileError(
            f"Selected node group is not the sole canonical source owner for {description}; "
            "it may be detached or belong to a different definition/specialization/instance."
        )
    return canonical


def detach_selected_source_owner(group) -> None:
    """Detach an explicitly selected copied group from canonical source lookup.

    This operation never chooses which duplicate is the user-owned copy. It
    requires the caller to supply the actual selected GeometryNodeTree and
    checks that another group still carries its same canonical source stamps.
    Only authoritative lookup attributes are removed; graph contents and
    generated-resource provenance remain untouched.
    """
    from ..errors import CompileError
    from ..function_instances import (
        FUNCTION_DEFINITION_OWNER_PROP, FUNCTION_INSTANCE_KEY_PROP,
    )

    if not _is_live_exact(group) or getattr(group, "bl_idname", None) != "GeometryNodeTree":
        raise CompileError("Select a live Geometry Node function-group copy to detach")
    if is_authority_ineligible_group(group):
        raise CompileError("Transaction-private groups cannot be source-detached")
    module_owner = str(group.get(FUNCTION_DEFINITION_OWNER_PROP) or "")
    module_identity = str(group.get("nodeforge_module_definition") or "")
    lib_namespace = str(group.get("nodeforge_library_namespace") or "")
    local_namespace = str(group.get("nodeforge_local_function_namespace") or "")
    local_kind = str(group.get("nodeforge_generated_kind") or "")
    if not module_owner and not lib_namespace and not (local_namespace and local_kind == "local_function_helper"):
        raise CompileError("Selected group has no canonical NodeForge source ownership to detach")
    if not (module_identity or lib_namespace or (local_namespace and local_kind == "local_function_helper")):
        raise CompileError("Selected group does not identify a NodeForge-managed source function")

    # Compare only source-authority identity. Fingerprints and physical Blender
    # names are deliberately excluded: even a changed user copy remains a
    # collision until the chosen copy is detached.
    keys = [FUNCTION_DEFINITION_OWNER_PROP, FUNCTION_INSTANCE_KEY_PROP]
    if module_identity:
        keys += ["nodeforge_module_definition"]
    elif lib_namespace:
        keys += ["nodeforge_library_namespace", "nodeforge_library_name",
                 "nodeforge_library_source_origin", "nodeforge_library_physical_origin",
                 "nodeforge_library_import_path", "nodeforge_package_id"]
    else:
        keys += ["nodeforge_generated_kind", "nodeforge_local_function_name",
                 "nodeforge_local_function_signature", "nodeforge_local_function_namespace"]
    identity = tuple(group.get(key) for key in keys)
    siblings = [candidate for candidate in bpy.data.node_groups
                if candidate is not group and getattr(candidate, "bl_idname", None) == "GeometryNodeTree"
                and not is_authority_ineligible_group(candidate)
                and tuple(candidate.get(key) for key in keys) == identity]
    if not siblings:
        raise CompileError(
            "Source ownership detachment requires a selected single-user copy "
            "with another eligible group retaining its canonical owner"
        )
    strip = [FUNCTION_DEFINITION_OWNER_PROP]
    if lib_namespace:
        strip.append("nodeforge_library_namespace")
    if local_namespace and local_kind == "local_function_helper":
        strip.append("nodeforge_local_function_namespace")
    stored = {key: group[key] for key in strip if key in group}
    try:
        for key in stored:
            del group[key]
    except Exception as exc:
        for key, value in stored.items():
            group[key] = value
        raise CompileError("Cannot detach source ownership from selected group") from exc


__all__ = [
    "BACKEND_PRIVATE_GROUP_PROPERTIES",
    "PROVISIONAL_REGISTRY_KEY",
    "TRANSACTION_PRIVATE_PROP",
    "forget",
    "find_unique_authoritative_group",
    "detach_selected_source_owner",
    "is_authority_ineligible_group",
    "is_provisional",
    "is_transaction_private",
    "mark_provisional",
    "mark_transaction_private",
    "publish",
]

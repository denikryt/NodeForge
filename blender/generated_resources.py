"""Core generated Blender ID ownership for NodeForge compiler backends."""

from __future__ import annotations

import json
import uuid
import warnings
from dataclasses import dataclass, field
import bpy

from .group_authority import is_authority_ineligible_group


GROUP_MANIFEST_PROP = "nodeforge_generated_resources_v1"
ID_METADATA_PROP = "nodeforge_generated_id_v1"
SCHEMA_VERSION = 1

# Same-process provenance. Copied datablocks inherit ID properties; only IDs
# that this process actually created and committed can be automatically
# retired. After reopening a .blend, ambiguous preexisting IDs are preserved.
_SESSION_COMMITTED_IDS: dict[int, tuple[object, GeneratedResourceRef]] = {}


def _physical_identity(id_obj) -> int:
    """Use Blender's stable datablock pointer, not its mutable display name."""
    return id_obj.as_pointer()


def _json_load(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except Exception:
            return None
        return data if isinstance(data, dict) else None
    if isinstance(value, dict):
        return dict(value)
    return None


def _json_dump(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))



def _id_collection(kind: str):
    if kind == "CURVE":
        return bpy.data.curves
    if kind == "MESH":
        return bpy.data.meshes
    if kind == "OBJECT":
        return bpy.data.objects
    if kind == "NODE_GROUP":
        return bpy.data.node_groups
    return None


def _kind_for_id(id_obj) -> str | None:
    if id_obj is None:
        return None
    if getattr(id_obj, "bl_rna", None) is not None:
        ident = getattr(id_obj.bl_rna, "identifier", "")
        if ident == "Curve":
            return "CURVE"
        if ident == "Mesh":
            return "MESH"
        if ident == "Object":
            return "OBJECT"
        if ident == "GeometryNodeTree":
            return "NODE_GROUP"
    try:
        if bpy.data.curves.get(id_obj.name) is id_obj:
            return "CURVE"
    except Exception:
        pass
    try:
        if bpy.data.meshes.get(id_obj.name) is id_obj:
            return "MESH"
    except Exception:
        pass
    try:
        if bpy.data.node_groups.get(id_obj.name) is id_obj:
            return "NODE_GROUP"
    except Exception:
        pass
    try:
        if bpy.data.objects.get(id_obj.name) is id_obj:
            return "OBJECT"
    except Exception:
        pass
    return None


@dataclass(frozen=True)
class GeneratedResourceRef:
    """Persistent reference to one NodeForge-generated Blender ID."""

    kind: str
    name: str
    owner_group_uuid: str
    generation_uuid: str
    role: str

    def to_dict(self):
        return {
            "kind": self.kind,
            "name": self.name,
            "owner_group_uuid": self.owner_group_uuid,
            "generation_uuid": self.generation_uuid,
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            return None
        kind = data.get("kind")
        name = data.get("name")
        owner = data.get("owner_group_uuid")
        generation = data.get("generation_uuid")
        role = data.get("role", "")
        if kind not in {"CURVE", "MESH", "OBJECT", "NODE_GROUP"} or not isinstance(name, str) or not isinstance(owner, str) or not isinstance(generation, str):
            return None
        return cls(kind=kind, name=name, owner_group_uuid=owner, generation_uuid=generation, role=str(role))


@dataclass
class GeneratedResourceTransaction:
    """Track exact generated Blender IDs created during one replacement compile."""

    owner_group_uuid: str
    generation_uuid: str = field(default_factory=lambda: uuid.uuid4().hex)
    resources: list[GeneratedResourceRef] = field(default_factory=list)
    _owned_ids: list[tuple[object, str]] = field(default_factory=list)
    committed: bool = False

    def add(self, id_obj, kind: str, role: str) -> GeneratedResourceRef:
        """Own one exact new ID before publishing generated-resource metadata."""
        if kind not in {"CURVE", "MESH", "OBJECT", "NODE_GROUP"}:
            raise ValueError("generated resource kind must be CURVE, MESH, OBJECT, or NODE_GROUP")
        if id_obj is None:
            raise ValueError("generated resource identity cannot be None")
        if any(existing is id_obj for existing, _kind in self._owned_ids):
            raise ValueError("generated resource identity is already owned by this transaction")
        self._owned_ids.append((id_obj, kind))
        ref = GeneratedResourceRef(
            kind=kind,
            name=id_obj.name,
            owner_group_uuid=self.owner_group_uuid,
            generation_uuid=self.generation_uuid,
            role=role,
        )
        _write_id_metadata(id_obj, ref)
        self.resources.append(ref)
        return ref

    def manifest(self, *, empty: bool = False):
        """Return the persisted manifest represented by this transaction."""
        return build_manifest(
            self.owner_group_uuid,
            [] if empty else [ref.to_dict() for ref in self.resources],
            generation_uuid=self.generation_uuid,
        )

    def rollback(self):
        """Best-effort remove every uncommitted exact ID in reverse creation order."""
        if self.committed:
            return
        failures = []
        failed_owned_ids = []
        for id_obj, kind in reversed(self._owned_ids):
            try:
                _remove_owned_id_exact(id_obj, kind)
            except ReferenceError:
                continue
            except Exception as exc:
                failures.append((kind, getattr(id_obj, "name", "<unknown>"), exc))
                failed_owned_ids.append((id_obj, kind))
        self._owned_ids = list(reversed(failed_owned_ids))
        self.resources.clear()
        if failures:
            summary = "; ".join(f"{kind} {name!r}: {exc}" for kind, name, exc in failures)
            raise RuntimeError(
                f"Generated-resource rollback failed for {len(failures)} ID(s): {summary}"
            ) from failures[0][2]

    def mark_committed(self):
        """Record exact committed IDs for safe same-session retirement."""
        self.committed = True
        for id_obj, kind in self._owned_ids:
            for ref in self.resources:
                if ref.kind == kind and _metadata_matches_ref(read_id_metadata(id_obj), ref):
                    _SESSION_COMMITTED_IDS[_physical_identity(id_obj)] = (id_obj, ref)
                    break


def _remove_owned_id_exact(id_obj, kind: str) -> None:
    """Remove one exact transaction-owned Blender ID without metadata lookup."""
    collection = _id_collection(kind)
    if collection is None:
        raise ValueError(f"unsupported generated resource kind {kind!r}")
    collection.remove(id_obj, do_unlink=True)


def build_manifest(owner_group_uuid: str, resources: list[dict], *, generation_uuid: str | None = None):
    return {
        "schema_version": SCHEMA_VERSION,
        "owner_group_uuid": owner_group_uuid,
        "generation_uuid": generation_uuid or uuid.uuid4().hex,
        "resources": list(resources),
    }


def read_group_manifest(group):
    try:
        return _normalize_manifest(_json_load(group.get(GROUP_MANIFEST_PROP)))
    except Exception:
        return None


def _normalize_manifest(data):
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return None
    owner = data.get("owner_group_uuid")
    if not isinstance(owner, str) or not owner:
        return None
    raw_resources = data.get("resources", [])
    if not isinstance(raw_resources, list):
        return None
    resources = []
    for item in raw_resources:
        ref = GeneratedResourceRef.from_dict(item)
        if ref is None or ref.owner_group_uuid != owner:
            return None
        resources.append(ref.to_dict())
    return build_manifest(owner, resources, generation_uuid=str(data.get("generation_uuid") or ""))


def ensure_owner_group_uuid(group) -> str:
    manifest = read_group_manifest(group)
    if manifest is not None:
        return manifest["owner_group_uuid"]
    return uuid.uuid4().hex


def write_group_manifest(group, manifest) -> None:
    group[GROUP_MANIFEST_PROP] = _json_dump(_normalize_manifest(manifest) or manifest)


def write_empty_manifest(group, owner_group_uuid: str) -> None:
    write_group_manifest(group, build_manifest(owner_group_uuid, []))


def clear_group_manifest(group) -> None:
    try:
        if GROUP_MANIFEST_PROP in group:
            del group[GROUP_MANIFEST_PROP]
    except Exception:
        pass


def manifest_resources(manifest) -> list[GeneratedResourceRef]:
    manifest = _normalize_manifest(manifest)
    if manifest is None:
        return []
    result = []
    for item in manifest.get("resources", []):
        ref = GeneratedResourceRef.from_dict(item)
        if ref is not None:
            result.append(ref)
    return result


def _write_id_metadata(id_obj, ref: GeneratedResourceRef) -> None:
    id_obj[ID_METADATA_PROP] = _json_dump({
        "schema_version": SCHEMA_VERSION,
        "kind": ref.kind,
        "name": ref.name,
        "owner_group_uuid": ref.owner_group_uuid,
        "generation_uuid": ref.generation_uuid,
        "role": ref.role,
    })


def read_id_metadata(id_obj):
    try:
        data = _json_load(id_obj.get(ID_METADATA_PROP))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return None
    ref = GeneratedResourceRef.from_dict(data)
    if ref is None:
        return None
    return ref


def _lookup_ref(ref: GeneratedResourceRef):
    collection = _id_collection(ref.kind)
    if collection is None:
        return None
    return collection.get(ref.name)


def _metadata_matches_ref(meta: GeneratedResourceRef | None, ref: GeneratedResourceRef) -> bool:
    return (
        meta is not None
        and meta.kind == ref.kind
        and meta.name == ref.name
        and meta.owner_group_uuid == ref.owner_group_uuid
        and meta.generation_uuid == ref.generation_uuid
        and meta.role == ref.role
    )


def _authoritative_object_refs(group) -> set:
    """Collect actual ObjectInfo pointers; generated labels alone are not proof."""
    result = set()
    for node in getattr(group, "nodes", ()):
        if getattr(node, "bl_idname", None) != "GeometryNodeObjectInfo":
            continue
        for sock in getattr(node, "inputs", ()):
            if getattr(sock, "name", None) == "Object" and getattr(sock, "default_value", None) is not None:
                result.add(sock.default_value)
    return result


def capture_owned_resource_ids(group, manifest):
    """Capture exact physically linked IDs before an authoritative group is changed.

    Manifest names locate candidates but never confer deletion rights: the
    original Object must be directly referenced by the authoritative group,
    and its Mesh/Curve must be reachable through that exact Object.data.
    """
    refs = manifest_resources(manifest)
    referenced_objects = _authoritative_object_refs(group)
    referenced_groups = {
        node.node_tree for node in getattr(group, "nodes", ())
        if getattr(node, "bl_idname", None) == "GeometryNodeGroup"
        and getattr(node, "node_tree", None) is not None
    }
    verified = []
    for ref in refs:
        collection = _id_collection(ref.kind)
        if collection is None:
            continue
        candidates = (obj for obj in collection if _metadata_matches_ref(read_id_metadata(obj), ref))
        for obj in candidates:
            evidence_record = _SESSION_COMMITTED_IDS.get(_physical_identity(obj))
            if (evidence_record is None or evidence_record[0] is not obj
                    or not _metadata_matches_ref(evidence_record[1], ref)):
                # A reused Blender pointer must not impersonate the exact ID
                # created by this session; copied metadata is insufficient.
                # After restart—or if a user substitutes a copied datablock—
                # the object graph does not prove this ID's creation origin.
                continue
            if ref.kind == "OBJECT":
                evidence = obj in referenced_objects
            elif ref.kind == "NODE_GROUP":
                evidence = obj in referenced_groups
            else:
                evidence = any(getattr(owner, "data", None) is obj for owner in referenced_objects)
            if evidence:
                verified.append((ref, obj))
                break
    return tuple(verified)


def _has_external_object_ref(obj, *, retired_owner_group=None) -> bool:
    """Do not delete a proxy used by a different user-authored node group."""
    for group in bpy.data.node_groups:
        if group is retired_owner_group:
            continue
        if obj in _authoritative_object_refs(group):
            return True
    return False


def _other_object_users(data_block) -> bool:
    """Any Object not physically captured as a retired proxy blocks data removal."""
    for obj in bpy.data.objects:
        try:
            if obj.data is data_block:
                return True
        except ReferenceError:
            continue
    return False


def _delete_captured_ref(ref, id_obj, *, owner_group=None) -> bool:
    """Retire a captured physical ID only if no external references remain."""
    if not _metadata_matches_ref(read_id_metadata(id_obj), ref):
        return False
    evidence = _SESSION_COMMITTED_IDS.get(_physical_identity(id_obj))
    if (evidence is None or evidence[0] is not id_obj
            or not _metadata_matches_ref(evidence[1], ref)):
        # Even a supplied captured_ids pair cannot authorize deletion if the
        # original physical ID has been replaced since capture (Undo/Redo).
        return False
    coll = _id_collection(ref.kind)
    if coll is None:
        return False
    try:
        if not any(item is id_obj for item in coll):
            return False
        if ref.kind == "OBJECT":
            if _has_external_object_ref(id_obj, retired_owner_group=owner_group):
                return False
            physical_id = _physical_identity(id_obj)
            coll.remove(id_obj, do_unlink=True)
            _SESSION_COMMITTED_IDS.pop(physical_id, None)
            return True
        if ref.kind in {"MESH", "CURVE"}:
            if _other_object_users(id_obj):
                return False
        if ref.kind == "NODE_GROUP":
            if any(getattr(mod, "node_group", None) is id_obj
                   for obj in bpy.data.objects for mod in obj.modifiers):
                return False
            if any(node.node_tree is id_obj for group in bpy.data.node_groups
                   for node in group.nodes if getattr(node, "bl_idname", None) == "GeometryNodeGroup"):
                return False
        physical_id = _physical_identity(id_obj)
        coll.remove(id_obj, do_unlink=True)
        _SESSION_COMMITTED_IDS.pop(physical_id, None)
        return True
    except ReferenceError:
        return False


def delete_generated_ref(ref: GeneratedResourceRef) -> bool:
    """Reject metadata-only deletion: caller has no authoritative ID pointer."""
    return False


def delete_generated_id_object(id_obj, *, expected_owner_group_uuid: str | None = None) -> bool:
    """Reject metadata-only deletion, even when the object retains a valid UUID."""
    return False


def cleanup_previous_after_commit(old_manifest, new_manifest, *, captured_ids=()) -> None:
    """Retire only old exact IDs captured from the authoritative group before cutover."""
    new_keys = {(r.kind, r.name, r.owner_group_uuid, r.generation_uuid) for r in manifest_resources(new_manifest)}
    eligible = [(ref, id_obj) for ref, id_obj in captured_ids
                if (ref.kind, ref.name, ref.owner_group_uuid, ref.generation_uuid) not in new_keys]
    # Object references to a data block must be removed first; a copied
    # user Object with identical metadata must never count as a retired owner.
    eligible.sort(key=lambda pair: _resource_delete_order(pair[0]))
    for ref, id_obj in eligible:
        if not _delete_captured_ref(ref, id_obj):
            warnings.warn(f"NodeForge preserved ambiguous generated {ref.kind} {ref.name!r}", RuntimeWarning, stacklevel=2)


def _resource_delete_order(ref: GeneratedResourceRef) -> int:
    """Unlink generated Object first; then Mesh/Curve; then private NodeTree."""
    return {"OBJECT": 0, "MESH": 1, "CURVE": 1, "NODE_GROUP": 2}.get(ref.kind, 3)


def live_manifest_resource_keys() -> set[tuple[str, str, str, str]]:
    keys = set()
    for group in bpy.data.node_groups:
        if getattr(group, "bl_idname", None) != "GeometryNodeTree":
            continue
        if is_authority_ineligible_group(group):
            continue
        for ref in manifest_resources(read_group_manifest(group)):
            keys.add((ref.kind, ref.name, ref.owner_group_uuid, ref.generation_uuid))
    return keys




def _can_access_blender_id_collections() -> bool:
    try:
        getattr(bpy.data, "node_groups")
        getattr(bpy.data, "objects")
        getattr(bpy.data, "curves")
        getattr(bpy.data, "meshes")
    except Exception:
        return False
    return True


def cleanup_restart_orphans_deferred():
    """Run restart-orphan cleanup immediately when possible, otherwise defer until bpy.data is unrestricted."""
    if _can_access_blender_id_collections():
        cleanup_restart_orphans()
        return None

    def _run_when_unrestricted():
        if not _can_access_blender_id_collections():
            return 0.1
        cleanup_restart_orphans()
        return None

    try:
        bpy.app.timers.register(_run_when_unrestricted, first_interval=0.0)
    except Exception:
        pass
    return None

def cleanup_restart_orphans() -> None:
    """Preserve ambiguous post-restart IDs; duplicate metadata cannot prove origin.

    Manifests remain useful for diagnostics, but an ID that has no live
    authoritative root pointer cannot be distinguished from a user duplicate
    after .blend reopening. The older metadata-only sweep was destructive.
    """
    # Lifecycle registration calls this after loading/initializing a file.
    # Clear any stale Python-side physical pointers from the previous file.
    _SESSION_COMMITTED_IDS.clear()
    live = live_manifest_resource_keys()
    suspects = []
    for kind in ("OBJECT", "CURVE", "MESH", "NODE_GROUP"):
        for obj in list(_id_collection(kind)):
            meta = read_id_metadata(obj)
            if meta is None or meta.kind != kind:
                continue
            key = (meta.kind, meta.name, meta.owner_group_uuid, meta.generation_uuid)
            if key not in live:
                suspects.append((kind, obj.name))
    if suspects:
        warnings.warn(
            f"NodeForge retained {len(suspects)} unverified generated ID(s) after restart; "
            "manual inspection is required before deletion",
            RuntimeWarning,
            stacklevel=2,
        )








__all__ = [
    "GROUP_MANIFEST_PROP", "ID_METADATA_PROP", "SCHEMA_VERSION",
    "GeneratedResourceRef", "GeneratedResourceTransaction",
    "read_group_manifest", "write_group_manifest", "write_empty_manifest", "clear_group_manifest",
    "manifest_resources", "cleanup_previous_after_commit", "cleanup_restart_orphans",
    "delete_generated_ref", "capture_owned_resource_ids",
]

"""Core generated Blender ID ownership for NodeForge compiler backends."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Iterable

import bpy

from .values import Value

GROUP_MANIFEST_PROP = "nodeforge_generated_resources_v1"
ID_METADATA_PROP = "nodeforge_generated_id_v1"
SCHEMA_VERSION = 1
_TEST_FAIL_AFTER_OBJECT_CREATE = False
_TEST_FAIL_AFTER_MESH_CREATE = False
_TEST_FAIL_AFTER_MESH_ATTRIBUTE_WRITE = False
_CLEANUP_WARNINGS: list[str] = []


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



def _record_cleanup_warning(message: str) -> None:
    _CLEANUP_WARNINGS.append(message)


def _id_collection(kind: str):
    if kind == "CURVE":
        return bpy.data.curves
    if kind == "MESH":
        return bpy.data.meshes
    if kind == "OBJECT":
        return bpy.data.objects
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
        if kind not in {"CURVE", "MESH", "OBJECT"} or not isinstance(name, str) or not isinstance(owner, str) or not isinstance(generation, str):
            return None
        return cls(kind=kind, name=name, owner_group_uuid=owner, generation_uuid=generation, role=str(role))


@dataclass
class GeneratedResourceTransaction:
    """Track generated IDs created during one replacement compile."""

    owner_group_uuid: str
    generation_uuid: str = field(default_factory=lambda: uuid.uuid4().hex)
    resources: list[GeneratedResourceRef] = field(default_factory=list)
    _ids: list[object] = field(default_factory=list)
    committed: bool = False

    def add(self, id_obj, kind: str, role: str) -> GeneratedResourceRef:
        ref = GeneratedResourceRef(
            kind=kind,
            name=id_obj.name,
            owner_group_uuid=self.owner_group_uuid,
            generation_uuid=self.generation_uuid,
            role=role,
        )
        _write_id_metadata(id_obj, ref)
        self.resources.append(ref)
        self._ids.append(id_obj)
        return ref

    def manifest(self, *, empty: bool = False):
        return build_manifest(
            self.owner_group_uuid,
            [] if empty else [ref.to_dict() for ref in self.resources],
            generation_uuid=self.generation_uuid,
        )

    def rollback(self):
        if self.committed:
            return
        for id_obj in reversed(list(self._ids)):
            delete_generated_id_object(id_obj, expected_owner_group_uuid=self.owner_group_uuid)
        self._ids.clear()
        self.resources.clear()

    def mark_committed(self):
        self.committed = True


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


def _verified_id_for_ref(ref: GeneratedResourceRef):
    id_obj = _lookup_ref(ref)
    if _metadata_matches_ref(read_id_metadata(id_obj), ref):
        return id_obj

    collection = _id_collection(ref.kind)
    if collection is None:
        return None
    matches = []
    for candidate in list(collection):
        if _metadata_matches_ref(read_id_metadata(candidate), ref):
            matches.append(candidate)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        _record_cleanup_warning(
            f"Skipped generated {ref.kind} cleanup because ownership metadata matched multiple IDs: {ref.name}"
        )
    return None


def delete_generated_ref(ref: GeneratedResourceRef) -> bool:
    id_obj = _verified_id_for_ref(ref)
    if id_obj is None:
        return False
    return delete_generated_id_object(id_obj, expected_owner_group_uuid=ref.owner_group_uuid)


def _data_block_object_users(data_block) -> list:
    """Return live Blender Objects that currently use *data_block* as their data-block."""
    users = []
    try:
        objects = list(bpy.data.objects)
    except Exception:
        return users
    for obj in objects:
        try:
            if getattr(obj, "data", None) is data_block:
                users.append(obj)
        except ReferenceError:
            continue
        except Exception:
            continue
    return users


def _has_matching_generated_object_metadata(obj, owner_group_uuid: str) -> bool:
    meta = read_id_metadata(obj)
    return meta is not None and meta.kind == "OBJECT" and meta.owner_group_uuid == owner_group_uuid


def _data_block_has_non_owned_object_users(data_block, owner_group_uuid: str) -> bool:
    for obj in _data_block_object_users(data_block):
        if not _has_matching_generated_object_metadata(obj, owner_group_uuid):
            return True
    return False


def delete_generated_id_object(id_obj, *, expected_owner_group_uuid: str | None = None) -> bool:
    meta = read_id_metadata(id_obj)
    if meta is None:
        return False
    if expected_owner_group_uuid is not None and meta.owner_group_uuid != expected_owner_group_uuid:
        return False
    kind = _kind_for_id(id_obj) or meta.kind
    try:
        if kind == "OBJECT":
            bpy.data.objects.remove(id_obj, do_unlink=True)
            return True
        if kind == "CURVE":
            # A generated Curve may be shared by user-created Objects.  Deterministic
            # names and the Curve's own metadata are not enough to prove that all
            # current users are NodeForge-owned; fail closed rather than unlinking
            # user Objects from their data-block.  Cleanup paths delete generated
            # Objects first, then re-enter this branch for the Curve.
            if _data_block_has_non_owned_object_users(id_obj, meta.owner_group_uuid):
                _record_cleanup_warning(
                    f"Skipped generated Curve deletion because non-owned Object users remain: {getattr(id_obj, 'name', '<unknown>')}"
                )
                return False
            bpy.data.curves.remove(id_obj, do_unlink=True)
            return True
        if kind == "MESH":
            if _data_block_has_non_owned_object_users(id_obj, meta.owner_group_uuid):
                _record_cleanup_warning(
                    f"Skipped generated Mesh deletion because non-owned Object users remain: {getattr(id_obj, 'name', '<unknown>')}"
                )
                return False
            bpy.data.meshes.remove(id_obj, do_unlink=True)
            return True
    except ReferenceError:
        return False
    except Exception:
        return False
    return False


def cleanup_previous_after_commit(old_manifest, new_manifest) -> None:
    """Delete old verified resources that are absent from the committed new manifest."""
    old_refs = manifest_resources(old_manifest)
    new_keys = {(r.kind, r.name, r.owner_group_uuid, r.generation_uuid) for r in manifest_resources(new_manifest)}
    # Delete objects before data-blocks so object users release Curve/Mesh data first.
    old_refs.sort(key=_resource_delete_order)
    for ref in old_refs:
        key = (ref.kind, ref.name, ref.owner_group_uuid, ref.generation_uuid)
        if key not in new_keys:
            delete_generated_ref(ref)


def _resource_delete_order(ref: GeneratedResourceRef) -> int:
    return 0 if ref.kind == "OBJECT" else 1


def live_manifest_resource_keys() -> set[tuple[str, str, str, str]]:
    keys = set()
    for group in bpy.data.node_groups:
        if getattr(group, "bl_idname", None) != "GeometryNodeTree":
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
        _record_cleanup_warning("Could not schedule generated-resource restart cleanup while bpy.data is restricted")
    return None

def cleanup_restart_orphans() -> None:
    """Remove NodeForge-owned generated IDs not referenced by live group metadata."""
    live = live_manifest_resource_keys()
    refs = []
    for obj in list(bpy.data.objects):
        ref = read_id_metadata(obj)
        if ref is not None:
            refs.append((obj, ref))
    for curve in list(bpy.data.curves):
        ref = read_id_metadata(curve)
        if ref is not None:
            refs.append((curve, ref))
    for mesh in list(bpy.data.meshes):
        ref = read_id_metadata(mesh)
        if ref is not None:
            refs.append((mesh, ref))
    # Objects first.
    refs.sort(key=lambda pair: _resource_delete_order(pair[1]))
    for id_obj, ref in refs:
        key = (ref.kind, ref.name, ref.owner_group_uuid, ref.generation_uuid)
        if key not in live:
            delete_generated_id_object(id_obj, expected_owner_group_uuid=ref.owner_group_uuid)


def cleanup_live_group_resources() -> None:
    """Remove verified generated IDs listed by live group metadata, used on unregister."""
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", None) != "GeometryNodeTree":
            continue
        manifest = read_group_manifest(group)
        for ref in sorted(manifest_resources(manifest), key=_resource_delete_order):
            delete_generated_ref(ref)
        if manifest is not None:
            write_empty_manifest(group, manifest["owner_group_uuid"])


def create_transaction(group) -> GeneratedResourceTransaction:
    return GeneratedResourceTransaction(owner_group_uuid=ensure_owner_group_uuid(group))


def create_curve_object_from_segments(transaction: GeneratedResourceTransaction, segments: Iterable, *, name_hint: str):
    """Create a generated Curve plus hidden Object for static baked segments."""
    safe_hint = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (name_hint or "NodeForge"))[:48]
    base = f"NodeForge.{safe_hint}.{transaction.owner_group_uuid[:8]}.{transaction.generation_uuid[:8]}"
    curve = bpy.data.curves.new(base + ".Curve", "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1
    try:
        curve.use_fake_user = False
    except Exception:
        pass
    transaction.add(curve, "CURVE", "static_baked_curve")
    try:
        for segment in segments:
            spline = curve.splines.new("POLY")
            spline.points.add(1)
            spline.points[0].co = (float(segment.start.x), float(segment.start.y), float(segment.start.z), 1.0)
            spline.points[1].co = (float(segment.end.x), float(segment.end.y), float(segment.end.z), 1.0)
    except Exception:
        transaction.rollback()
        raise
    obj = bpy.data.objects.new(base + ".Object", curve)
    transaction.add(obj, "OBJECT", "static_baked_object")
    global _TEST_FAIL_AFTER_OBJECT_CREATE
    if _TEST_FAIL_AFTER_OBJECT_CREATE:
        _TEST_FAIL_AFTER_OBJECT_CREATE = False
        transaction.rollback()
        raise RuntimeError("Injected NodeForge generated-object failure")
    try:
        scene = getattr(bpy.context, "scene", None)
        collection = getattr(scene, "collection", None) or getattr(bpy.context, "collection", None)
        if collection is not None:
            collection.objects.link(obj)
    except Exception:
        pass
    obj.hide_viewport = True
    obj.hide_render = True
    try:
        obj.hide_select = True
    except Exception:
        pass
    try:
        obj.use_fake_user = False
    except Exception:
        pass
    return curve, obj



def _safe_name_hint(name_hint: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (name_hint or "NodeForge"))[:48]


def _hide_generated_object(obj) -> None:
    obj.hide_viewport = True
    obj.hide_render = True
    try:
        obj.hide_select = True
    except Exception:
        pass
    try:
        obj.use_fake_user = False
    except Exception:
        pass


def _link_object_to_context_collection(obj) -> None:
    try:
        scene = getattr(bpy.context, "scene", None)
        collection = getattr(scene, "collection", None) or getattr(bpy.context, "collection", None)
        if collection is not None:
            collection.objects.link(obj)
    except Exception:
        pass


def _write_attribute_values(attribute, values) -> None:
    """Write scalar or vector values into a Blender geometry attribute."""
    data = attribute.data
    if len(data) != len(values):
        raise RuntimeError(f"Generated attribute {attribute.name} length mismatch")
    if values and isinstance(values[0], (tuple, list)):
        flat = [float(component) for value in values for component in value]
        try:
            data.foreach_set("vector", flat)
            return
        except Exception:
            pass
        for item, value in zip(data, values):
            item.vector = tuple(float(component) for component in value)
        return
    try:
        data.foreach_set("value", values)
        return
    except Exception:
        pass
    for item, value in zip(data, values):
        item.value = value


def _create_command_mesh_object_from_specs(transaction: GeneratedResourceTransaction, table, *, name_hint: str, mesh_role: str, object_role: str, point_attributes, edge_attributes):
    """Create generated Mesh/Object command data from explicit attribute specs."""
    safe_hint = _safe_name_hint(name_hint)
    base = f"NodeForge.{safe_hint}.{transaction.owner_group_uuid[:8]}.{transaction.generation_uuid[:8]}"
    mesh = bpy.data.meshes.new(base + ".CommandMesh")
    transaction.add(mesh, "MESH", mesh_role)
    global _TEST_FAIL_AFTER_MESH_CREATE, _TEST_FAIL_AFTER_MESH_ATTRIBUTE_WRITE, _TEST_FAIL_AFTER_OBJECT_CREATE
    if _TEST_FAIL_AFTER_MESH_CREATE:
        _TEST_FAIL_AFTER_MESH_CREATE = False
        transaction.rollback()
        raise RuntimeError("Injected NodeForge generated-mesh failure")
    try:
        mesh.from_pydata(list(table.vertices), list(table.edges), [])
        mesh.update()
        for attr_name, data_type, values in point_attributes:
            attr = mesh.attributes.new(attr_name, data_type, "POINT")
            _write_attribute_values(attr, values)
        for attr_name, data_type, values in edge_attributes:
            attr = mesh.attributes.new(attr_name, data_type, "EDGE")
            _write_attribute_values(attr, values)
        if _TEST_FAIL_AFTER_MESH_ATTRIBUTE_WRITE:
            _TEST_FAIL_AFTER_MESH_ATTRIBUTE_WRITE = False
            raise RuntimeError("Injected NodeForge command-mesh attribute failure")
        mesh.update()
    except Exception:
        transaction.rollback()
        raise
    obj = bpy.data.objects.new(base + ".CommandObject", mesh)
    transaction.add(obj, "OBJECT", object_role)
    if _TEST_FAIL_AFTER_OBJECT_CREATE:
        _TEST_FAIL_AFTER_OBJECT_CREATE = False
        transaction.rollback()
        raise RuntimeError("Injected NodeForge generated-object failure")
    _link_object_to_context_collection(obj)
    _hide_generated_object(obj)
    return mesh, obj

def _marker_point_attribute_specs(table):
    ids = getattr(table, "marker_id", ()) or [(0, 0, 0, 0)] * len(table.vertices)
    specs = [
        ("nf_lsys_marker_id_a", "INT", [item[0] for item in ids]),
        ("nf_lsys_marker_id_b", "INT", [item[1] for item in ids]),
        ("nf_lsys_marker_id_c", "INT", [item[2] for item in ids]),
        ("nf_lsys_marker_id_d", "INT", [item[3] for item in ids]),
        ("nf_lsys_marker_mask", "BOOLEAN", getattr(table, "marker_mask", [False] * len(table.vertices))),
        ("nf_lsys_marker_tangent", "FLOAT_VECTOR", getattr(table, "marker_tangent", [(0.0, 0.0, 0.0)] * len(table.vertices))),
        ("nf_lsys_marker_depth", "INT", getattr(table, "marker_depth", [0] * len(table.vertices))),
        ("nf_lsys_marker_path_id", "INT", getattr(table, "marker_path_id", [0] * len(table.vertices))),
        ("nf_lsys_marker_iteration", "INT", getattr(table, "marker_iteration", [-1] * len(table.vertices))),
    ]
    for name, values in getattr(table, "marker_param_static", {}).items():
        specs.append((name, "FLOAT", values))
        specs.append((f"nf_lsys_marker_param_index_{name}", "INT", getattr(table, "marker_param_index", {})[name]))
    return tuple(specs)


def create_command_mesh_object_from_table(transaction: GeneratedResourceTransaction, table, *, name_hint: str):
    """Create generated Mesh/Object command data for branch-free runtime L-systems."""
    return _create_command_mesh_object_from_specs(
        transaction,
        table,
        name_hint=name_hint,
        mesh_role="branch_free_runtime_command_mesh",
        object_role="branch_free_runtime_command_object",
        point_attributes=(
            ("nf_lsys_move_mask", "FLOAT", table.move_mask),
            ("nf_lsys_heading_index", "FLOAT", table.heading_index),
            ("nf_lsys_move_distance_static", "FLOAT", table.move_distance_static),
            ("nf_lsys_move_param_index", "INT", table.move_param_index),
            ("nf_lsys_turn_degrees_static", "FLOAT", table.turn_degrees_static),
            ("nf_lsys_turn_param_index", "INT", table.turn_param_index),
            ("nf_lsys_turn_sign", "FLOAT", table.turn_sign),
        ) + _marker_point_attribute_specs(table),
        edge_attributes=(("nf_lsys_draw_mask", "BOOLEAN", table.draw_mask),),
    )


def create_branch_aware_command_mesh_object_from_table(transaction: GeneratedResourceTransaction, table, *, name_hint: str):
    """Create generated Mesh/Object command data for branch-aware runtime L-systems."""
    return _create_command_mesh_object_from_specs(
        transaction,
        table,
        name_hint=name_hint,
        mesh_role="branch_aware_runtime_command_mesh",
        object_role="branch_aware_runtime_command_object",
        point_attributes=(
            ("nf_lsys_move_mask", "FLOAT", table.move_mask),
            ("nf_lsys_heading_index", "FLOAT", table.heading_index),
            ("nf_lsys_path_id", "INT", table.path_id),
            ("nf_lsys_path_depth", "INT", table.path_depth),
            ("nf_lsys_parent_attach_index", "INT", table.parent_attach_index),
            ("nf_lsys_anchor_mask", "BOOLEAN", table.anchor_mask),
            ("nf_lsys_move_distance_static", "FLOAT", table.move_distance_static),
            ("nf_lsys_move_param_index", "INT", table.move_param_index),
            ("nf_lsys_turn_degrees_static", "FLOAT", table.turn_degrees_static),
            ("nf_lsys_turn_param_index", "INT", table.turn_param_index),
            ("nf_lsys_turn_sign", "FLOAT", table.turn_sign),
        ) + _marker_point_attribute_specs(table),
        edge_attributes=(("nf_lsys_draw_mask", "BOOLEAN", table.draw_mask),),
    )


def create_marker_mesh_object_from_points(transaction: GeneratedResourceTransaction, markers, *, name_hint: str):
    """Create generated Mesh/Object point data for static L-system markers."""
    safe_hint = _safe_name_hint(name_hint)
    base = f"NodeForge.{safe_hint}.{transaction.owner_group_uuid[:8]}.{transaction.generation_uuid[:8]}"
    mesh = bpy.data.meshes.new(base + ".MarkerMesh")
    transaction.add(mesh, "MESH", "static_marker_mesh")
    global _TEST_FAIL_AFTER_MESH_CREATE, _TEST_FAIL_AFTER_MESH_ATTRIBUTE_WRITE, _TEST_FAIL_AFTER_OBJECT_CREATE
    if _TEST_FAIL_AFTER_MESH_CREATE:
        _TEST_FAIL_AFTER_MESH_CREATE = False
        transaction.rollback()
        raise RuntimeError("Injected NodeForge generated-mesh failure")
    try:
        vertices = [(float(m.position.x), float(m.position.y), float(m.position.z)) for m in markers]
        mesh.from_pydata(vertices, [], [])
        mesh.update()
        ids = [m.marker_identity for m in markers]
        attrs = [
            ("nf_lsys_marker_id_a", "INT", [item[0] for item in ids]),
            ("nf_lsys_marker_id_b", "INT", [item[1] for item in ids]),
            ("nf_lsys_marker_id_c", "INT", [item[2] for item in ids]),
            ("nf_lsys_marker_id_d", "INT", [item[3] for item in ids]),
            ("nf_lsys_marker_mask", "BOOLEAN", [True] * len(markers)),
            ("nf_lsys_marker_tangent", "FLOAT_VECTOR", [(float(m.tangent.x), float(m.tangent.y), float(m.tangent.z)) for m in markers]),
            ("nf_lsys_marker_depth", "INT", [int(m.branch_depth) for m in markers]),
            ("nf_lsys_marker_path_id", "INT", [int(m.path_id) for m in markers]),
            ("nf_lsys_marker_iteration", "INT", [int(m.creation_iteration if m.creation_iteration is not None else -1) for m in markers]),
        ]
        runtime_slots = []
        param_names = sorted({name for marker in markers for name in marker.parameters})
        for name in param_names:
            values = []
            indexes = []
            for marker in markers:
                value = marker.parameters.get(name, 0.0)
                if isinstance(value, Value):
                    slot = -1
                    for idx, (slot_name, slot_value) in enumerate(runtime_slots):
                        if slot_name == name and slot_value is value:
                            slot = idx
                            break
                    if slot < 0:
                        runtime_slots.append((name, value))
                        slot = len(runtime_slots) - 1
                    values.append(0.0)
                    indexes.append(slot)
                else:
                    values.append(float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0)
                    indexes.append(-1)
            attrs.append((name, "FLOAT", values))
            attrs.append((f"nf_lsys_marker_param_index_{name}", "INT", indexes))
        for attr_name, data_type, values in attrs:
            attr = mesh.attributes.new(attr_name, data_type, "POINT")
            _write_attribute_values(attr, values)
        if _TEST_FAIL_AFTER_MESH_ATTRIBUTE_WRITE:
            _TEST_FAIL_AFTER_MESH_ATTRIBUTE_WRITE = False
            raise RuntimeError("Injected NodeForge marker-mesh attribute failure")
        mesh.update()
    except Exception:
        transaction.rollback()
        raise
    obj = bpy.data.objects.new(base + ".MarkerObject", mesh)
    transaction.add(obj, "OBJECT", "static_marker_object")
    if _TEST_FAIL_AFTER_OBJECT_CREATE:
        _TEST_FAIL_AFTER_OBJECT_CREATE = False
        transaction.rollback()
        raise RuntimeError("Injected NodeForge generated-object failure")
    _link_object_to_context_collection(obj)
    _hide_generated_object(obj)
    return mesh, obj, tuple(runtime_slots)

__all__ = [
    "GROUP_MANIFEST_PROP", "ID_METADATA_PROP", "SCHEMA_VERSION",
    "GeneratedResourceRef", "GeneratedResourceTransaction", "create_transaction",
    "read_group_manifest", "write_group_manifest", "write_empty_manifest", "clear_group_manifest",
    "manifest_resources", "cleanup_previous_after_commit", "cleanup_restart_orphans",
    "cleanup_live_group_resources", "create_curve_object_from_segments",
    "create_command_mesh_object_from_table", "create_branch_aware_command_mesh_object_from_table", "create_marker_mesh_object_from_points", "delete_generated_ref",
]

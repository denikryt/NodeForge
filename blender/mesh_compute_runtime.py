"""Mesh Compute's transport-independent boundary for evaluated Blender mesh buffers.

This module is deliberately not a scheduler: the modifier/scene lifecycle is owned
by the physical root runtime stage, while this layer only reads copied float32
buffers, calls a trusted numerical function, and validates its detached result.
"""

from __future__ import annotations

from array import array
import hashlib
import math
from dataclasses import dataclass
from collections.abc import Callable, Mapping

from ..mesh_compute import MeshInputs, MeshComputeResult, normalize_mesh_result


@dataclass(frozen=True)
class PreparedMeshComputeStage:
    """Uncommitted physical stage and captured callable awaiting root-modifier binding.

    Owned Blender IDs are tracked separately by the existing build transaction;
    this record is not persisted or registered globally during graph lowering.
    """

    prefix: object
    result_proxy: object
    scalar_keys: Mapping[str, tuple[str, object]]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    kernel: Callable
    source_origin: str | None = None


@dataclass(frozen=True)
class EvaluatedMeshCompute:
    """A validated result ready for a transport to publish into a fresh Blender ID."""

    inputs: MeshInputs
    result: MeshComputeResult


def mesh_inputs_from_evaluated(mesh, reads: tuple[str, ...]) -> MeshInputs:
    """Read detached float32 POINT arrays from one evaluated Mesh without mutating it."""
    if not isinstance(reads, tuple) or any(not isinstance(name, str) or not name for name in reads):
        raise TypeError("Mesh Compute reads must be a tuple of nonempty attribute names")
    if len(set(reads)) != len(reads):
        raise ValueError("Mesh Compute reads must not contain duplicate names")
    vertex_count = len(mesh.vertices)
    if vertex_count <= 0:
        raise ValueError("Mesh Compute requires nonempty evaluated Mesh")
    positions = array("f", [0.0]) * (vertex_count * 3)
    # Blender 5.2's built-in position attribute exposes contiguous vector data.
    # Mesh.vertices.foreach_get("co") visits vertices far more slowly on dense grids.
    position = mesh.attributes.get("position")
    if position is None or position.domain != "POINT" or position.data_type != "FLOAT_VECTOR":
        raise ValueError("Mesh Compute evaluated Mesh lacks built-in POINT FLOAT_VECTOR position")
    position.data.foreach_get("vector", positions)
    attrs = {}
    for name in reads:
        attribute = mesh.attributes.get(name)
        if attribute is None or attribute.domain != "POINT" or attribute.data_type != "FLOAT":
            raise ValueError(f"Mesh Compute input {name!r} must be a POINT FLOAT attribute")
        values = array("f", [0.0]) * vertex_count
        attribute.data.foreach_get("value", values)
        attrs[name] = values
    return MeshInputs(positions, attrs, vertex_count)


def evaluate_mesh_callback(
    mesh,
    *,
    kernel: Callable,
    reads: tuple[str, ...],
    writes: tuple[str, ...],
    parameters: Mapping[str, float | int | bool],
) -> EvaluatedMeshCompute:
    """Execute a trusted deterministic kernel and normalize its result before publication.

    No Blender ID or evaluated mesh is passed to the callback. Exceptions propagate;
    the caller owns the result gate and must leave its prior output invalid on errors.
    """
    if not callable(kernel):
        raise TypeError("Mesh Compute kernel must be callable")
    inputs = mesh_inputs_from_evaluated(mesh, reads)
    if not isinstance(parameters, Mapping) or any(type(value) not in (float, int, bool) for value in parameters.values()):
        raise ValueError("Mesh Compute parameters must be ordinary float, int or bool scalars")
    result = kernel(inputs, **dict(parameters))
    normalized = normalize_mesh_result(result, vertex_count=inputs.vertex_count, writes=writes)
    return EvaluatedMeshCompute(inputs, normalized)


def prefix_from_geometry_socket(group, socket, parameters: Mapping[str, object], transaction):
    """Copy a partial GN graph into a transaction-owned private prefix NodeTree.

    `parameters` contains the already-resolved ExtensionBackendValue scalar sockets.
    Their values are baked to private POINT attributes in the *prefix only* and
    must never be published as user attributes.
    """
    from ..nf_types import NFType

    if socket is None or not getattr(socket, "is_output", False) or socket.type != "GEOMETRY":
        raise ValueError("Mesh Compute prefix requires an output Geometry socket")
    if socket.node.id_data.as_pointer() != group.as_pointer():
        raise ValueError("Mesh Compute prefix socket must belong to its source group")
    if not isinstance(parameters, Mapping):
        raise TypeError("Mesh Compute parameters must be a mapping")
    parameter_sockets = {}
    for name, value in parameters.items():
        if not isinstance(name, str) or not name.isidentifier() or name.startswith("__"):
            raise ValueError("Mesh Compute scalar parameter needs a valid name")
        if hasattr(value, "socket"):
            if value.typ not in {NFType.FLOAT, NFType.INT, NFType.BOOL}:
                raise ValueError(f"Mesh Compute parameter {name!r} requires scalar input")
            if value.socket.node.id_data.as_pointer() != group.as_pointer() or not value.socket.is_output:
                raise ValueError(f"Mesh Compute parameter {name!r} is outside the source group")
        elif type(value) not in {float, int, bool} or type(value) is float and not math.isfinite(value):
            raise ValueError(f"Mesh Compute parameter {name!r} needs a finite typed scalar")
        parameter_sockets[name] = value

    prefix = group.copy()
    # A private evaluated-prefix NodeTree is never a second authoritative root.
    # Blender copies custom properties with ID.copy(), including root-owner
    # identity and the old group's generated-resource manifest.
    from ..function_instances import FUNCTION_ROOT_OWNER_ID_PROP
    from .group_authority import mark_transaction_private
    from .generated_resources import clear_group_manifest

    # Register the exact ID before any other mutation can fail; transaction
    # rollback deletes this ID, not an object found by copied metadata.
    transaction.add(prefix, "NODE_GROUP", "mesh-compute-prefix")
    mark_transaction_private(prefix)
    if FUNCTION_ROOT_OWNER_ID_PROP in prefix:
        del prefix[FUNCTION_ROOT_OWNER_ID_PROP]
    clear_group_manifest(prefix)
    prefix.name = "NodeForge.MeshComputePrefix"
    source_node = prefix.nodes.get(socket.node.name)
    if source_node is None:
        raise ValueError("Mesh Compute source node did not survive prefix copy")
    source_output = next((s for s in source_node.outputs if s.identifier == socket.identifier), None)
    if source_output is None:
        raise ValueError("Mesh Compute source socket did not survive prefix copy")
    current = source_output
    names = {}
    for name, value in parameter_sockets.items():
        internal = ".nf_mesh_compute_param_" + name
        if hasattr(value, "socket"):
            node = prefix.nodes.get(value.socket.node.name)
            source_param = next((s for s in node.outputs if s.identifier == value.socket.identifier), None) if node else None
            if source_param is None:
                raise ValueError(f"Mesh Compute scalar socket {name!r} did not survive prefix copy")
            typ = value.typ
        else:
            typ = {float: NFType.FLOAT, int: NFType.INT, bool: NFType.BOOL}[type(value)]
            if typ is NFType.FLOAT:
                constant = prefix.nodes.new("ShaderNodeValue")
                constant.outputs["Value"].default_value = value
                source_param = constant.outputs["Value"]
            elif typ is NFType.INT:
                constant = prefix.nodes.new("FunctionNodeInputInt")
                constant.integer = value
                source_param = constant.outputs[0]
            else:
                constant = prefix.nodes.new("FunctionNodeInputBool")
                constant.boolean = value
                source_param = constant.outputs[0]
        store = prefix.nodes.new("GeometryNodeStoreNamedAttribute")
        store.domain = "POINT"
        store.data_type = {NFType.FLOAT: "FLOAT", NFType.INT: "INT", NFType.BOOL: "BOOLEAN"}[typ]
        store.inputs["Name"].default_value = internal
        prefix.links.new(current, store.inputs["Geometry"])
        prefix.links.new(source_param, store.inputs["Value"])
        current = store.outputs["Geometry"]
        names[name] = (internal, typ)

    for output in list(prefix.nodes):
        if output.bl_idname == "NodeGroupOutput":
            prefix.nodes.remove(output)
    for item in list(prefix.interface.items_tree):
        if item.item_type == "SOCKET" and item.in_out == "OUTPUT":
            prefix.interface.remove(item)
    prefix.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_output = prefix.nodes.new("NodeGroupOutput")
    group_output.is_active_output = True
    prefix.links.new(current, group_output.inputs["Geometry"])
    return prefix, names


def runtime_parameters_from_evaluated(mesh, declaration: Mapping[str, tuple[str, object]]) -> dict[str, object]:
    """Resolve private POINT-domain uniform scalar fields to typed kernel parameters."""
    from ..nf_types import NFType

    out = {}
    count = len(mesh.vertices)
    if count == 0:
        raise ValueError("Mesh Compute requires nonempty evaluated Mesh")
    for name, (attribute_name, typ) in declaration.items():
        attr = mesh.attributes.get(attribute_name)
        expected = {NFType.FLOAT: ("FLOAT", "f"), NFType.INT: ("INT", "i"), NFType.BOOL: ("BOOLEAN", "i")}.get(typ)
        if expected is None or attr is None or attr.domain != "POINT" or attr.data_type != expected[0]:
            raise ValueError(f"Mesh Compute parameter {name!r} missing declared scalar attribute")
        values = array(expected[1], [0]) * count
        attr.data.foreach_get("value", values)
        if values.count(values[0]) != count:
            raise ValueError(f"Mesh Compute parameter {name!r} varies across vertices")
        out[name] = {NFType.FLOAT: float, NFType.INT: int, NFType.BOOL: bool}[typ](values[0])
    return out


__all__ = ["PreparedMeshComputeStage", "EvaluatedMeshCompute", "mesh_inputs_from_evaluated", "evaluate_mesh_callback", "prefix_from_geometry_socket", "runtime_parameters_from_evaluated"]


@dataclass
class RuntimeModifierBinding:
    """One committed root-modifier stage and its private evaluated-prefix source.

    The root group and its result proxy are borrowed from the owning group
    transaction. Only `source_scene` and `source_object` are owned here.
    """

    owner: object
    modifier: object
    group: object
    prepared: PreparedMeshComputeStage
    source_scene: object
    source_object: object
    source_modifier: object
    status: str = "READY_PENDING_EVALUATION"
    last_error: str | None = None
    numerical_key: str | None = None
    numerical_result: MeshComputeResult | None = None
    last_published: object | None = None
    publication_key: str | None = None
    trust_revoked: bool = False


_ACTIVE_BINDINGS: dict[int, RuntimeModifierBinding] = {}
_SCHEDULER_RUNNING = False
_GUARDED_MODIFIERS: set[int] = set()
# Saved runtime roots have no executable callback after opening another .blend.
# This is transient session state, not an executable/source trust grant.
_MISSING_SAVED_RUNTIME: dict[int, tuple[object, object, str]] = {}


def _saved_private_result_proxy(owner, modifier):
    """Verify saved root→Object Info→result proxy without trusting ID labels alone.

    No saved metadata can establish an orphan's provenance. We only authorize
    a new *empty* Mesh assignment to a proxy physically referenced by a unique
    authoritative Mesh Compute root and its exact first GN modifier.
    """
    import bpy
    from ..function_instances import FUNCTION_ROOT_OWNER_ID_PROP
    from .generated_resources import manifest_resources, read_group_manifest, read_id_metadata
    from .group_authority import is_authority_ineligible_group

    if owner.type != 'MESH' or len(owner.modifiers) != 1 or owner.modifiers[0].as_pointer() != modifier.as_pointer():
        raise ValueError('Mesh Compute saved root requires a private first and only modifier')
    if modifier.type != 'NODES' or modifier.node_group is None:
        raise ValueError('Mesh Compute saved root needs a Geometry Nodes modifier')
    group = modifier.node_group
    if (group.bl_idname != 'GeometryNodeTree' or not group.get('nodeforge_mesh_compute_stage_v1')
            or is_authority_ineligible_group(group)):
        raise ValueError('Mesh Compute saved root descriptor is absent or private')
    owner_id = group.get(FUNCTION_ROOT_OWNER_ID_PROP)
    if not owner_id or any(g.as_pointer() != group.as_pointer() and g.get(FUNCTION_ROOT_OWNER_ID_PROP) == owner_id
                           for g in bpy.data.node_groups):
        raise ValueError('Mesh Compute saved root owner identity is missing or ambiguous')
    group_owners = [(obj, mod) for obj in bpy.data.objects for mod in obj.modifiers
                    if mod.type == 'NODES' and mod.node_group is group]
    group_nodes = [node for tree in bpy.data.node_groups for node in tree.nodes
                   if getattr(node, 'node_tree', None) is group]
    if (len(group_owners) != 1 or group_owners[0][0].as_pointer() != owner.as_pointer()
            or group_owners[0][1].as_pointer() != modifier.as_pointer()
            or group_nodes or group.use_fake_user):
        raise ValueError('Mesh Compute saved root is shared or user-held')
    manifest = read_group_manifest(group)
    if manifest is None:
        raise ValueError('Mesh Compute saved root has no valid resource manifest')
    refs = [ref for ref in manifest_resources(manifest)
            if ref.kind == 'OBJECT' and ref.role == 'mesh-compute-result']
    targets = [socket.default_value for node in group.nodes
               if node.bl_idname == 'GeometryNodeObjectInfo'
               for socket in node.inputs if socket.name == 'Object' and socket.default_value is not None]
    if len(refs) != 1 or len(targets) != 1:
        raise ValueError('Mesh Compute saved proxy has ambiguous object references')
    proxy = targets[0]
    ref = refs[0]
    metadata = read_id_metadata(proxy)
    if (proxy.type != 'MESH' or proxy.name != ref.name or metadata != ref
            or manifest['owner_group_uuid'] != ref.owner_group_uuid):
        raise ValueError('Mesh Compute saved proxy ownership cannot be verified')
    if any(tree.as_pointer() != group.as_pointer() and any(node.bl_idname == 'GeometryNodeObjectInfo'
           and any(socket.name == 'Object' and socket.default_value is not None and socket.default_value.as_pointer() == proxy.as_pointer() for socket in node.inputs)
           for node in tree.nodes) for tree in bpy.data.node_groups):
        raise ValueError('Mesh Compute saved proxy has an external node-group user')
    return group, proxy


def saved_runtime_status(owner, modifier=None) -> str | None:
    """Report a saved, nonexecutable Mesh Compute root without invoking Python."""
    if modifier is None:
        if not getattr(owner, 'modifiers', None):
            return None
        modifier = owner.modifiers[0]
    record = _MISSING_SAVED_RUNTIME.get(_modifier_identity(modifier))
    if record is None:
        return None
    return 'MISSING_RUNTIME' if record[0].as_pointer() == owner.as_pointer() and modifier.node_group is not None and modifier.node_group.as_pointer() == record[1].as_pointer() else None


def _invalidate_saved_runtime_roots():
    """Close verified saved output proxies after a file load or addon registration."""
    import bpy
    import warnings

    _MISSING_SAVED_RUNTIME.clear()
    for owner in tuple(bpy.data.objects):
        if owner.type != 'MESH' or not owner.modifiers:
            continue
        modifier = owner.modifiers[0]
        if modifier.type != 'NODES' or modifier.node_group is None:
            continue
        live = _ACTIVE_BINDINGS.get(_modifier_identity(modifier))
        if live is not None and live.owner.as_pointer() == owner.as_pointer() and live.group.as_pointer() == modifier.node_group.as_pointer():
            continue
        group = modifier.node_group
        if not group.get('nodeforge_mesh_compute_stage_v1'):
            continue
        try:
            group, proxy = _saved_private_result_proxy(owner, modifier)
        except (ValueError, ReferenceError, AttributeError) as exc:
            warnings.warn(f'Mesh Compute preserved unverifiable saved proxy: {exc}', RuntimeWarning)
            continue
        # TODO(MESH_COMPUTE_TEMP): On file load, clear persisted output until trusted Local callback restoration by source identity is implemented.
        # Retain the previous physical Mesh: a reopened file cannot prove that
        # its data has no user-owned references. An already-empty proxy needs
        # no additional replacement (repeated load/history handlers are idempotent).
        if proxy.data is None or len(proxy.data.vertices) != 0:
            empty = bpy.data.meshes.new('NodeForge.MeshComputeMissingRuntime')
            try:
                proxy.data = empty
            except BaseException:
                if empty.users == 0:
                    bpy.data.meshes.remove(empty)
                raise
            group.update_tag()
        _MISSING_SAVED_RUNTIME[_modifier_identity(modifier)] = (owner, group, 'Reload Compute to restore Python')


def on_file_load_post(_dummy=None):
    """Discard stale in-process code and invalidate verified persisted results."""
    global _SCHEDULER_RUNNING
    _ACTIVE_BINDINGS.clear()  # Old bpy IDs may no longer be valid after file load.
    _GUARDED_MODIFIERS.clear()
    _SCHEDULER_RUNNING = False
    _remove_runtime_handlers_if_unused()
    _invalidate_saved_runtime_roots()


def _on_history_restored(_dummy=None):
    """Discard stale RNA identities after Blender rebuilds IDs on Undo/Redo.

    Blender replaces ID wrappers during a global undo restore; keeping a
    previously bound modifier or proxy would risk returning a stale result.
    The same verified saved-root gate as file load makes the restored graph
    explicitly require Reload Compute rather than executing an old callback.
    """
    on_file_load_post(_dummy)


def register_runtime_load_handler():
    """Register persistent load invalidation and inspect an already-open file."""
    import bpy
    from bpy.app.handlers import persistent
    persistent(on_file_load_post)
    persistent(_on_history_restored)
    if on_file_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_file_load_post)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _on_history_restored not in handlers:
            handlers.append(_on_history_restored)
    # Blender invokes addon.register() with restricted bpy.data in some paths.
    # Install the load hook immediately, but defer saved-root inspection until
    # unrestricted ID collections are available (same rule as resource cleanup).
    from .generated_resources import _can_access_blender_id_collections
    if _can_access_blender_id_collections():
        _invalidate_saved_runtime_roots()
    else:
        def _inspect_when_unrestricted():
            """Run saved-root invalidation after Blender's registration phase."""
            if not _can_access_blender_id_collections():
                return 0.1
            _invalidate_saved_runtime_roots()
            return None
        bpy.app.timers.register(_inspect_when_unrestricted, first_interval=0.0)


def unregister_runtime_load_handler():
    """Stop the persistent callback without altering any saved user datablocks."""
    import bpy
    if on_file_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_file_load_post)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _on_history_restored in handlers:
            handlers.remove(_on_history_restored)
    _MISSING_SAVED_RUNTIME.clear()


def shutdown_runtime_bindings():
    """Invalidate live results and release exact ephemeral Scene/Object IDs."""
    import bpy
    for binding in tuple(_ACTIVE_BINDINGS.values()):
        try:
            _saved_private_result_proxy(binding.owner, binding.modifier)
            empty = bpy.data.meshes.new('NodeForge.MeshComputeDisabled')
            binding.prepared.result_proxy.data = empty
            binding.group.update_tag()
        except (ReferenceError, ValueError, AttributeError):
            # Do not modify an unverified proxy belonging to another user.
            pass
        release_modifier_binding(binding)
    _ACTIVE_BINDINGS.clear()
    _GUARDED_MODIFIERS.clear()
    unregister_runtime_handlers()



def _can_skip_foreign_depsgraph_updates(scene) -> bool:
    """Prove a PRE event irrelevant to *all* live stages in the active scene.

    Blender 5.2 does not always supply the depsgraph argument to PRE hooks.
    The active ViewLayer's pending updates can identify unrelated datablocks,
    but absence, ambiguity or a dependency we cannot model must run the old
    full validation. This is an optimization, never a cache authority.
    """
    import bpy
    from ..local_sources import local_python_permission

    if scene is None or bpy.context.scene is not scene:
        return False
    layer = bpy.context.view_layer
    if layer is None:
        return False
    updates = tuple(layer.depsgraph.updates)
    if not updates:
        return False

    relevant = set()
    for binding in tuple(_ACTIVE_BINDINGS.values()):
        # Trust is stored outside Blender's depsgraph. Revocation must always
        # reach evaluate_modifier_binding's fail-closed output invalidation.
        if (binding.trust_revoked or binding.status != 'READY' or
                (binding.prepared.source_origin is not None and
                 not local_python_permission(binding.prepared.source_origin))):
            return False
        owner = binding.owner
        if not any(item.as_pointer() == owner.as_pointer() for item in scene.objects):
            continue
        if (owner.type != 'MESH' or owner.data is None or owner.parent is not None or
                len(owner.constraints) or
                (owner.animation_data is not None and len(owner.animation_data.drivers)) or
                (owner.data.animation_data is not None and
                 len(owner.data.animation_data.drivers))):
            return False
        if (not owner.modifiers or owner.modifiers[0].as_pointer() != binding.modifier.as_pointer() or
                binding.modifier.node_group is None or
                binding.modifier.node_group.as_pointer() != binding.group.as_pointer()):
            return False
        # v1 forbids arbitrary Object Info/Collection Info as inputs. Reject
        # modified or nested GN graphs that introduce undeclared external IDs.
        seen = set()
        def prefix_is_local(tree):
            """Reject node dependencies that may originate outside this stage."""
            pointer = tree.as_pointer()
            if pointer in seen:
                return True
            seen.add(pointer)
            for node in tree.nodes:
                if node.bl_idname in {'GeometryNodeObjectInfo', 'GeometryNodeCollectionInfo',
                                      'GeometryNodeInputSelfObject', 'GeometryNodeInputSceneTime'}:
                    return False
                for socket in node.inputs:
                    value = getattr(socket, 'default_value', None)
                    if isinstance(value, bpy.types.ID):
                        return False
                sub_tree = getattr(node, 'node_tree', None)
                if sub_tree is not None and not prefix_is_local(sub_tree):
                    return False
            return True
        if not prefix_is_local(binding.prepared.prefix):
            return False
        for item in (owner, owner.data, binding.group, binding.prepared.prefix,
                     binding.source_object, binding.source_scene,
                     binding.prepared.result_proxy, binding.prepared.result_proxy.data):
            if item is not None:
                relevant.add(item.as_pointer())

    if not relevant:
        return False
    for update in updates:
        raw = update.id
        original = getattr(raw, 'original', None)
        if original is None:
            return False
        if not isinstance(original, (bpy.types.Object, bpy.types.Mesh)):
            return False
        if original.as_pointer() in relevant:
            return False
        # Another Object sharing the source Mesh is not safely unrelated.
        if isinstance(original, bpy.types.Object) and original.data is not None:
            if original.data.as_pointer() in relevant:
                return False
    return True


def _handle_scene_update(scene) -> None:
    """Refresh live modifier stages before GN consumers use their proxy meshes.

    A publication fingerprint prevents feedback from our own proxy mesh's
    update notification. The PRE hook may skip provably foreign updates,
    while frame/render hooks retain this unfiltered validation.
    """
    global _SCHEDULER_RUNNING
    if _SCHEDULER_RUNNING or scene is None:
        return
    _SCHEDULER_RUNNING = True
    try:
        for binding in tuple(_ACTIVE_BINDINGS.values()):
            if binding.trust_revoked or _modifier_identity(binding.modifier) in _GUARDED_MODIFIERS:
                continue
            try:
                if not any(obj.as_pointer() == binding.owner.as_pointer() for obj in scene.objects):
                    continue
                evaluate_modifier_binding(binding.owner, binding.modifier, source_scene=scene,
                                          skip_unchanged=True)
            except (ReferenceError, RuntimeError, ValueError) as exc:
                # Individual stage errors are stored by evaluate_modifier_binding;
                # a deleted/stale owner must not crash unrelated Blender edits.
                try:
                    if binding.status != "MISSING_RUNTIME":
                        binding.status = "ERROR"
                    binding.last_error = str(exc)
                except ReferenceError:
                    pass
    finally:
        _SCHEDULER_RUNNING = False


def _on_depsgraph_update_pre(scene, depsgraph=None):
    """Update stages before evaluated GN consumers, ignoring proven foreign IDs."""
    if _SCHEDULER_RUNNING:
        return
    try:
        if _can_skip_foreign_depsgraph_updates(scene):
            return
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        # Unknown view-layer or stale RNA identity: use the original full path.
        pass
    _handle_scene_update(scene)


def _on_frame_change_pre(scene, depsgraph=None):
    """Update frame-dependent mesh inputs before GN reevaluation."""
    _handle_scene_update(scene)


def _on_render_pre(scene, depsgraph=None):
    """Prepare headless rendering without mutating IDs from a GUI render worker.

    Blender 5.2 calls render_pre from a render thread in interactive renders;
    evaluating a private Scene/depsgraph there can crash Blender. Interactive
    roots are kept current by frame/depsgraph pre-handlers on the main thread;
    UI rendering must lock the interface to prevent concurrent edits.
    """
    import bpy
    if bpy.app.background:
        _handle_scene_update(scene)
    elif not scene.render.use_lock_interface:
        import warnings
        warnings.warn('Mesh Compute interactive render requires Lock Interface; '
                      'runtime refresh is not supported from the render thread', RuntimeWarning)


def _install_runtime_handlers():
    """Install the fixed event hooks before a new binding can commit."""
    import bpy

    for handlers, callback in (
        (bpy.app.handlers.depsgraph_update_pre, _on_depsgraph_update_pre),
        (bpy.app.handlers.frame_change_pre, _on_frame_change_pre),
        (bpy.app.handlers.render_pre, _on_render_pre),
    ):
        if callback not in handlers:
            handlers.append(callback)


def _remove_runtime_handlers_if_unused():
    """Unregister runtime hooks when no live stage requires them."""
    if _ACTIVE_BINDINGS:
        return
    import bpy

    for handlers, callback in (
        (bpy.app.handlers.depsgraph_update_pre, _on_depsgraph_update_pre),
        (bpy.app.handlers.frame_change_pre, _on_frame_change_pre),
        (bpy.app.handlers.render_pre, _on_render_pre),
    ):
        if callback in handlers:
            handlers.remove(callback)


def unregister_runtime_handlers():
    """Stop automatic execution during addon teardown, without deleting user IDs."""
    import bpy

    for handlers, callback in (
        (bpy.app.handlers.depsgraph_update_pre, _on_depsgraph_update_pre),
        (bpy.app.handlers.frame_change_pre, _on_frame_change_pre),
        (bpy.app.handlers.render_pre, _on_render_pre),
    ):
        if callback in handlers:
            handlers.remove(callback)



def suspend_modifier_binding(owner, modifier=None) -> RuntimeModifierBinding:
    """Freeze event-driven publication before a runtime Reload candidate exists."""
    binding = get_modifier_binding(owner, modifier)
    key = _modifier_identity(binding.modifier)
    if key in _GUARDED_MODIFIERS:
        raise ValueError("Mesh Compute Reload already in progress")
    _GUARDED_MODIFIERS.add(key)
    return binding


def resume_modifier_binding(modifier) -> None:
    """Release a Reload guard only after commit or complete rollback."""
    _GUARDED_MODIFIERS.discard(_modifier_identity(modifier))


def _modifier_identity(modifier) -> int:
    """Identify one physical modifier independently of its mutable name."""
    return int(modifier.as_pointer())


def prepare_modifier_binding(owner, modifier, group, prepared: PreparedMeshComputeStage):
    """Create isolated evaluation state without entering the live binding map.

    The compiler calls this before group publication. The owning transaction
    rolls it back on every failed build and flips its reference only at commit.
    """
    import bpy

    if owner.type != "MESH" or owner.data is None:
        raise ValueError("Mesh Compute modifier requires a Mesh Object")
    if modifier.type != "NODES" or modifier.node_group is not group:
        raise ValueError("Mesh Compute requires the exact root Geometry Nodes modifier")
    if tuple(owner.modifiers) != (modifier,):
        raise ValueError("Mesh Compute requires the first and only modifier in v1")
    if group.bl_idname != "GeometryNodeTree" or not isinstance(prepared, PreparedMeshComputeStage):
        raise ValueError("Mesh Compute requires a prepared private GN root")
    if any(other.as_pointer() != owner.as_pointer() and any(
            m.type == "NODES" and m.node_group is not None and m.node_group.as_pointer() == group.as_pointer() for m in other.modifiers
    ) for other in bpy.data.objects):
        raise ValueError("Mesh Compute root group cannot be assigned to another Object")

    scene = None
    source = None
    try:
        scene = bpy.data.scenes.new("NodeForge.MeshComputeSource")
        source = bpy.data.objects.new("NodeForge.MeshComputeSource", owner.data)
        scene.collection.objects.link(source)
        source_modifier = source.modifiers.new("NodeForge.MeshComputePrefix", "NODES")
        source_modifier.node_group = prepared.prefix
        _install_runtime_handlers()
        return RuntimeModifierBinding(owner, modifier, group, prepared, scene, source, source_modifier)
    except BaseException:
        if source is not None and any(obj is source for obj in bpy.data.objects):
            bpy.data.objects.remove(source, do_unlink=True)
        if scene is not None and any(item is scene for item in bpy.data.scenes):
            bpy.data.scenes.remove(scene, do_unlink=True)
        _remove_runtime_handlers_if_unused()
        raise


def release_modifier_binding(binding: RuntimeModifierBinding) -> None:
    """Remove only exact private session IDs, never the user's source Mesh."""
    import bpy

    if binding.source_object is not None and any(obj is binding.source_object for obj in bpy.data.objects):
        bpy.data.objects.remove(binding.source_object, do_unlink=True)
    if binding.source_scene is not None and any(s is binding.source_scene for s in bpy.data.scenes):
        bpy.data.scenes.remove(binding.source_scene, do_unlink=True)
    _remove_runtime_handlers_if_unused()


def commit_modifier_binding(binding: RuntimeModifierBinding) -> None:
    """Perform the prevalidated, in-memory binding flip at group commit PONR."""
    _ACTIVE_BINDINGS[_modifier_identity(binding.modifier)] = binding
    _MISSING_SAVED_RUNTIME.pop(_modifier_identity(binding.modifier), None)


def get_modifier_binding(owner, modifier=None) -> RuntimeModifierBinding:
    """Resolve only an exact active owner/modifier/root association."""
    if modifier is None:
        if not getattr(owner, "modifiers", None):
            raise ValueError("Mesh Compute object has no modifier")
        modifier = owner.modifiers[0]
    binding = _ACTIVE_BINDINGS.get(_modifier_identity(modifier))
    if (binding is None or binding.owner.as_pointer() != owner.as_pointer()
            or _modifier_identity(binding.modifier) != _modifier_identity(modifier)
            or modifier.node_group is None or modifier.node_group.as_pointer() != binding.group.as_pointer()):
        raise ValueError("Mesh Compute modifier has no active trusted runtime binding")
    return binding


def suspend_saved_runtime_binding(owner):
    """Guard a uniquely verified saved root that has no executable live callback."""
    if saved_runtime_status(owner) != 'MISSING_RUNTIME':
        raise ValueError('Mesh Compute has no saved runtime eligible for Reload')
    modifier = owner.modifiers[0]
    group, _proxy = _saved_private_result_proxy(owner, modifier)
    key = _modifier_identity(modifier)
    if key in _GUARDED_MODIFIERS:
        raise ValueError('Mesh Compute Reload already in progress')
    _GUARDED_MODIFIERS.add(key)
    return modifier, group


def _mirror_prefix_inputs(binding: RuntimeModifierBinding) -> None:
    """Copy the modifier's effective scalar values to its isolated GN prefix.

    Blender calls frame_change_pre before applying action FCurves to the live
    modifier RNA. Evaluate supported keyed inputs for the requested frame so
    the first GN evaluation uses the correct frame, without a post-eval hook.
    """
    animation = binding.owner.animation_data
    keyed_values = {}
    if animation is not None and animation.action is not None and animation.action_slot is not None:
        frame = binding.source_scene.frame_current_final
        prefix = f'modifiers["{binding.modifier.name}"].properties.inputs.'
        slot_handle = animation.action_slot.handle
        for layer in animation.action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    if bag.slot_handle != slot_handle:
                        continue
                    for curve in bag.fcurves:
                        if curve.array_index == 0 and curve.data_path.startswith(prefix) and curve.data_path.endswith('.value'):
                            key = curve.data_path[len(prefix):-len('.value')]
                            keyed_values[key] = curve.evaluate(frame)
    for item in binding.prepared.prefix.interface.items_tree:
        if item.item_type != "SOCKET" or item.in_out != "INPUT" or item.socket_type == "NodeSocketGeometry":
            continue
        key = item.identifier
        try:
            original = getattr(binding.modifier.properties.inputs, key)
            private = getattr(binding.source_modifier.properties.inputs, key)
            value = keyed_values.get(key, original.value)
            if key in keyed_values:
                # FCurve.evaluate() returns float even for INT/BOOL sockets.
                value = (bool(round(value)) if type(original.value) is bool else
                         int(round(value)) if type(original.value) is int else float(value))
            private.value = value
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"Mesh Compute cannot mirror modifier input {item.name!r}: {exc}") from exc


def _retire_previous_published(binding: RuntimeModifierBinding, previous) -> None:
    """Retire only the exact ID allocated by this same live runtime generation."""
    import bpy

    if previous is not None and previous.users == 0 and any(mesh is previous for mesh in bpy.data.meshes):
        bpy.data.meshes.remove(previous)


def _tag_published_group(group) -> None:
    """Invalidate Blender's GN evaluation after the proxy pointer is updated."""
    group.update_tag()


def _publication_fingerprint(mesh) -> str:
    """Fingerprint the *entire* evaluated source Mesh used by direct publication.

    This is independent of the callback cache: a new edge, UV or material
    must republish the native mesh even when Python observes the same buffers.
    Unsupported exotic attributes use a conservative representation instead
    of silently dropping them from the freshness test.
    """
    digest = hashlib.blake2b(digest_size=24)

    def numeric(collection, property_name: str, typecode: str, width: int = 1):
        values = array(typecode, [0]) * (len(collection) * width)
        if len(values):
            collection.foreach_get(property_name, values)
        digest.update(values.tobytes())

    for collection, fields in (
        # Vertex count is included here. Coordinates are hashed through the
        # Blender 5.2 built-in POINT FLOAT_VECTOR "position" attribute below,
        # avoiding a second Mesh.vertices.foreach_get("co") traversal.
        (mesh.vertices, ()),
        # Blender 5.2 exposes edge endpoints as .edge_verts, loop endpoints as
        # .corner_vert/.corner_edge, seam flags as uv_seam, sharp flags as
        # sharp_edge/sharp_face. They are all included by the native attribute
        # iteration below (even when a layer is absent and its default applies).
        # Avoid expensive RNA per-element edge/loop/flag traversals that would
        # hash the same native data a second time.
        (mesh.edges, ()),
        (mesh.loops, ()),
        # Face boundaries have no guaranteed native-attribute equivalent.
        # Material indices are handled below; where Blender exposes a FACE
        # INT layer, reading the RNA polygon values again is redundant.
        (mesh.polygons, (("loop_start", "i", 1), ("loop_total", "i", 1))),
    ):
        digest.update(str(len(collection)).encode())
        for name, typecode, width in fields:
            numeric(collection, name, typecode, width)
    for attribute in mesh.attributes:
        digest.update(repr((attribute.name, attribute.domain, attribute.data_type, len(attribute.data))).encode())
        kind = {
            "FLOAT": ("value", "f", 1), "INT": ("value", "i", 1),
            "BOOLEAN": ("value", "b", 1), "FLOAT_VECTOR": ("vector", "f", 3),
            "INT32_2D": ("value", "i", 2),
            "FLOAT2": ("vector", "f", 2), "FLOAT_COLOR": ("color", "f", 4),
            "BYTE_COLOR": ("color", "f", 4), "QUATERNION": ("value", "f", 4),
        }.get(attribute.data_type)
        if kind is not None:
            try:
                numeric(attribute.data, *kind)
                continue
            except (TypeError, ValueError, AttributeError):
                pass
        for datum in attribute.data:
            for field in ("value", "vector", "color"):
                if hasattr(datum, field):
                    digest.update(repr(getattr(datum, field)[:]
                                       if hasattr(getattr(datum, field), "__iter__")
                                       else getattr(datum, field)).encode())
    material_index = mesh.attributes.get("material_index")
    if (material_index is None or material_index.domain != "FACE" or
            material_index.data_type != "INT" or len(material_index.data) != len(mesh.polygons)):
        # Native FACE INT is not guaranteed for every evaluated Mesh. Keep
        # the RNA path unless the same values were hashed as native data.
        numeric(mesh.polygons, "material_index", "i", 1)
    for layer in mesh.uv_layers:
        digest.update(layer.name.encode())
        native_uv = mesh.attributes.get(layer.name)
        if (native_uv is None or native_uv.domain != "CORNER" or
                native_uv.data_type != "FLOAT2" or len(native_uv.data) != len(layer.data)):
            # Blender 5.2 exposes a CORNER FLOAT2 UV map through *both* APIs.
            # The native-attribute loop already hashed its values. Fall back
            # when evaluated geometry does not expose a matching layer.
            numeric(layer.data, "uv", "f", 2)
    for material in mesh.materials:
        digest.update(str(material.as_pointer() if material else 0).encode())
    return digest.hexdigest()


def evaluate_modifier_binding(owner, modifier=None, *, source_scene=None, skip_unchanged=False):
    """Synchronously evaluate a committed GN prefix, compute and publish its result.

    Registered handlers also invoke this synchronously, skipping publication
    only if both numerical inputs and the entire native source are unchanged.
    """
    import bpy
    from ..mesh_compute import fingerprint_mesh_inputs
    from .mesh_compute_transports import direct_mesh_update

    binding = get_modifier_binding(owner, modifier)
    if _modifier_identity(binding.modifier) in _GUARDED_MODIFIERS:
        raise ValueError("Mesh Compute Reload has suspended this modifier")
    scene = source_scene or bpy.context.scene
    new_mesh = None
    previous_published = binding.last_published
    try:
        if binding.trust_revoked:
            raise PermissionError("Mesh Compute requires Reload after a Local Python permission revocation")
        if binding.prepared.source_origin is not None:
            from ..local_sources import local_python_permission
            if not local_python_permission(binding.prepared.source_origin):
                binding.trust_revoked = True
                raise PermissionError("Mesh Compute Local executable Python permission was revoked")
        if any(other.as_pointer() != owner.as_pointer() and any(
                mod.type == "NODES" and mod.node_group is not None and
                mod.node_group.as_pointer() == binding.group.as_pointer()
        for mod in other.modifiers) for other in bpy.data.objects):
            raise ValueError("Mesh Compute root was shared across Objects")
        binding.source_object.data = owner.data
        binding.source_object.matrix_world = owner.matrix_world.copy()
        binding.source_scene.frame_set(scene.frame_current, subframe=scene.frame_subframe)
        _mirror_prefix_inputs(binding)
        binding.source_object.update_tag(refresh={'OBJECT', 'DATA', 'TIME'})
        with bpy.context.temp_override(scene=binding.source_scene, view_layer=binding.source_scene.view_layers[0]):
            binding.source_scene.view_layers[0].update()
            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated = binding.source_object.evaluated_get(depsgraph)
            temporary_mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
            try:
                parameters = runtime_parameters_from_evaluated(temporary_mesh, binding.prepared.scalar_keys)
                inputs = mesh_inputs_from_evaluated(temporary_mesh, binding.prepared.reads)
                key = fingerprint_mesh_inputs(
                    inputs, declared_reads=binding.prepared.reads, parameters=parameters,
                    implementation_fingerprint=str(id(binding.prepared.kernel)),
                )
                if binding.numerical_key == key and binding.numerical_result is not None:
                    normalized = binding.numerical_result
                else:
                    raw = binding.prepared.kernel(inputs, **parameters)
                    normalized = normalize_mesh_result(
                        raw, vertex_count=inputs.vertex_count, writes=binding.prepared.writes
                    )
                publication_key = _publication_fingerprint(temporary_mesh)
                if (skip_unchanged and binding.status == "READY" and
                        binding.numerical_key == key and binding.publication_key == publication_key):
                    return binding.prepared.result_proxy.data
                candidate = EvaluatedMeshCompute(inputs, normalized)
                new_mesh = direct_mesh_update.prepare_publication(
                    evaluated, depsgraph, candidate,
                    temporary_attribute_names=tuple(v[0] for v in binding.prepared.scalar_keys.values()),
                )
            finally:
                evaluated.to_mesh_clear()

        # No Blender Object/Modifier has been mutated until validation succeeds.
        previous = binding.prepared.result_proxy.data
        binding.prepared.result_proxy.data = new_mesh
        # Blender must accept the group tag before the runtime records this
        # generation as committed. A tagging exception is fail-closed and the
        # just-installed candidate is then removed by exact physical identity.
        _tag_published_group(binding.group)
        binding.numerical_key = key
        binding.publication_key = publication_key
        binding.numerical_result = normalized
        binding.last_published = new_mesh
        binding.status = "READY"
        binding.last_error = None
        # Candidate initial empty Mesh is transaction-owned; retire separately
        # when its group is safely destroyed, not by name/metadata here.
        if previous is previous_published:
            try:
                _retire_previous_published(binding, previous)
            except Exception as retire_error:
                # Pointer publication has committed: an old-ID retirement
                # warning must not invalidate the new authoritative result.
                import warnings
                warnings.warn(f"Mesh Compute retained previous Mesh: {retire_error}",
                              RuntimeWarning, stacklevel=2)
        return new_mesh
    except BaseException as exc:
        binding.status = "MISSING_RUNTIME" if isinstance(exc, PermissionError) else "ERROR"
        binding.last_error = str(exc)
        binding.publication_key = None
        # Fail closed even if a candidate Mesh was already installed. The
        # proxy's physical ID, not a manifest label, determines what to change.
        previous = binding.prepared.result_proxy.data
        try:
            if previous is None or len(previous.vertices) != 0:
                empty = bpy.data.meshes.new("NodeForge.MeshComputeInvalid")
                try:
                    binding.prepared.result_proxy.data = empty
                except BaseException:
                    if empty.users == 0:
                        bpy.data.meshes.remove(empty)
                    raise
                binding.last_published = empty
                # The original error remains authoritative if Blender cannot
                # also invalidate the consumer group.
                try:
                    binding.group.update_tag()
                except Exception as tag_error:
                    import warnings
                    warnings.warn(f"Mesh Compute could not tag invalid output: {tag_error}",
                                  RuntimeWarning, stacklevel=2)
            # A previously committed Mesh may have been detached by either
            # the successful empty gate or the failed candidate pointer swap.
            if (previous_published is not None and
                    binding.prepared.result_proxy.data is not previous_published):
                try:
                    _retire_previous_published(binding, previous_published)
                except Exception as retire_error:
                    import warnings
                    warnings.warn(f"Mesh Compute retained previous Mesh after error: {retire_error}",
                                  RuntimeWarning, stacklevel=2)
        finally:
            # Created in this evaluation, and possibly briefly assigned.
            # Never delete a candidate still referenced by another user.
            if (new_mesh is not None and binding.prepared.result_proxy.data is not new_mesh
                    and new_mesh.users == 0 and any(mesh is new_mesh for mesh in bpy.data.meshes)):
                try:
                    bpy.data.meshes.remove(new_mesh)
                except Exception as cleanup_error:
                    import warnings
                    warnings.warn(f"Mesh Compute retained unreferenced candidate Mesh: {cleanup_error}",
                                  RuntimeWarning, stacklevel=2)
        raise


@dataclass
class ModifierInputMigration:
    """Reversible Blender 5.2 RNA/FCurve edits scoped to one modifier group swap."""

    modifier: object
    old_group: object
    new_group: object
    values: dict
    target_ids: dict
    curves: list
    installed: bool = False

    def install(self) -> None:
        """Assign candidate group and retarget only verified declaration-keyed inputs."""
        self.modifier.node_group = self.new_group
        self.installed = True  # Must be set immediately: all edits below can fail.
        for stable_key, state in self.values.items():
            new_input = getattr(self.modifier.properties.inputs, self.target_ids[stable_key])
            for field in state["companions"]:
                if not hasattr(new_input, field):
                    raise ValueError("Mesh Compute Reload cannot preserve modifier attribute-mode input")
            new_input.value = state["value"]
            for field, field_value in state["companions"].items():
                setattr(new_input, field, field_value)
        for curve, old_path, new_path in self.curves:
            curve.data_path = new_path

    def rollback(self) -> None:
        """Restore FCurve paths and the exact old modifier group/value state."""
        if not self.installed:
            return
        for curve, old_path, _ in self.curves:
            curve.data_path = old_path
        self.modifier.node_group = self.old_group
        for stable_key, state in self.values.items():
            old_input = getattr(self.modifier.properties.inputs, state["identifier"])
            old_input.value = state["value"]
            for field, field_value in state["companions"].items():
                setattr(old_input, field, field_value)
        self.installed = False


def prepare_modifier_input_migration(owner, modifier, old_group, new_group) -> ModifierInputMigration:
    """Resolve durable declarations and reject unmappable animated inputs before cutover.

    NodeForge's InputDeclarationId is authoritative; display names and the
    Blender Socket_N identifier are physical locators only, not identity.
    """
    from ..errors import CompileError
    from .interface import _get_group_input_declarations

    def declaration_socket_map(group):
        sockets = {}
        occurrence = {}
        for item in group.interface.items_tree:
            if item.item_type != "SOCKET" or item.in_out != "INPUT":
                continue
            key = (item.name, item.socket_type)
            index = occurrence.get(key, 0)
            occurrence[key] = index + 1
            sockets[(item.name, item.socket_type, index)] = item.identifier
        result = {}
        for stable_key, record in _get_group_input_declarations(group).items():
            physical = (record["display_name"], record["socket_type"], record["occurrence"])
            identifier = sockets.get(physical)
            if identifier is None:
                raise CompileError("Mesh Compute Reload declaration has no physical socket")
            result[stable_key] = (identifier, record)
        return result

    old = declaration_socket_map(old_group)
    new = declaration_socket_map(new_group)
    if not old or not new:
        raise CompileError("Mesh Compute Reload requires stored input declarations")
    values = {}
    new_ids = {}
    old_id_to_new_id = {}
    for stable_key, (identifier, record) in old.items():
        if record["socket_type"] not in {"NodeSocketFloat", "NodeSocketInt", "NodeSocketBool"}:
            continue
        replacement = new.get(stable_key)
        if replacement is None or replacement[1]["socket_type"] != record["socket_type"]:
            raise CompileError("Mesh Compute Reload removed or changed a declared scalar input")
        dest_id = replacement[0]
        old_input = getattr(modifier.properties.inputs, identifier)
        companions = {}
        for field in ("use_attribute", "attribute_name"):
            if hasattr(old_input, field):
                companions[field] = getattr(old_input, field)
        values[stable_key] = {"identifier": identifier, "value": old_input.value,
                              "companions": companions}
        new_ids[stable_key] = dest_id
        old_id_to_new_id[identifier] = dest_id

    animation = owner.animation_data
    if animation is not None and len(animation.drivers):
        raise CompileError("Mesh Compute Reload does not support driven modifier inputs")
    if animation is not None and len(animation.nla_tracks):
        raise CompileError("Mesh Compute Reload does not support NLA modifier animations")
    curves = []
    if animation is not None and animation.action is not None:
        action = animation.action
        # Action v2 can have multiple independently owned channel bags. Never
        # rewrite another object's slot merely because an Action is shared.
        active_slot = animation.action_slot
        if active_slot is None:
            raise CompileError("Mesh Compute Reload requires an active action slot")
        all_curves = [curve for layer in action.layers for strip in layer.strips
                      for bag in strip.channelbags if bag.slot_handle == active_slot.handle
                      for curve in bag.fcurves]
        prefix = f'modifiers["{modifier.name}"].properties.inputs.'
        for curve in all_curves:
            if not curve.data_path.startswith(prefix):
                continue
            suffix = curve.data_path[len(prefix):]
            old_id, dot, attribute = suffix.partition(".")
            if not dot or attribute != "value" or old_id not in old_id_to_new_id:
                raise CompileError("Mesh Compute Reload cannot map an animated modifier property")
            new_path = prefix + old_id_to_new_id[old_id] + ".value"
            curves.append((curve, curve.data_path, new_path))
    return ModifierInputMigration(modifier, old_group, new_group, values, new_ids, curves)

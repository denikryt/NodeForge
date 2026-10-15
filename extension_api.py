"""Public Blender-facing physical API for backend-only Python extensions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .blender.socket_types import validate_runtime_value_socket
from .nf_types import NFType

if TYPE_CHECKING:
    import bpy


def _safe_name_part(value: str) -> str:
    """Return a bounded Blender-friendly generated-resource name component."""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)[:48]


@dataclass(frozen=True)
class ExtensionBackendValue:
    """Package-facing typed wrapper around one physical Blender runtime socket."""

    socket: object
    typ: NFType

    def __post_init__(self) -> None:
        """Require a canonical NodeForge runtime type token."""
        if not isinstance(self.typ, NFType):
            raise TypeError("ExtensionBackendValue.typ must be an NFType")


class ExtensionBackendContext:
    """Expose the minimal physical realization surface to one extension invocation."""

    def __init__(self, *, group, location, generated_resource_transaction):
        """Bind one invocation to its target group, layout anchor, and outer transaction."""
        if group is None:
            raise ValueError("ExtensionBackendContext requires a target node group")
        if (
            not isinstance(location, tuple)
            or len(location) != 2
            or not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in location)
        ):
            raise TypeError("ExtensionBackendContext.location must be an (x, y) numeric tuple")
        if generated_resource_transaction is None:
            raise ValueError("ExtensionBackendContext requires the active generated-resource transaction")
        self._group = group
        self._location = (float(location[0]), float(location[1]))
        self._generated_resource_transaction = generated_resource_transaction

    @property
    def group(self) -> "bpy.types.GeometryNodeTree":
        """Return the exact current candidate Geometry Nodes group."""
        return self._group

    @property
    def location(self) -> tuple[float, float]:
        """Return the ordinary Call IR layout anchor for this invocation."""
        return self._location

    def value(self, socket: object, typ: NFType) -> ExtensionBackendValue:
        """Validate and wrap one current-group output socket as a runtime value."""
        if not isinstance(typ, NFType):
            raise TypeError("context.value() typ must be an NFType")
        validate_runtime_value_socket(
            socket,
            typ,
            self._group,
            context="extension context.value()",
            require_output=True,
        )
        return ExtensionBackendValue(socket, typ)

    def _generated_name(self, role: str, name_hint: str, suffix: str) -> str:
        """Return an advisory generated-ID name tied to current owner/generation identity."""
        if not isinstance(role, str) or not role:
            raise TypeError("generated resource role must be a non-empty string")
        if not isinstance(name_hint, str):
            raise TypeError("generated resource name_hint must be a string")
        tx = self._generated_resource_transaction
        hint = _safe_name_part(name_hint or role or "NodeForge") or "NodeForge"
        return f"NodeForge.{hint}.{tx.owner_group_uuid[:8]}.{tx.generation_uuid[:8]}.{suffix}"

    def _own_and_publish(self, id_obj, kind: str, role: str):
        """Own one new exact ID before publishing persistent generated-resource metadata."""
        self._generated_resource_transaction.add(id_obj, kind, role)
        return id_obj

    def mesh_compute(self, geometry, *, kernel, parameters, reads=(), writes=(), source_origin=None):
        """Stage a Local Mesh Compute boundary in this build's resource transaction.

        The stage is *not* active here. Its owning root-modifier cutover must
        validate and bind it before publication commit; no global runtime or
        handler can observe a candidate built by an unsuccessful compilation.
        """
        from .blender.mesh_compute_runtime import prefix_from_geometry_socket
        from .blender.mesh_compute_transports.direct_mesh_update import build_boundary

        if not isinstance(geometry, ExtensionBackendValue) or geometry.typ is not NFType.GEOMETRY:
            raise TypeError("Mesh Compute requires an ExtensionBackendValue Geometry input")
        validate_runtime_value_socket(geometry.socket, geometry.typ, self._group, context="mesh_compute geometry", require_output=True)
        if not callable(kernel) or getattr(kernel, "__nodeforge_mesh_compute__", None) != (tuple(reads), tuple(writes)):
            raise ValueError("Mesh Compute kernel is not a verified @mesh_compute callable")
        if not isinstance(parameters, dict) or any(not isinstance(name, str) or not name.isidentifier() for name in parameters):
            raise ValueError("Mesh Compute scalar arguments must be named")
        if any(not isinstance(names, tuple) or len(set(names)) != len(names) or any(not isinstance(x, str) or not x for x in names) for names in (reads, writes)):
            raise ValueError("Mesh Compute reads and writes must be tuples of distinct POINT FLOAT names")
        tx = self._generated_resource_transaction
        if getattr(tx, "mesh_compute_stage", None) is not None:
            raise ValueError("One Mesh Compute stage per root modifier is supported")
        prefix, scalar_keys = prefix_from_geometry_socket(self._group, geometry.socket, parameters, tx)
        proxy_mesh = self.new_generated_mesh(role="mesh-compute-result", name_hint="MeshCompute")
        proxy = self.new_generated_object(proxy_mesh, role="mesh-compute-result", name_hint="MeshCompute")
        output = build_boundary(self._group, proxy, self._location)
        from .blender.mesh_compute_runtime import PreparedMeshComputeStage
        stage = PreparedMeshComputeStage(prefix=prefix, result_proxy=proxy, scalar_keys=scalar_keys,
                                         reads=tuple(reads), writes=tuple(writes), kernel=kernel,
                                         source_origin=source_origin)
        tx.mesh_compute_stage = stage
        self._group["nodeforge_mesh_compute_stage_v1"] = tx.generation_uuid
        return self.value(output, NFType.GEOMETRY)

    def new_generated_mesh(self, *, role: str, name_hint: str = "") -> "bpy.types.Mesh":
        """Create one transaction-owned generated Mesh and publish its metadata."""
        name = self._generated_name(role, name_hint, "Mesh")
        import bpy

        mesh = bpy.data.meshes.new(name)
        return self._own_and_publish(mesh, "MESH", role)

    def new_generated_curve(self, *, role: str, name_hint: str = "") -> "bpy.types.Curve":
        """Create one transaction-owned CURVE datablock and publish its metadata."""
        name = self._generated_name(role, name_hint, "Curve")
        import bpy

        curve = bpy.data.curves.new(name, "CURVE")
        return self._own_and_publish(curve, "CURVE", role)

    def new_generated_object(
        self,
        data: "bpy.types.ID | None" = None,
        *,
        role: str,
        name_hint: str = "",
    ) -> "bpy.types.Object":
        """Create one transaction-owned Object without claiming ownership of its data."""
        name = self._generated_name(role, name_hint, "Object")
        import bpy

        obj = bpy.data.objects.new(name, data)
        return self._own_and_publish(obj, "OBJECT", role)


__all__ = ["ExtensionBackendContext", "ExtensionBackendValue", "NFType"]

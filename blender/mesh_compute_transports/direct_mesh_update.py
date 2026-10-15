"""Topology-preserving direct Mesh publication for Mesh Compute.

The caller owns the generated proxy and its publication transaction. This module
only constructs a complete, unpublished main-database mesh copy.
"""

from __future__ import annotations

from ..mesh_compute_runtime import EvaluatedMeshCompute


def build_boundary(group, proxy_object, location=(0.0, 0.0)):
    """Create the single Object Info boundary used by direct Mesh publication."""
    if proxy_object is None or getattr(proxy_object, "type", None) != "MESH":
        raise ValueError("Direct Mesh transport needs an owned Mesh proxy Object")
    info = group.nodes.new("GeometryNodeObjectInfo")
    info.transform_space = "ORIGINAL"
    info.inputs["Object"].default_value = proxy_object
    info.location = location
    return info.outputs["Geometry"]


def prepare_publication(evaluated_object, depsgraph, evaluated: EvaluatedMeshCompute, *, temporary_attribute_names=()):
    """Copy evaluated geometry and apply validated arrays without publishing it.

    Caller must either publish the returned main-database ID or remove it on
    error. No live Object.data pointer or borrowed evaluated Mesh is mutated.
    """
    import bpy

    if not isinstance(evaluated, EvaluatedMeshCompute):
        raise TypeError("Direct transport needs a validated EvaluatedMeshCompute result")
    output = None
    try:
        output = bpy.data.meshes.new_from_object(evaluated_object, preserve_all_data_layers=True, depsgraph=depsgraph)
        if output is None or len(output.vertices) != evaluated.inputs.vertex_count:
            raise ValueError("Direct Mesh transport copy changed vertex count")
        for name in temporary_attribute_names:
            existing = output.attributes.get(name)
            if existing is not None:
                output.attributes.remove(existing)
        result = evaluated.result
        for name, values in result.point_attributes.items():
            attr = output.attributes.get(name)
            if attr is not None and (attr.domain != "POINT" or attr.data_type != "FLOAT"):
                raise ValueError(f"Mesh Compute output {name!r} conflicts with an existing attribute domain/type")
            if attr is None:
                attr = output.attributes.new(name, "FLOAT", "POINT")
            attr.data.foreach_set("value", values)
        if result.positions is not None:
            # Bulk writes through built-in position avoid the per-vertex RNA cost
            # of Mesh.vertices.foreach_set("co") in Blender 5.2.
            position = output.attributes.get("position")
            if position is None or position.domain != "POINT" or position.data_type != "FLOAT_VECTOR":
                raise ValueError("Direct Mesh output lacks built-in POINT FLOAT_VECTOR position")
            position.data.foreach_set("vector", result.positions)
        output.update()
        return output
    except BaseException:
        if output is not None and bpy.data.meshes.get(output.name) is output:
            bpy.data.meshes.remove(output)
        raise


__all__ = ["build_boundary", "prepare_publication"]

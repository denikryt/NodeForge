"""Verify Local unique editor lifecycle and atomic updates."""

import bpy
import pytest

from NodeForge import compiler, local_sources
from NodeForge.errors import CompileError
from NodeForge.function_instances import FUNCTION_INSTANCE_KEY_PROP

LEAF = 'x=input_float("X")\noutput("Value", x+1.0)\n'
CURVE = """from local import leaf
x=input_float("X")
y=node("ShaderNodeFloatCurve", inputs={"Factor":1.0,"Value":x}, output="Value", typ=Float)
output("Value", leaf(y))
"""
ROOT = """from local.terrain import curve as first
from local import terrain as t
a=first(0.5, __unique__=True)
b=t.curve(0.5, __unique__=True)
c=first(0.5)
d=t.curve(0.5, __unique__=False)
output("Geometry", point(vector(a+b+c+d, 0.0, 0.0)))
"""


@pytest.fixture
def sources(tmp_path, monkeypatch):
    """Use isolated sources and remove all node groups produced by each scenario."""
    root = tmp_path / "Local"
    (root / "terrain").mkdir(parents=True)
    (root / "leaf.nf").write_text(LEAF)
    (root / "terrain/curve.nf").write_text(CURVE)
    monkeypatch.setattr(local_sources, "_default_local_catalog_dir", lambda: root)
    monkeypatch.setattr(
        local_sources,
        "_local_sources_registry_path",
        lambda: tmp_path / "registry.json",
    )
    before = {g.as_pointer() for g in bpy.data.node_groups}
    yield root
    for group in list(bpy.data.node_groups):
        if group.as_pointer() not in before:
            bpy.data.node_groups.remove(group, do_unlink=True)


def children(group):
    """Return immediate source-call groups in their emitted call order."""
    return [n.node_tree for n in group.nodes if n.bl_idname == "GeometryNodeGroup"]


def curve_point(group):
    """Return editable state from the marked callable's actual Float Curve node."""
    node = next(n for n in group.nodes if n.bl_idname == "ShaderNodeFloatCurve")
    return node.mapping.curves[0].points[0]


def evaluate(group):
    """Run the compiled graph through Blender's depsgraph and read its point position."""
    mesh = bpy.data.meshes.new("LocalCharacterization")
    obj = bpy.data.objects.new("LocalCharacterization", mesh)
    bpy.context.collection.objects.link(obj)
    obj.modifiers.new("Nodes", "NODES").node_group = group
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    result = evaluated.to_mesh()
    try:
        assert len(result.vertices) == 1
        return tuple(result.vertices[0].co)
    finally:
        evaluated.to_mesh_clear()
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)

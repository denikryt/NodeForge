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


def test_local_unique_editor_lifecycle(sources, tmp_path):
    """Characterize retained manual state, source invalidation and exact-origin reload."""
    group = compiler.create_expression_group(ROOT, "LocalUniqueLifecycle")
    group.use_fake_user = True
    key = children(group)[0][FUNCTION_INSTANCE_KEY_PROP]
    curve_point(children(group)[0]).location = (0.0, 0.25)
    blend = tmp_path / "local-unique.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    group = bpy.data.node_groups["LocalUniqueLifecycle"]
    pointers = [g.as_pointer() for g in children(group)]
    group.name = "RenamedLocalUnique"
    compiler.update_expression_group(group, ROOT)
    assert [g.as_pointer() for g in children(group)][:2] == pointers[:2]
    assert children(group)[2].as_pointer() != pointers[2]
    assert children(group)[0][FUNCTION_INSTANCE_KEY_PROP] == key
    assert tuple(curve_point(children(group)[0]).location) == pytest.approx((0.0, 0.25))
    other = compiler.create_expression_group(ROOT, "IndependentLocalUnique")
    assert children(other)[0].as_pointer() != pointers[0]
    assert tuple(curve_point(children(other)[0]).location) == pytest.approx((0.0, 0.0))
    (sources / "leaf.nf").write_text(LEAF.replace("x+1.0", "x+4.0"))
    compiler.update_expression_group(group, ROOT)
    assert children(group)[0].as_pointer() == pointers[0]
    assert tuple(curve_point(children(group)[0]).location) == pytest.approx((0.0, 0.0))
    assert evaluate(group) == pytest.approx((18.0, 0.0, 0.0))
    assert evaluate(other) == pytest.approx((6.0, 0.0, 0.0))
    selected = children(group)[0]
    (sources / "terrain/curve.nf").write_text(CURVE.replace("leaf(y)", "leaf(y)+2.0"))
    compiler.update_library_catalog_group(selected)
    assert selected.as_pointer() == pointers[0]
    assert selected[FUNCTION_INSTANCE_KEY_PROP] == key
    assert evaluate(group) == pytest.approx((20.0, 0.0, 0.0))
    compiler.update_expression_group(group, ROOT)
    assert children(group)[0].as_pointer() == pointers[0]
    assert children(group)[0][FUNCTION_INSTANCE_KEY_PROP] == key
    assert evaluate(group) == pytest.approx((26.0, 0.0, 0.0))


def test_local_unique_update_rollback(sources, monkeypatch):
    """A late failed update must restore both graph identity and evaluated behavior."""
    from NodeForge.blender import library_groups

    group = compiler.create_expression_group(ROOT, "LocalUniqueRollback")
    pointers = [g.as_pointer() for g in children(group)]
    keys = [g.get(FUNCTION_INSTANCE_KEY_PROP, "") for g in children(group)]
    before = {g.as_pointer() for g in bpy.data.node_groups}
    (sources / "leaf.nf").write_text(LEAF.replace("x+1.0", "x+4.0"))
    original = library_groups._write_package_metadata
    published = []

    def fail_second_unique(group, record):
        """Fail after the first existing unique group has already changed."""
        if record.name == "curve":
            published.append(group.as_pointer())
            if len(published) == 2:
                raise CompileError("injected second unique publication failure")
        return original(group, record)

    monkeypatch.setattr(library_groups, "_write_package_metadata", fail_second_unique)
    with pytest.raises(CompileError, match="second unique publication failure"):
        compiler.update_expression_group(group, ROOT)
    assert len(published) == 2
    assert [g.as_pointer() for g in children(group)] == pointers
    assert [g.get(FUNCTION_INSTANCE_KEY_PROP, "") for g in children(group)] == keys
    assert {g.as_pointer() for g in bpy.data.node_groups} == before
    assert evaluate(group) == pytest.approx((6.0, 0.0, 0.0))

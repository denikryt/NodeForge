"""Root composition identity must not accidentally become source group identity."""

from helpers import *
from NodeForge.function_instances import COMPOSITION_ROOT_ID_PROP


def test_root_physical_scopes_differ_for_one_source_but_group_only_does_not_stamp():
    """An explicit shared Text context is independent from the physical target tree."""
    a = bpy.data.node_groups.new("PR2_root_scope_a", "GeometryNodeTree")
    b = bpy.data.node_groups.new("PR2_root_scope_b", "GeometryNodeTree")
    from NodeForge.function_instances import new_root_owner_id
    pa, *_ = compiler._prepare_direct_source("x = cube(1)", a, module_context="text:shared",
                                              physical_root_id=new_root_owner_id())
    pb, *_ = compiler._prepare_direct_source("x = cube(1)", b, module_context="text:shared",
                                              physical_root_id=new_root_owner_id())
    assert pa.identity.owner_scope == pb.identity.owner_scope
    assert pa.identity.physical_owner_scope != pb.identity.physical_owner_scope
    compiler.compile_source_in_tree("FLAG = False", a, module_context="text:shared")
    assert COMPOSITION_ROOT_ID_PROP not in a
    assert COMPOSITION_ROOT_ID_PROP not in b


def test_copying_persisted_composition_uuid_fails_closed_before_root_update():
    """A copy of a live root must never inherit authority for the other root."""
    from NodeForge.function_instances import new_root_owner_id
    a = bpy.data.node_groups.new("PR2_root_owner_copy_a", "GeometryNodeTree")
    b = bpy.data.node_groups.new("PR2_root_owner_copy_b", "GeometryNodeTree")
    shared = new_root_owner_id()
    a[COMPOSITION_ROOT_ID_PROP] = b[COMPOSITION_ROOT_ID_PROP] = shared
    before = len(a.nodes)
    with pytest.raises(CompileError, match="Multiple root node groups"):
        compiler.compile_source_in_tree("x = cube(1)", a)
    assert len(a.nodes) == before
    # A GROUPS_ONLY attempt does not transfer or stamp a root.
    assert compiler.compile_source_in_tree("FLAG = False", a).outcome == "GROUPS_ONLY"
    assert a[COMPOSITION_ROOT_ID_PROP] == shared

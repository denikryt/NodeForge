import pytest

from NodeForge.errors import CompileError
from NodeForge.systems.lsystem.runtime_tables import build_branch_aware_command_table

pytestmark = pytest.mark.unit


def assert_branch_aware_table_invariants(table):
    assert len(table.move_mask) == table.vertex_count
    assert len(table.heading_index) == table.vertex_count
    assert len(table.path_id) == table.vertex_count
    assert len(table.path_depth) == table.vertex_count
    assert len(table.parent_attach_index) == table.vertex_count
    assert len(table.anchor_mask) == table.vertex_count
    assert len(table.draw_mask) == table.edge_count
    for path in table.paths:
        assert table.anchor_mask[path.anchor_point_index]
        assert table.path_id[path.anchor_point_index] == path.path_id
        if path.parent_path_id >= 0:
            attach = path.parent_attach_point_index
            assert 0 <= attach < table.vertex_count
            assert table.path_id[attach] == path.parent_path_id


def test_branch_aware_anchor_contract_for_sibling_branches():
    table = build_branch_aware_command_table("F[+F]F[-F]F")

    assert_branch_aware_table_invariants(table)
    assert table.draw_count == 5
    assert table.max_branch_depth == 1
    assert len(table.paths) == 3
    assert table.paths[0].anchor_point_index == 0
    assert table.paths[1].parent_attach_point_index == 1
    assert table.paths[2].parent_attach_point_index == 5
    assert sum(1 for flag in table.anchor_mask if flag) == len(table.paths)


def test_branch_aware_nested_branch_depth():
    table = build_branch_aware_command_table("F[+F[-F]F]F")

    assert_branch_aware_table_invariants(table)
    assert table.max_branch_depth == 2
    assert table.draw_count == 5


@pytest.mark.parametrize(
    "stream, draw_count",
    [("F[+fF]F", 3), ("F[+XF]F", 3)],
)
def test_branch_aware_lowercase_moves_and_ignored_symbols(stream, draw_count):
    table = build_branch_aware_command_table(stream)

    assert_branch_aware_table_invariants(table)
    assert table.draw_count == draw_count


@pytest.mark.parametrize("stream", ["[F", "[f]"])
def test_branch_aware_rejects_invalid_streams(stream):
    with pytest.raises(CompileError):
        build_branch_aware_command_table(stream)


def test_branch_aware_table_preserves_marker_vertices_in_paths():
    from NodeForge.systems.lsystem.model import LSystemMarker, LSystemModule
    from NodeForge.systems.lsystem.modules import marker_identity
    marker = LSystemMarker('Bud', (), marker_identity('Bud'))
    modules = tuple(LSystemModule(ch, (), ch) for ch in 'F[') + (LSystemModule('Bud', (), 'Bud'),) + tuple(LSystemModule(ch, (), ch) for ch in ']F')
    table = build_branch_aware_command_table(modules, angle_degrees=30.0, step=1.0, markers={'Bud': marker})
    assert_branch_aware_table_invariants(table)
    assert table.marker_count == 1
    marker_index = table.marker_mask.index(True)
    assert table.path_depth[marker_index] == 1
    assert table.marker_id[marker_index] == marker_identity('Bud')
    assert table.marker_tangent[marker_index] == pytest.approx((1.0, 0.0, 0.0))


def test_branched_runtime_marker_param_arrays_match_vertex_count_when_attr_first_seen_at_marker():
    from NodeForge.systems.lsystem.model import LSystemMarker, LSystemModule, ModuleArg
    from NodeForge.systems.lsystem.modules import marker_identity
    from NodeForge.values import Value

    runtime = Value(None, "FLOAT")
    marker = LSystemMarker("Leaf", ("size",), marker_identity("Leaf"))
    stream = (
        LSystemModule("F", (ModuleArg("length", runtime, True, "length"),), "F(length)"),
        LSystemModule("["),
        LSystemModule("+", (ModuleArg("angle", runtime, True, "angle"),), "+(angle)"),
        LSystemModule("Leaf", (ModuleArg("size", runtime, True, "size"),), "Leaf(size)"),
        LSystemModule("]"),
    )

    table = build_branch_aware_command_table(stream, angle_degrees=30, step=1, markers={"Leaf": marker})

    assert len(table.marker_param_static["size"]) == table.vertex_count
    assert len(table.marker_param_index["size"]) == table.vertex_count
    assert table.marker_param_index["size"][-1] >= 0

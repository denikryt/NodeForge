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

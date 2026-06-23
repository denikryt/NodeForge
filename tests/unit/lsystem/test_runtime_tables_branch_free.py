import pytest

from NodeForge.errors import CompileError
from NodeForge.systems.lsystem.runtime_tables import build_branch_free_command_table

pytestmark = pytest.mark.unit


def test_branch_free_command_table_masks_and_headings():
    table = build_branch_free_command_table("+F-F")

    assert table.vertex_count == 8
    assert table.edge_count == 4
    assert table.draw_count == 2
    assert table.move_mask == (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    assert table.heading_index == (0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0)
    assert table.draw_mask == (False, True, False, True)


def test_branch_free_preserves_ignored_symbols_and_lowercase_movement():
    table = build_branch_free_command_table("XfF")

    assert table.vertex_count == 6
    assert table.edge_count == 3
    assert table.draw_count == 1
    assert table.move_mask == (0.0, 0.0, 0.0, 1.0, 0.0, 1.0)
    assert table.draw_mask == (False, False, True)


@pytest.mark.parametrize("stream", ["F[+F]", "Xf"])
def test_branch_free_rejects_unsupported_or_zero_draw_streams(stream):
    with pytest.raises(CompileError):
        build_branch_free_command_table(stream)

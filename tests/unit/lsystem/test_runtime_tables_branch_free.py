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


def test_branch_free_table_stores_parameterized_motion_and_marker_attrs():
    from NodeForge.systems.lsystem.model import LSystemMarker, LSystemModule, ModuleArg
    from NodeForge.systems.lsystem.modules import marker_identity
    runtime = __import__('NodeForge.values', fromlist=['Value']).Value(None, 'FLOAT')
    marker = LSystemMarker('Leaf', ('size',), marker_identity('Leaf'))
    modules = (
        LSystemModule('F', (ModuleArg('length', runtime, True, 'length'),), 'F(length)'),
        LSystemModule('+', (ModuleArg('45', 45.0, False),), '+(45)'),
        LSystemModule('Leaf', (ModuleArg('size', runtime, True, 'size'),), 'Leaf(size)'),
    )
    table = build_branch_free_command_table(modules, angle_degrees=30.0, step=1.0, markers={'Leaf': marker})
    assert table.runtime_params[0][0] == 'length'
    assert table.move_param_index[1] == 0
    assert 45.0 in table.turn_degrees_static
    assert table.marker_count == 1
    assert table.marker_mask[-1] is True
    assert table.marker_id[-1] == marker_identity('Leaf')
    assert table.marker_tangent[-1] == pytest.approx((0.7071067811865476, 0.7071067811865475, 0.0))
    assert 'size' in table.marker_param_index


def test_runtime_marker_param_arrays_match_vertex_count_when_attr_first_seen_at_marker():
    from NodeForge.systems.lsystem.model import LSystemMarker, LSystemModule, ModuleArg
    from NodeForge.systems.lsystem.modules import marker_identity
    from NodeForge.values import Value

    runtime = Value(None, "FLOAT")
    marker = LSystemMarker("Leaf", ("size",), marker_identity("Leaf"))
    stream = (
        LSystemModule("F", (ModuleArg("length", runtime, True, "length"),), "F(length)"),
        LSystemModule("+", (ModuleArg("angle", runtime, True, "angle"),), "+(angle)"),
        LSystemModule("Leaf", (ModuleArg("size", runtime, True, "size"),), "Leaf(size)"),
    )

    table = build_branch_free_command_table(stream, angle_degrees=30, step=1, markers={"Leaf": marker})

    assert len(table.marker_param_static["size"]) == table.vertex_count
    assert len(table.marker_param_index["size"]) == table.vertex_count
    assert table.marker_param_index["size"][-1] >= 0

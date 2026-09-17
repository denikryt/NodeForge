from helpers import *


pytestmark = pytest.mark.blender


def test_compile_time_range_accepts_integer_constant_assignments():
    source = """
BASE_SEGMENTS = 4
EXTRA_SEGMENTS = 2
MAX_SEGMENTS = BASE_SEGMENTS + EXTRA_SEGMENTS
parts = []
for i in range(MAX_SEGMENTS):
    parts.append(cube(1))
output("Geometry", join(parts))
"""

    group = compile_group(source, "NFTest_compile_time_range_constants")

    assert "MAX_SEGMENTS" not in group.interface.items_tree
    assert "Geometry" in group.interface.items_tree


def test_compile_time_range_accepts_unary_integer_bounds_and_step():
    source = """
START = -2
STOP = +3
STEP = 2
parts = []
for i in range(START, STOP, STEP):
    parts.append(cube(1))
output("Geometry", join(parts))
"""

    group = compile_group(source, "NFTest_compile_time_range_unary_integer_constants")

    assert "START" not in group.interface.items_tree
    assert "STOP" not in group.interface.items_tree
    assert "STEP" not in group.interface.items_tree
    assert "Geometry" in group.interface.items_tree


def test_compile_time_range_rejects_invalid_arguments_in_blender_path():
    with pytest.raises(CompileError, match=r"range\(\) arguments must be compile-time integers"):
        compiler.create_expression_group(
            'for i in range(2.5):\n    output("x", i)\n',
            "NFTest_compile_time_range_float_rejected",
        )

    with pytest.raises(CompileError, match=r"range\(\) arguments must be compile-time integers"):
        compiler.create_expression_group(
            'for i in range(True):\n    output("x", i)\n',
            "NFTest_compile_time_range_bool_rejected",
        )

    with pytest.raises(CompileError, match=r"range\(\) step must not be zero"):
        compiler.create_expression_group(
            'for i in range(0, 4, 0):\n    output("x", i)\n',
            "NFTest_compile_time_range_zero_step_rejected",
        )


def test_compile_time_range_pure_arithmetic_is_folded_before_node_generation():
    source = """
x = 0
for i in range(8):
    x = x + i
output("x", x)
"""

    group = compile_group(source, "NFTest_compile_time_range_pure_arithmetic_folded")

    assert not [n for n in group.nodes if n.bl_idname == "GeometryNodeRepeatInput"]
    assert not [n for n in group.nodes if n.bl_idname == "GeometryNodeRepeatOutput"]
    assert not [n for n in group.nodes if n.bl_idname == "ShaderNodeMath"]



def test_compile_time_range_is_late_consumed_after_runtime_if_join(monkeypatch):
    """Equal runtime-if exits restore CT knowledge for a later compile-time-owned range."""
    from NodeForge import statement_compiler

    def forbidden_legacy_statement(*_args, **_kwargs):
        raise AssertionError("late compile-time range unexpectedly entered legacy statement lowering")

    monkeypatch.setattr(statement_compiler, "compile_statement", forbidden_legacy_statement)
    source = """
n = 2
flag = input_bool("Flag")
if flag:
    n = 2
else:
    n = 2
xs = range(n)
parts = []
for i in xs:
    parts.append(cube(1))
output("Geometry", join(parts))
"""

    group = compile_group(source, "NFTest_compile_time_range_late_after_runtime_if")

    assert len([node for node in group.nodes if node.bl_idname == "GeometryNodeSwitch"]) == 1
    assert len([node for node in group.nodes if node.bl_idname == "GeometryNodeMeshCube"]) == 2
    assert not [node for node in group.nodes if node.bl_idname in {"GeometryNodeRepeatInput", "GeometryNodeRepeatOutput"}]
    bpy.data.node_groups.remove(group)

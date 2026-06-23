"""Reusable L-system source and benchmark fixtures."""

from __future__ import annotations

from NodeForge.systems.lsystem.backends import MAX_LSYSTEM_BRANCH_DEPTH

LSYSTEM_GALLERY_EXAMPLES = {
    "static_koch_curve": '''
geo = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F+F--F+F"),
    ls_iterations(3),
    ls_angle(60),
    ls_step(0.1),
)
output("Geometry", geo)
''',
    "runtime_branch_free_curve": '''
angle_value = input_float("Angle", default=90.0)
step_value = input_float("Step", default=0.25)

geo = ls_system(
    ls_axiom("F+F+F+F"),
    ls_iterations(0),
    ls_angle(angle_value),
    ls_step(step_value),
)
output("Geometry", geo)
''',
    "runtime_branched_plant": '''
angle_value = input_float("Angle", default=25.0)
step_value = input_float("Step", default=0.12)

geo = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F[+F]F[-F]F"),
    ls_iterations(2),
    ls_angle(angle_value),
    ls_step(step_value),
)
output("Geometry", geo)
''',
    "grammar_symbols": '''
geo = ls_system(
    ls_axiom("X"),
    ls_rule("X", "F+X"),
    ls_iterations(3),
    ls_angle(60),
    ls_step(0.1),
)
output("Geometry", geo)
''',
    "composed_geometry": '''
plant = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F[+F]F[-F]F"),
    ls_iterations(1),
    ls_angle(25),
    ls_step(0.2),
)
plant = transform(plant, translation=vector(0, 0, 1))
base = grid(2, 2)
geo = join(base, plant)
output("Geometry", geo)
''',
}

LSYSTEM_BENCHMARK_FIXTURES = (
    {
        "name": "static_straight_1k",
        "category": "static_straight",
        "axiom": "F",
        "rules": (("F", "FF"),),
        "iterations": 10,
        "runtime": False,
    },
    {
        "name": "static_straight_10k",
        "category": "static_straight",
        "axiom": "F",
        "rules": (("F", "FF"),),
        "iterations": 14,
        "runtime": False,
    },
    {
        "name": "static_straight_large",
        "category": "static_straight",
        "axiom": "F",
        "rules": (("F", "FF"),),
        "iterations": 16,
        "runtime": False,
    },
    {
        "name": "static_branched",
        "category": "static_branched",
        "axiom": "F",
        "rules": (("F", "F[+F]F[-F]F"),),
        "iterations": 4,
        "runtime": False,
    },
    {
        "name": "branch_free_runtime_line_large",
        "category": "branch_free_runtime_line",
        "axiom": "F",
        "rules": (("F", "FF"),),
        "iterations": 11,
        "runtime": True,
    },
    {
        "name": "branch_free_runtime_turns",
        "category": "branch_free_runtime_turns",
        "axiom": "F",
        "rules": (("F", "F+F--F+F"),),
        "iterations": 3,
        "runtime": True,
    },
    {
        "name": "branched_runtime_shallow_wide",
        "category": "branched_runtime_shallow_wide",
        "axiom": "F" + "[+F]" * 64,
        "rules": (),
        "iterations": 0,
        "runtime": True,
    },
    {
        "name": "branched_runtime_deep_narrow",
        "category": "branched_runtime_deep_narrow",
        "axiom": "[" * MAX_LSYSTEM_BRANCH_DEPTH + "F" + "]" * MAX_LSYSTEM_BRANCH_DEPTH,
        "rules": (),
        "iterations": 0,
        "runtime": True,
    },
    {
        "name": "limit_symbols",
        "category": "limit_failure",
        "axiom": "F",
        "rules": (("F", "FF"),),
        "iterations": 18,
        "runtime": False,
        "expect_error": True,
    },
    {
        "name": "limit_branch_depth",
        "category": "limit_failure",
        "axiom": "[" * (MAX_LSYSTEM_BRANCH_DEPTH + 1) + "F" + "]" * (MAX_LSYSTEM_BRANCH_DEPTH + 1),
        "rules": (),
        "iterations": 0,
        "runtime": True,
        "expect_error": True,
    },
)

def lsystem_source(axiom, *, rules=(), iterations=0, angle="60", step="1.0", runtime=False):
    """Build an L-system source fixture from explicit constructor values."""
    lines = []
    angle_expr = str(angle)
    step_expr = str(step)
    if runtime:
        lines.extend([
            f'angle_value = input_float("Angle", default={angle})',
            f'step_value = input_float("Step", default={step})',
        ])
        angle_expr = "angle_value"
        step_expr = "step_value"
    parts = [f'ls_axiom("{axiom}")']
    for symbol, replacement in rules:
        parts.append(f'ls_rule("{symbol}", "{replacement}")')
    parts.extend([f"ls_iterations({iterations})", f"ls_angle({angle_expr})", f"ls_step({step_expr})"])
    lines.append("geo = ls_system(" + ", ".join(parts) + ")")
    lines.append('output("Geometry", geo)')
    return "\n".join(lines) + "\n"

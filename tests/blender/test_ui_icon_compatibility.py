"""Check that NodeForge panel icon identifiers exist in the running Blender."""

from __future__ import annotations

import ast
from pathlib import Path

import bpy
import pytest

from NodeForge import ui

pytestmark = pytest.mark.blender


def _icon_literals(expression):
    """Return icon strings from literals and conditional icon choices only."""
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return (expression.value,)
    if isinstance(expression, ast.IfExp):
        return (*_icon_literals(expression.body), *_icon_literals(expression.orelse))
    return ()


def _declared_ui_icons():
    """Yield literal icon identifiers passed to UI layout drawing methods."""
    source = ast.parse(Path(ui.__file__).read_text(encoding="utf-8"))
    for call in ast.walk(source):
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in {"operator", "label", "prop", "menu", "popover"}):
            continue
        for keyword in call.keywords:
            if keyword.arg != "icon":
                continue
            yield from _icon_literals(keyword.value)


def test_ui_icons_exist_in_running_blender():
    """Every literal panel icon must be valid before a user opens the sidebar."""
    available = set(bpy.types.UILayout.bl_rna.functions["operator"].parameters["icon"].enum_items.keys())
    declared = set(_declared_ui_icons())
    assert declared  # A vacuous pass would not protect the UI.
    assert declared <= available, f"Unsupported Blender UI icons: {sorted(declared - available)}"
    assert "GEOMETRY_NODES" in declared


def test_ui_icon_validation_rejects_obsolete_modifier_icon():
    """A removed Blender icon must never silently pass our compatibility check."""
    available = set(bpy.types.UILayout.bl_rna.functions["operator"].parameters["icon"].enum_items.keys())
    assert "GEOMETRY_NODES" in available
    assert "MOD_NODES" not in available


def test_refresh_examples_with_valid_sidebar_icons():
    """Refreshing Examples and redrawing the shared sidebar must remain valid."""
    registered_here = not hasattr(bpy.types.Scene, "gn_script_mvp")
    if registered_here:
        ui.register()
    try:
        assert bpy.ops.nodeforge.refresh_function_library(namespace="examples") == {"FINISHED"}
        available = set(bpy.types.UILayout.bl_rna.functions["operator"].parameters["icon"].enum_items.keys())
        assert set(_declared_ui_icons()) <= available
    finally:
        if registered_here:
            ui.unregister()

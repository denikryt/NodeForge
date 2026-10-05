"""Static contract for Text-only Save to Local."""

import ast
from pathlib import Path


UI_SOURCE = Path(__file__).resolve().parents[2] / "ui.py"


def test_save_to_local_has_no_source_selector_or_group_resolver():
    source = UI_SOURCE.read_text(encoding="utf-8")
    assert "local_source_kind" not in source
    assert "_source_for_local_save" not in source
    module = ast.parse(source)
    operator = next(node for node in module.body if isinstance(node, ast.ClassDef)
                    and node.name == "NODEFORGE_OT_save_to_local")
    assert not any(isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                   and node.target.id == "source_kind" for node in operator.body)
    calls = {node.func.id for node in ast.walk(operator)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert calls.isdisjoint({"_source_from_props", "_selected_group_node", "_extract_group_source"})

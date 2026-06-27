"""Static regression tests for the Save to Local operator contract."""

from pathlib import Path


UI_SOURCE = Path(__file__).resolve().parents[2] / "ui.py"


def test_save_to_local_operator_declares_source_kind_enum():
    """Save to Local must expose an explicit source selector in its dialog."""
    source = UI_SOURCE.read_text(encoding="utf-8")
    assert "source_kind: EnumProperty" in source
    assert '("TEXT", "Text Block"' in source
    assert '("SELECTED_GROUP", "Selected Group"' in source
    assert "props.local_source_kind" in source


def test_save_to_local_source_resolution_is_not_implicit_fallback():
    """Source extraction must branch by source_kind instead of Text-then-group fallback."""
    source = UI_SOURCE.read_text(encoding="utf-8")
    helper_start = source.index("def _source_for_local_save")
    helper_end = source.index("class NODEFORGE_OT_save_to_local", helper_start)
    helper = source[helper_start:helper_end]
    assert "source_kind == \"TEXT\"" in helper
    assert "source_kind == \"SELECTED_GROUP\"" in helper
    assert "if source:" not in helper
    assert "return \"\"" not in helper

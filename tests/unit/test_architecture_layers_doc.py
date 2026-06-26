from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "ARCHITECTURE_LAYERS.md"


def test_architecture_layers_doc_exists_with_stage_entry_contract():
    text = DOC.read_text(encoding="utf-8")

    assert "# Architecture Layers" in text
    assert "functions/name.nf" in text
    assert "from functions import *" in text
    assert "does not mutate `builtins/registry.py`" in text
    assert "package-local backend helper names" in text
    assert "Conflicts raise controlled `CompileError`s" in text
    assert "`node(...)` remains the raw global escape hatch" in text

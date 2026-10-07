"""Keep the developer architecture note aligned with executable ownership boundaries."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DOC = PACKAGE_ROOT / "dev" / "ARCHITECTURE_LAYERS.md"


def test_architecture_layers_doc_names_current_owner_packages_and_boundaries():
    """The architecture note documents the dependency boundaries enforced by tests."""
    text = DOC.read_text(encoding="utf-8")
    assert "# Architecture Layers" in text
    assert "`semantic/` is Blender-independent" in text
    assert "`extensions/` owns the internal Extension API v2" in text
    assert "`blender/` owns physical realization" in text
    assert "`catalog.py` never imports `local_sources.py`" in text
    assert "production module-scope import graph must remain acyclic" in text
    assert "Internal filenames are not architectural contracts" in text


def test_architecture_layers_doc_does_not_present_retired_namespaces_as_current_owners():
    """Retired mixed namespaces are not described as current implementation owners."""
    text = DOC.read_text(encoding="utf-8")
    assert "builtins/registry.py" not in text
    assert "systems/registry.py" not in text

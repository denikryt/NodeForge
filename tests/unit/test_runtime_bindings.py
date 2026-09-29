"""Pure tests for permanent frontend runtime-binding metadata and reservation rules."""

from __future__ import annotations

import pytest

from NodeForge.compiler_identities import BindingId
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.runtime_bindings import (
    RuntimeBindingSymbol,
    allows_existing_top_level_shadow,
    format_reserved_binding_label,
    reserved_binding_label,
    validate_runtime_binding_target,
)


def test_runtime_binding_symbol_keeps_canonical_identity_and_type():
    """A permanent runtime binding is exactly one BindingId plus one NFType."""
    symbol = RuntimeBindingSymbol(BindingId("owner", 3), NFType.VECTOR)
    assert symbol.binding_id == BindingId("owner", 3)
    assert symbol.typ is NFType.VECTOR


def test_runtime_binding_symbol_rejects_noncanonical_identity_or_type():
    """Malformed frontend binding metadata fails at its constructor boundary."""
    with pytest.raises(TypeError, match="binding_id must be a BindingId"):
        RuntimeBindingSymbol(object(), NFType.FLOAT)
    with pytest.raises(TypeError, match="typ must be an NFType"):
        RuntimeBindingSymbol(BindingId("owner", 0), "FLOAT")


def test_runtime_binding_target_allows_documented_top_level_shadowing():
    """Existing builtin/compile-time labels retain the permanent shadowing contract."""
    labels = {"x": "DSL builtin", "k": "compile-time constant"}
    validate_runtime_binding_target("x", labels)
    validate_runtime_binding_target("k", labels)
    assert allows_existing_top_level_shadow("DSL builtin")
    assert reserved_binding_label(labels, "x") == "DSL builtin"


def test_runtime_binding_target_rejects_owned_names_with_stable_diagnostics():
    """Imported/local/type-token ownership is rejected at the binding-target boundary."""
    labels = {
        "f": "imported function",
        "g": "local function",
        "Float": "type token",
    }
    for name in labels:
        with pytest.raises(CompileError, match=f"Cannot assign to {name}"):
            validate_runtime_binding_target(name, labels)
    assert format_reserved_binding_label("imported function") == "already registered as imported function"


def test_runtime_binding_module_has_no_backend_or_legacy_binding_store_dependency():
    """Permanent binding metadata remains Blender-independent and has no mutable legacy store."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "runtime_bindings.py").read_text(encoding="utf-8")
    assert "from .values" not in source
    assert "import bpy" not in source
    assert "FrontendRuntimeBindings" not in source
    assert "legacy_structural" not in source

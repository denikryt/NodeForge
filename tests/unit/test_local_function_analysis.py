"""Unit checks for local definition identity and delegated materialization."""

from pathlib import Path

import pytest

from NodeForge.compiler_identities import local_function_id

pytestmark = pytest.mark.unit


def test_local_function_definition_owner_identity_is_non_lossy():
    """Distinct parent owner strings remain distinct in canonical local FunctionId identity."""
    dotted = local_function_id("A.B", "helper", "x:FLOAT")
    underscored = local_function_id("A_B", "helper", "x:FLOAT")
    assert dotted != underscored
    assert dotted.stable_key() != underscored.stable_key()


def test_local_call_hands_physical_materialization_to_function_materializer():
    """Frontend source-call analysis and physical local-helper realization stay separate."""
    root = Path(__file__).resolve().parents[2]
    semantic_source = (root / "semantic/analysis.py").read_text(encoding="utf-8")
    lowering_source = (root / "blender/ir_lowering.py").read_text(encoding="utf-8")
    physical_source = (root / "blender/local_functions.py").read_text(encoding="utf-8")

    assert "session.prepare_definition(" in semantic_source
    assert "IRFunctionMaterialization(" in semantic_source
    assert 'function_id.kind == "MODULE_DEF"' in lowering_source
    assert ".materialize_module(" in lowering_source
    assert "def compile_local_function_call" not in physical_source
    assert "resolve_reusable_function_materialization" not in physical_source

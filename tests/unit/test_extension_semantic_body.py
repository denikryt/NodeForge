"""Expression-local semantic body-boundary and semantic-only freshness tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from NodeForge.compiler_identities import GroupCompilationIdentity
from NodeForge.errors import CompileError
from NodeForge.extension_registry import ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot
from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
from NodeForge.semantic_group import analyze_group_source

pytestmark = pytest.mark.unit


def _fixture(tmp_path: Path):
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "interface.py").write_text(
        '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
@dataclass(frozen=True)
class Part:
    value: Float
@dataclass(frozen=True)
class _State:
    part: Part
EXTENSIONS = {"make": None, "make_empty": None, "consume": ".backend:consume"}
def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
def make_empty() -> list[Part]: ...
def consume(part: Part) -> Float: ...
''',
        encoding="utf-8",
    )
    (owner / "semantic.py").write_text(
        '''
from .interface import Part, _State
def make(value) -> Part: return Part(value)
def make_empty() -> list[Part]: return []
def consume(part) -> _State: return _State(part)
''',
        encoding="utf-8",
    )
    (owner / "backend.py").write_text("def consume(context, state): return None\n", encoding="utf-8")
    owner_key = ("system", "vendor.semantic", "body")
    session = ExtensionOwnerSession(capture_owner_code_snapshot(owner_key, owner))
    registry = ExtensionRegistry((session,))
    families, _ = session.normalize_interface()
    by_name = {cid.name: cid for cid in families}
    environment = ResolvedEnvironment(
        {
            "functions": ResolvedCatalog("functions", {}),
            "examples": ResolvedCatalog("examples", {}),
            "local": ResolvedCatalog("local", {}),
        },
        {},
        extension_registry=registry,
        extension_system_callables=by_name,
    )
    identity = GroupCompilationIdentity(None, "ROOT/package_semantic_values", "ROOT/package_semantic_values", "ROOT/package_semantic_values")
    return session, environment, identity


def test_nested_semantic_only_child_records_freshness_without_child_ircall(tmp_path):
    """Semantic-only use contributes the owner fingerprint even though only the executable parent reaches IR."""
    session, environment, identity = _fixture(tmp_path)
    compilation = analyze_group_source(
        "output(consume(make(x)))\n",
        compilation_identity=identity,
        resolved_environment=environment,
    )
    assert compilation.extension_dependencies == ((session.snapshot.owner_key, session.snapshot.fingerprint),)


def test_semantic_record_assignment_hits_expression_local_migration_boundary(tmp_path):
    """Expression-local semantic values refuse body persistence instead of publishing a runtime/structural/legacy binding."""
    _session, environment, identity = _fixture(tmp_path)
    with pytest.raises(CompileError, match="expression-local until persistent semantic-value integration"):
        analyze_group_source(
            "part = make(x)\noutput(x)\n",
            compilation_identity=identity,
            resolved_environment=environment,
        )


def test_empty_semantic_list_assignment_hits_same_migration_boundary(tmp_path):
    """Empty semantic LIST roots cannot escape the guard merely because their concrete ArrayResultShape has no leaves."""
    _session, environment, identity = _fixture(tmp_path)
    with pytest.raises(CompileError, match="expression-local until persistent semantic-value integration"):
        analyze_group_source(
            "parts = make_empty()\noutput(x)\n",
            compilation_identity=identity,
            resolved_environment=environment,
        )

"""Freshness-dependency coverage for semantic v2 extension use."""

from __future__ import annotations

from pathlib import Path

import pytest

from NodeForge.compiler_identities import GroupCompilationIdentity
from NodeForge.extension_registry import ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot
from NodeForge.function_instances import (
    FUNCTION_COMPILER_VERSION,
    FunctionCompilationTrace,
    function_group_owner_scope,
)
from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
from NodeForge.semantic_group import analyze_group_source

pytestmark = pytest.mark.unit


def _fixture(tmp_path: Path):
    """Build one detached v2 system registry and matching resolved environment."""
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "interface.py").write_text(
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
""",
        encoding="utf-8",
    )
    (owner / "operations.py").write_text("def build(context, value): return value\n", encoding="utf-8")
    owner_key = ("system", "vendor.demo", "math")
    session = ExtensionOwnerSession(capture_owner_code_snapshot(owner_key, owner))
    registry = ExtensionRegistry((session,))
    callable_id = next(iter(session.normalize_interface()[0]))
    environment = ResolvedEnvironment(
        {
            "functions": ResolvedCatalog("functions", {}),
            "examples": ResolvedCatalog("examples", {}),
            "local": ResolvedCatalog("local", {}),
        },
        {},
        extension_registry=registry,
        extension_system_callables={"foo": callable_id},
    )
    identity = GroupCompilationIdentity(None, "ROOT/freshness", "ROOT/freshness", "ROOT/freshness")
    return session, environment, identity


def test_semantic_use_records_one_deduplicated_owner_dependency(tmp_path):
    """Repeated accepted extension calls freeze one deterministic owner/fingerprint row."""
    session, environment, identity = _fixture(tmp_path)
    compilation = analyze_group_source(
        "a = foo(x)\nb = foo(a)\noutput(b)\n",
        compilation_identity=identity,
        resolved_environment=environment,
    )

    assert compilation.extension_dependencies == (
        (session.snapshot.owner_key, session.snapshot.fingerprint),
    )


def test_discovered_but_unused_extension_owner_records_no_dependency(tmp_path):
    """Registry discovery alone does not contaminate reusable-group freshness."""
    _session, environment, identity = _fixture(tmp_path)
    compilation = analyze_group_source(
        "output(x)\n",
        compilation_identity=identity,
        resolved_environment=environment,
    )
    assert compilation.extension_dependencies == ()


def test_existing_trace_accepts_extension_owner_scope_and_compiler_token_is_optional(tmp_path):
    """Prepared owner rows feed the existing trace serializer without a compiler epoch."""
    session, _environment, _identity = _fixture(tmp_path)
    scope = function_group_owner_scope("EXTENSION", *session.snapshot.owner_key)
    assert isinstance(scope, str) and scope
    assert FUNCTION_COMPILER_VERSION is None

    trace = FunctionCompilationTrace()
    with trace.group("ROOT/test", {"source": "x"}) as frame:
        frame.record_dependency_identity(scope, session.snapshot.fingerprint)
        first = frame.finish("iface")
    with trace.group("ROOT/test", {"source": "x"}) as frame:
        frame.record_dependency_identity(scope, session.snapshot.fingerprint + "changed")
        second = frame.finish("iface")
    assert first.fingerprint
    assert second.fingerprint
    assert first.fingerprint != second.fingerprint

"""Expression-local semantic body-boundary and semantic-only freshness tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from NodeForge.compiler_identities import GroupCompilationIdentity
from NodeForge.errors import CompileError
from NodeForge.extension_registry import ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot
from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
from NodeForge.semantic_group import analyze_group_source
from NodeForge.source_callables import SourceCallableSession

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _SourceRecord:
    """Minimal pure-source catalog record for extension/source boundary tests."""

    namespace: str
    name: str
    source_path: Path
    package_id: str = "vendor.pkg"
    package_version: str = "1.0.0"
    module_path: Path | None = None


def _fixture(tmp_path: Path):
    owner = tmp_path / "owner"
    owner.mkdir(parents=True)
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


def test_semantic_record_assignment_persists_and_keeps_owner_freshness(tmp_path):
    """Semantic-only assignment persists while still recording the owner fingerprint."""
    session, environment, identity = _fixture(tmp_path)
    compilation = analyze_group_source(
        'x = input_float("X")\npart = make(x)\noutput(consume(part))\n',
        compilation_identity=identity,
        resolved_environment=environment,
    )
    assert compilation.extension_dependencies == ((session.snapshot.owner_key, session.snapshot.fingerprint),)


def test_empty_semantic_list_assignment_is_valid_body_state(tmp_path):
    """Empty semantic LIST assignment survives the body boundary without fabricated runtime IR."""
    _session, environment, identity = _fixture(tmp_path)
    compilation = analyze_group_source(
        'x = input_float("X")\nparts = make_empty()\noutput(x)\n',
        compilation_identity=identity,
        resolved_environment=environment,
    )
    assert compilation.body is not None


def test_local_source_body_may_use_semantic_state_internally_when_runtime_result_escapes(tmp_path):
    """Source callable implementation may keep package semantic state internal and return an ordinary runtime result."""
    _session, environment, identity = _fixture(tmp_path)
    source_session = SourceCallableSession(resolved_environment=environment)
    compilation = analyze_group_source(
        'def f(v):\n    part = make(v)\n    return consume(part)\nx = input_float("X")\noutput(f(x))\n',
        compilation_identity=identity,
        resolved_environment=environment,
        source_callable_session=source_session,
    )
    assert compilation.body is not None


def test_transient_semantic_composition_in_group_body_has_no_persistence_statement(tmp_path):
    """A semantic value consumed in the same expression never crosses the Stage-34 persistence boundary."""
    _session, environment, identity = _fixture(tmp_path)
    compilation = analyze_group_source(
        'x = input_float("X")\ny = consume(make(x))\noutput(y)\n',
        compilation_identity=identity,
        resolved_environment=environment,
    )
    from NodeForge.semantic_ir import IRBindLeaves
    assert not any(isinstance(statement, IRBindLeaves) for statement in compilation.body.statements)


def test_source_group_output_rejects_package_semantic_value(tmp_path):
    """Package semantic state cannot become a source-callable public output."""
    _session, environment, identity = _fixture(tmp_path)
    with pytest.raises(CompileError, match="Package-defined semantic value cannot be used where a runtime value is required"):
        analyze_group_source(
            'x = input_float("X")\noutput(make(x))\n',
            compilation_identity=identity,
            resolved_environment=environment,
        )


def test_local_source_argument_rejects_package_semantic_value(tmp_path):
    """Package semantic state cannot cross a local source-callable input contract."""
    _session, environment, identity = _fixture(tmp_path)
    source_session = SourceCallableSession(resolved_environment=environment)
    with pytest.raises(CompileError, match="source function arguments cannot be package semantic values"):
        analyze_group_source(
            'x = input_float("X")\npart = make(x)\ndef f(p):\n    return p\noutput(f(part))\n',
            compilation_identity=identity,
            resolved_environment=environment,
            source_callable_session=source_session,
        )


def test_local_source_capture_rejects_package_semantic_value(tmp_path):
    """Package semantic state cannot cross a local source-callable capture contract."""
    _session, environment, identity = _fixture(tmp_path)
    source_session = SourceCallableSession(resolved_environment=environment)
    with pytest.raises(CompileError, match="cannot capture part: package semantic values are not supported"):
        analyze_group_source(
            'x = input_float("X")\npart = make(x)\ndef f():\n    return part\noutput(f())\n',
            compilation_identity=identity,
            resolved_environment=environment,
            source_callable_session=source_session,
        )

def test_imported_source_argument_rejects_package_semantic_value(tmp_path):
    """Persistent semantic state cannot cross an imported pure-source callable input contract."""
    _session, base, identity = _fixture(tmp_path / "extension")
    source_path = tmp_path / "demo.nf"
    source_path.write_text('v = input_float("V")\noutput(v)\n', encoding="utf-8")
    record = _SourceRecord("functions", "demo", source_path)
    environment = ResolvedEnvironment(
        {
            "functions": ResolvedCatalog("functions", {"demo": record}),
            "examples": base.catalog("examples"),
            "local": base.catalog("local"),
        },
        {},
        extension_registry=base.extension_registry,
        extension_system_callables=base.extension_system_callables,
    )
    source_session = SourceCallableSession(resolved_environment=environment)
    with pytest.raises(CompileError, match="source function arguments cannot be package semantic values"):
        analyze_group_source(
            'from functions import demo\nx = input_float("X")\npart = make(x)\noutput(demo(part))\n',
            compilation_identity=identity,
            resolved_environment=environment,
            source_callable_session=source_session,
        )


def test_persistent_read_does_not_reexecute_interface_module(tmp_path, monkeypatch):
    """Later persistent reads use the existing owner session and never remount interface.py discovery."""
    session, environment, identity = _fixture(tmp_path)
    original_mounted = session._mounted
    interface_mounts = []

    def guarded_mounted(phase):
        if phase == "INTERFACE":
            interface_mounts.append(phase)
        return original_mounted(phase)

    monkeypatch.setattr(session, "_mounted", guarded_mounted)
    compilation = analyze_group_source(
        'x = input_float("X")\npart = make(x)\ny = consume(part)\noutput(y)\n',
        compilation_identity=identity,
        resolved_environment=environment,
    )
    assert compilation.body is not None
    assert interface_mounts == []



def test_imported_source_body_may_use_semantic_state_internally(tmp_path):
    """Imported .nf implementation may keep package semantic state local before returning runtime data."""
    _session, base, identity = _fixture(tmp_path / "extension_internal")
    source_path = tmp_path / "demo_internal.nf"
    source_path.write_text(
        """v = input_float("V")
part = make(v)
y = consume(part)
output(y)
""",
        encoding="utf-8",
    )
    record = _SourceRecord("functions", "demo_internal", source_path)
    environment = ResolvedEnvironment(
        {
            "functions": ResolvedCatalog("functions", {"demo_internal": record}),
            "examples": base.catalog("examples"),
            "local": base.catalog("local"),
        },
        {},
        extension_registry=base.extension_registry,
        extension_system_callables=base.extension_system_callables,
    )
    source_session = SourceCallableSession(resolved_environment=environment)
    compilation = analyze_group_source(
        """from functions import demo_internal
y = demo_internal(2.0)
output(y)
""",
        compilation_identity=identity,
        resolved_environment=environment,
        source_callable_session=source_session,
    )
    assert compilation.body is not None

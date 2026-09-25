"""Owner-session phase isolation and mounted invocation regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from NodeForge.errors import CompileError
from NodeForge.extension_registry import ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot

pytestmark = pytest.mark.unit


def _session(tmp_path: Path, interface: str, **modules: str):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "interface.py").write_text(interface, encoding="utf-8")
    for name, source in modules.items():
        (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    session = ExtensionOwnerSession(capture_owner_code_snapshot(("system", "vendor.phase", tmp_path.name), tmp_path))
    registry = ExtensionRegistry((session,))
    families, _refs = session.normalize_interface()
    return session, registry, {cid.name: cid for cid in families}


def test_backend_body_lazy_relative_import_executes_inside_implementation_mount(tmp_path):
    """Physical code may perform a relative import from inside the function body."""
    interface = '''
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
'''
    _session_obj, registry, ids = _session(
        tmp_path,
        interface,
        backend='''\ndef foo(context, value):\n    from .helper import TOKEN\n    return TOKEN\n''',
        helper="TOKEN = 'implementation-generation'\n",
    )
    assert registry.invoke_implementation(ids["foo"], None, None) == "implementation-generation"


def test_implementation_invocation_enters_and_leaves_implementation_mount(tmp_path):
    """The only physical execution API mounts IMPLEMENTATION for the complete function body."""
    interface = '''
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
'''
    session, registry, ids = _session(
        tmp_path,
        interface,
        backend='''
def foo(context, value):
    from .helper import TOKEN
    return TOKEN
''',
        helper="TOKEN = 'mounted-invocation'\n",
    )
    assert session._phase is None
    assert registry.invoke_implementation(ids["foo"], None, None) == "mounted-invocation"
    assert session._phase is None


def test_semantic_body_lazy_relative_import_executes_inside_semantic_mount(tmp_path):
    """Semantic code may perform a relative import from inside the semantic function body."""
    interface = '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
@dataclass(frozen=True)
class Part:
    value: Float
EXTENSIONS = {"make": None}
def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
'''
    session, registry, ids = _session(
        tmp_path,
        interface,
        semantic='''\nfrom .interface import Part\ndef make(value) -> Part:\n    from .helper import STATIC\n    return Part(STATIC)\n''',
        helper="STATIC = 2.0\n",
    )
    return_type = registry.semantic_return_type(ids["make"])
    assert return_type.kind == "RECORD"
    result = registry.invoke_semantic(ids["make"], object())
    assert result.value == 2.0
    assert type(result) is registry.python_class_for(return_type.record_type)


def test_shared_helper_module_generation_is_not_reused_across_backend_and_semantic_phases(tmp_path):
    """A helper retaining a backend module in IMPLEMENTATION cannot carry that reference into SEMANTIC."""
    interface = '''
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo", "aux": ".backend_aux:aux"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
def aux(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
'''
    session, registry, ids = _session(
        tmp_path,
        interface,
        semantic='''\ndef foo(value) -> bool:\n    from . import shared\n    return shared.retained is None\n''',
        backend='''\ndef foo(context, state):\n    from . import shared\n    shared.retain_backend()\n    return shared.retained\n''',
        backend_aux="def aux(context, value): return value\n",
        shared='''\nretained = None\ndef retain_backend():\n    global retained\n    from . import backend_aux\n    retained = backend_aux\n''',
    )
    retained = registry.invoke_implementation(ids["foo"], None, None)
    assert retained.__name__.endswith(".backend_aux")
    # A fresh semantic-phase helper generation must not inherit retained backend globals.
    assert registry.invoke_semantic(ids["foo"], None) is True
    semantic_shared = session._phase_modules["SEMANTIC"]["shared"]
    implementation_shared = session._phase_modules["IMPLEMENTATION"]["shared"]
    assert semantic_shared is not implementation_shared
    assert semantic_shared.retained is None


def test_semantic_phase_cannot_import_known_physical_target(tmp_path):
    """Physical implementation target imports are rejected structurally from semantic.py."""
    interface = '''
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
'''
    _session_obj, registry, ids = _session(
        tmp_path,
        interface,
        semantic='''\nfrom . import backend\ndef foo(value) -> int: return 1\n''',
        backend="def foo(context, state): return state\n",
    )
    with pytest.raises(CompileError, match="SEMANTIC"):
        registry.semantic_return_type(ids["foo"])




@pytest.mark.parametrize(
    "owner_import",
    [
        "from . import shared",
        "from .shared import VALUE",
        'import importlib; importlib.import_module(".shared", __package__)',
    ],
)
def test_interface_rejects_all_owner_local_child_import_forms(tmp_path, owner_import):
    """Canonical interface.py cannot retain any owner-local module object in its globals."""
    root = tmp_path / "interface-owner-import"
    root.mkdir()
    interface = f"""
{owner_import}
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {{"foo": ".backend:foo"}}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
"""
    root.joinpath("interface.py").write_text(interface, encoding="utf-8")
    root.joinpath("shared.py").write_text("VALUE = 1\n", encoding="utf-8")
    root.joinpath("backend.py").write_text("def foo(context, value): return value\n", encoding="utf-8")
    session = ExtensionOwnerSession(
        capture_owner_code_snapshot(("system", "vendor.phase", f"interface-owner-{abs(hash(owner_import))}"), root)
    )
    with pytest.raises(CompileError, match="declaration-only.*owner-local"):
        session.normalize_interface()


def test_interface_owner_local_import_ban_prevents_cross_phase_global_leak(tmp_path):
    """A helper can never enter canonical interface globals and become reachable from SEMANTIC."""
    root = tmp_path / "interface-global-leak"
    root.mkdir()
    root.joinpath("interface.py").write_text(
        """
from . import shared
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".backend:foo"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
""",
        encoding="utf-8",
    )
    root.joinpath("shared.py").write_text("VALUE = 7\n", encoding="utf-8")
    root.joinpath("semantic.py").write_text(
        "from . import interface\ndef foo(value) -> int: return interface.shared.VALUE\n",
        encoding="utf-8",
    )
    root.joinpath("backend.py").write_text("def foo(context, value): return value\n", encoding="utf-8")
    session = ExtensionOwnerSession(
        capture_owner_code_snapshot(("system", "vendor.phase", "interface-global-leak"), root)
    )
    with pytest.raises(CompileError, match="declaration-only.*owner-local"):
        session.normalize_interface()
    assert "shared" not in session.modules


def test_interface_is_declaration_only_and_cannot_be_physical_target(tmp_path):
    """The one canonical cross-phase module cannot also carry physical implementation code."""
    interface = '''
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".interface:_foo_impl"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
def _foo_impl(context, value): return value
'''
    (tmp_path / "interface.py").write_text(interface, encoding="utf-8")
    session = ExtensionOwnerSession(capture_owner_code_snapshot(("system", "vendor.phase", "interface-target"), tmp_path))
    with pytest.raises(CompileError, match="declaration-only"):
        session.normalize_interface()


def test_semantic_source_participates_in_fingerprint_but_mtime_does_not(tmp_path):
    """Freshness hashes captured bytes, including semantic.py, rather than filesystem metadata."""
    interface = '''\nfrom typing import Annotated\nfrom NodeForge import EvaluationMode, Float\nEXTENSION_API=2\nEXTENSIONS={"foo": None}\ndef foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n'''
    (tmp_path / "interface.py").write_text(interface, encoding="utf-8")
    semantic = tmp_path / "semantic.py"
    semantic.write_text("def foo(value) -> int: return 1\n", encoding="utf-8")
    first = capture_owner_code_snapshot(("system", "vendor.phase", "fingerprint"), tmp_path)
    assert "semantic" in first.modules
    stat = semantic.stat()
    semantic.touch()
    second = capture_owner_code_snapshot(("system", "vendor.phase", "fingerprint"), tmp_path)
    assert second.fingerprint == first.fingerprint
    semantic.write_text("def foo(value) -> int: return 2\n", encoding="utf-8")
    third = capture_owner_code_snapshot(("system", "vendor.phase", "fingerprint"), tmp_path)
    assert third.fingerprint != first.fingerprint


def test_interface_normalization_does_not_execute_semantic_module(tmp_path):
    """Installing/normalizing interface declarations never executes semantic.py."""
    interface = '''\nfrom dataclasses import dataclass\nfrom typing import Annotated\nfrom NodeForge import EvaluationMode, Float\nEXTENSION_API=2\n@dataclass(frozen=True)\nclass Part:\n    value: Float\nEXTENSIONS={"make": None}\ndef make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...\n'''
    session, _registry, _ids = _session(
        tmp_path,
        interface,
        semantic="raise RuntimeError('semantic executed too early')\n",
    )
    families, _refs = session.normalize_interface()
    assert families
    assert "semantic" not in session.modules


def test_interface_and_backend_cannot_import_semantic_module(tmp_path):
    """semantic.py is phase-owned and unavailable to declaration/physical code."""
    interface_imports_semantic = '''\nfrom . import semantic\nfrom typing import Annotated\nfrom NodeForge import EvaluationMode, Float\nEXTENSION_API=2\nEXTENSIONS={"foo": ".backend:foo"}\ndef foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n'''
    root = tmp_path / "interface"
    root.mkdir()
    root.joinpath("interface.py").write_text(interface_imports_semantic, encoding="utf-8")
    root.joinpath("semantic.py").write_text("VALUE=1\n", encoding="utf-8")
    root.joinpath("backend.py").write_text("def foo(context, value): return value\n", encoding="utf-8")
    with pytest.raises(CompileError, match="declaration-only.*owner-local"):
        ExtensionOwnerSession(capture_owner_code_snapshot(("system", "vendor.phase", "if-sem"), root)).normalize_interface()

    interface = '''\nfrom typing import Annotated\nfrom NodeForge import EvaluationMode, Float\nEXTENSION_API=2\nEXTENSIONS={"foo": ".backend:foo"}\ndef foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n'''
    _session_obj, registry, ids = _session(
        tmp_path / "backend",
        interface,
        semantic="VALUE=1\n",
        backend='''\ndef foo(context, value):\n    from . import semantic\n    return semantic.VALUE\n''',
    )
    with pytest.raises(CompileError, match="IMPLEMENTATION"):
        registry.invoke_implementation(ids["foo"], None, None)


def test_interface_record_class_identity_is_canonical_across_semantic_and_implementation(tmp_path):
    """Only interface.py crosses phases so record classes keep one exact session identity."""
    interface = '''\nfrom dataclasses import dataclass\nfrom typing import Annotated\nfrom NodeForge import EvaluationMode, Float\nEXTENSION_API=2\n@dataclass(frozen=True)\nclass Part:\n    value: Float\nEXTENSIONS={"foo": ".backend:foo"}\ndef foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n'''
    session, registry, ids = _session(
        tmp_path,
        interface,
        semantic='''\nfrom .interface import Part\ndef foo(value) -> int:\n    return id(Part)\n''',
        backend='''\ndef foo(context, state):\n    from .interface import Part\n    return id(Part)\n''',
    )
    part_id = next(type_id for type_id in session.type_specs() if type_id.name == "Part")
    canonical = registry.python_class_for(part_id)
    assert registry.invoke_semantic(ids["foo"], None) == id(canonical)
    assert registry.invoke_implementation(ids["foo"], None, None) == id(canonical)


def test_phase_owned_child_attributes_are_removed_after_unmount(tmp_path):
    """Canonical package containers cannot retain semantic/backend helper modules after a phase exits."""
    interface = '''\nfrom typing import Annotated\nfrom NodeForge import EvaluationMode, Float\nEXTENSION_API=2\nEXTENSIONS={"foo": ".backend:foo"}\ndef foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n'''
    session, registry, ids = _session(
        tmp_path,
        interface,
        semantic='''\ndef foo(value) -> int:\n    from . import helper\n    return helper.VALUE\n''',
        backend='''\ndef foo(context, state):\n    from . import helper\n    return helper.VALUE\n''',
        helper="VALUE=3\n",
    )
    assert registry.invoke_semantic(ids["foo"], None) == 3
    assert not hasattr(session._base_module, "semantic")
    assert not hasattr(session._base_module, "helper")
    assert registry.invoke_implementation(ids["foo"], None, None) == 3
    assert not hasattr(session._base_module, "backend")
    assert not hasattr(session._base_module, "helper")


def test_semantic_exception_is_wrapped_once_and_preserves_cause(tmp_path):
    """Unexpected package semantic failures become controlled diagnostics with the original cause."""
    interface = '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API=2
@dataclass(frozen=True)
class Part:
    value: Float
EXTENSIONS={"make": None}
def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
'''
    _session_obj, registry, ids = _session(
        tmp_path,
        interface,
        semantic='''\nfrom .interface import Part\ndef make(value) -> Part:\n    raise RuntimeError("boom")\n''',
    )
    with pytest.raises(CompileError, match="semantic implementation.*failed") as exc_info:
        registry.invoke_semantic(ids["make"], 1.0)
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "boom"

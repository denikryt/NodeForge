"""Snapshot, laziness, and owner freshness tests for ExtensionRegistry."""

import builtins
import sys
import types
from pathlib import Path

import pytest

from NodeForge.errors import CompileError
from NodeForge.extensions.registry import ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot

pytestmark = pytest.mark.unit


_INTERFACE = """
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build_foo"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
"""


def _owner(tmp_path: Path, operations: str):
    """Capture one minimal system owner with a lazy operations module."""
    (tmp_path / "interface.py").write_text(_INTERFACE, encoding="utf-8")
    (tmp_path / "operations.py").write_text(operations, encoding="utf-8")
    return capture_owner_code_snapshot(("system", "vendor.demo", "math"), tmp_path)


def test_implementation_module_is_lazy_and_executes_captured_bytes(tmp_path):
    """Implementation resolution must stay lazy and use captured, not live, bytes."""
    marker = "_nodeforge_extension_v2_registry_hits"
    if hasattr(builtins, marker):
        delattr(builtins, marker)
    snapshot = _owner(
        tmp_path,
        f"import builtins\nbuiltins.{marker} = getattr(builtins, {marker!r}, 0) + 1\ndef build_foo(context, value): return value\n",
    )
    session = ExtensionOwnerSession(snapshot)
    registry = ExtensionRegistry((session,))
    assert not hasattr(builtins, marker)
    callable_id = next(iter(session.normalize_interface()[0]))

    (tmp_path / "operations.py").write_text("raise RuntimeError('live disk must not be used')\n", encoding="utf-8")
    assert registry.invoke_implementation(callable_id, None, None) is None
    assert getattr(builtins, marker) == 1
    delattr(builtins, marker)


def test_interface_eager_import_of_physical_implementation_is_rejected(tmp_path):
    """Declaration-only interface.py rejects physical owner-local modules at the loader boundary."""
    (tmp_path / "interface.py").write_text(
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float
from .operations import build_foo

EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build_foo"}

def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
""",
        encoding="utf-8",
    )
    (tmp_path / "operations.py").write_text(
        "def build_foo(context, value): return value\n",
        encoding="utf-8",
    )
    session = ExtensionOwnerSession(
        capture_owner_code_snapshot(("system", "vendor.demo", "eager"), tmp_path)
    )

    with pytest.raises(CompileError, match=r"declaration-only.*owner-local module '\.operations'"):
        session.normalize_interface()


def test_lazy_implementation_load_does_not_poison_cached_interface_normalization(tmp_path):
    """Modules loaded after successful normalization do not become false eager-import failures."""
    snapshot = _owner(tmp_path, "def build_foo(context, value): return value\n")
    session = ExtensionOwnerSession(snapshot)
    families, refs = session.normalize_interface()
    callable_id = next(iter(families))

    session.invoke_implementation(callable_id, None, None)
    cached_families, cached_refs = session.normalize_interface()

    assert cached_families is families
    assert cached_refs is refs


def test_owner_fingerprint_changes_with_implementation_bytes_not_mtime(tmp_path):
    """Owner freshness must depend on source bytes rather than filesystem metadata."""
    first = _owner(tmp_path, "def build_foo(context, value): return value\n")
    second = capture_owner_code_snapshot(first.owner_key, tmp_path)
    assert first.fingerprint == second.fingerprint
    (tmp_path / "operations.py").write_text("def build_foo(context, value):\n    return value\n", encoding="utf-8")
    third = capture_owner_code_snapshot(first.owner_key, tmp_path)
    assert third.fingerprint != first.fingerprint


def test_root_init_affects_fingerprint_without_becoming_executable_module(tmp_path):
    """Root __init__.py stays a freshness input without becoming runtime snapshot state."""
    (tmp_path / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    first = _owner(tmp_path, "def build_foo(context, value): return value\n")

    assert "" not in first.modules
    assert "__init__" not in first.modules

    (tmp_path / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
    second = capture_owner_code_snapshot(first.owner_key, tmp_path)

    assert second.fingerprint != first.fingerprint
    assert "" not in second.modules
    assert "__init__" not in second.modules


def test_new_owner_session_masks_stale_synthetic_modules(tmp_path):
    """A fresh registry generation must never import a prior-session module object."""
    snapshot = _owner(tmp_path, "def build_foo(context, value): return 'fresh'\n")
    session = ExtensionOwnerSession(snapshot)
    registry = ExtensionRegistry((session,))
    callable_id = next(iter(session.normalize_interface()[0]))

    stale_name = snapshot.module_base + ".operations"
    stale_module = types.ModuleType(stale_name)
    stale_module.build_foo = lambda context, value: "stale"
    previous = sys.modules.get(stale_name)
    sys.modules[stale_name] = stale_module
    try:
        assert registry.invoke_implementation(callable_id, None, None) == "fresh"
        assert sys.modules.get(stale_name) is stale_module
    finally:
        if previous is None:
            sys.modules.pop(stale_name, None)
        else:
            sys.modules[stale_name] = previous


def test_interface_cannot_persist_manually_injected_owner_module(tmp_path):
    """Only snapshot-loaded modules may survive between interface and implementation phases."""
    interface = """
import sys
import types
from typing import Annotated
from NodeForge import EvaluationMode, Float

EXTENSION_API = 2

fake = types.ModuleType(__package__ + ".operations")
fake.build_foo = lambda context, value: "fake"
fake.injected_fake = True
sys.modules[__package__ + ".operations"] = fake

EXTENSIONS = {"foo": ".operations:build_foo"}

def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
"""
    (tmp_path / "interface.py").write_text(interface, encoding="utf-8")
    (tmp_path / "operations.py").write_text(
        "def build_foo(context, value): return 'captured'\n",
        encoding="utf-8",
    )
    snapshot = capture_owner_code_snapshot(("system", "vendor.demo", "injected"), tmp_path)
    session = ExtensionOwnerSession(snapshot)
    registry = ExtensionRegistry((session,))
    callable_id = next(iter(session.normalize_interface()[0]))

    assert registry.invoke_implementation(callable_id, None, None) == "captured"

    assert not getattr(session.modules["operations"], "injected_fake", False)

def test_nested_owner_local_imports_use_snapshot_package_hierarchy(tmp_path):
    """Nested implementation helpers resolve through the mounted IMPLEMENTATION snapshot generation."""
    (tmp_path / "helpers").mkdir()
    (tmp_path / "helpers" / "deep.py").write_text("CONST = 2\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("FLAG = 3\n", encoding="utf-8")
    (tmp_path / "impl").mkdir()
    (tmp_path / "impl" / "ops.py").write_text(
        "def build_foo(context, value):\n"
        "    from ..helpers.deep import CONST\n"
        "    from ..pkg import FLAG\n"
        "    return value, CONST + FLAG\n",
        encoding="utf-8",
    )
    (tmp_path / "interface.py").write_text(
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float

EXTENSION_API = 2
EXTENSIONS = {"foo": ".impl.ops:build_foo"}

def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
""",
        encoding="utf-8",
    )

    previous_root = sys.modules.get("_nodeforge_ext")
    sentinel_root = types.ModuleType("_nodeforge_ext")
    sys.modules["_nodeforge_ext"] = sentinel_root
    try:
        snapshot = capture_owner_code_snapshot(("system", "vendor.demo", "nested"), tmp_path)
        session = ExtensionOwnerSession(snapshot)
        families, _refs = session.normalize_interface()
        callable_id = next(iter(families))
        assert session.invoke_implementation(callable_id, None, 7) == (7, 5)
        assert sys.modules["_nodeforge_ext"] is sentinel_root
    finally:
        if previous_root is None:
            sys.modules.pop("_nodeforge_ext", None)
        else:
            sys.modules["_nodeforge_ext"] = previous_root


def test_namespace_packages_are_only_missing_parent_modules(tmp_path):
    """Captured packages are not also classified as namespace packages."""
    (tmp_path / "interface.py").write_text(_INTERFACE, encoding="utf-8")
    (tmp_path / "operations.py").write_text(
        "def build_foo(context, value): return value\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "leaf.py").write_text("VALUE = 3\n", encoding="utf-8")

    snapshot = capture_owner_code_snapshot(("system", "vendor.demo", "namespace"), tmp_path)

    assert "pkg" in snapshot.modules
    assert "pkg" not in snapshot.namespace_packages
    assert "nested" in snapshot.namespace_packages

"""Focused interface-record normalization tests for package semantic values."""

from __future__ import annotations

from pathlib import Path

import pytest

from NodeForge.errors import CompileError
from NodeForge.extensions.registry import ExtensionOwnerSession, capture_owner_code_snapshot

pytestmark = pytest.mark.unit


def _normalize(tmp_path: Path, owner_suffix: str, source: str):
    """Normalize one isolated interface module and return its owner session."""
    root = tmp_path / owner_suffix
    root.mkdir()
    (root / "interface.py").write_text(source, encoding="utf-8")
    session = ExtensionOwnerSession(
        capture_owner_code_snapshot(("system", "vendor.records", owner_suffix), root)
    )
    session.normalize_interface()
    return session


def test_imported_record_lookalike_is_not_registered_as_owner_semantic_type(tmp_path, monkeypatch):
    """Only dataclasses defined by the selected interface module become nominal owner types."""
    external = tmp_path / "external_records.py"
    external.write_text(
        "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Part:\n    value: float\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    root = tmp_path / "imported"
    root.mkdir()
    (root / "interface.py").write_text(
        '''
from typing import Annotated
from NodeForge import EvaluationMode, Float
from external_records import Part
EXTENSION_API = 2
EXTENSIONS = {"make": None}
def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
''',
        encoding="utf-8",
    )
    session = ExtensionOwnerSession(
        capture_owner_code_snapshot(("system", "vendor.records", "imported"), root)
    )
    with pytest.raises(CompileError, match="Unsupported.*result annotation"):
        session.normalize_interface()


def test_mutable_dataclass_is_rejected(tmp_path):
    """Package semantic records must be frozen declarations before any semantic execution."""
    source = '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
@dataclass
class Part:
    value: Float
EXTENSIONS = {"make": None}
def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
'''
    with pytest.raises(CompileError, match="frozen"):
        _normalize(tmp_path, "mutable", source)


def test_record_alias_plus_redefinition_is_rejected_before_type_id_collision(tmp_path):
    """Two owner-local class objects cannot become candidates for one owner/name semantic identity."""
    source = '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2

@dataclass(frozen=True)
class Part:
    x: Float

OldPart = Part

@dataclass(frozen=True)
class Part:
    y: Float

EXTENSIONS = {"make": None}
def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
'''
    with pytest.raises(CompileError, match="declared class name|type identity"):
        _normalize(tmp_path, "alias-redefinition", source)


def test_public_record_cannot_inherit_private_record(tmp_path):
    """Private semantic implementation types cannot leak through a public nominal hierarchy."""
    source = '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
@dataclass(frozen=True)
class _Private:
    value: Float
@dataclass(frozen=True)
class Public(_Private):
    pass
EXTENSIONS = {"make": None}
def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Public: ...
'''
    with pytest.raises(CompileError, match="private"):
        _normalize(tmp_path, "public-private", source)

"""Contract and analysis regressions for omitted Extension API v2 typed arguments."""

import inspect
from pathlib import Path

import pytest

from NodeForge.errors import CompileError
from NodeForge.extensions.registry import ExtensionOwnerSession, capture_owner_code_snapshot
from NodeForge.evaluation_modes import EvaluationMode

pytestmark = pytest.mark.unit


def _owner(tmp_path: Path, interface: str, *, semantic: str | None = None):
    """Capture a synthetic extension owner in the same source layout as packages."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "interface.py").write_text(interface, encoding="utf-8")
    (tmp_path / "backend.py").write_text("def sample(*args, **kwargs): pass\n", encoding="utf-8")
    if semantic is not None:
        (tmp_path / "semantic.py").write_text(semantic, encoding="utf-8")
    return ExtensionOwnerSession(capture_owner_code_snapshot(("system", "test.optional", tmp_path.name), tmp_path))


@pytest.mark.parametrize("mode", list(EvaluationMode))
def test_declaration_accepts_omitted_typed_union_without_type(tmp_path, mode):
    """None is a declaration-level omission marker, including for runtime-only unions."""
    source = f'''from typing import Annotated
from NodeForge import Float, Int, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {{"sample": ".backend:sample"}}
def sample(required: Annotated[Float, EvaluationMode.RUNTIME_ONLY],
           *, optional: Annotated[Float | Int, EvaluationMode.{mode.name}] = None) -> Float: ...
'''
    owner = _owner(tmp_path, source)
    spec = next(iter(owner.normalize_interface()[0].values()))[0]
    assert spec.parameters[0].default is inspect.Parameter.empty
    assert spec.parameters[1].default is None and spec.parameters[1].default_type is None
    bound = spec.python_signature().bind(object())
    bound.apply_defaults()
    assert bound.arguments["optional"] is None
    with pytest.raises(TypeError):
        spec.python_signature().bind()


@pytest.mark.parametrize("annotation, default, error", [
    ("Annotated[Float, EvaluationMode.RUNTIME_ONLY]", "1.0", "RUNTIME_ONLY"),
    ("Annotated[Float | None, EvaluationMode.COMPILE_TIME_ONLY]", "None", "union members"),
    ("Annotated[Any, EvaluationMode.COMPILE_TIME_ONLY]", "None", "Any"),
    ("Annotated[Float, EvaluationMode.COMPILE_TIME_ONLY]", "'wrong'", "default"),
])
def test_invalid_optional_declarations_stay_rejected(tmp_path, annotation, default, error):
    """Only an exact Python None default can bypass typed-value normalization."""
    source = f'''from typing import Annotated, Any
from NodeForge import Float, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {{"sample": ".backend:sample"}}
def sample(value: {annotation} = {default}) -> Float: ...
'''
    with pytest.raises(CompileError, match=error):
        _owner(tmp_path, source).normalize_interface()

"""Interface normalization tests for backend-only v2 extension declarations."""

from pathlib import Path

import pytest

from NodeForge import Bool, Float
from NodeForge.errors import CompileError
from NodeForge.extension_registry import ExtensionOwnerSession, capture_owner_code_snapshot
from NodeForge.nf_types import NFType

pytestmark = pytest.mark.unit


def _session(tmp_path: Path, interface_source: str, operations_source: str = "def build(*args, **kwargs): return None\n"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "interface.py").write_text(interface_source, encoding="utf-8")
    (tmp_path / "operations.py").write_text(operations_source, encoding="utf-8")
    snapshot = capture_owner_code_snapshot(("system", "vendor.demo", "math"), tmp_path)
    return ExtensionOwnerSession(snapshot)


def test_extends_keys_are_single_inventory_and_overloads_are_source_ordered(tmp_path):
    """EXTENSIONS alone defines inventory and overload stubs retain source order."""
    session = _session(
        tmp_path,
        """
from typing import Annotated, overload
from NodeForge import Bool, EvaluationMode, Float, Int
EXTENSION_API = 2
EXTENSIONS = {"select": ".operations:build"}
@overload
def select(cond: Annotated[Bool, EvaluationMode.RUNTIME_ONLY], a: Annotated[Int, EvaluationMode.RUNTIME_ONLY], b: Annotated[Int, EvaluationMode.RUNTIME_ONLY]) -> Int: ...
@overload
def select(cond: Annotated[Bool, EvaluationMode.RUNTIME_ONLY], a: Annotated[Float, EvaluationMode.RUNTIME_ONLY], b: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
def select(*args, **kwargs): ...
def helper(): ...
""",
    )
    families, refs = session.normalize_interface()
    assert [callable_id.name for callable_id in families] == ["select"]
    specs = next(iter(families.values()))
    assert [next(iter(spec.result.nf_types)) for spec in specs] == [NFType.INT, NFType.FLOAT]
    assert next(iter(refs.values())).attribute == "build"


def test_runtime_only_default_is_rejected_without_synthetic_runtime_literal(tmp_path):
    """Runtime-only defaults stay unsupported instead of creating AST-free operands."""
    session = _session(
        tmp_path,
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY] = 1.0) -> Float: ...
""",
    )
    with pytest.raises(CompileError, match="RUNTIME_ONLY"):
        session.normalize_interface()


def test_imported_keyed_declaration_is_rejected(tmp_path):
    """A keyed declaration must be defined in the current fresh interface globals."""
    (tmp_path / "decls.py").write_text(
        "from typing import Annotated\nfrom NodeForge import EvaluationMode, Float\ndef foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n",
        encoding="utf-8",
    )
    session = _session(
        tmp_path,
        """
from .decls import foo
EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build"}
""",
    )
    with pytest.raises(CompileError, match="current interface"):
        session.normalize_interface()


@pytest.mark.parametrize(
    "header, message",
    [
        ("", "EXTENSION_API"),
        ("EXTENSION_API = 1\n", "EXTENSION_API"),
        ("EXTENSION_API = 2\n", "EXTENSIONS"),
        ("EXTENSION_API = 2\nEXTENSIONS = []\n", "EXTENSIONS"),
        ("EXTENSION_API = 2\nEXTENSIONS = {}\n", "EXTENSIONS"),
    ],
)
def test_interface_protocol_header_is_strict(tmp_path, header, message):
    """The v2 marker and one non-empty mapping are mandatory protocol data."""
    session = _session(tmp_path, header)
    with pytest.raises(CompileError, match=message):
        session.normalize_interface()


def test_only_exact_exported_markers_are_accepted(tmp_path):
    """Marker subclasses and lookalikes cannot impersonate compiler-owned annotations."""
    session = _session(
        tmp_path,
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float
class MyFloat(Float): pass
EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build"}
def foo(value: Annotated[MyFloat, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
""",
    )
    with pytest.raises(CompileError, match="Unsupported"):
        session.normalize_interface()

    class FakeFloat:
        __nodeforge_nf_type__ = NFType.FLOAT
    assert FakeFloat is not Float


def test_marker_classes_are_annotation_only_and_non_instantiable():
    """Public annotation markers must not become runtime DSL values."""
    with pytest.raises(TypeError, match="annotation markers"):
        Float()
    with pytest.raises(TypeError, match="annotation markers"):
        Bool()


@pytest.mark.parametrize(
    "annotation, message",
    [
        ("Float", "EvaluationMode"),
        ("Annotated[Float, EvaluationMode.RUNTIME_ONLY, EvaluationMode.COMPILE_TIME_ONLY]", "exactly one"),
        ("Annotated[NFType.FLOAT, EvaluationMode.RUNTIME_ONLY]", "NFType"),
        ("Annotated[Any, EvaluationMode.RUNTIME_ONLY]", "Any"),
        ("Annotated[list[Float], EvaluationMode.RUNTIME_ONLY]", "Unsupported"),
        ("Annotated[tuple[Float, Int], EvaluationMode.RUNTIME_ONLY]", "Unsupported"),
    ],
)
def test_parameter_annotation_surface_is_narrow(tmp_path, annotation, message):
    """Unsupported annotation forms fail during interface normalization."""
    session = _session(
        tmp_path,
        f"""
from typing import Annotated, Any
from NodeForge import EvaluationMode, Float, Int
from NodeForge.extension_api import NFType
EXTENSION_API = 2
EXTENSIONS = {{"foo": ".operations:build"}}
def foo(value: {annotation}) -> Float: ...
""",
    )
    with pytest.raises(CompileError, match=message):
        session.normalize_interface()


def test_reserved_parameter_and_kwargs_are_rejected(tmp_path):
    """Compiler-reserved keyword syntax and VAR_KEYWORD stay outside the public ABI."""
    reserved = _session(
        tmp_path / "reserved",
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build"}
def foo(__unique__: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
""",
    )
    with pytest.raises(CompileError, match="reserved"):
        reserved.normalize_interface()

    kwargs = _session(
        tmp_path / "kwargs",
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build"}
def foo(**kwargs: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
""",
    )
    with pytest.raises(CompileError, match=r"\*\*kwargs"):
        kwargs.normalize_interface()


def test_result_requires_exact_leaf_types(tmp_path):
    """Union results are rejected while fixed tuples of exact leaves are executable."""
    union = _session(
        tmp_path / "union",
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float, Int
EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float | Int: ...
""",
    )
    with pytest.raises(CompileError, match="exact NFType"):
        union.normalize_interface()

    fixed = _session(
        tmp_path / "fixed",
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float, Int
EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> tuple[Float, Int]: ...
""",
    )
    families, _refs = fixed.normalize_interface()
    result = next(iter(families.values()))[0].result
    assert result.kind == "TUPLE_FIXED"
    assert [next(iter(item.nf_types)) for item in result.items] == [NFType.FLOAT, NFType.INT]


def test_detached_defaults_are_canonicalized_for_supported_types(tmp_path):
    """Compile-time and mixed defaults become detached canonical contract values once."""
    session = _session(
        tmp_path,
        """
from typing import Annotated
from NodeForge import Bool, EvaluationMode, Float, Int, String, Vector
EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build"}
def foo(
    *,
    f: Annotated[Float, EvaluationMode.COMPILE_TIME_ONLY] = 1,
    i: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = 2,
    b: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = True,
    v: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = (1, 2, 3),
    s: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = "x",
) -> Float: ...
""",
    )
    specs, _refs = session.normalize_interface()
    params = {item.name: item for item in next(iter(specs.values()))[0].parameters}
    assert params["f"].default == 1.0 and params["f"].default_type is NFType.FLOAT
    assert params["i"].default == 2 and params["i"].default_type is NFType.INT
    assert params["b"].default is True and params["b"].default_type is NFType.BOOL
    assert params["v"].default == (1.0, 2.0, 3.0) and params["v"].default_type is NFType.VECTOR
    assert params["s"].default == "x" and params["s"].default_type is NFType.STRING


def test_none_default_and_unresolved_result_tuple_are_rejected(tmp_path):
    """Nullability and variadic result tuples are deferred rather than inferred implicitly."""
    none_default = _session(
        tmp_path / "none",
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build"}
def foo(value: Annotated[Float, EvaluationMode.COMPILE_TIME_ONLY] = None) -> Float: ...
""",
    )
    with pytest.raises(CompileError, match="Unsupported extension parameter default"):
        none_default.normalize_interface()

    tuple_var = _session(
        tmp_path / "tuplevar",
        """
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"foo": ".operations:build"}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> tuple[Float, ...]: ...
""",
    )
    with pytest.raises(CompileError, match="variadic tuple"):
        tuple_var.normalize_interface()


def test_implementation_reference_is_owner_relative_and_structural(tmp_path):
    """Interface validation accepts only captured owner-relative symbolic implementation refs."""
    for ref, message in [
        ("operations:build", "owner-relative"),
        ("..operations:build", "owner-relative"),
        (".semantic:build", "semantic.py"),
        (".missing:build", "does not exist"),
        (".operations:build.attr", "attribute"),
    ]:
        root = tmp_path / ref.replace("/", "_").replace(":", "_").replace(".", "d")
        session = _session(
            root,
            f"""
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {{"foo": {ref!r}}}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
""",
        )
        with pytest.raises(CompileError, match=message):
            session.normalize_interface()

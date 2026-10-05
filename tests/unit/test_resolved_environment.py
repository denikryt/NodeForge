"""Unit contracts for immutable compilation-session environment resolution."""

from __future__ import annotations

import ast
import sys
import types
from types import MappingProxyType
from dataclasses import dataclass
from pathlib import Path

import pytest

import NodeForge
from NodeForge import packages
from NodeForge.errors import CompileError
from NodeForge.compiler_identities import library_function_id, local_function_id
from NodeForge.resolved_environment import (
    PackageCallableExport,
    ResolvedCatalog,
    ResolvedCompileErrorFailure,
    ResolvedEnvironment,
    ResolvedOSErrorFailure,
    ResolvedPackageNamespace,
    resolve_environment,
)


@dataclass(frozen=True)
class _Record:
    """Minimal immutable catalog record used by data-model tests."""

    namespace: str
    name: str
    package_id: str = ""
    package_version: str = ""


def _catalogs(functions=None, examples=None, local=None):
    """Return the complete supported catalog mapping."""
    return {
        "functions": ResolvedCatalog("functions", functions or {}),
        "examples": ResolvedCatalog("examples", examples or {}),
        "local": ResolvedCatalog("local", local or {}),
    }


def test_catalog_and_environment_defensively_freeze_nested_mappings():
    record = _Record("functions", "demo", "vendor.demo", "1.0.0")
    entries = {"demo": record}
    catalogs = _catalogs(functions=entries)
    environment = ResolvedEnvironment(catalogs)

    entries.clear()
    catalogs.clear()

    assert environment.catalog("functions").find("demo") is record
    assert environment.catalog("functions").names() == frozenset({"demo"})
    assert environment.catalog("functions").records() == (record,)
    with pytest.raises(TypeError):
        environment.catalogs["extra"] = ResolvedCatalog("local", {})
    with pytest.raises(TypeError):
        environment.catalog("functions").entries["extra"] = record


def test_environment_rejects_incomplete_and_mismatched_maps():
    with pytest.raises(ValueError, match="requires functions, examples, and local"):
        ResolvedEnvironment({})
    with pytest.raises(ValueError, match="catalog key"):
        ResolvedEnvironment(
            {
                "functions": ResolvedCatalog("examples", {}),
                "examples": ResolvedCatalog("functions", {}),
                "local": ResolvedCatalog("local", {}),
            }
        )
    bad_namespace = ResolvedPackageNamespace("vendor.demo", "demo", "Demo", "1.0", {})
    with pytest.raises(ValueError, match="package namespace key"):
        ResolvedEnvironment(_catalogs(), package_namespaces={"wrong.owner": bad_namespace})


def test_catalog_rejects_bad_record_placement_and_partial_failure():
    with pytest.raises(ValueError, match="namespace/name"):
        ResolvedCatalog("functions", {"wrong": _Record("functions", "actual")})
    failure = ResolvedCompileErrorFailure(("broken",))
    with pytest.raises(ValueError, match="partial entries"):
        ResolvedCatalog("functions", {"demo": _Record("functions", "demo")}, failure)
    with pytest.raises(CompileError, match="Unknown library catalog"):
        ResolvedCatalog("unknown", {})


def test_compile_error_failure_replays_fresh_exception_without_discovery():
    catalog = ResolvedCatalog(
        "local",
        {},
        ResolvedCompileErrorFailure(("Unsupported Local source registry format",)),
    )
    caught = []
    for method in (catalog.names, catalog.records, lambda: catalog.find("demo")):
        with pytest.raises(CompileError, match="Unsupported Local source registry format") as exc_info:
            method()
        caught.append(exc_info.value)
    assert len({id(error) for error in caught}) == 3


@pytest.mark.parametrize(
    "original",
    [
        OSError(2, "Not found"),
        PermissionError(13, "Permission denied", "/catalog/private"),
        OSError(18, "Cross-device link", "/catalog/source", None, "/catalog/target"),
    ],
)
def test_oserror_failure_replays_complete_diagnostic(original):
    failure = ResolvedOSErrorFailure(
        exception_type=type(original),
        exception_args=original.args,
        errno=original.errno,
        strerror=original.strerror,
        filename=original.filename,
        filename2=original.filename2,
        winerror=getattr(original, "winerror", None),
    )
    catalog = ResolvedCatalog("local", {}, failure)

    with pytest.raises(type(original)) as first_info:
        catalog.names()
    with pytest.raises(type(original)) as second_info:
        catalog.records()

    for replayed in (first_info.value, second_info.value):
        assert replayed is not original
        assert type(replayed) is type(original)
        assert replayed.args == original.args
        assert replayed.errno == original.errno
        assert replayed.strerror == original.strerror
        assert replayed.filename == original.filename
        assert replayed.filename2 == original.filename2
        assert getattr(replayed, "winerror", None) == getattr(original, "winerror", None)
        assert str(replayed) == str(original)
    assert first_info.value is not second_info.value


def test_oserror_failure_rejects_non_oserror_type():
    with pytest.raises(TypeError, match="OSError subclass"):
        ResolvedOSErrorFailure(RuntimeError, (), None, None, None, None, None)


@pytest.mark.skipif(not hasattr(OSError(), "winerror"), reason="winerror is Windows-only")
def test_oserror_failure_preserves_winerror_when_platform_exposes_it():
    """Windows filesystem diagnostics retain the platform-specific numeric code."""
    original = OSError(2, "Not found", "C:\\missing", 3)
    failure = ResolvedOSErrorFailure(
        type(original),
        original.args,
        original.errno,
        original.strerror,
        original.filename,
        original.filename2,
        original.winerror,
    )
    with pytest.raises(type(original)) as exc_info:
        failure.raise_error()
    assert exc_info.value.winerror == original.winerror
    assert str(exc_info.value) == str(original)


def test_resolve_environment_reads_manifest_snapshot_once_and_builds_owner_namespace(monkeypatch):
    """One compilation snapshot derives owner-qualified namespaces without live name registries."""
    manifest = packages.PackageManifest(
        package_id="vendor.demo",
        import_name="demo",
        name="Demo",
        version="1.0.0",
        author="",
        description="",
        root=Path("/package"),
        contents={},
        permissions={},
    )
    calls = {"snapshot": 0}

    def snapshot():
        calls["snapshot"] += 1
        return (manifest,)

    fake_library = types.ModuleType("NodeForge.library")
    fake_library._read_local_source_registry = lambda: ()
    fake_library._candidate_records_from_inputs = lambda *args, **kwargs: ()
    fake_library._unique_records_from_candidates = lambda namespace, candidates: {}
    monkeypatch.setattr(packages, "active_package_manifest_snapshot", snapshot)
    monkeypatch.setattr(packages, "library_roots_from_manifests", lambda namespace, manifests: ())
    monkeypatch.setattr(packages, "system_package_records_from_manifests", lambda manifests: ())
    monkeypatch.setitem(sys.modules, "NodeForge.library", fake_library)
    monkeypatch.setattr(NodeForge, "library", fake_library, raising=False)

    environment = resolve_environment()

    assert calls == {"snapshot": 1}
    namespace = environment.package_by_id("vendor.demo")
    assert namespace is not None
    assert namespace.import_name == "demo"
    assert environment.package_by_import_name("demo") is namespace


def test_existing_environment_is_stable_and_later_resolution_observes_new_owner_snapshot(monkeypatch):
    """Resolved package ownership is immutable per session and refreshed for later sessions."""
    generations = iter(("first", "second"))

    def snapshot():
        generation = next(generations)
        return (packages.PackageManifest(
            package_id=f"vendor.{generation}",
            import_name=generation,
            name=generation,
            version="1.0.0",
            author="",
            description="",
            root=Path("/package"),
            contents={},
            permissions={},
        ),)

    fake_library = types.ModuleType("NodeForge.library")
    fake_library._read_local_source_registry = lambda: ()
    fake_library._candidate_records_from_inputs = lambda *args, **kwargs: ()
    fake_library._unique_records_from_candidates = lambda namespace, candidates: {}
    monkeypatch.setattr(packages, "active_package_manifest_snapshot", snapshot)
    monkeypatch.setattr(packages, "library_roots_from_manifests", lambda namespace, manifests: ())
    monkeypatch.setattr(packages, "system_package_records_from_manifests", lambda manifests: ())
    monkeypatch.setitem(sys.modules, "NodeForge.library", fake_library)
    monkeypatch.setattr(NodeForge, "library", fake_library, raising=False)

    first = resolve_environment()
    second = resolve_environment()

    assert first.package_by_id("vendor.first") is not None
    assert first.package_by_id("vendor.second") is None
    assert second.package_by_id("vendor.second") is not None
    assert first.package_by_import_name("first").package_id == "vendor.first"


def test_malformed_local_registry_is_captured_once_and_replayed_without_discovery(
    monkeypatch,
    tmp_path,
):
    """Actual Local registry parsing becomes one completed deferred failure result."""
    _import_compiler_with_fake_bpy(monkeypatch)
    from NodeForge import library

    registry_path = tmp_path / "local_sources.json"
    local_root = tmp_path / "local"
    registry_path.write_text('{"version": 999, "roots": []}', encoding="utf-8")
    reads = []
    real_read = library._read_local_source_registry

    def counted_read():
        reads.append(True)
        return real_read()

    monkeypatch.setattr(library, "_local_sources_registry_path", lambda: registry_path)
    monkeypatch.setattr(library, "_default_local_catalog_dir", lambda: local_root)
    monkeypatch.setattr(library, "_read_local_source_registry", counted_read)
    catalog = library.resolve_catalog(
        "local",
        package_roots=(),
    )
    registry_path.write_text('{"version": 1, "roots": []}', encoding="utf-8")

    for lookup in (catalog.names, catalog.records, lambda: catalog.find("demo")):
        with pytest.raises(CompileError, match="Unsupported Local source registry format"):
            lookup()
    assert reads == [True]


def test_unsupported_nested_local_source_layout_is_stored_failure(monkeypatch, tmp_path):
    """Nested package-style Local sources are deferred without exposing partial entries."""
    _import_compiler_with_fake_bpy(monkeypatch)
    from NodeForge import library

    local_root = tmp_path / "local"
    (local_root / "nested").mkdir(parents=True)
    (local_root / "nested" / "source.nf").write_text('output("Value", 1.0)\n', encoding="utf-8")
    monkeypatch.setattr(library, "_read_local_source_registry", lambda: [])
    monkeypatch.setattr(library, "_default_local_catalog_dir", lambda: local_root)

    catalog = library.resolve_catalog(
        "local",
        package_roots=(),
    )

    assert dict(catalog.entries) == {}
    with pytest.raises(CompileError, match="Unsupported local source layout: nested/source.nf"):
        catalog.names()


def _import_compiler_with_fake_bpy(monkeypatch):
    """Import compiler-owned binding helpers without requiring Blender runtime."""
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.app = types.SimpleNamespace(driver_namespace={})
    fake_bpy.data = types.SimpleNamespace(node_groups=[])
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    from NodeForge import compiler

    return compiler


def test_import_validation_binds_exact_snapshot_records_and_preserves_sorting(monkeypatch):
    """Explicit, aliased, and star imports retain selected record object identity."""
    from NodeForge import semantic_group
    from NodeForge.parsing import FunctionImport

    alpha = _Record("examples", "alpha")
    zeta = _Record("examples", "zeta")
    environment = ResolvedEnvironment(
        _catalogs(examples={"zeta": zeta, "alpha": alpha}),
    )
    imports = (
        FunctionImport("examples", "zeta", "renamed"),
        FunctionImport("examples", None, None, is_star=True),
    )

    bindings = semantic_group._validate_import_bindings(imports, (), {}, environment)

    assert tuple(bindings) == ("renamed", "alpha", "zeta")
    assert bindings["renamed"].record is zeta
    assert bindings["alpha"].record is alpha
    assert bindings["zeta"].record is zeta


def test_inherited_import_requires_same_record_identity(monkeypatch):
    """Nested compilation may not silently rebind an inherited callable."""
    from NodeForge import semantic_group
    selected = _Record("examples", "alpha")
    different = _Record("examples", "alpha")
    environment = ResolvedEnvironment(_catalogs(examples={"alpha": selected}))
    inherited = {"alias": semantic_group.LibraryBinding("examples", "alpha", different)}

    with pytest.raises(CompileError, match="inherited library binding does not match"):
        semantic_group._validate_import_bindings((), (), {}, environment, inherited)


def test_package_namespace_alias_is_not_globally_reserved_inside_local_lexical_scope():
    """Package aliases are direct-scope bindings; local lexical names may shadow them."""
    from NodeForge import semantic_group
    labels = semantic_group._registered_name_labels({}, {}, {"math": object()})
    assert "math" not in labels


def test_global_system_name_registry_is_removed_after_package_namespace_cutover():
    """Stage 40 has no global system-constructor name authority."""
    root = Path(__file__).resolve().parents[2]
    assert not (root / "systems" / "registry.py").exists()
    assert "registry" not in (root / "systems" / "__init__.py").read_text(encoding="utf-8")


def test_source_call_migration_removes_v1_extension_execution_and_keeps_v2_boundary():
    """Permanent source/extension calls have no v1 executable dispatcher after cutover."""
    root = Path(__file__).resolve().parents[2]
    assert not (root / "library_calls.py").exists()
    assert not (root / "expression_compiler.py").exists()
    assert not (root / "statement_compiler.py").exists()
    local_source = (root / "local_functions.py").read_text(encoding="utf-8")
    semantic_source = (root / "semantic_analysis.py").read_text(encoding="utf-8")
    library_source = (root / "library.py").read_text(encoding="utf-8")
    assert "def compile_local_function_call" not in local_source
    assert "CallableKind.SYSTEM" not in semantic_source
    assert "CallableKind.BACKEND_HELPER" not in semantic_source
    assert "compile_module_library_entry_call" not in library_source
    assert "backend_builtins_for_entry" not in library_source


def test_source_callable_session_uses_exact_resolved_record_without_live_discovery(tmp_path):
    """Pure imported source preparation consumes the exact record captured by the environment."""
    from NodeForge.compiler_identities import GroupCompilationIdentity
    from NodeForge.source_callables import SourceCallableSession

    source_path = tmp_path / "selected.nf"
    source_path.write_text('x = input_float("X")\noutput("X", x)\n', encoding="utf-8")
    record = types.SimpleNamespace(
        namespace="functions",
        name="selected",
        source_path=source_path,
        module_path=None,
        package_id="vendor.selected",
        package_version="2.0.0",
    )
    environment = ResolvedEnvironment(_catalogs(functions={"selected": record}))
    function_id = library_function_id("functions", record.package_id, record.name)
    identity = GroupCompilationIdentity(None, "LIBRARY/test", function_id.stable_key(), function_id.stable_key())
    prepared = SourceCallableSession(resolved_environment=environment).prepare_library(
        function_id=function_id,
        identity=identity,
        record=record,
    )
    assert prepared.contract.function_id == function_id
    assert prepared.group.source == source_path.read_text(encoding="utf-8")



def test_group_backend_public_callback_uses_semantic_preparation_before_publication(monkeypatch):
    """Raw source remains only at the public callback facade, not the permanent backend request."""
    compiler = _import_compiler_with_fake_bpy(monkeypatch)
    from dataclasses import fields
    from NodeForge.blender_group_backend import BlenderGroupBuildRequest

    request_fields = {field.name for field in fields(BlenderGroupBuildRequest)}
    assert request_fields == {
        "prepared_compilation",
        "name",
        "existing_group",
        "helper_namespace",
        "function_group_cache",
        "function_group_transaction",
        "function_compilation_trace",
        "function_compilation_inputs",
        "function_instance_key",
        "source_callable_session",
        "preserve_if_equivalent",
    }
    source = Path(compiler.__file__).read_text(encoding="utf-8")
    assert "def prepare(source, *, compilation_identity" in source
    assert "analyze_group_source(" in source



def test_blender_test_harness_does_not_import_removed_system_registry():
    """Blender regressions use the owner-qualified package inventory, not the removed flat registry."""
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "tests/blender/conftest.py",
        "tests/blender/helpers.py",
        "tests/blender/expression_characterization/harness.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "NodeForge.systems import registry" not in source
        assert "systems_registry." not in source


def test_compilation_modules_do_not_call_live_resolution_apis():
    root = Path(__file__).resolve().parents[2]
    sources = {
        name: (root / name).read_text(encoding="utf-8")
        for name in (
            "compiler.py",
            "semantic_analysis.py",
            "blender_ir_lowering.py",
            "function_materializer.py",
            "local_functions.py",
            "semantic_group.py",
            "source_callables.py",
        )
    }
    forbidden = (
        "systems_registry.has_system_constructor(",
        "systems_registry.constructor_names(",
        "systems_registry.get_handler(",
        "systems_registry.compile_call(",
        "find_library_entry_record(",
        "library_entry_names(",
    )
    for name, source in sources.items():
        for call in forbidden:
            assert call not in source, f"{name} still calls live resolver {call}"

    backend_source = (root / "blender_group_backend.py").read_text(encoding="utf-8")
    materializer_source = (root / "function_materializer.py").read_text(encoding="utf-8")
    assert "resolved_environment" not in backend_source
    assert "resolved_environment" not in materializer_source
    assert "ResolvedEnvironment" not in backend_source
    assert "ResolvedEnvironment" not in materializer_source

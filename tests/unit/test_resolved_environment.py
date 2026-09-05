"""Unit contracts for immutable compilation-session environment resolution."""

from __future__ import annotations

import ast
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

import NodeForge
from NodeForge import packages
from NodeForge.errors import CompileError
from NodeForge.compiler_identities import library_function_id, local_function_id
from NodeForge.resolved_environment import (
    ResolvedCatalog,
    ResolvedCompileErrorFailure,
    ResolvedEnvironment,
    ResolvedOSErrorFailure,
    resolve_environment,
)
from NodeForge.systems import registry as systems_registry
from NodeForge.systems.registry import ResolvedSystemConstructor


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
    systems = {"marker": types.SimpleNamespace(name="marker")}
    catalogs = _catalogs(functions=entries)
    environment = ResolvedEnvironment(catalogs, systems)

    entries.clear()
    catalogs.clear()
    systems.clear()

    assert environment.catalog("functions").find("demo") is record
    assert environment.catalog("functions").names() == frozenset({"demo"})
    assert environment.catalog("functions").records() == (record,)
    assert environment.system_names() == ("marker",)
    with pytest.raises(TypeError):
        environment.catalogs["extra"] = ResolvedCatalog("local", {})
    with pytest.raises(TypeError):
        environment.catalog("functions").entries["extra"] = record
    with pytest.raises(TypeError):
        environment.system_constructors["extra"] = types.SimpleNamespace(name="extra")


def test_environment_rejects_incomplete_and_mismatched_maps():
    with pytest.raises(ValueError, match="requires functions, examples, and local"):
        ResolvedEnvironment({}, {})
    with pytest.raises(ValueError, match="catalog key"):
        ResolvedEnvironment(
            {
                "functions": ResolvedCatalog("examples", {}),
                "examples": ResolvedCatalog("functions", {}),
                "local": ResolvedCatalog("local", {}),
            },
            {},
        )
    with pytest.raises(ValueError, match="constructor name"):
        ResolvedEnvironment(_catalogs(), {"wrong": types.SimpleNamespace(name="actual")})


def test_resolved_system_constructor_defensively_freezes_declared_names():
    """A caller-owned declaration list cannot mutate an existing system binding."""
    constructors = ["marker"]
    binding = ResolvedSystemConstructor("marker", types.SimpleNamespace(), constructors)
    constructors.append("later")
    assert binding.constructors == ("marker",)


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


def test_resolve_environment_reads_active_inventory_once_and_uses_explicit_derivation(monkeypatch):
    manifest = packages.PackageManifest(
        package_id="vendor.demo",
        name="Demo",
        version="1.0.0",
        author="",
        description="",
        root=Path("/package"),
        contents={},
        permissions={},
    )
    active = packages.ActivePackage(manifest, {}, False)
    calls = {"active": 0, "roots": [], "systems": 0, "catalogs": []}

    def active_records(*, include_invalid=False):
        assert include_invalid is False
        calls["active"] += 1
        return [active]

    def roots(namespace, manifests):
        assert tuple(manifests) == (manifest,)
        calls["roots"].append(namespace)
        return ()

    def system_records(manifests):
        assert tuple(manifests) == (manifest,)
        calls["systems"] += 1
        return ()

    fake_library = types.ModuleType("NodeForge.library")

    def resolve_catalog(namespace, *, package_roots, system_constructor_names):
        assert tuple(package_roots) == ()
        assert system_constructor_names == frozenset()
        calls["catalogs"].append(namespace)
        return ResolvedCatalog(namespace, {})

    fake_library.resolve_catalog = resolve_catalog
    monkeypatch.setattr(packages, "active_package_records", active_records)
    monkeypatch.setattr(packages, "library_roots_from_manifests", roots)
    monkeypatch.setattr(packages, "system_package_records_from_manifests", system_records)
    monkeypatch.setattr(systems_registry, "resolve_constructors", lambda records: {})
    monkeypatch.setitem(sys.modules, "NodeForge.library", fake_library)
    monkeypatch.setattr(NodeForge, "library", fake_library, raising=False)

    environment = resolve_environment()

    assert calls == {
        "active": 1,
        "roots": ["functions", "examples", "local"],
        "systems": 1,
        "catalogs": ["functions", "examples", "local"],
    }
    assert environment.system_names() == ()


def test_existing_environment_is_stable_and_later_resolution_observes_new_state(monkeypatch):
    """Session selections stay fixed while the next session receives fresh records."""
    generations = iter(("first", "second"))
    current = {"generation": None}

    def active_records(*, include_invalid=False):
        assert include_invalid is False
        current["generation"] = next(generations)
        manifest = packages.PackageManifest(
            package_id=f"vendor.{current['generation']}",
            name=current["generation"],
            version="1.0.0",
            author="",
            description="",
            root=Path("/package"),
            contents={},
            permissions={},
        )
        return [packages.ActivePackage(manifest, {}, False)]

    monkeypatch.setattr(packages, "active_package_records", active_records)
    monkeypatch.setattr(packages, "system_package_records_from_manifests", lambda manifests: ())
    monkeypatch.setattr(systems_registry, "resolve_constructors", lambda records: {})
    monkeypatch.setattr(packages, "library_roots_from_manifests", lambda namespace, manifests: ())

    fake_library = types.ModuleType("NodeForge.library")

    def resolve_catalog(namespace, *, package_roots, system_constructor_names):
        record = _Record(namespace, current["generation"], f"vendor.{current['generation']}", "1.0.0")
        return ResolvedCatalog(namespace, {record.name: record})

    fake_library.resolve_catalog = resolve_catalog
    monkeypatch.setitem(sys.modules, "NodeForge.library", fake_library)
    monkeypatch.setattr(NodeForge, "library", fake_library, raising=False)

    first = resolve_environment()
    second = resolve_environment()

    assert first.catalog("functions").names() == frozenset({"first"})
    assert second.catalog("functions").names() == frozenset({"second"})
    assert first.catalog("functions").find("first").package_id == "vendor.first"


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
        system_constructor_names=frozenset(),
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
        system_constructor_names=frozenset(),
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
    compiler = _import_compiler_with_fake_bpy(monkeypatch)
    from NodeForge.parsing import FunctionImport

    alpha = _Record("functions", "alpha")
    zeta = _Record("functions", "zeta")
    environment = ResolvedEnvironment(
        _catalogs(functions={"zeta": zeta, "alpha": alpha}),
        {},
    )
    imports = (
        FunctionImport("functions", "zeta", "renamed"),
        FunctionImport("functions", None, None, is_star=True),
    )

    bindings = compiler._validate_import_bindings(imports, (), {}, set(), environment)

    assert tuple(bindings) == ("renamed", "alpha", "zeta")
    assert bindings["renamed"].record is zeta
    assert bindings["alpha"].record is alpha
    assert bindings["zeta"].record is zeta


def test_inherited_import_requires_same_record_identity(monkeypatch):
    """Nested compilation may not silently rebind an inherited callable."""
    compiler = _import_compiler_with_fake_bpy(monkeypatch)
    selected = _Record("functions", "alpha")
    different = _Record("functions", "alpha")
    environment = ResolvedEnvironment(_catalogs(functions={"alpha": selected}), {})
    inherited = {"alias": compiler.LibraryBinding("functions", "alpha", different)}

    with pytest.raises(CompileError, match="inherited library binding does not match"):
        compiler._validate_import_bindings((), (), {}, set(), environment, inherited)


def test_reserved_name_labels_use_snapshot_system_names(monkeypatch):
    """Compiler reservation labels consume the supplied system-name view."""
    compiler = _import_compiler_with_fake_bpy(monkeypatch)
    labels = compiler._registered_name_labels({}, set(), {}, ("snapshot_marker",))
    assert labels["snapshot_marker"] == "embedded-system constructor"


def test_expression_dispatch_passes_exact_resolved_system_binding(monkeypatch):
    """Expression dispatch forwards the environment-owned system record unchanged."""
    compiler = _import_compiler_with_fake_bpy(monkeypatch)
    from NodeForge import expression_compiler

    binding = types.SimpleNamespace(name="snapshot_marker")
    environment = types.SimpleNamespace(system_constructors={"snapshot_marker": binding})
    comp = types.SimpleNamespace(
        resolved_environment=environment,
        imported_library_functions={},
        local_functions={},
        backend_builtins={},
        consts={},
        vars={},
        reserved_name_labels={},
        snapshot_runtime_bindings=lambda: compiler.RuntimeBindingSnapshot({}, {}),
    )
    observed = {}

    def compile_resolved_call(received_comp, expr, received_binding, depth=0):
        observed.update(comp=received_comp, expr=expr, binding=received_binding, depth=depth)
        return "selected"

    monkeypatch.setattr(systems_registry, "compile_resolved_call", compile_resolved_call)
    expr = ast.parse("snapshot_marker()", mode="eval").body

    assert expression_compiler.compile_expr(comp, expr, 7) == "selected"
    assert observed["comp"] is comp
    assert observed["binding"] is binding
    assert observed["depth"] == 7
    assert observed["expr"].func.id == "snapshot_marker"


def test_library_call_uses_binding_record_without_live_discovery(monkeypatch):
    """Imported native calls use the exact record carried by their source binding."""
    compiler = _import_compiler_with_fake_bpy(monkeypatch)
    from NodeForge import library_calls

    source = Path("/selected/source.nf")
    module = Path("/selected/function.py")
    record = types.SimpleNamespace(
        namespace="functions",
        name="selected",
        source_path=source,
        module_path=module,
        package_id="vendor.selected",
        package_version="2.0.0",
    )
    binding = compiler.LibraryBinding("functions", "selected", record)
    observed = {}
    monkeypatch.setattr(library_calls, "has_native_compile_call_for_record", lambda item: item is record)
    monkeypatch.setattr(
        library_calls,
        "compile_module_library_entry_call_for_record",
        lambda comp, expr, item, depth=0: observed.setdefault("call", (comp, expr, item, depth)),
    )
    expr = ast.parse("selected()", mode="eval").body
    comp = object()

    result = library_calls.compile_library_function_call(
        comp,
        expr,
        5,
        binding=binding,
        function_id=library_function_id("functions", "vendor.selected", "selected"),
    )

    assert result == (comp, expr, record, 5)
    assert observed["call"][2] is record


@pytest.mark.parametrize(
    "wrong_function_id",
    [
        local_function_id("owner", "selected", "sig"),
        library_function_id("examples", "vendor.selected", "selected"),
        library_function_id("functions", "vendor.other", "selected"),
        library_function_id("functions", "vendor.selected", "other"),
    ],
    ids=["kind", "namespace", "package_id", "name"],
)
def test_native_library_call_rejects_mismatched_function_id_before_module_execution(monkeypatch, wrong_function_id):
    """Canonical imported identity is validated before native module code can run."""
    compiler = _import_compiler_with_fake_bpy(monkeypatch)
    from NodeForge import library_calls

    record = types.SimpleNamespace(
        namespace="functions",
        name="selected",
        source_path=None,
        module_path=Path("/selected/function.py"),
        package_id="vendor.selected",
        package_version="2.0.0",
    )
    binding = compiler.LibraryBinding("functions", "selected", record)
    observed = {"native_probe": 0, "handler": 0}

    def has_native(item):
        observed["native_probe"] += 1
        return True

    def compile_native(comp, expr, item, depth=0):
        observed["handler"] += 1
        return "native"

    monkeypatch.setattr(library_calls, "has_native_compile_call_for_record", has_native)
    monkeypatch.setattr(library_calls, "compile_module_library_entry_call_for_record", compile_native)

    with pytest.raises(CompileError, match="FunctionId does not match resolved binding"):
        library_calls.compile_library_function_call(
            object(),
            ast.parse("selected()", mode="eval").body,
            binding=binding,
            function_id=wrong_function_id,
        )

    assert observed == {"native_probe": 0, "handler": 0}


def test_direct_compiler_fallback_resolves_once_and_binds_its_backend(monkeypatch):
    """Standalone Compiler construction creates one snapshot-backed nested callback."""
    compiler = _import_compiler_with_fake_bpy(monkeypatch)
    environment = ResolvedEnvironment(_catalogs(), {})
    calls = []
    monkeypatch.setattr(compiler, "resolve_environment", lambda: calls.append(True) or environment)

    comp = compiler.Compiler(types.SimpleNamespace(name="Direct"), None)

    assert calls == [True]
    assert comp.resolved_environment is environment
    assert comp.group_backend._resolved_environment_for_session() is environment


def test_compiler_rejects_unbound_and_mismatched_backends(monkeypatch):
    """Caller backends cannot bypass exact compilation-session identity checks."""
    compiler = _import_compiler_with_fake_bpy(monkeypatch)
    first = ResolvedEnvironment(_catalogs(), {})
    second = ResolvedEnvironment(_catalogs(), {})
    group = types.SimpleNamespace(name="Direct")

    with pytest.raises(CompileError, match="not bound to a resolved environment"):
        compiler.Compiler(group, None, group_backend=object(), resolved_environment=first)
    with pytest.raises(CompileError, match="uses a different resolved environment"):
        compiler.Compiler(
            group,
            None,
            group_backend=compiler._new_group_backend(second),
            resolved_environment=first,
        )


def test_parse_failure_precedes_environment_resolution(monkeypatch):
    """Invalid root syntax fails before the session slot reads package state."""
    compiler = _import_compiler_with_fake_bpy(monkeypatch)
    provider = lambda: pytest.fail("parse failure must not resolve the environment")
    with pytest.raises(CompileError):
        compiler._populate_group(
            types.SimpleNamespace(),
            "if:",
            resolved_environment_for_session=provider,
        )


def test_compilation_modules_do_not_call_live_resolution_apis():
    root = Path(__file__).resolve().parents[2]
    sources = {
        name: (root / name).read_text(encoding="utf-8")
        for name in (
            "compiler.py",
            "expression_compiler.py",
            "library_calls.py",
            "function_materializer.py",
            "local_functions.py",
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


def test_direct_compiler_compatibility_marker_matches_plan_exactly():
    """The only new temporary branch remains searchable with its removal contract."""
    source = (Path(__file__).resolve().parents[2] / "compiler.py").read_text(encoding="utf-8")
    marker = """        # RESOLVED_ENVIRONMENT_MIGRATION: Compiler is still exported and legacy tests or
        # integrations may construct it directly without the root compiler entry points.
        # A standalone Compiler creates one snapshot and an environment-bound backend, or
        # adopts the exact snapshot exposed by a supplied environment-bound backend. Reject
        # arbitrary backends because their nested populate callback could resolve again.
        # Production root entry points always pass one shared ResolvedEnvironment. Remove
        # this fallback when Compiler is internal/session-owned and every caller must pass
        # both an explicit environment and its matching session-bound backend."""
    assert source.count("RESOLVED_ENVIRONMENT_MIGRATION") == 1
    assert marker in source

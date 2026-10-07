"""Unit coverage for Package inventory package inventory contracts."""

from __future__ import annotations

import importlib
import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

from NodeForge import packages
from NodeForge.errors import CompileError


@pytest.fixture(autouse=True)
def package_inventory(tmp_path):
    """Use an isolated inventory with packages installed explicitly by the test setup."""
    packages.set_packages_dir_for_tests(tmp_path)
    yield tmp_path
    packages.set_packages_dir_for_tests(None)



class _FakeGroupBackend:
    """Exercise library adapters through the prepared-only group-backend contract."""

    def __init__(self, callback):
        self._callback = callback
        self._resolved_environment = None
        self.requests = []

    def _environment(self):
        """Return one minimal immutable environment for source-only adapter fixtures."""
        if self._resolved_environment is None:
            from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
            self._resolved_environment = ResolvedEnvironment(
                {
                    "functions": ResolvedCatalog("functions", {}),
                    "examples": ResolvedCatalog("examples", {}),
                    "local": ResolvedCatalog("local", {}),
                }
            )
        return self._resolved_environment

    def new_source_callable_session(self):
        """Return one root-attempt semantic source-call session."""
        from NodeForge.semantic.source_callable_session import SourceCallableSession
        return SourceCallableSession(resolved_environment=self._environment())

    def prepare_source_compilation(
        self, source, *, compilation_identity, source_callable_session=None, **kwargs
    ):
        """Prepare source semantics before the fake physical callback is invoked."""
        from NodeForge.semantic.group import analyze_group_source
        session = source_callable_session or self.new_source_callable_session()
        return analyze_group_source(
            source,
            compilation_identity=compilation_identity,
            resolved_environment=self._environment(),
            helper_namespace=kwargs.get("helper_namespace") or "NodeForge Group",
            source_callable_session=session,
        )

    def create_or_update(self, request, *, finalize_before_commit=None):
        self.requests.append(request)
        kwargs = {}
        if request.existing_group is not None:
            kwargs["existing_group"] = request.existing_group
        if request.function_group_cache is not None:
            kwargs["function_group_cache"] = request.function_group_cache
        if request.function_group_transaction is not None:
            kwargs["function_group_transaction"] = request.function_group_transaction
        if request.function_compilation_trace is not None:
            kwargs["function_compilation_trace"] = request.function_compilation_trace
        if request.function_compilation_inputs is not None:
            kwargs["function_compilation_inputs"] = request.function_compilation_inputs
        if request.function_instance_key is not None:
            kwargs["function_instance_key"] = request.function_instance_key
        if request.preserve_if_equivalent:
            kwargs["preserve_if_equivalent"] = True
        group = self._callback(request.prepared_compilation.source, request.name, **kwargs)
        if not hasattr(group, "interface"):
            group.interface = types.SimpleNamespace(items_tree=[])
        if finalize_before_commit is not None:
            finalize_before_commit(group)
        return group

    def compile_group_callback(self, source, name="NodeForge Group", **kwargs):
        """Provide the package-facing callback while keeping source out of physical publication."""
        from NodeForge.blender_group_backend import BlenderGroupBuildRequest
        from NodeForge.compiler_identities import GroupCompilationIdentity
        existing_group = kwargs.pop("existing_group", None)
        preserve = bool(kwargs.pop("preserve_if_equivalent", False))
        source_callable_session = kwargs.pop("source_callable_session", None)
        owner = f"FAKE/{name}"
        prepared = self.prepare_source_compilation(
            source,
            compilation_identity=GroupCompilationIdentity(None, owner, owner, owner),
            helper_namespace=name,
            source_callable_session=source_callable_session,
        )
        request = BlenderGroupBuildRequest(
            prepared_compilation=prepared,
            name=name,
            existing_group=existing_group,
            helper_namespace=name,
            function_group_cache=kwargs.pop("function_group_cache", None),
            function_group_transaction=(
                kwargs.pop("function_group_transaction", None)
                or kwargs.pop("local_helper_transaction", None)
            ),
            function_compilation_trace=kwargs.pop("function_compilation_trace", None),
            function_compilation_inputs=kwargs.pop("function_compilation_inputs", None),
            function_instance_key=kwargs.pop("function_instance_key", None),
            source_callable_session=source_callable_session,
            preserve_if_equivalent=preserve,
        )
        assert not kwargs
        return self.create_or_update(request)

def _write_manifest(
    root: Path,
    package_id="vendor.demo",
    *,
    contents=None,
    python=False,
    version="1.0.0",
    nodeforge_min_version="0.49.47",
    nodeforge_max_version=None,
    import_name=None,
):
    if contents is None:
        contents = {"functions": "functions"}
    root.mkdir(parents=True, exist_ok=True)
    (root / "nodeforge_package.json").write_text(
        json.dumps(
            {
                **({"import_name": import_name} if import_name is not None else {}),
                "schema_version": 1,
                "id": package_id,
                "name": package_id,
                "version": version,
                "author": "Tester",
                "description": "Test package",
                "nodeforge_min_version": nodeforge_min_version,
                "nodeforge_max_version": nodeforge_max_version,
                "contents": contents,
                "permissions": {"python": python},
            }
        ),
        encoding="utf-8",
    )




def _write_v2_system(system_root: Path, public_name: str) -> None:
    """Write one minimal backend-only Extension API v2 system owner fixture."""
    system_root.mkdir(parents=True, exist_ok=True)
    (system_root / "interface.py").write_text(
        "from NodeForge import Float, EvaluationMode\n"
        "from typing import Annotated\n"
        "EXTENSION_API = 2\n"
        f"EXTENSIONS = {{{public_name!r}: '.backend:{public_name}'}}\n"
        f"def {public_name}(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n",
        encoding="utf-8",
    )
    (system_root / "backend.py").write_text(
        f"def {public_name}(context, value):\n    return value\n",
        encoding="utf-8",
    )

def test_explicitly_installed_packages_use_unified_inventory(package_inventory, tmp_path):
    source = tmp_path / "vendor.inventory"
    (source / "functions").mkdir(parents=True)
    (source / "examples").mkdir()
    (source / "functions" / "identity.nf").write_text('value = input_float("Value")\noutput("Value", value)\n', encoding="utf-8")
    (source / "examples" / "demo.nf").write_text('output("Value", 1)\n', encoding="utf-8")
    _write_manifest(source, package_id="vendor.inventory", contents={"functions": "functions", "examples": "examples"})
    packages.install_package_directory(source, allow_python=False)

    manifests = {m.package_id: m for m in packages.active_package_manifests()}
    assert set(manifests) == {"vendor.inventory"}
    state = packages.load_package_state()
    assert set(state["packages"]) == {"vendor.inventory"}
    assert packages.library_roots("functions")[0].package_id == "vendor.inventory"
    assert {root.package_id for root in packages.library_roots("examples")} == {"vendor.inventory"}


def test_manifest_derived_roots_match_live_wrappers_without_loading_state(
    package_inventory,
    tmp_path,
    monkeypatch,
):
    """Explicit derivation must preserve live ordering without owning inventory reads."""
    source = tmp_path / "derived_roots"
    (source / "functions").mkdir(parents=True)
    (source / "examples").mkdir()
    (source / "systems" / "marker").mkdir(parents=True)
    (source / "functions" / "alpha.nf").write_text("output(value=1)\n", encoding="utf-8")
    (source / "examples" / "beta.nf").write_text("output(value=2)\n", encoding="utf-8")
    _write_v2_system(source / "systems" / "marker", "derived_marker")
    _write_manifest(
        source,
        package_id="vendor.derived",
        contents={"functions": "functions", "examples": "examples", "systems": "systems"},
        python=True,
    )
    packages.install_package_directory(source, allow_python=True)

    manifests = tuple(packages.active_package_manifests())
    expected_functions = tuple(packages.library_roots("functions"))
    expected_examples = tuple(packages.library_roots("examples"))
    expected_systems = tuple(packages.system_package_records())
    monkeypatch.setattr(
        packages,
        "active_package_manifests",
        lambda *args, **kwargs: pytest.fail("explicit derivation must not load package state"),
    )

    assert packages.library_roots_from_manifests("functions", manifests) == expected_functions
    assert packages.library_roots_from_manifests("examples", manifests) == expected_examples
    assert packages.library_roots_from_manifests("local", manifests) == ()
    assert packages.system_package_records_from_manifests(manifests) == expected_systems


def test_system_resolution_uses_v2_interface_without_v1_handler_dispatch(package_inventory, tmp_path):
    """System discovery exposes the captured v2 owner and no executable v1 handler API."""
    source = tmp_path / "resolved_system"
    system_root = source / "systems" / "marker"
    _write_manifest(
        source,
        package_id="vendor.resolved",
        contents={"systems": "systems"},
        python=True,
    )
    _write_v2_system(system_root, "resolved_marker")
    packages.install_package_directory(source, allow_python=True)

    records = tuple(packages.system_package_records())
    assert len(records) == 1
    assert records[0].interface_path.name == "interface.py"
    manifest = packages.active_package_manifests()[0]
    inventory = packages._normalize_package_callable_inventory(manifest, normalize_native_libraries=True)
    assert inventory.systems == {"resolved_marker": "marker"}
def test_uninstall_validates_state_pointer_before_deleting_files(package_inventory, tmp_path):
    source_a = tmp_path / "source_a"
    (source_a / "functions").mkdir(parents=True)
    (source_a / "functions" / "a.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(source_a, package_id="vendor.a")
    packages.install_package_directory(source_a, allow_python=False)

    source_b = tmp_path / "source_b"
    (source_b / "functions").mkdir(parents=True)
    (source_b / "functions" / "b.nf").write_text("output(value=2)\n", encoding="utf-8")
    _write_manifest(source_b, package_id="vendor.b")
    packages.install_package_directory(source_b, allow_python=False)

    state = packages.load_package_state()
    b_path = state["packages"]["vendor.b"]["installed_path"]
    b_dir = packages.packages_dir() / b_path
    state["packages"]["vendor.a"]["installed_path"] = b_path
    packages.save_package_state(state)

    packages.uninstall_package("vendor.a")

    after = packages.load_package_state()
    assert "vendor.a" not in after["packages"]
    assert "vendor.b" in after["packages"]
    assert b_dir.exists()


def test_replace_skips_cleanup_when_old_pointer_is_invalid(package_inventory, tmp_path):
    source_old = tmp_path / "source_old"
    (source_old / "functions").mkdir(parents=True)
    (source_old / "functions" / "old.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(source_old, package_id="vendor.replace")
    packages.install_package_directory(source_old, allow_python=False)

    source_b = tmp_path / "source_b"
    (source_b / "functions").mkdir(parents=True)
    (source_b / "functions" / "b.nf").write_text("output(value=2)\n", encoding="utf-8")
    _write_manifest(source_b, package_id="vendor.b")
    packages.install_package_directory(source_b, allow_python=False)

    state = packages.load_package_state()
    b_path = state["packages"]["vendor.b"]["installed_path"]
    b_dir = packages.packages_dir() / b_path
    state["packages"]["vendor.replace"]["installed_path"] = b_path
    packages.save_package_state(state)

    replacement = tmp_path / "replacement"
    (replacement / "functions").mkdir(parents=True)
    (replacement / "functions" / "new.nf").write_text("output(value=3)\n", encoding="utf-8")
    _write_manifest(replacement, package_id="vendor.replace", version="2.0.0")

    packages.install_package_directory(replacement, allow_python=False, replace=True)

    after = packages.load_package_state()
    assert after["packages"]["vendor.replace"]["installed_version"] == "2.0.0"
    assert after["packages"]["vendor.replace"]["installed_path"] != b_path
    assert b_dir.exists()
    assert "vendor.b" in after["packages"]


def test_install_rechecks_duplicate_package_id_at_final_commit(package_inventory, tmp_path, monkeypatch):
    candidate = tmp_path / "candidate_race"
    (candidate / "functions").mkdir(parents=True)
    (candidate / "functions" / "candidate.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(candidate, package_id="vendor.race")

    concurrent = tmp_path / "concurrent_race"
    (concurrent / "functions").mkdir(parents=True)
    (concurrent / "functions" / "concurrent.nf").write_text("output(value=2)\n", encoding="utf-8")
    _write_manifest(concurrent, package_id="vendor.race")

    real_install = packages._install_validated_directory
    concurrent_record = {}

    def install_and_interleave(source_dir, manifest, *, allow_python, origin):
        record = real_install(source_dir, manifest, allow_python=allow_python, origin=origin)
        if manifest.package_id == "vendor.race" and not concurrent_record:
            other_manifest = packages.validate_package_root(concurrent, origin="user")
            other_record = real_install(concurrent, other_manifest, allow_python=False, origin="user")
            state = packages.load_package_state()
            state.setdefault("packages", {})["vendor.race"] = other_record
            packages.save_package_state(state)
            concurrent_record.update(other_record)
        return record

    monkeypatch.setattr(packages, "_install_validated_directory", install_and_interleave)

    with pytest.raises(packages.PackageError, match="already installed"):
        packages.install_package_directory(candidate, allow_python=False)

    state_record = packages.load_package_state()["packages"]["vendor.race"]
    assert state_record["installed_path"] == concurrent_record["installed_path"]
    installed_roots = sorted((packages.installed_dir() / "vendor.race").iterdir())
    assert installed_roots == [packages.packages_dir() / concurrent_record["installed_path"]]


def test_state_installed_path_is_validated_as_untrusted(package_inventory):
    state = packages.load_package_state()
    state["packages"]["vendor.demo"] = {
        "installed_path": "../outside",
        "installed_version": "1.0.0",
        "allow_python": False,
        "origin": "user",
    }
    packages.save_package_state(state)

    ids = {m.package_id for m in packages.active_package_manifests()}
    diagnostics = packages.active_package_manifests(include_invalid=True)

    assert "vendor.demo" not in ids
    assert any(getattr(item, "package_id", None) == "vendor.demo" for item in diagnostics)


def test_current_python_requirement_is_recomputed_on_load(package_inventory, tmp_path):
    source = tmp_path / "source"
    (source / "functions").mkdir(parents=True)
    (source / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(source)
    manifest = packages.install_package_directory(source, allow_python=False)
    active_root = next(m.root for m in packages.active_package_manifests() if m.package_id == manifest.package_id)
    (active_root / "functions" / "demo" ).mkdir()
    (active_root / "functions" / "demo" / "function.py").write_text("X = 1\n", encoding="utf-8")

    assert manifest.package_id not in {m.package_id for m in packages.active_package_manifests()}
    assert any(getattr(item, "package_id", None) == manifest.package_id for item in packages.active_package_manifests(include_invalid=True))



def test_manifest_version_bounds_are_enforced_before_commit(package_inventory, tmp_path):
    source = tmp_path / "future"
    (source / "functions").mkdir(parents=True)
    (source / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(source, package_id="vendor.future", nodeforge_min_version="999.0.0")

    with pytest.raises(packages.PackageError, match="requires NodeForge"):
        packages.install_package_directory(source, allow_python=False)

    assert "vendor.future" not in packages.load_package_state()["packages"]


def test_manifest_expired_max_version_is_rejected_for_zip_install(package_inventory, tmp_path):
    source = tmp_path / "expired" / "vendor.expired"
    (source / "functions").mkdir(parents=True)
    (source / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(source, package_id="vendor.expired", nodeforge_max_version="0.1.0")
    archive = tmp_path / "expired.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in source.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(source.parent).as_posix())

    with pytest.raises(packages.PackageError, match="supports NodeForge"):
        packages.install_package_zip(archive, allow_python=False)

    assert "vendor.expired" not in packages.load_package_state()["packages"]


def test_malformed_manifest_versions_are_rejected(package_inventory, tmp_path):
    source = tmp_path / "bad_version"
    (source / "functions").mkdir(parents=True)
    (source / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(source, package_id="vendor.badversion", version="1.beta.0")

    with pytest.raises(packages.PackageError, match="version"):
        packages.install_package_directory(source, allow_python=False)


def test_nonmath_package_materialization_does_not_reuse_uninstalled_group(package_inventory, tmp_path, monkeypatch):
    class FakeGroup(dict):
        def __init__(self, name):
            super().__init__()
            self.name = name
            self.bl_idname = "GeometryNodeTree"

    class FakeNodeGroups(dict):
        def get(self, name, default=None):
            return super().get(name, default)

        def __iter__(self):
            return iter(self.values())

    fake_bpy = types.SimpleNamespace(
        data=types.SimpleNamespace(node_groups=FakeNodeGroups()),
        app=types.SimpleNamespace(driver_namespace={}),
        utils=types.SimpleNamespace(user_resource=lambda *_args, **_kwargs: str(tmp_path / "user_data")),
    )
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    import NodeForge.blender.library_groups as library_groups

    library_groups = importlib.reload(library_groups)

    def make_source(root: Path, package_id: str):
        (root / "examples").mkdir(parents=True)
        (root / "examples" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
        _write_manifest(root, package_id=package_id, contents={"examples": "examples"})

    source_a = tmp_path / "pkg_a"
    source_b = tmp_path / "pkg_b"
    make_source(source_a, "vendor.a")
    make_source(source_b, "vendor.b")

    packages.install_package_directory(source_a, allow_python=False)

    compiled = []

    def compile_group(source, group_name, existing_group=None, backend_builtins=None, **kwargs):
        group = existing_group or FakeGroup(group_name)
        group["compiled_source"] = source
        fake_bpy.data.node_groups[group_name] = group
        compiled.append((group_name, existing_group, kwargs))
        return group

    backend_a = _FakeGroupBackend(compile_group)
    group_a = library_groups.materialize_library_entry_group("examples", "demo", backend_a)
    from NodeForge.compiler_identities import library_function_id
    from NodeForge.function_instances import (
        FUNCTION_DEFINITION_OWNER_PROP,
        FUNCTION_INSTANCE_KEY_PROP,
        function_group_owner_scope,
    )

    function_id_a = library_function_id("examples", "vendor.a", "demo")
    expected_owner_a = function_group_owner_scope("LIBRARY", "examples", "vendor.a", "demo", instance_key=None)
    assert group_a["nodeforge_package_id"] == "vendor.a"
    assert group_a[FUNCTION_INSTANCE_KEY_PROP] == ""
    assert group_a[FUNCTION_DEFINITION_OWNER_PROP] == function_id_a.stable_key()
    assert backend_a.requests[0].prepared_compilation.identity.owner_scope == expected_owner_a
    assert backend_a.requests[0].prepared_compilation.identity.declaration_owner == function_id_a.stable_key()
    assert compiled[0][2]["function_instance_key"] == ""
    assert group_a.name in fake_bpy.data.node_groups

    direct_again = library_groups.materialize_library_entry_group("examples", "demo", _FakeGroupBackend(compile_group))
    assert direct_again is group_a
    assert compiled[-1][1] is group_a

    packages.uninstall_package("vendor.a")
    packages.install_package_directory(source_b, allow_python=False)

    group_b = library_groups.materialize_library_entry_group("examples", "demo", _FakeGroupBackend(compile_group))

    assert group_b is not group_a
    assert group_b.name != group_a.name
    assert group_b["nodeforge_package_id"] == "vendor.b"
    assert group_b["nodeforge_package_version"] == "1.0.0"
    assert group_a["nodeforge_package_id"] == "vendor.a"




def test_library_materialization_contract_discriminator_is_explicit(package_inventory, tmp_path, monkeypatch):
    """Prepared source-call artifacts select reusable semantics while None selects direct catalog build."""
    class FakeGroup(dict):
        def __init__(self, name):
            super().__init__()
            self.name = name
            self.bl_idname = "GeometryNodeTree"

    class FakeNodeGroups(list):
        def get(self, name, default=None):
            return next((group for group in self if group.name == name), default)

    fake_groups = FakeNodeGroups()
    fake_bpy = types.SimpleNamespace(
        data=types.SimpleNamespace(node_groups=fake_groups),
        app=types.SimpleNamespace(driver_namespace={}),
        utils=types.SimpleNamespace(user_resource=lambda *_args, **_kwargs: str(tmp_path / "user_data")),
    )
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    import NodeForge.blender.library_groups as library_groups
    from NodeForge import catalog
    library_groups = importlib.reload(library_groups)

    source = tmp_path / "contract_pkg"
    (source / "functions").mkdir(parents=True)
    (source / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(source, package_id="vendor.contract", contents={"functions": "functions"})
    packages.install_package_directory(source, allow_python=False)

    calls = []

    def compile_group(source_text, group_name, existing_group=None, **kwargs):
        group = existing_group or FakeGroup(f"{group_name}.{len(fake_groups)}")
        if group not in fake_groups:
            fake_groups.append(group)
        calls.append(kwargs)
        return group

    from NodeForge.compiler_identities import CallSiteId, GroupCompilationIdentity, library_function_id
    from NodeForge.function_instances import (
        function_group_owner_scope,
        function_materialization_owner_scope,
        instance_key_for,
    )
    from NodeForge.semantic.ir import IRFunctionMaterialization, IRFunctionMaterializationMode
    from NodeForge.function_materializer import FunctionMaterializationContext, FunctionMaterializer

    backend = _FakeGroupBackend(compile_group)
    function_id = library_function_id("functions", "vendor.contract", "demo")
    direct_result = library_groups.get_or_create_library_entry_group(
        "functions", "demo", backend, materialization=None
    )
    direct = direct_result.group
    assert direct_result.instance_key == ""
    assert direct.get("nodeforge_function_instance_key") == ""
    assert backend.requests[-1].prepared_compilation.identity.owner_scope == function_group_owner_scope(
        "LIBRARY", "functions", "vendor.contract", "demo", instance_key=None
    )
    assert "function_group_cache" not in calls[-1]
    assert "function_group_transaction" not in calls[-1]
    assert "function_compilation_trace" not in calls[-1]

    call_site = CallSiteId("root-owner", function_id, 0)
    materialization = IRFunctionMaterialization(
        function_id, IRFunctionMaterializationMode.UNIQUE, call_site
    )
    from NodeForge.environment_resolution import resolve_environment

    environment = resolve_environment()
    record = environment.package_function_record("vendor.contract", "demo")
    assert record is not None
    owner_scope = function_materialization_owner_scope(materialization)
    stable_id = function_id.stable_key()
    session = backend.new_source_callable_session()
    prepared_callable = session.prepare_library(
        function_id=function_id,
        identity=GroupCompilationIdentity(None, owner_scope, stable_id, stable_id),
        record=record,
    )
    cache = {}
    context = FunctionMaterializationContext(cache, None, None, session)
    unique_result = library_groups.materialize_prepared_library_callable(
        record,
        FunctionMaterializer(group_backend=backend),
        prepared_callable,
        materialization=materialization,
        materialization_context=context,
    )
    unique = unique_result.group
    unique_key = instance_key_for(call_site)
    assert unique_result.instance_key == unique_key
    assert unique is not direct
    assert unique.get("nodeforge_function_instance_key") == unique_key
    assert backend.requests[-1].prepared_compilation.identity.owner_scope == function_group_owner_scope(
        "LIBRARY", "functions", "vendor.contract", "demo", instance_key=unique_key
    )
    assert cache[("library", function_id, unique_key)] is unique

    other_id = library_function_id("functions", "vendor.other", "demo")
    wrong = IRFunctionMaterialization(other_id, IRFunctionMaterializationMode.SHARED)
    with pytest.raises(CompileError, match="inconsistent materialization identity"):
        library_groups.materialize_prepared_library_callable(
            record,
            FunctionMaterializer(group_backend=backend),
            prepared_callable,
            materialization=wrong,
            materialization_context=context,
        )


def test_zip_rejects_multiple_manifest_candidates(package_inventory, tmp_path):
    archive = tmp_path / "multiple_manifests.zip"
    root_manifest = {
        "schema_version": 1,
        "id": "vendor.root",
        "name": "vendor.root",
        "version": "1.0.0",
        "author": "Tester",
        "description": "Root package",
        "nodeforge_min_version": "0.49.47",
        "nodeforge_max_version": None,
        "contents": {"functions": "functions"},
        "permissions": {"python": False},
    }
    nested_manifest = dict(root_manifest, id="vendor.other", name="vendor.other")
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nodeforge_package.json", json.dumps(root_manifest))
        zf.writestr("functions/root.nf", "output(value=1)\n")
        zf.writestr("vendor.other/nodeforge_package.json", json.dumps(nested_manifest))
        zf.writestr("vendor.other/functions/other.nf", "output(value=2)\n")

    with pytest.raises(packages.PackageError, match="exactly one"):
        packages.install_package_zip(archive, allow_python=False)

    assert "vendor.root" not in packages.load_package_state()["packages"]


def test_zip_rejects_deep_manifest_candidate(package_inventory, tmp_path):
    archive = tmp_path / "deep_manifest.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a/b/nodeforge_package.json", "{}")
        zf.writestr("a/b/functions/demo.nf", "output(value=1)\n")

    with pytest.raises(packages.PackageError, match="root or one top-level directory"):
        packages.install_package_zip(archive, allow_python=False)


def test_package_id_regex_matches_manifest_contract(package_inventory, tmp_path):
    valid = tmp_path / "valid_3d"
    (valid / "functions").mkdir(parents=True)
    (valid / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(valid, package_id="vendor.3d", import_name="pkg3d")
    manifest = packages.install_package_directory(valid, allow_python=False)
    assert manifest.package_id == "vendor.3d"

    invalid = tmp_path / "invalid_underscore"
    (invalid / "functions").mkdir(parents=True)
    (invalid / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(invalid, package_id="ven_dor.foo")
    with pytest.raises(packages.PackageError, match="Package id"):
        packages.install_package_directory(invalid, allow_python=False)


def test_package_id_allows_underscore_after_dot(package_inventory, tmp_path):
    for index, package_id in enumerate(("vendor._tools", "vendor.tools_extra")):
        source = tmp_path / package_id.replace(".", "_")
        (source / "functions").mkdir(parents=True)
        (source / "functions" / f"demo_{index}.nf").write_text("output(value=1)\n", encoding="utf-8")
        _write_manifest(source, package_id=package_id, import_name=f"tools_{index}")
        manifest = packages.install_package_directory(source, allow_python=False)
        assert manifest.package_id == package_id


def test_package_import_name_explicit_and_default_are_manifest_owned(package_inventory, tmp_path):
    """Import spelling is validated manifest metadata and defaults to the final package-id segment."""
    explicit = tmp_path / "explicit_import"
    (explicit / "functions").mkdir(parents=True)
    (explicit / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(explicit, package_id="vendor.explicit", import_name="tools")
    assert packages.install_package_directory(explicit, allow_python=False).import_name == "tools"

    defaulted = tmp_path / "default_import"
    (defaulted / "functions").mkdir(parents=True)
    (defaulted / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(defaulted, package_id="vendor.defaulted")
    assert packages.install_package_directory(defaulted, allow_python=False).import_name == "defaulted"


@pytest.mark.parametrize("import_name", ["class", "_private", "not-valid"])
def test_package_import_name_rejects_non_public_source_identifiers(package_inventory, tmp_path, import_name):
    """Keyword/private/non-identifier spellings never enter the package source namespace."""
    source = tmp_path / import_name.replace("-", "_")
    (source / "functions").mkdir(parents=True)
    (source / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(source, package_id=f"vendor.bad{len(import_name)}", import_name=import_name)
    with pytest.raises(packages.PackageError, match="import_name"):
        packages.install_package_directory(source, allow_python=False)


def test_package_import_names_are_unique_across_active_owners(package_inventory, tmp_path):
    """Two canonical owners cannot publish the same source package import spelling."""
    for package_id in ("vendor.first", "vendor.second"):
        source = tmp_path / package_id.replace(".", "_")
        (source / "functions").mkdir(parents=True)
        (source / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
        _write_manifest(source, package_id=package_id, import_name="shared")
        if package_id.endswith("first"):
            packages.install_package_directory(source, allow_python=False)
        else:
            with pytest.raises(packages.PackageError, match=r"Package import name 'shared'.*vendor\.first"):
                packages.install_package_directory(source, allow_python=False)


def test_package_replace_rechecks_import_name_collision_before_commit(package_inventory, tmp_path):
    """Replacing one owner cannot steal another active owner's import spelling."""
    first = tmp_path / "first"
    (first / "functions").mkdir(parents=True)
    (first / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(first, package_id="vendor.first", import_name="shared")
    packages.install_package_directory(first, allow_python=False)

    second = tmp_path / "second"
    (second / "functions").mkdir(parents=True)
    (second / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(second, package_id="vendor.second", import_name="second", version="1.0.0")
    packages.install_package_directory(second, allow_python=False)

    replacement = tmp_path / "second_v2"
    (replacement / "functions").mkdir(parents=True)
    (replacement / "functions" / "demo.nf").write_text("output(value=2)\n", encoding="utf-8")
    _write_manifest(replacement, package_id="vendor.second", import_name="shared", version="2.0.0")
    with pytest.raises(packages.PackageError, match=r"Package import name 'shared'.*vendor\.first"):
        packages.install_package_directory(replacement, allow_python=False, replace=True)
    assert packages.load_package_state()["packages"]["vendor.second"]["installed_version"] == "1.0.0"

def test_zip_rejects_duplicate_casefold_destinations(package_inventory, tmp_path):
    archive = tmp_path / "demo.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("vendor.demo/nodeforge_package.json", "{}")
        zf.writestr("vendor.demo/functions/Foo.nf", "output(value=1)\n")
        zf.writestr("vendor.demo/functions/foo.nf", "output(value=1)\n")

    with pytest.raises(packages.PackageError, match="Duplicate zip destination"):
        packages.install_package_zip(archive, allow_python=False)



def test_zip_rejects_unsafe_directory_entries(package_inventory, tmp_path):
    cases = [
        ("traversal", "../evil/", None),
        ("absolute", "/evil/", None),
        ("drive", "C:/evil/", None),
    ]
    for label, unsafe_name, attrs in cases:
        archive = tmp_path / f"{label}.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(unsafe_name, "")
            zf.writestr("vendor.safe/nodeforge_package.json", "{}")
            zf.writestr("vendor.safe/functions/demo.nf", "output(value=1)\n")

        with pytest.raises(packages.PackageError, match="Unsafe zip member path"):
            packages.install_package_zip(archive, allow_python=False)


def test_zip_rejects_symlink_and_special_directory_entries(package_inventory, tmp_path):
    for label, mode in {"symlink_dir": 0o120777, "fifo_dir": 0o010666}.items():
        archive = tmp_path / f"{label}.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            info = zipfile.ZipInfo("vendor.safe/unsafe/")
            info.external_attr = mode << 16
            zf.writestr(info, "")
            zf.writestr("vendor.safe/nodeforge_package.json", "{}")
            zf.writestr("vendor.safe/functions/demo.nf", "output(value=1)\n")

        with pytest.raises(packages.PackageError, match="Unsupported special file"):
            packages.install_package_zip(archive, allow_python=False)




def test_zip_rejects_dot_and_empty_segments_in_member_paths(package_inventory, tmp_path):
    cases = [
        ("dot_manifest", "vendor.dotseg/./nodeforge_package.json", "vendor.dotseg/functions/demo.nf"),
        ("empty_manifest", "vendor.emptyseg//nodeforge_package.json", "vendor.emptyseg/functions/demo.nf"),
        ("dot_function", "vendor.dotfunc/nodeforge_package.json", "vendor.dotfunc/functions/./demo.nf"),
        ("empty_function", "vendor.emptyfunc/nodeforge_package.json", "vendor.emptyfunc/functions//demo.nf"),
    ]
    for label, manifest_name, function_name in cases:
        archive = tmp_path / f"{label}.zip"
        package_id = f"vendor.{label}"
        manifest = {
            "schema_version": 1,
            "id": package_id,
            "name": package_id,
            "version": "1.0.0",
            "author": "Tester",
            "description": "Test package",
            "nodeforge_min_version": "0.49.47",
            "nodeforge_max_version": None,
            "contents": {"functions": "functions"},
            "permissions": {"python": False},
        }
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(manifest_name, json.dumps(manifest))
            zf.writestr(function_name, "output(value=1)\n")

        with pytest.raises(packages.PackageError, match="Unsafe zip member path"):
            packages.install_package_zip(archive, allow_python=False)

        assert package_id not in packages.load_package_state()["packages"]


def test_manifest_rejects_dot_and_empty_segments_in_content_roots(package_inventory, tmp_path):
    cases = [
        ({"functions": "functions/."}, "contents.functions"),
        ({"functions": "functions//sub"}, "contents.functions"),
        ({"systems": "systems/./x"}, "contents.systems"),
    ]
    for index, (contents, expected_field) in enumerate(cases):
        source = tmp_path / f"bad_manifest_path_{index}"
        for rel in contents.values():
            # Create the normalized directory so failure proves raw manifest validation, not missing path.
            normalized = Path(*[part for part in rel.split("/") if part not in {"", "."}])
            (source / normalized).mkdir(parents=True, exist_ok=True)
        _write_manifest(source, package_id=f"vendor.badpath{index}", contents=contents, python="systems" in contents)

        with pytest.raises(packages.PackageError, match=expected_field):
            packages.install_package_directory(source, allow_python=True)

        assert f"vendor.badpath{index}" not in packages.load_package_state()["packages"]


def test_directory_install_rejects_internal_duplicate_function_public_names(package_inventory, tmp_path):
    source = tmp_path / "dupe_functions"
    (source / "functions" / "foo").mkdir(parents=True)
    (source / "functions" / "foo.nf").write_text("output(value=1)\n", encoding="utf-8")
    (source / "functions" / "foo" / "source.nf").write_text("output(value=2)\n", encoding="utf-8")
    _write_manifest(source, package_id="vendor.dupefunc")

    with pytest.raises(packages.PackageError, match="Duplicate functions library entry"):
        packages.install_package_directory(source, allow_python=False)

    assert "vendor.dupefunc" not in packages.load_package_state()["packages"]


def test_zip_install_rejects_internal_duplicate_example_public_names(package_inventory, tmp_path):
    source = tmp_path / "zip_dupe" / "vendor.dupeexamples"
    (source / "examples" / "demo").mkdir(parents=True)
    (source / "examples" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    (source / "examples" / "demo" / "source.nf").write_text("output(value=2)\n", encoding="utf-8")
    _write_manifest(source, package_id="vendor.dupeexamples", contents={"examples": "examples"})
    archive = tmp_path / "dupe_examples.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in source.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(source.parent).as_posix())

    with pytest.raises(packages.PackageError, match="Duplicate examples library entry"):
        packages.install_package_zip(archive, allow_python=False)

    assert "vendor.dupeexamples" not in packages.load_package_state()["packages"]

def test_install_rejects_v1_system_owner_without_executing_module(package_inventory, tmp_path):
    source = tmp_path / "sys_pkg"
    system_root = source / "systems" / "test"
    system_root.mkdir(parents=True)
    _write_manifest(source, contents={"systems": "systems"}, python=True)
    sentinel = tmp_path / "v1_executed"
    (system_root / "system.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n"
        "CONSTRUCTORS = ['test_marker']\n"
        "def load_handlers(): return {}\n",
        encoding="utf-8",
    )

    with pytest.raises(packages.PackageError, match="unsupported Extension API v1 system.py"):
        packages.install_package_directory(source, allow_python=True)

    assert not sentinel.exists()

def test_install_rejects_v1_system_before_state_commit(package_inventory, tmp_path):
    source = tmp_path / "bad_sys_pkg"
    (source / "systems" / "bad").mkdir(parents=True)
    _write_manifest(source, package_id="vendor.bad", contents={"systems": "systems"}, python=True)
    (source / "systems" / "bad" / "system.py").write_text(
        "CONSTRUCTORS = ['bad_call']\n",
        encoding="utf-8",
    )

    with pytest.raises(packages.PackageError, match="unsupported Extension API v1 system.py"):
        packages.install_package_directory(source, allow_python=True)

    assert "vendor.bad" not in packages.load_package_state()["packages"]


def test_install_allows_package_export_colliding_with_core_builtin(package_inventory, tmp_path):
    """Owner-qualified exports may use a core callable spelling such as cube."""
    source = tmp_path / "core_collision"
    (source / "systems" / "test").mkdir(parents=True)
    _write_manifest(source, package_id="vendor.corecollision", contents={"systems": "systems"}, python=True)
    _write_v2_system(source / "systems" / "test", "cube")

    packages.install_package_directory(source, allow_python=True)

    manifest = next(item for item in packages.active_package_manifests() if item.package_id == "vendor.corecollision")
    inventory = packages._normalize_package_callable_inventory(manifest, normalize_native_libraries=True)
    assert inventory.systems == {"cube": "test"}


def test_replace_allows_new_export_colliding_with_core_builtin(package_inventory, tmp_path):
    """Replacing a package may add a core-spelled export without retaining the old member."""
    original = tmp_path / "original"
    (original / "systems" / "test").mkdir(parents=True)
    _write_manifest(original, package_id="vendor.replace", contents={"systems": "systems"}, python=True)
    _write_v2_system(original / "systems" / "test", "replace_marker")
    packages.install_package_directory(original, allow_python=True)

    replacement = tmp_path / "replacement"
    (replacement / "systems" / "test").mkdir(parents=True)
    _write_manifest(replacement, package_id="vendor.replace", contents={"systems": "systems"}, python=True, version="2.0.0")
    _write_v2_system(replacement / "systems" / "test", "cube")
    packages.install_package_directory(replacement, allow_python=True, replace=True)

    state_record = packages.load_package_state()["packages"]["vendor.replace"]
    assert state_record["installed_version"] == "2.0.0"
    manifest = next(item for item in packages.active_package_manifests() if item.package_id == "vendor.replace")
    inventory = packages._normalize_package_callable_inventory(manifest, normalize_native_libraries=True)
    assert inventory.systems == {"cube": "test"}


def test_cross_package_function_and_extension_names_may_overlap(package_inventory, tmp_path):
    """Same member spelling in different package owners is legal admission state."""
    owner = tmp_path / "active_constructor"
    (owner / "systems" / "test").mkdir(parents=True)
    _write_manifest(owner, package_id="vendor.constructorowner", contents={"systems": "systems"}, python=True)
    _write_v2_system(owner / "systems" / "test", "active_marker")
    packages.install_package_directory(owner, allow_python=True)

    source = tmp_path / "function_constructor_collision"
    (source / "functions").mkdir(parents=True)
    (source / "functions" / "active_marker.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(source, package_id="vendor.funcconstructor", contents={"functions": "functions"})
    packages.install_package_directory(source, allow_python=False)

    assert {"vendor.constructorowner", "vendor.funcconstructor"} <= set(packages.load_package_state()["packages"])


def test_cross_package_extension_and_function_names_may_overlap_reverse_order(package_inventory, tmp_path):
    """Admission overlap is symmetric with respect to installation order."""
    owner = tmp_path / "active_function"
    (owner / "functions").mkdir(parents=True)
    (owner / "functions" / "active_function.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(owner, package_id="vendor.functionowner", contents={"functions": "functions"})
    packages.install_package_directory(owner, allow_python=False)

    source = tmp_path / "constructor_function_collision"
    (source / "systems" / "test").mkdir(parents=True)
    _write_manifest(source, package_id="vendor.constructorfunc", contents={"systems": "systems"}, python=True)
    _write_v2_system(source / "systems" / "test", "active_function")
    packages.install_package_directory(source, allow_python=True)

    assert {"vendor.functionowner", "vendor.constructorfunc"} <= set(packages.load_package_state()["packages"])


def test_install_rejects_same_package_function_and_constructor_name(package_inventory, tmp_path):
    source = tmp_path / "same_package_collision"
    (source / "functions").mkdir(parents=True)
    (source / "functions" / "marker.nf").write_text("output(value=1)\n", encoding="utf-8")
    (source / "systems" / "test").mkdir(parents=True)
    _write_manifest(
        source,
        package_id="vendor.samecollision",
        contents={"functions": "functions", "systems": "systems"},
        python=True,
    )
    _write_v2_system(source / "systems" / "test", "marker")

    with pytest.raises(packages.PackageError, match="function and constructor"):
        packages.install_package_directory(source, allow_python=True)

    assert "vendor.samecollision" not in packages.load_package_state()["packages"]


def test_replace_allows_cross_owner_member_overlap(package_inventory, tmp_path):
    """Package replacement is not blocked by another owner's same-named export."""
    original = tmp_path / "original_cross_kind"
    (original / "functions").mkdir(parents=True)
    (original / "functions" / "original.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(original, package_id="vendor.crossreplace", contents={"functions": "functions"})
    packages.install_package_directory(original, allow_python=False)

    owner = tmp_path / "active_cross_constructor"
    (owner / "systems" / "test").mkdir(parents=True)
    _write_manifest(owner, package_id="vendor.crossowner", contents={"systems": "systems"}, python=True)
    _write_v2_system(owner / "systems" / "test", "cross_marker")
    packages.install_package_directory(owner, allow_python=True)

    replacement = tmp_path / "replacement_cross_kind"
    (replacement / "functions").mkdir(parents=True)
    (replacement / "functions" / "cross_marker.nf").write_text("output(value=2)\n", encoding="utf-8")
    _write_manifest(replacement, package_id="vendor.crossreplace", contents={"functions": "functions"}, version="2.0.0")
    packages.install_package_directory(replacement, allow_python=False, replace=True)

    state_record = packages.load_package_state()["packages"]["vendor.crossreplace"]
    assert state_record["installed_version"] == "2.0.0"
    assert {"vendor.crossowner", "vendor.crossreplace"} <= set(packages.load_package_state()["packages"])


def test_malformed_active_system_record_is_invalid_diagnostic_not_global_registry_failure(package_inventory, tmp_path):
    source = tmp_path / "bad_sys_pkg"
    (source / "systems" / "bad").mkdir(parents=True)
    _write_manifest(source, package_id="vendor.bad", contents={"systems": "systems"}, python=True)
    (source / "systems" / "bad" / "system.py").write_text(
        "CONSTRUCTORS = ['bad_call']\n",
        encoding="utf-8",
    )

    # Simulate a corrupt or pre-fix state record that points at an invalid installed system package.
    active_root = packages.installed_dir() / "vendor.bad" / "install_bad"
    import shutil

    shutil.copytree(source, active_root)
    state = packages.load_package_state()
    state["packages"]["vendor.bad"] = {
        "installed_path": "installed/vendor.bad/install_bad",
        "installed_version": "1.0.0",
        "allow_python": True,
        "origin": "user",
    }
    packages.save_package_state(state)

    diagnostics = packages.active_package_manifests(include_invalid=True)
    assert any(
        getattr(item, "package_id", None) == "vendor.bad" and "unsupported Extension API v1 system.py" in getattr(item, "message", "")
        for item in diagnostics
    )


def test_state_uses_origin_not_provenance(package_inventory, tmp_path):
    source = tmp_path / "source"
    (source / "functions").mkdir(parents=True)
    (source / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(source, package_id="vendor.origin")

    packages.install_package_directory(source, allow_python=False, origin="user")
    record = packages.load_package_state()["packages"]["vendor.origin"]

    assert record["origin"] == "user"
    assert "provenance" not in record


def test_invalid_package_diagnostic_exposes_python_consent_fields(package_inventory, tmp_path):
    source = tmp_path / "source"
    (source / "functions" / "demo").mkdir(parents=True)
    (source / "functions" / "demo" / "source.nf").write_text("output(value=1)\n", encoding="utf-8")
    (source / "functions" / "demo" / "function.py").write_text("X = 1\n", encoding="utf-8")
    _write_manifest(source, package_id="vendor.pyblocked", contents={"functions": "functions"}, python=True)
    manifest = packages.install_package_directory(source, allow_python=True)
    state = packages.load_package_state()
    state["packages"][manifest.package_id]["allow_python"] = False
    packages.save_package_state(state)

    active_ids = {m.package_id for m in packages.active_package_manifests()}
    diagnostics = packages.active_package_records(include_invalid=True)
    diag = next(item for item in diagnostics if isinstance(item, packages.PackageDiagnostic) and item.package_id == "vendor.pyblocked")

    assert "vendor.pyblocked" not in active_ids
    assert diag.python_required is True
    assert diag.python_allowed is False
    assert "Python" in diag.message
    assert diag.name == "vendor.pyblocked"
    assert diag.version == "1.0.0"





def test_inventory_does_not_auto_install_packages(tmp_path):
    packages.set_packages_dir_for_tests(tmp_path / "empty")

    assert packages.active_package_manifests() == []
    assert packages.load_package_state()["packages"] == {}


def test_uninstall_removes_invalid_record_without_deleting_untrusted_target(package_inventory, tmp_path):
    source = tmp_path / "invalid_source"
    (source / "functions").mkdir(parents=True)
    (source / "functions" / "demo.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(source, package_id="vendor.invalid")
    packages.install_package_directory(source, allow_python=False)

    other = tmp_path / "must_survive"
    other.mkdir()
    marker = other / "marker.txt"
    marker.write_text("keep", encoding="utf-8")

    state = packages.load_package_state()
    state["packages"]["vendor.invalid"]["installed_path"] = "../must_survive"
    packages.save_package_state(state)

    packages.uninstall_package("vendor.invalid")

    assert "vendor.invalid" not in packages.load_package_state()["packages"]
    assert marker.read_text(encoding="utf-8") == "keep"


def test_local_catalog_adapter_uses_build_local_transaction_cache(monkeypatch, tmp_path):
    """Local source materialization reuses the transaction-tracked build cache and fingerprint."""
    class FakeGroup(dict):
        def __init__(self, name):
            super().__init__()
            self.name = name
            self.bl_idname = "GeometryNodeTree"
            self.interface = types.SimpleNamespace(items_tree=[])

    class FakeNodeGroups(list):
        def get(self, name, default=None):
            return next((group for group in self if group.name == name), default)

    class Frame:
        def __init__(self):
            self.identity_children = []

        def record_dependency_identity(self, owner_scope, fingerprint):
            self.identity_children.append((owner_scope, fingerprint))

    fake_groups = FakeNodeGroups()
    fake_bpy = types.SimpleNamespace(
        data=types.SimpleNamespace(node_groups=fake_groups),
        app=types.SimpleNamespace(driver_namespace={}),
    )
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    import NodeForge.blender.library_groups as library_groups
    from NodeForge import catalog, local_sources
    library_groups = importlib.reload(library_groups)

    from NodeForge.function_instances import (
        FUNCTION_COMPILATION_FINGERPRINT_PROP,
        direct_library_owner_scope,
    )
    from NodeForge.function_materializer import FunctionMaterializationContext

    source_path = tmp_path / "demo.nf"
    source_path.write_text("output(value=1)\n", encoding="utf-8")
    record = catalog.LibraryEntryRecord(
        namespace="local",
        name="demo",
        kind="source",
        path=source_path,
        source_path=source_path,
    )
    monkeypatch.setattr(local_sources, "find_local_entry_record", lambda name: record)

    transaction = object()
    frame = Frame()
    trace = types.SimpleNamespace(current=frame)
    callback_calls = []

    def compile_group(source, group_name, **kwargs):
        callback_calls.append(kwargs)
        group = FakeGroup(group_name)
        group[FUNCTION_COMPILATION_FINGERPRINT_PROP] = "local-fingerprint"
        fake_groups.append(group)
        return group

    backend = _FakeGroupBackend(compile_group)
    cache = {}
    session = backend.new_source_callable_session()
    context = FunctionMaterializationContext(cache, transaction, trace, session)

    first = library_groups.get_or_create_library_entry_group(
        "local", "demo", backend, materialization_context=context
    )
    second = library_groups.get_or_create_library_entry_group(
        "local", "demo", backend, materialization_context=context
    )

    assert first.group is second.group
    assert first.instance_key == second.instance_key == ""
    assert len(callback_calls) == 1
    from NodeForge.compiler_identities import library_function_id
    function_id = library_function_id("local", None, "demo")
    assert cache[("local-catalog", function_id)] is first.group
    assert callback_calls[0]["function_group_transaction"] is transaction
    assert callback_calls[0]["function_compilation_trace"] is trace
    assert callback_calls[0]["function_compilation_inputs"]["kind"] == "library"
    assert callback_calls[0]["function_compilation_inputs"]["namespace"] == "local"
    owner = direct_library_owner_scope("local", function_id.package_id, "demo")
    assert frame.identity_children == [
        (owner, "local-fingerprint"),
        (owner, "local-fingerprint"),
    ]


def test_functions_catalog_keeps_duplicate_member_rows_owner_qualified(monkeypatch, tmp_path):
    """Functions records retain owner identity instead of collapsing duplicate public names."""
    from NodeForge import catalog

    def record(package_id: str, name: str) -> catalog.LibraryEntryRecord:
        path = tmp_path / f"{package_id.replace('.', '_')}_{name}.nf"
        path.write_text("output(value=1)\n", encoding="utf-8")
        return catalog.LibraryEntryRecord(
            namespace="functions", name=name, kind="source", path=path, source_path=path,
            package_id=package_id, package_name=package_id, package_version="1.0.0",
        )

    records = (record("vendor.a", "foo"), record("vendor.b", "foo"), record("nodeforge.lsystem", "points"))
    assert [(item.name, item.package_id) for item in records] == [
        ("foo", "vendor.a"), ("foo", "vendor.b"), ("points", "nodeforge.lsystem")
    ]
    with pytest.raises(CompileError, match="Duplicate functions library entry 'foo'"):
        catalog.unique_records_from_candidates("functions", records)

def test_package_function_reload_uses_persisted_owner_and_never_substitutes_same_name(monkeypatch, tmp_path):
    """Reload resolves stored package ownership before member text and fails closed when that owner disappears."""
    fake_bpy = types.SimpleNamespace(
        data=types.SimpleNamespace(node_groups=[]),
        app=types.SimpleNamespace(driver_namespace={}),
    )
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    import NodeForge.blender.library_groups as library_groups
    from NodeForge import catalog
    library_groups = importlib.reload(library_groups)

    def record(package_id: str, name: str) -> catalog.LibraryEntryRecord:
        """Create one reloadable owner-specific source record."""
        path = tmp_path / f"{package_id.replace('.', '_')}_{name}.nf"
        path.write_text("output(value=1)\n", encoding="utf-8")
        return catalog.LibraryEntryRecord(
            namespace="functions",
            name=name,
            kind="source",
            path=path,
            source_path=path,
            package_id=package_id,
            package_name=package_id,
            package_version="1.0.0",
        )

    owner_a = record("vendor.a", "foo")
    owner_b = record("vendor.b", "foo")
    environment = types.SimpleNamespace(package_function_record=lambda package_id, name: owner_a if package_id == "vendor.a" else owner_b if package_id == "vendor.b" else None)
    group = {
        "nodeforge_library_namespace": "functions",
        "nodeforge_library_name": "foo",
        "nodeforge_package_id": "vendor.a",
    }
    assert library_groups.resolve_reloadable_library_entry(group, resolved_environment=environment) is owner_a

    environment = types.SimpleNamespace(package_function_record=lambda package_id, name: owner_b if package_id == "vendor.b" else None)
    with pytest.raises(CompileError, match=r"Current source.*'foo'.*vendor\.a.*unavailable"):
        library_groups.resolve_reloadable_library_entry(group, resolved_environment=environment)

    core_spelled = record("nodeforge.lsystem", "points")
    environment = types.SimpleNamespace(package_function_record=lambda package_id, name: core_spelled if package_id == "nodeforge.lsystem" else None)
    points_group = {
        "nodeforge_library_namespace": "functions",
        "nodeforge_library_name": "points",
        "nodeforge_package_id": "nodeforge.lsystem",
    }
    assert library_groups.resolve_reloadable_library_entry(points_group, resolved_environment=environment) is core_spelled

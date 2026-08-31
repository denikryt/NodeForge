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
from NodeForge.systems import registry as systems_registry


@pytest.fixture(autouse=True)
def package_inventory(tmp_path):
    """Use an isolated inventory with packages installed explicitly by the test setup."""
    packages.set_packages_dir_for_tests(tmp_path)
    systems_registry.invalidate_cache()
    yield tmp_path
    packages.set_packages_dir_for_tests(None)
    systems_registry.invalidate_cache()



class _FakeGroupBackend:
    """Exercise library adapters through the explicit group-backend contract."""

    def __init__(self, callback):
        self._callback = callback

    def create_or_update(
        self, *, source, name, existing_group=None, build_options=None,
        preserve_if_equivalent=False, finalize_before_commit=None
    ):
        kwargs = dict(build_options or {})
        if existing_group is not None:
            kwargs["existing_group"] = existing_group
        if preserve_if_equivalent:
            kwargs["preserve_if_equivalent"] = True
        group = self._callback(source, name, **kwargs)
        if not hasattr(group, "interface"):
            group.interface = types.SimpleNamespace(items_tree=[])
        if finalize_before_commit is not None:
            finalize_before_commit(group)
        return group

    def compile_group_callback(self, source, name="NodeForge Group", **kwargs):
        """Provide the package-Python authoring callback over the same fake backend."""
        existing_group = kwargs.pop("existing_group", None)
        preserve = bool(kwargs.pop("preserve_if_equivalent", False))
        return self.create_or_update(
            source=source,
            name=name,
            existing_group=existing_group,
            build_options=kwargs,
            preserve_if_equivalent=preserve,
        )

def _write_manifest(
    root: Path,
    package_id="vendor.demo",
    *,
    contents=None,
    python=False,
    version="1.0.0",
    nodeforge_min_version="0.49.47",
    nodeforge_max_version=None,
):
    if contents is None:
        contents = {"functions": "functions"}
    root.mkdir(parents=True, exist_ok=True)
    (root / "nodeforge_package.json").write_text(
        json.dumps(
            {
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

    fake_bpy = types.SimpleNamespace(data=types.SimpleNamespace(node_groups=FakeNodeGroups()), app=types.SimpleNamespace(driver_namespace={}))
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    import NodeForge.library as library

    library = importlib.reload(library)

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

    group_a = library.materialize_library_entry_group("examples", "demo", _FakeGroupBackend(compile_group))
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
    assert compiled[0][2]["function_group_owner_scope"] == expected_owner_a
    assert compiled[0][2]["function_definition_identity"] == function_id_a.stable_key()
    assert compiled[0][2]["function_instance_key"] == ""
    assert group_a.name in fake_bpy.data.node_groups

    direct_again = library.materialize_library_entry_group("examples", "demo", _FakeGroupBackend(compile_group))
    assert direct_again is group_a
    assert compiled[-1][1] is group_a

    packages.uninstall_package("vendor.a")
    packages.install_package_directory(source_b, allow_python=False)
    systems_registry.invalidate_cache()

    group_b = library.materialize_library_entry_group("examples", "demo", _FakeGroupBackend(compile_group))

    assert group_b is not group_a
    assert group_b.name != group_a.name
    assert group_b["nodeforge_package_id"] == "vendor.b"
    assert group_b["nodeforge_package_version"] == "1.0.0"
    assert group_a["nodeforge_package_id"] == "vendor.a"




def test_library_materialization_contract_discriminator_is_explicit(package_inventory, tmp_path, monkeypatch):
    """IR materialization selects call semantics while None selects direct shared definition."""
    class FakeGroup(dict):
        def __init__(self, name):
            super().__init__()
            self.name = name
            self.bl_idname = "GeometryNodeTree"

    class FakeNodeGroups(list):
        def get(self, name, default=None):
            return next((group for group in self if group.name == name), default)

    fake_groups = FakeNodeGroups()
    fake_bpy = types.SimpleNamespace(data=types.SimpleNamespace(node_groups=fake_groups), app=types.SimpleNamespace(driver_namespace={}))
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    import NodeForge.library as library
    library = importlib.reload(library)

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

    from NodeForge.compiler_identities import CallSiteId, library_function_id
    from NodeForge.function_instances import function_group_owner_scope, instance_key_for
    from NodeForge.semantic_ir import IRFunctionMaterialization, IRFunctionMaterializationMode

    from NodeForge.function_materializer import FunctionMaterializationContext

    function_id = library_function_id("functions", "vendor.contract", "demo")
    direct_result = library.get_or_create_library_entry_group(
        "functions", "demo", _FakeGroupBackend(compile_group), materialization=None
    )
    direct = direct_result.group
    assert direct_result.instance_key == ""
    assert direct.get("nodeforge_function_instance_key") == ""
    assert calls[-1]["function_group_owner_scope"] == function_group_owner_scope(
        "LIBRARY", "functions", "vendor.contract", "demo", instance_key=None
    )
    assert calls[-1]["function_group_cache"] is None
    assert calls[-1]["function_group_transaction"] is None
    assert calls[-1]["function_compilation_trace"] is None

    call_site = CallSiteId("root-owner", function_id, 0)
    materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.UNIQUE, call_site)
    monkeypatch.setattr(
        library,
        "library_function_id",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reusable adapter must not reconstruct FunctionId")),
    )
    cache = {}
    context = FunctionMaterializationContext(cache, None, None)
    unique_result = library.get_or_create_library_entry_group(
        "functions",
        "demo",
        _FakeGroupBackend(compile_group),
        materialization=materialization,
        function_id=function_id,
        materialization_context=context,
    )
    unique = unique_result.group
    unique_key = instance_key_for(call_site)
    assert unique_result.instance_key == unique_key
    assert unique is not direct
    assert unique.get("nodeforge_function_instance_key") == unique_key
    assert calls[-1]["function_group_owner_scope"] == function_group_owner_scope(
        "LIBRARY", "functions", "vendor.contract", "demo", instance_key=unique_key
    )
    assert cache[("library", function_id, unique_key)] is unique

    other_id = library_function_id("functions", "vendor.other", "demo")
    wrong = IRFunctionMaterialization(other_id, IRFunctionMaterializationMode.SHARED)
    with pytest.raises(CompileError, match="does not match"):
        library.get_or_create_library_entry_group(
            "functions",
            "demo",
            _FakeGroupBackend(compile_group),
            materialization=wrong,
            function_id=function_id,
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
    _write_manifest(valid, package_id="vendor.3d")
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
        _write_manifest(source, package_id=package_id)
        manifest = packages.install_package_directory(source, allow_python=False)
        assert manifest.package_id == package_id

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

def test_system_entrypoint_declares_names_and_loads_handlers_lazily(package_inventory, tmp_path):
    source = tmp_path / "sys_pkg"
    (source / "systems" / "test").mkdir(parents=True)
    _write_manifest(source, contents={"systems": "systems"}, python=True)
    (source / "systems" / "test" / "system.py").write_text(
        "CONSTRUCTORS = ['test_marker']\n"
        "def load_handlers():\n"
        "    from .runtime import HANDLERS\n"
        "    return HANDLERS\n",
        encoding="utf-8",
    )
    (source / "systems" / "test" / "runtime.py").write_text(
        "LOADED = True\nHANDLERS = {'test_marker': lambda comp, expr, depth=0: ('ok', depth)}\n",
        encoding="utf-8",
    )
    packages.install_package_directory(source, allow_python=True)

    import sys

    before = set(sys.modules)
    assert "test_marker" in systems_registry.constructor_names()
    assert not any(name.endswith(".runtime") for name in set(sys.modules) - before)
    owner = systems_registry._constructor_map()["test_marker"]
    assert systems_registry._load_handlers(owner)["test_marker"](None, None, 3) == ("ok", 3)
    assert any(name.endswith(".runtime") for name in set(sys.modules) - before)


def test_install_rejects_system_missing_load_handlers_before_state_commit(package_inventory, tmp_path):
    source = tmp_path / "bad_sys_pkg"
    (source / "systems" / "bad").mkdir(parents=True)
    _write_manifest(source, package_id="vendor.bad", contents={"systems": "systems"}, python=True)
    (source / "systems" / "bad" / "system.py").write_text(
        "CONSTRUCTORS = ['bad_call']\n",
        encoding="utf-8",
    )

    with pytest.raises(packages.PackageError, match="load_handlers"):
        packages.install_package_directory(source, allow_python=True)

    assert "vendor.bad" not in packages.load_package_state()["packages"]
    assert "bad_call" not in systems_registry.constructor_names()


def test_install_rejects_system_constructor_colliding_with_core_builtin(package_inventory, tmp_path):
    source = tmp_path / "core_collision"
    (source / "systems" / "test").mkdir(parents=True)
    _write_manifest(source, package_id="vendor.corecollision", contents={"systems": "systems"}, python=True)
    (source / "systems" / "test" / "system.py").write_text(
        "CONSTRUCTORS = ['cube']\n"
        "def load_handlers():\n"
        "    return {'cube': lambda comp, expr, depth=0: None}\n",
        encoding="utf-8",
    )

    with pytest.raises(packages.PackageError, match="core callable"):
        packages.install_package_directory(source, allow_python=True)

    assert "vendor.corecollision" not in packages.load_package_state()["packages"]
    assert not systems_registry.has_system_constructor("cube")


def test_replace_rejects_core_builtin_constructor_collision_and_keeps_old_pointer(package_inventory, tmp_path):
    original = tmp_path / "original"
    (original / "systems" / "test").mkdir(parents=True)
    _write_manifest(original, package_id="vendor.replace", contents={"systems": "systems"}, python=True)
    (original / "systems" / "test" / "system.py").write_text(
        "CONSTRUCTORS = ['replace_marker']\n"
        "def load_handlers():\n"
        "    return {'replace_marker': lambda comp, expr, depth=0: None}\n",
        encoding="utf-8",
    )
    packages.install_package_directory(original, allow_python=True)
    old_pointer = packages.load_package_state()["packages"]["vendor.replace"]["installed_path"]
    assert systems_registry.has_system_constructor("replace_marker")

    replacement = tmp_path / "replacement"
    (replacement / "systems" / "test").mkdir(parents=True)
    _write_manifest(replacement, package_id="vendor.replace", contents={"systems": "systems"}, python=True, version="2.0.0")
    (replacement / "systems" / "test" / "system.py").write_text(
        "CONSTRUCTORS = ['cube']\n"
        "def load_handlers():\n"
        "    return {'cube': lambda comp, expr, depth=0: None}\n",
        encoding="utf-8",
    )

    with pytest.raises(packages.PackageError, match="core callable"):
        packages.install_package_directory(replacement, allow_python=True, replace=True)

    state_record = packages.load_package_state()["packages"]["vendor.replace"]
    assert state_record["installed_path"] == old_pointer
    assert state_record["installed_version"] == "1.0.0"
    systems_registry.invalidate_cache()
    assert systems_registry.has_system_constructor("replace_marker")
    assert not systems_registry.has_system_constructor("cube")


def test_install_rejects_function_name_colliding_with_active_constructor(package_inventory, tmp_path):
    owner = tmp_path / "active_constructor"
    (owner / "systems" / "test").mkdir(parents=True)
    _write_manifest(owner, package_id="vendor.constructorowner", contents={"systems": "systems"}, python=True)
    (owner / "systems" / "test" / "system.py").write_text(
        "CONSTRUCTORS = ['active_marker']\n"
        "def load_handlers():\n"
        "    return {'active_marker': lambda comp, expr, depth=0: None}\n",
        encoding="utf-8",
    )
    packages.install_package_directory(owner, allow_python=True)

    source = tmp_path / "function_constructor_collision"
    (source / "functions").mkdir(parents=True)
    (source / "functions" / "active_marker.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(source, package_id="vendor.funcconstructor", contents={"functions": "functions"})

    with pytest.raises(packages.PackageError, match="Public name collision.*active_marker"):
        packages.install_package_directory(source, allow_python=False)

    assert "vendor.funcconstructor" not in packages.load_package_state()["packages"]


def test_install_rejects_constructor_name_colliding_with_active_function(package_inventory, tmp_path):
    owner = tmp_path / "active_function"
    (owner / "functions").mkdir(parents=True)
    (owner / "functions" / "active_function.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(owner, package_id="vendor.functionowner", contents={"functions": "functions"})
    packages.install_package_directory(owner, allow_python=False)

    source = tmp_path / "constructor_function_collision"
    (source / "systems" / "test").mkdir(parents=True)
    _write_manifest(source, package_id="vendor.constructorfunc", contents={"systems": "systems"}, python=True)
    (source / "systems" / "test" / "system.py").write_text(
        "CONSTRUCTORS = ['active_function']\n"
        "def load_handlers():\n"
        "    return {'active_function': lambda comp, expr, depth=0: None}\n",
        encoding="utf-8",
    )

    with pytest.raises(packages.PackageError, match="Public name collision.*active_function"):
        packages.install_package_directory(source, allow_python=True)

    assert "vendor.constructorfunc" not in packages.load_package_state()["packages"]


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
    (source / "systems" / "test" / "system.py").write_text(
        "CONSTRUCTORS = ['marker']\n"
        "def load_handlers():\n"
        "    return {'marker': lambda comp, expr, depth=0: None}\n",
        encoding="utf-8",
    )

    with pytest.raises(packages.PackageError, match="function and constructor"):
        packages.install_package_directory(source, allow_python=True)

    assert "vendor.samecollision" not in packages.load_package_state()["packages"]


def test_replace_rejects_cross_kind_collision_and_keeps_old_pointer(package_inventory, tmp_path):
    original = tmp_path / "original_cross_kind"
    (original / "functions").mkdir(parents=True)
    (original / "functions" / "original.nf").write_text("output(value=1)\n", encoding="utf-8")
    _write_manifest(original, package_id="vendor.crossreplace", contents={"functions": "functions"})
    packages.install_package_directory(original, allow_python=False)
    old_pointer = packages.load_package_state()["packages"]["vendor.crossreplace"]["installed_path"]

    owner = tmp_path / "active_cross_constructor"
    (owner / "systems" / "test").mkdir(parents=True)
    _write_manifest(owner, package_id="vendor.crossowner", contents={"systems": "systems"}, python=True)
    (owner / "systems" / "test" / "system.py").write_text(
        "CONSTRUCTORS = ['cross_marker']\n"
        "def load_handlers():\n"
        "    return {'cross_marker': lambda comp, expr, depth=0: None}\n",
        encoding="utf-8",
    )
    packages.install_package_directory(owner, allow_python=True)

    replacement = tmp_path / "replacement_cross_kind"
    (replacement / "functions").mkdir(parents=True)
    (replacement / "functions" / "cross_marker.nf").write_text("output(value=2)\n", encoding="utf-8")
    _write_manifest(
        replacement,
        package_id="vendor.crossreplace",
        contents={"functions": "functions"},
        version="2.0.0",
    )

    with pytest.raises(packages.PackageError, match="Public name collision.*cross_marker"):
        packages.install_package_directory(replacement, allow_python=False, replace=True)

    state_record = packages.load_package_state()["packages"]["vendor.crossreplace"]
    assert state_record["installed_path"] == old_pointer
    assert state_record["installed_version"] == "1.0.0"


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
    systems_registry.invalidate_cache()

    assert "bad_call" not in systems_registry.constructor_names()
    diagnostics = packages.active_package_manifests(include_invalid=True)
    assert any(
        getattr(item, "package_id", None) == "vendor.bad" and "load_handlers" in getattr(item, "message", "")
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
    systems_registry.invalidate_cache()

    assert packages.active_package_manifests() == []
    assert packages.load_package_state()["packages"] == {}
    assert systems_registry.constructor_names() == ()


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


def test_local_catalog_adapter_does_not_use_generic_function_group_cache(monkeypatch, tmp_path):
    """Active Local catalog materialization must not acquire reusable-cache semantics."""
    class FakeGroup(dict):
        def __init__(self, name):
            super().__init__()
            self.name = name
            self.bl_idname = "GeometryNodeTree"
            self.interface = types.SimpleNamespace(items_tree=[])

    class FakeNodeGroups(list):
        def get(self, name, default=None):
            return next((group for group in self if group.name == name), default)

    class ExplodingMapping(dict):
        def __getitem__(self, key):
            raise AssertionError("Local catalog must not read function_group_cache")
        def get(self, key, default=None):
            raise AssertionError("Local catalog must not read function_group_cache")
        def __contains__(self, key):
            raise AssertionError("Local catalog must not inspect function_group_cache")
        def __setitem__(self, key, value):
            raise AssertionError("Local catalog must not write function_group_cache")
        def setdefault(self, key, default=None):
            raise AssertionError("Local catalog must not write function_group_cache")

    class Frame:
        def __init__(self):
            self.unproven = []
            self.children = []
        def mark_unproven(self, reason):
            self.unproven.append(reason)
        def record_child(self, owner, fingerprint):
            self.children.append((owner, fingerprint))

    fake_groups = FakeNodeGroups()
    fake_bpy = types.SimpleNamespace(data=types.SimpleNamespace(node_groups=fake_groups), app=types.SimpleNamespace(driver_namespace={}))
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    import NodeForge.library as library
    library = importlib.reload(library)
    from NodeForge.function_materializer import FunctionMaterializationContext

    source_path = tmp_path / "demo.nf"
    source_path.write_text("output(value=1)\n", encoding="utf-8")
    record = library.LibraryEntryRecord(
        namespace="local",
        name="demo",
        kind="source",
        path=source_path,
        source_path=source_path,
    )
    monkeypatch.setattr(library, "find_library_entry_record", lambda namespace, name: record)
    monkeypatch.setattr(library, "load_library_entry_source", lambda namespace, name: "output(value=1)\n")
    monkeypatch.setattr(library, "backend_builtins_for_entry", lambda namespace, name: {})

    transaction = object()
    frame = Frame()
    trace = types.SimpleNamespace(current=frame)
    callback_calls = []

    def compile_group(source, group_name, **kwargs):
        callback_calls.append(kwargs)
        group = FakeGroup(group_name)
        fake_groups.append(group)
        return group

    materialized = library.get_or_create_library_entry_group(
        "local",
        "demo",
        _FakeGroupBackend(compile_group),
        materialization_context=FunctionMaterializationContext(
            ExplodingMapping(), transaction, trace
        ),
    )
    group = materialized.group

    assert materialized.instance_key == ""
    assert group is fake_groups[-1]
    assert callback_calls[0]["function_group_transaction"] is transaction
    assert callback_calls[0]["function_compilation_trace"] is trace
    assert "function_group_cache" not in callback_calls[0]
    assert frame.unproven == ["local catalog dependency"]
    assert frame.children == []

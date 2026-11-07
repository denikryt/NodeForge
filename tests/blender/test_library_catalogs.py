from helpers import *

from NodeForge.blender import library_groups

from NodeForge import local_sources

import json
import tempfile
from pathlib import Path

from NodeForge import packages
from NodeForge import environment_resolution
from NodeForge.semantic import source_callable_session


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _cleanup(path):
    if path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        for child in sorted(path.rglob('*'), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        path.rmdir()


def test_resolved_environment_defers_local_failure_without_repeating_discovery(monkeypatch):
    """An unused failed Local result stays deferred and replays without filesystem reads."""
    calls = []

    def malformed_registry():
        calls.append(True)
        raise CompileError("Unsupported Local source registry format")

    monkeypatch.setattr(local_sources, "_read_local_source_registry", malformed_registry)
    environment = environment_resolution.resolve_environment()
    backend = compiler._new_group_backend(environment)
    group = prepared_create_or_update(
        'def nf_fixture() -> Float:\n    return 2.0\n',
        "NFTest_deferred_local_failure",
        backend=backend,
    )

    check(group is not None, "source without Local imports failed on deferred Local error")
    check(calls == [True], f"Local registry was read more than once during resolution: {calls}")
    for lookup in (
        environment.catalog("local").names,
        environment.catalog("local").records,
        lambda: environment.catalog("local").find("missing"),
    ):
        try:
            lookup()
        except CompileError as exc:
            check(str(exc) == "Unsupported Local source registry format", "deferred Local diagnostic changed")
        else:
            raise AssertionError("failed Local catalog lookup did not replay its error")
    check(calls == [True], "deferred Local lookup repeated registry discovery")


def test_explicit_local_import_replays_deferred_local_failure(monkeypatch):
    """Local catalog failures remain lazy until source syntax actually needs Local resolution."""
    calls = []

    def malformed_registry():
        calls.append(True)
        raise CompileError("Unsupported Local source registry format")

    monkeypatch.setattr(local_sources, "_read_local_source_registry", malformed_registry)
    environment = environment_resolution.resolve_environment()
    backend = compiler._new_group_backend(environment)
    with pytest.raises(CompileError, match="Unsupported Local source registry format"):
        prepared_create_or_update(
            'from local import missing\ndef nf_fixture() -> Float:\n    return 2.0\n',
            "NFTest_deferred_local_failure_used",
            backend=backend,
        )
    check(calls == [True], "explicit Local use repeated registry discovery")




def test_new_environment_observes_local_changes_without_mutating_old_snapshot():
    """A later root session sees new Local state while an older selection stays fixed."""
    local = local_sources.ensure_local_catalog_dir()
    first_path = local / "environment_generation_first.nf"
    second_path = local / "environment_generation_second.nf"
    try:
        _write(first_path, 'output("Value", 1.0)\n')
        first = environment_resolution.resolve_environment()
        _write(second_path, 'output("Value", 2.0)\n')
        second = environment_resolution.resolve_environment()

        check(first.catalog("local").find("environment_generation_first") is not None, "first snapshot lost its record")
        check(first.catalog("local").find("environment_generation_second") is None, "old snapshot observed later Local state")
        check(second.catalog("local").find("environment_generation_second") is not None, "new snapshot missed later Local state")
    finally:
        first_path.unlink(missing_ok=True)
        second_path.unlink(missing_ok=True)







def test_local_save_ui_uses_selected_text_source_only():
    from types import SimpleNamespace
    from NodeForge import ui

    text_source = 'x = input_float("Text", default=1.0)\noutput("x", x)\n'
    text = SimpleNamespace(as_string=lambda: text_source)
    props = SimpleNamespace(text_block=text)
    check(ui._source_from_props(props) == text_source, 'Local Save did not use the selected Text datablock')
    check(ui._source_from_props(SimpleNamespace(text_block=None)) == '', 'Local Save source unexpectedly fell back without a Text datablock')








def _library_backing(group, namespace, name):
    """Return the imported catalog backing group used by *group*."""
    return next(
        node.node_tree
        for node in group.nodes
        if getattr(getattr(node, "node_tree", None), "get", lambda *args: None)("nodeforge_library_namespace") == namespace
        and node.node_tree.get("nodeforge_library_name") == name
    )




def test_reload_local_catalog_group_fails_before_mutation_when_source_disappears():
    """A missing current catalog source must leave the materialized group untouched."""
    local = local_sources.ensure_local_catalog_dir()
    source = local / "local_reload_missing.nf"
    group = None
    try:
        _write(source, 'def reload_value(x: Float["X"] = 1.0) -> Float:\n    return x\n')
        group = compiler.create_library_catalog_group("local", "local_reload_missing")
        pointer = group.as_pointer()
        embedded = group.get("nodeforge_library_source")
        node_count = len(group.nodes)
        source.unlink()
        try:
            compiler.update_library_catalog_group(group)
        except CompileError:
            pass
        else:
            raise AssertionError("reload accepted a missing current Local source")
        check(group.as_pointer() == pointer, "missing-source reload replaced the root datablock")
        check(group.get("nodeforge_library_source") == embedded, "missing-source reload changed provenance source")
        check(len(group.nodes) == node_count, "missing-source reload mutated the root graph")
    finally:
        source.unlink(missing_ok=True)
        if group is not None and bpy.data.node_groups.get(group.name) is group:
            bpy.data.node_groups.remove(group, do_unlink=True)


def test_reload_local_catalog_group_rolls_back_invalid_current_source_without_leaks():
    """Compilation failure during reload must preserve the root and clean transaction groups."""
    local = local_sources.ensure_local_catalog_dir()
    source = local / "local_reload_invalid.nf"
    group = None
    try:
        original = 'def reload_value(x: Float["X"] = 1.0) -> Float:\n    return x\n'
        _write(source, original)
        group = compiler.create_library_catalog_group("local", "local_reload_invalid")
        pointer = group.as_pointer()
        node_names = [node.name for node in group.nodes]
        _write(source, 'def reload_value(x: Float["X"] = 1.0) -> Float:\n    return missing_reload_function(x)\n')
        try:
            compiler.update_library_catalog_group(group)
        except Exception:
            pass
        else:
            raise AssertionError("reload accepted invalid current source")
        check(group.as_pointer() == pointer, "failed reload replaced the root datablock")
        check([node.name for node in group.nodes] == node_names, "failed reload did not preserve the root graph")
        leaked = [
            g.name for g in bpy.data.node_groups
            if g.name.startswith("NodeForge.replacement.") or g.name.startswith("NodeForge.rollback.") or g.name.startswith("NodeForge.preflight.")
        ]
        check(not leaked, f"failed library reload leaked transaction groups: {leaked}")
    finally:
        source.unlink(missing_ok=True)
        if group is not None and bpy.data.node_groups.get(group.name) is group:
            bpy.data.node_groups.remove(group, do_unlink=True)


def _make_reload_package(root: Path, *, package_id: str, version: str, value: float) -> None:
    """Create a source-only functions package used by reload ownership tests."""
    functions = root / "functions"
    functions.mkdir(parents=True)
    (functions / "reload_value.nf").write_text(
        f'def reload_value(value: Float = {value}) -> Float:\n    return value\n',
        encoding="utf-8",
    )
    (root / "nodeforge_package.json").write_text(
        json.dumps({
            "schema_version": 1,
            "id": package_id,
            "name": package_id,
            "version": version,
            "author": "Tests",
            "description": "Reload ownership fixture",
            "nodeforge_min_version": "0.49.47",
            "nodeforge_max_version": None,
            "contents": {"functions": "functions"},
            "permissions": {"python": False},
        }),
        encoding="utf-8",
    )




def test_native_only_library_entry_is_not_reloadable():
    """Native-only catalog entries stay outside the in-place source reload path."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        packages.set_packages_dir_for_tests(tmp / "inventory")
        group = None
        try:
            source = tmp / "native_only"
            entry = source / "functions" / "reload_native"
            entry.mkdir(parents=True)
            (entry / "function.py").write_text(
                "def materialize_group(compile_group_callback):\n    raise AssertionError('reload resolver executed native materializer')\n",
                encoding="utf-8",
            )
            (source / "nodeforge_package.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "id": "vendor.native_reload",
                    "name": "Vendor Native Reload",
                    "version": "1.0.0",
                    "author": "Tests",
                    "description": "Native-only reload fixture",
                    "nodeforge_min_version": "0.49.47",
                    "nodeforge_max_version": None,
                    "contents": {"functions": "functions"},
                    "permissions": {"python": True},
                }),
                encoding="utf-8",
            )
            packages.install_package_directory(source)
            group = bpy.data.node_groups.new("NFTest_native_only_reload", "GeometryNodeTree")
            group["nodeforge_library_namespace"] = "functions"
            group["nodeforge_library_name"] = "reload_native"
            group["nodeforge_package_id"] = "vendor.native_reload"
            try:
                library_groups.resolve_reloadable_library_entry(group)
            except CompileError as exc:
                check("no reloadable .nf source" in str(exc), f"unexpected native-only reload error: {exc}")
            else:
                raise AssertionError("native-only library entry was treated as reloadable")
        finally:
            if group is not None and bpy.data.node_groups.get(group.name) is group:
                bpy.data.node_groups.remove(group, do_unlink=True)
            packages.set_packages_dir_for_tests(None)
            packages.invalidate_caches()

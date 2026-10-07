"""Physical Examples materialization and snapshot-consistent Functions reload."""

from helpers import *

import json
from pathlib import Path

import pytest

from NodeForge import compiler, catalog, local_sources, packages
from NodeForge.blender import library_groups
from NodeForge.errors import CompileError
from NodeForge.environment_resolution import resolve_environment


def _package(root, package_id, namespace, *, python=False):
    """Create one isolated installable package for the physical compiler path."""
    root.mkdir()
    (root / namespace).mkdir()
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': package_id, 'name': package_id,
        'version': '1.0.0', 'author': 'Tests', 'description': 'qualified package-callable review',
        'nodeforge_min_version': '0.65.0', 'nodeforge_max_version': None,
        'contents': {namespace: namespace}, 'permissions': {'python': python},
    }))


def test_python_example_import_materializes_through_registry(tmp_path):
    root = tmp_path / 'example-package'
    _package(root, 'vendor.review', 'examples', python=True)
    owner = root / 'examples' / 'review_example'
    owner.mkdir()
    (owner / 'interface.py').write_text(
        'from typing import Annotated\nfrom NodeForge import Float, EvaluationMode\n'
        'EXTENSION_API = 2\nEXTENSIONS = {"review_example": ".backend:build"}\n'
        'def review_example(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n'
    )
    (owner / 'backend.py').write_text('def build(context, value):\n    return value\n')
    packages.install_package_directory(root, allow_python=True)
    group = compile_group(
        'from examples import review_example\nx = input_float("X")\noutput("Value", review_example(x))\n',
        'NFTest_review_python_example',
    )
    check(group is not None, 'Python Example failed physical materialization')
    expect_compile_error(
        'from packages import review\nx = input_float("X")\noutput(review.review_example(x))\n',
        'NFTest_review_example_not_export',
    )


def test_functions_reload_uses_one_environment_without_live_rediscovery(tmp_path, monkeypatch):
    root = tmp_path / 'function-package'
    _package(root, 'vendor.review', 'functions')
    source = root / 'functions' / 'foo.nf'
    source.write_text('x = input_float("X")\noutput("Value", x + 1)\n')
    installed = packages.install_package_directory(root, allow_python=False)
    group = compiler.create_package_function_group('vendor.review', 'foo')
    (installed.root / 'functions' / 'foo.nf').write_text(
        'x = input_float("X")\noutput("Value", x + 2)\n'
    )
    snapshot = resolve_environment()
    calls = []
    def one_snapshot():
        calls.append(snapshot)
        assert len(calls) == 1
        return snapshot
    def forbidden(*args, **kwargs):
        pytest.fail('Reload repeated live catalog discovery')
    monkeypatch.setattr(compiler, 'resolve_environment', one_snapshot)
    monkeypatch.setattr(catalog, 'candidate_records_from_inputs', forbidden)
    updated = compiler.update_library_catalog_group(group, 'functions', 'foo')
    check(updated is group, 'Reload replaced the selected group identity')
    check(group.get('nodeforge_package_id') == 'vendor.review', 'Reload lost canonical owner')
    assert calls == [snapshot]
    with pytest.raises(CompileError, match='belongs to'):
        library_groups.resolve_reloadable_library_entry(group, 'functions', 'other', resolved_environment=snapshot)

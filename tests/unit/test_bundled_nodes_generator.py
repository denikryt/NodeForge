"""Pinned Inspector and generated Blender 5.2 Nodes package stay installable and in sync."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from NodeForge import packages

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'packages' / 'nodes'
BUILD = ROOT / 'tools' / 'nodegen' / 'build.py'


def test_bundled_package_declares_generated_blender_owner():
    """Generated extensions are a normal system owner with direct package callables."""
    manifest = packages.validate_package_root(PACKAGE)
    records = packages.system_package_records_from_manifests((manifest,))
    assert manifest.package_id == 'nodeforge.nodes'
    assert manifest.import_name == 'nodes'
    assert [(r.system_id, r.package_id) for r in records] == [('blender', 'nodeforge.nodes')]
    assert not (PACKAGE / 'functions').exists()
    text = (PACKAGE / 'systems' / 'blender' / 'interface.py').read_text(encoding='utf-8')
    assert 'def resample_curve(' in text
    assert 'def set_position(' in text
    assert "'resample_curve': '.backend:resample_curve'" in text


def test_generated_bindings_and_bundle_match_inspector():
    """Regeneration must not alter manually edited manifests or generated bindings."""
    before = (PACKAGE / 'nodeforge_package.json').read_bytes()
    run = subprocess.run([sys.executable, str(BUILD), '--check'],
                         capture_output=True, text=True, check=True)
    assert 'GENERATED 297 functions' in run.stdout
    assert (PACKAGE / 'nodeforge_package.json').read_bytes() == before
    meta = json.loads(before)
    archive = ROOT / 'package_install' / f"{meta['id']}-v{meta['version']}_nf-v{meta['nodeforge_min_version']}.zip"
    with zipfile.ZipFile(archive) as zf:
        assert zf.read('nodeforge_package.json') == before
        assert 'systems/blender/interface.py' in zf.namelist()
        assert not any('/unified/' in path for path in zf.namelist())


def test_stock_bundle_installs_without_special_package_logic(tmp_path):
    """A bundled ZIP is handled by the existing versioned package installer."""
    packages.set_packages_dir_for_tests(tmp_path / 'inventory')
    try:
        assert packages.install_included_packages(ROOT) == ()
        manifest, = [record.manifest for record in packages.active_package_records()
                     if record.manifest.package_id == 'nodeforge.nodes']
        assert manifest.import_name == 'nodes'
        assert manifest.root != PACKAGE
        assert [record.system_id for record in packages.system_package_records()] == ['blender']
        state = packages.load_package_state()
        assert state['packages']['nodeforge.nodes']['installed_version'] == '1.4.0'
        assert packages.install_included_packages(ROOT) == ()
    finally:
        packages.set_packages_dir_for_tests(None)
        packages.invalidate_caches()


def test_generator_can_target_an_alternate_package(tmp_path):
    """A custom destination receives fresh bindings without rewriting manifest metadata."""
    target = tmp_path / 'custom_nodes'
    run = subprocess.run([sys.executable, str(BUILD), '--output', str(target), '--no-zip',
                          '--results', str(tmp_path / 'results')],
                         capture_output=True, text=True, check=True)
    assert 'GENERATED 297 functions' in run.stdout
    assert (target / 'systems' / 'blender' / 'interface.py').read_bytes() == (
        PACKAGE / 'systems' / 'blender' / 'interface.py').read_bytes()
    assert (target / 'nodeforge_package.json').read_bytes() == (
        PACKAGE / 'nodeforge_package.json').read_bytes()
    coverage = json.loads((tmp_path / 'results' / 'coverage.json').read_text())
    assert coverage['generated'] == 297
    assert coverage['configurations'] == 2671
    assert coverage['excluded_zones']
    assert coverage['skipped']

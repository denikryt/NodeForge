"""Regression coverage for offline release Packages in the existing inventory."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from NodeForge import packages


@pytest.fixture(autouse=True)
def inventory(tmp_path):
    """Isolate user state from every test."""
    packages.set_packages_dir_for_tests(tmp_path / "data")
    yield
    packages.set_packages_dir_for_tests(None)


def archive(directory: Path, package_id: str, version: str, *, maximum="0.70.0", minimum="0.50.0") -> Path:
    """Create a valid minimal ZIP with a distinct source identifier."""
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{package_id}-{version}.zip"
    manifest = {
        "schema_version": 1, "id": package_id, "name": package_id,
        "version": version, "contents": {"functions": "functions"},
        "nodeforge_min_version": minimum, "nodeforge_max_version": maximum,
        "permissions": {"python": False},  # Obsolete metadata must be ignored.
    }
    with zipfile.ZipFile(output, "w") as z:
        z.writestr(f"{package_id}/nodeforge_package.json", json.dumps(manifest))
        z.writestr(f"{package_id}/functions/entry.nf", "output(1.0)\n")
    return output


def release(tmp_path, *archives):
    """Prepare an extension directory with ordinary package installer ZIPs."""
    directory = tmp_path / "extension" / "package_install"
    directory.mkdir(parents=True, exist_ok=True)
    for path in archives:
        shutil.copy2(path, directory / path.name)
    return directory.parent


def test_fresh_install_is_idempotent_without_python_consent(tmp_path):
    """Startup and re-enable use the same package inventory, no second model."""
    a = archive(tmp_path / "a", "vendor.sample", "1.0.0")
    root = release(tmp_path, a)
    assert packages.install_included_packages(root) == ()
    first = packages.load_package_state()
    current = packages.active_package_records()[0]
    assert current.manifest.package_id == "vendor.sample"
    assert current.manifest.version == "1.0.0"
    assert "allow_python" not in current.state_record
    assert packages.package_compatibility_warning(current.manifest)
    assert packages.install_included_packages(root) == ()
    assert packages.load_package_state() == first


def test_version_upgrade_and_no_downgrade(tmp_path):
    """Embedded packages cannot replace an equal or newer independent install."""
    one = archive(tmp_path / "a", "vendor.sample", "1.0.0")
    two = archive(tmp_path / "b", "vendor.sample", "2.0.0")
    current_root = release(tmp_path, one)
    assert not packages.install_included_packages(current_root)
    packages.install_package_zip(two, replace=True)
    old = packages.load_package_state()
    assert packages.install_included_packages(current_root) == ()
    assert packages.load_package_state() == old
    # Upgrading from a lower version through the embedded archive works.
    packages.uninstall_package("vendor.sample")
    packages.install_package_zip(one)
    updated = release(tmp_path, two)
    # Both ZIPs coexist in the staging test fixture: make the release unambiguous.
    for f in (updated / "package_install").glob('*.zip'):
        if f.name != two.name:
            f.unlink()
    assert packages.install_included_packages(updated) == ()
    assert packages.active_package_records()[0].manifest.version == '2.0.0'


def test_malformed_inventory_never_overwritten(tmp_path):
    """Bootstrap and ordinary installs preserve the bytes of corrupt top-level state."""
    candidate = archive(tmp_path / "a", "vendor.sample", "1.0.0")
    root = release(tmp_path, candidate)
    state = packages.state_path()
    for payload in ({"schema_version": 1}, {"schema_version": 1, "packages": []},
                    {"schema_version": 1, "packages": None}):
        state.write_text(json.dumps(payload), encoding="utf-8")
        before = state.read_bytes()
        with pytest.raises(packages.PackageError, match="packages must be an object"):
            packages.install_included_packages(root)
        with pytest.raises(packages.PackageError, match="packages must be an object"):
            packages.install_package_zip(candidate)
        assert state.read_bytes() == before
        assert not list(packages.installed_dir().glob('**/install_*'))


def test_individual_corrupt_package_does_not_get_replaced(tmp_path):
    """A malformed installed record is not interpreted as an absent package."""
    candidate = archive(tmp_path / 'a', 'vendor.sample', '2.0.0')
    root = release(tmp_path, candidate)
    packages.state_path().write_text(json.dumps({"schema_version": 1, "packages": {
        "vendor.sample": {"installed_path": "../../escape", "installed_version": "1.0.0"}
    }}), encoding='utf8')
    errors = packages.install_included_packages(root)
    assert errors and 'invalid' in errors[0]
    assert packages.load_package_state()['packages']['vendor.sample']['installed_version'] == '1.0.0'


def test_duplicate_ids_do_not_change_state(tmp_path):
    """Conflicting release inputs are rejected before any installed record changes."""
    a = archive(tmp_path / 'a', 'vendor.sample', '1.0.0')
    b = archive(tmp_path / 'b', 'vendor.sample', '2.0.0')
    root = release(tmp_path, a, b)
    assert 'Duplicate included package ID' in packages.install_included_packages(root)[0]
    assert packages.load_package_state()['packages'] == {}
    # A return inside try must still execute the staging cleanup in finally.
    assert not list((packages.packages_dir() / '.staging').glob('*'))
    assert not list(packages.installed_dir().glob('**/install_*'))


def test_invalid_max_is_advisory_but_min_is_strict(tmp_path):
    """An exceeded maximum warns, while an unmet minimum rejects installation."""
    older = archive(tmp_path / 'a', 'vendor.sample', '1.0.0', maximum='0.50.0')
    assert packages.install_included_packages(release(tmp_path, older)) == ()
    assert 'Compatibility is not guaranteed' in packages.package_compatibility_warning(
        packages.active_package_records()[0].manifest)
    newer = archive(tmp_path / 'b', 'vendor.newer', '1.0.0', minimum='99.0.0')
    errors = packages.install_included_packages(release(tmp_path, newer))
    assert errors and 'requires NodeForge' in errors[0]
    assert all(r.manifest.package_id != 'vendor.newer' for r in packages.active_package_records())


def test_post_publish_fsync_error_retains_installed_directory(tmp_path, monkeypatch):
    """After the state publication point, a sync failure cannot roll back the payload."""
    candidate = archive(tmp_path / 'a', 'vendor.sample', '1.0.0')
    def fail_sync(_path):
        """Simulate a failed directory durability operation after os.replace."""
        raise OSError('injected fsync failure')
    monkeypatch.setattr(packages, '_fsync_directory', fail_sync)
    packages.install_package_zip(candidate)
    assert packages.active_package_records()[0].manifest.version == '1.0.0'
    record = packages.load_package_state()['packages']['vendor.sample']
    assert (packages.packages_dir() / record['installed_path']).is_dir()


def test_copy_failure_removes_partial_directory(tmp_path, monkeypatch):
    """A failed directory copy leaves no orphan, and the old state is unchanged."""
    candidate = archive(tmp_path / 'a', 'vendor.sample', '1.0.0')
    with zipfile.ZipFile(candidate) as z:
        z.extractall(tmp_path / 'extracted')
    root = tmp_path / 'extracted' / 'vendor.sample'
    native_copy = packages.shutil.copytree
    def fail_copy(src, dst, **kwargs):
        """Leave partial output and raise at the copy boundary."""
        Path(dst).mkdir(parents=True, exist_ok=True)
        (Path(dst) / 'partial.py').write_text('broken')
        raise OSError('injected copy failure')
    monkeypatch.setattr(packages.shutil, 'copytree', fail_copy)
    with pytest.raises(OSError, match='copy failure'):
        packages.install_package_directory(root)
    assert packages.load_package_state()['packages'] == {}
    assert not list(packages.installed_dir().glob('**/install_*'))
    monkeypatch.setattr(packages.shutil, 'copytree', native_copy)


def test_legacy_fields_ignored_and_dropped_on_write(tmp_path):
    """Old package grants never change package activity or survive normal writes."""
    a = archive(tmp_path / 'a', 'vendor.sample', '1.0.0')
    packages.install_package_zip(a)
    state = packages.load_package_state()
    state['packages']['vendor.sample']['allow_python'] = False
    state['seed_sources'] = ['old']
    packages.state_path().write_text(json.dumps(state))
    assert packages.active_package_records()[0].manifest.package_id == 'vendor.sample'
    packages.save_package_state(packages.load_package_state())
    assert 'allow_python' not in packages.state_path().read_text()
    assert 'seed_sources' not in packages.state_path().read_text()


@pytest.mark.skipif(not hasattr(packages.os, "O_DIRECTORY"), reason="No O_DIRECTORY support")
@pytest.mark.parametrize("fails", [False, True])
def test_directory_fsync_always_closes_descriptor_and_reports_sync_error(tmp_path, monkeypatch, fails):
    """Directory sync reports actual fsync failures to the state writer and closes its fd."""
    seen = []
    fake_fd = 123456

    def open_dir(path, flags):
        """Represent successful directory opening without using a real descriptor."""
        assert Path(path) == tmp_path
        assert flags & packages.os.O_DIRECTORY
        seen.append("open")
        return fake_fd

    def sync_dir(fd):
        """Record the sync call and optionally inject a durability error."""
        assert fd == fake_fd
        seen.append("fsync")
        if fails:
            raise OSError("injected directory fsync failure")

    def close_dir(fd):
        """Record that the directory descriptor is released in both paths."""
        assert fd == fake_fd
        seen.append("close")

    monkeypatch.setattr(packages.os, "open", open_dir)
    monkeypatch.setattr(packages.os, "fsync", sync_dir)
    monkeypatch.setattr(packages.os, "close", close_dir)
    if fails:
        with pytest.raises(OSError, match="injected directory fsync failure"):
            packages._fsync_directory(tmp_path)
    else:
        packages._fsync_directory(tmp_path)
    assert seen == ["open", "fsync", "close"]

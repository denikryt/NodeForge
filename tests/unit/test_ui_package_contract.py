"""Static coverage for package-management UI contract names."""

from pathlib import Path

UI_SOURCE = Path(__file__).resolve().parents[2] / "ui.py"


def _ui_source() -> str:
    return UI_SOURCE.read_text(encoding="utf-8")


def test_package_item_exposes_short_name_and_diagnostic_fields():
    source = _ui_source()

    assert "class NODEFORGE_PackageItem" in source
    for field in (
        "id: StringProperty",
        "name: StringProperty",
        "version: StringProperty",
        "origin: StringProperty",
        "status: StringProperty",
        "path: StringProperty",
        "compatibility_warning: StringProperty",
        "invalid_reason: StringProperty",
    ):
        assert field in source


def test_package_refresh_populates_invalid_reason_and_advisory_warning():
    source = _ui_source()
    assert 'item.invalid_reason = record.message' in source
    assert 'item.compatibility_warning = packages.package_compatibility_warning(manifest)' in source
    assert 'item.status = "installed"' in source
    assert 'Allow executable Python' not in source



def test_ui_has_no_special_preinstalled_package_controls():
    source = _ui_source()

    assert "NODEFORGE_OT_install_package_source" not in source
    assert "NODEFORGE_OT_reinstall_shipped_package" not in source
    assert "Install Math Source" not in source
    assert "ensure_seeded_packages" not in source

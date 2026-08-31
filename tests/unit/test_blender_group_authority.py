"""Pure tests for shared Blender group authority eligibility state."""

import importlib
import sys
from types import ModuleType, SimpleNamespace


class FakeGroup(dict):
    """Minimal RNA-like node group with stable process-local pointer identity."""

    _next_pointer = 100

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.bl_idname = "GeometryNodeTree"
        self._pointer = FakeGroup._next_pointer
        FakeGroup._next_pointer += 1

    def as_pointer(self):
        """Return the fake stable RNA address."""
        return self._pointer


class FakeNodeGroups(list):
    """List-like node-group collection used by the authority module."""


def _load_authority(monkeypatch, groups, driver_namespace):
    """Import the authority module against a controlled fake bpy process state."""
    fake_bpy = ModuleType("bpy")
    fake_bpy.data = SimpleNamespace(node_groups=groups)
    fake_bpy.app = SimpleNamespace(driver_namespace=driver_namespace)
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    sys.modules.pop("NodeForge.blender_group_authority", None)
    return importlib.import_module("NodeForge.blender_group_authority")


def test_transaction_private_marker_is_persistent_authority_filter(monkeypatch):
    """Persistent private marker rejects a temporary group independently of transaction state."""
    groups = FakeNodeGroups()
    authority = _load_authority(monkeypatch, groups, {})
    group = FakeGroup("rollback")
    groups.append(group)

    authority.mark_transaction_private(group)

    assert authority.is_authority_ineligible_group(group) is True
    assert group[authority.TRANSACTION_PRIVATE_PROP] is True


def test_provisional_registry_survives_module_reload_and_publishes_infallibly(monkeypatch):
    """Fresh provisional state survives module reload via Blender-owned process storage."""
    groups = FakeNodeGroups()
    driver_namespace = {}
    authority = _load_authority(monkeypatch, groups, driver_namespace)
    group = FakeGroup("fresh")
    groups.append(group)
    authority.mark_provisional(group)

    assert authority.is_authority_ineligible_group(group) is True
    assert authority.PROVISIONAL_REGISTRY_KEY in driver_namespace

    sys.modules.pop("NodeForge.blender_group_authority", None)
    authority = importlib.import_module("NodeForge.blender_group_authority")
    assert authority.is_provisional(group) is True

    authority.publish([group])
    assert authority.is_authority_ineligible_group(group) is False


def test_removed_group_lazily_drops_stale_provisional_entry(monkeypatch):
    """Registry cleanup never lets a removed object's stale pointer claim another group."""
    groups = FakeNodeGroups()
    authority = _load_authority(monkeypatch, groups, {})
    old = FakeGroup("old")
    groups.append(old)
    authority.mark_provisional(old)
    groups.remove(old)

    replacement = FakeGroup("replacement")
    replacement._pointer = old._pointer
    groups.append(replacement)

    assert authority.is_provisional(replacement) is False
    registry = authority._registry()
    assert old._pointer not in registry

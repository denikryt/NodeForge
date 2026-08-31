"""Pure contract tests for the Blender group transaction/backend boundary."""

import importlib
import sys
from types import ModuleType, SimpleNamespace
import warnings


class FakeGroup(dict):
    """Minimal group object for transaction/copy-policy tests."""

    def __init__(self, name="Group"):
        super().__init__()
        self.name = name
        self.bl_idname = "GeometryNodeTree"
        self._pointer = id(self)

    def as_pointer(self):
        """Return stable fake physical identity."""
        return self._pointer


class FakeResourceTransaction:
    """Record infallible authoritative ownership flips."""

    def __init__(self, label, calls):
        self.label = label
        self.calls = calls
        self.committed = False

    def mark_committed(self):
        """Record the authoritative bookkeeping transition."""
        self.calls.append(("commit", self.label))
        self.committed = True

    def rollback(self):
        """Record rollback for completeness."""
        self.calls.append(("rollback", self.label))


def _load_backend(monkeypatch):
    """Import backend with the minimal bpy process objects it needs at import time."""
    fake_bpy = ModuleType("bpy")
    fake_bpy.app = SimpleNamespace(driver_namespace={})
    fake_bpy.data = SimpleNamespace(node_groups=[])
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    for name in (
        "NodeForge.blender_group_backend",
        "NodeForge.blender_group_authority",
        "NodeForge.generated_resources",
        "NodeForge.storage",
        "NodeForge.update",
    ):
        sys.modules.pop(name, None)
    return importlib.import_module("NodeForge.blender_group_backend")


def test_backend_private_properties_are_destination_sticky_and_source_nonpropagating(monkeypatch):
    """Temporary authority state survives backup copy but never reaches authority."""
    backend = _load_backend(monkeypatch)
    private = next(iter(backend.BACKEND_PRIVATE_GROUP_PROPERTIES))
    authoritative = FakeGroup("authoritative")
    authoritative["owner"] = "canonical"
    backup = FakeGroup("backup")
    backup[private] = True
    backup["old"] = "discard"

    backend._copy_custom_properties(authoritative, backup, strict=True)
    assert backup[private] is True
    assert backup["owner"] == "canonical"
    assert "old" not in backup

    restored = FakeGroup("restored")
    restored["existing"] = "old"
    backend._copy_custom_properties(backup, restored, strict=True)
    assert private not in restored
    assert restored["owner"] == "canonical"


def test_commit_marks_all_resources_before_nonraising_retirement(monkeypatch):
    """PONR commits every resource before best-effort cleanup can fail."""
    backend = _load_backend(monkeypatch)
    calls = []
    tx_a = FakeResourceTransaction("a", calls)
    tx_b = FakeResourceTransaction("b", calls)
    transaction = backend.BlenderGroupBuildTransaction()
    transaction.created_groups.extend([(FakeGroup("A"), tx_a), (FakeGroup("B"), tx_b)])
    transaction._mutation_journal.append({
        "group": FakeGroup("existing"),
        "backup": FakeGroup("backup"),
        "external_state": None,
        "resource_transaction": None,
        "old_manifest": {"owner_group_uuid": "owner"},
        "new_manifest": {"owner_group_uuid": "owner"},
        "original_name": "existing",
    })

    monkeypatch.setattr(backend, "publish_provisional", lambda groups: calls.append(("publish", tuple(g.name for g in groups))))
    monkeypatch.setattr(backend, "_remove_node_group_if_live", lambda group, failures=None: True)

    def fail_cleanup(old_manifest, new_manifest):
        calls.append(("cleanup", "old"))
        raise RuntimeError("retirement failed")

    monkeypatch.setattr(backend.generated_resources, "cleanup_previous_after_commit", fail_cleanup)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        transaction.commit()

    assert calls[:3] == [("commit", "a"), ("commit", "b"), ("publish", ("A", "B"))]
    assert calls[3] == ("cleanup", "old")
    assert tx_a.committed and tx_b.committed
    assert transaction._closed is True
    assert len(transaction.retirement_failures) == 1
    assert caught and "committed with cleanup warning" in str(caught[0].message)


def test_restore_mutation_restores_original_physical_name(monkeypatch):
    """Rollback journal restores the existing datablock name as transaction state."""
    backend = _load_backend(monkeypatch)
    transaction = backend.BlenderGroupBuildTransaction()
    group = FakeGroup("renamed")
    backup = FakeGroup("backup")
    event = {
        "group": group,
        "backup": backup,
        "external_state": object(),
        "resource_transaction": None,
        "old_manifest": None,
        "new_manifest": None,
        "original_name": "original",
    }
    monkeypatch.setattr(backend, "_node_group_is_live", lambda value: value is group)
    monkeypatch.setattr(backend, "_copy_group_contents", lambda src, dst: None)
    monkeypatch.setattr(backend, "_restore_group_external_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(backend, "_remove_node_group_if_live", lambda *args, **kwargs: True)

    failures = []
    transaction._restore_mutation(event, failures)

    assert failures == []
    assert group.name == "original"

"""Durable explicit-input identity and conservative update migration contracts."""

import ast
import sys
from types import SimpleNamespace

import pytest

from NodeForge.semantic.compile_time import CompileTimeSnapshot

sys.modules.setdefault("bpy", SimpleNamespace())

from NodeForge.compiler_identities import InputDeclarationId
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge import interface, update
from NodeForge.semantic.builtin_calls import (
    INPUT_DECLARATION_BUILTIN_NAMES,
    IR_CAPABLE_BUILTIN_NAMES,
    analyze_input_declaration_call,
)
from NodeForge.semantic.call_resolution import CallableEnvironment
from NodeForge.semantic.body import lower_basic_body
from NodeForge.semantic.ir import IRIf, IRInputDeclaration

pytestmark = pytest.mark.unit


class _Group(dict):
    """Minimal IDProperty-like group with an ordered interface tree."""


class _Item:
    """Interface stand-in that can be value-equal without sharing physical identity."""

    item_type = "SOCKET"
    in_out = "INPUT"

    def __init__(self, name="Scale", socket_type="NodeSocketFloat"):
        self.name = name
        self.socket_type = socket_type

    def __eq__(self, other):
        return isinstance(other, _Item) and (self.name, self.socket_type) == (other.name, other.socket_type)


def _group_with_items(*items):
    group = _Group()
    group.interface = SimpleNamespace(items_tree=list(items))
    return group


def _record(group, item, declaration_id, typ=NFType.FLOAT, default=None):
    interface._record_group_input_declaration(
        group,
        declaration_id,
        item.name,
        typ,
        default,
        interface_item=item,
    )


def _state_for(reference):
    return {"group_nodes": [{"input_overrides": [{"reference": reference, "value": 3.0}]}], "links": []}


def test_interface_item_fallback_never_uses_value_equality():
    """Distinct value-equal stand-ins remain distinct physical interface items."""
    first = _Item()
    second = _Item()
    assert first == second
    assert not interface._same_interface_item(first, second)
    group = _group_with_items(first, second)
    assert interface._interface_socket_key(group, first, NFType.FLOAT)[2] == 0
    assert interface._interface_socket_key(group, second, NFType.FLOAT)[2] == 1


def test_versioned_declaration_metadata_is_deterministic_and_rejects_duplicate_ids():
    """Persisted declaration records contain both semantic identity and physical locator."""
    first = _Item()
    second = _Item()
    group = _group_with_items(first, second)
    first_id = InputDeclarationId("owner", "first", 0)
    second_id = InputDeclarationId("owner", "second", 0)
    _record(group, first, first_id, default=1.0)
    _record(group, second, second_id, default=7.0)

    records = interface._get_group_input_declarations(group)
    assert list(sorted(records)) == sorted([first_id.stable_key(), second_id.stable_key()])
    assert records[first_id.stable_key()]["occurrence"] == 0
    assert records[second_id.stable_key()]["occurrence"] == 1
    assert records[second_id.stable_key()]["default"] == 7.0
    assert group[interface.INPUT_DECLARATIONS_PROP]["schema_version"] == 1

    with pytest.raises(CompileError, match="Duplicate NodeForge InputDeclarationId"):
        _record(group, second, first_id, default=9.0)




def test_declaration_metadata_write_failure_is_not_best_effort():
    """Mandatory declaration identity metadata must fail compilation when IDProperty write fails."""
    class FailingGroup(_Group):
        def __setitem__(self, key, value):
            if key == interface.INPUT_DECLARATIONS_PROP:
                raise RuntimeError("injected metadata write failure")
            return super().__setitem__(key, value)

    group = FailingGroup()
    group.interface = SimpleNamespace(items_tree=[])
    with pytest.raises(CompileError, match="Failed to persist NodeForge input declaration metadata") as exc_info:
        interface._write_group_input_declarations(group, [])
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_malformed_or_unknown_declaration_metadata_is_rejected():
    """New declaration metadata fails closed instead of silently degrading to occurrence matching."""
    group = _group_with_items()
    group[interface.INPUT_DECLARATIONS_PROP] = {"schema_version": 99, "records": {}}
    with pytest.raises(CompileError, match="Unsupported NodeForge input declaration metadata schema"):
        interface._get_group_input_declarations(group)
    group[interface.INPUT_DECLARATIONS_PROP] = {"schema_version": 1, "records": {"0": {}}}
    with pytest.raises(CompileError, match="Malformed NodeForge input declaration identity metadata"):
        interface._get_group_input_declarations(group)


def test_replacement_resolution_uses_declaration_id_not_duplicate_label_occurrence():
    """An inserted duplicate label does not retarget live state from an existing declaration."""
    inserted = _Item()
    first = _Item()
    second = _Item()
    replacement = _group_with_items(inserted, first, second)
    _record(replacement, inserted, InputDeclarationId("owner", "inserted", 0))
    _record(replacement, first, InputDeclarationId("owner", "first", 0))
    second_id = InputDeclarationId("owner", "second", 0)
    _record(replacement, second, second_id)

    reference = {
        "old_socket_key": ("INPUT", "Scale", "NodeSocketFloat", 1),
        "new_socket_key": None,
        "declaration_key": second_id.stable_key(),
        "display_name": "Scale",
        "socket_type": "NodeSocketFloat",
    }
    update._validate_group_external_state_for_replacement(replacement, _state_for(reference))
    assert reference["new_socket_key"] == ("INPUT", "Scale", "NodeSocketFloat", 2)


def test_same_declaration_id_with_incompatible_type_drops_state_by_existing_update_contract():
    """Logical identity never bypasses physical compatibility or reuses the state positionally."""
    item = _Item(socket_type="NodeSocketVector")
    replacement = _group_with_items(item)
    declaration_id = InputDeclarationId("owner", "x", 0)
    _record(replacement, item, declaration_id, typ=NFType.VECTOR)
    reference = {
        "old_socket_key": ("INPUT", "Scale", "NodeSocketFloat", 0),
        "new_socket_key": None,
        "declaration_key": declaration_id.stable_key(),
        "display_name": "Scale",
        "socket_type": "NodeSocketFloat",
    }
    update._validate_group_external_state_for_replacement(replacement, _state_for(reference))
    assert reference["new_socket_key"] is None


def test_legacy_live_state_migrates_only_when_replacement_correspondence_is_unambiguous():
    """Occurrence is never guessed across versions for legacy live state."""
    only = _Item()
    replacement = _group_with_items(only)
    _record(replacement, only, InputDeclarationId("owner", "x", 0))
    reference = {
        "old_socket_key": ("INPUT", "Scale", "NodeSocketFloat", 0),
        "new_socket_key": None,
        "declaration_key": None,
        "display_name": "Scale",
        "socket_type": "NodeSocketFloat",
    }
    update._validate_group_external_state_for_replacement(replacement, _state_for(reference))
    assert reference["new_socket_key"] == ("INPUT", "Scale", "NodeSocketFloat", 0)

    first = _Item()
    second = _Item()
    ambiguous = _group_with_items(first, second)
    _record(ambiguous, first, InputDeclarationId("owner", "first", 0))
    _record(ambiguous, second, InputDeclarationId("owner", "second", 0))
    reference["new_socket_key"] = None
    with pytest.raises(CompileError, match="correspondence is ambiguous"):
        update._validate_group_external_state_for_replacement(ambiguous, _state_for(reference))



def test_input_frontend_owns_existing_public_defaults_and_interface_helper_requires_explicit_default():
    """Input omission is normalized before the physical interface helper is called."""
    cases = {
        'input_float("X")': 0.0,
        'input_int("X")': 0,
        'input_bool("X")': False,
        'input_vector("X")': (0.0, 0.0, 0.0),
        'input_string("X")': "",
        'input_geometry("X")': None,
        'input_material("X")': None,
        'input_object("X")': None,
        'input_bundle("X")': None,
    }
    for source, expected in cases.items():
        expr = ast.parse(source, mode="eval").body
        assert analyze_input_declaration_call(expr, {}).default == expected

    with pytest.raises(TypeError, match="default"):
        interface._create_group_input_socket(object(), object(), "X", NFType.FLOAT)


def test_panel_interface_helper_requires_explicit_collapsed_state():
    """Physical panel creation cannot reconstruct the source default for collapsed=."""
    with pytest.raises(TypeError, match="collapsed"):
        interface._create_interface_panel(object(), (), "P")

def _control_flow_declaration_keys(source):
    """Return deterministic declaration stable keys from one pure root-body analysis."""
    callables = CallableEnvironment(
        callable_builtins=frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES),
        local_functions={},
        imported_functions={},
    )
    result = lower_basic_body(
        ast.parse(source, mode="exec").body,
        initial_runtime_bindings={},
        initial_compile_time=CompileTimeSnapshot({}),
        reserved_name_labels={},
        callable_environment=callables,
        owner_scope="owner",
        declaration_owner="owner",
    )
    branch = next(statement for statement in result.body.statements if isinstance(statement, IRIf))
    declarations = [
        statement
        for body in (branch.true_body, branch.false_body)
        for statement in body.statements
        if isinstance(statement, IRInputDeclaration)
    ]
    return [statement.declaration_id.stable_key() for statement in declarations]


def test_control_flow_input_declaration_ordinals_are_monotonic_and_rebuild_stable():
    """Opposite branches share one non-rewinding declaration allocator per root analysis."""
    source = (
        'flag = input_bool("Flag")\n'
        'if flag:\n    x = input_float("A")\nelse:\n    x = input_float("B")\n'
        'output(x)'
    )
    first = _control_flow_declaration_keys(source)
    second = _control_flow_declaration_keys(source)
    assert first == second
    assert first == [
        InputDeclarationId("owner", "x", 0).stable_key(),
        InputDeclarationId("owner", "x", 1).stable_key(),
    ]


def test_literal_condition_input_declaration_ordinals_visit_both_runtime_branches():
    """Literal conditions allocate declarations exactly like dynamic runtime branches."""
    source = (
        'if True:\n    x = input_float("A")\nelse:\n    x = input_float("B")\n'
        'output(x)'
    )
    assert _control_flow_declaration_keys(source) == [
        InputDeclarationId("owner", "x", 0).stable_key(),
        InputDeclarationId("owner", "x", 1).stable_key(),
    ]

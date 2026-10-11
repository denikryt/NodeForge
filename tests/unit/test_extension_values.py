"""Package semantic record grammar and detached value storage tests."""

from __future__ import annotations

from pathlib import Path
import ast

import pytest

from NodeForge.compiler_identities import BindingId
from NodeForge.errors import CompileError
from NodeForge.extensions.contracts import PythonScalarKind, TypeSpec
from NodeForge.extensions.registry import ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot
from NodeForge.extension_semantic_api import RuntimeRef
from NodeForge.extensions.semantics import (
    ExtensionDependencySource,
    ExtensionSemanticPayload,
    compact_extension_semantic_payload,
)
from NodeForge.extensions.values import (
    ExtensionDependencySlot,
    ExtensionValue,
    pack_value,
    select_declared_nf_type,
    static_nf_type,
    type_spec_accepts_nf,
    unpack_value,
)
from NodeForge.nf_types import NFType

pytestmark = pytest.mark.unit


INTERFACE = '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float, Int, Vector
EXTENSION_API = 2

@dataclass(frozen=True)
class Part:
    value: Float | Int

@dataclass(frozen=True)
class Angle(Part):
    label: str

@dataclass(frozen=True)
class Container:
    part: Part
    values: list[Float]
    fixed: tuple[int, float]
    vectors: tuple[Vector, ...]
    by_name: dict[str, Part]
    maybe: Part | None

@dataclass(frozen=True)
class _Private:
    value: Int

EXTENSIONS = {"make": None}
def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
'''


def _fixture(tmp_path: Path):
    (tmp_path / "interface.py").write_text(INTERFACE, encoding="utf-8")
    session = ExtensionOwnerSession(capture_owner_code_snapshot(("system", "vendor.values", "records"), tmp_path))
    registry = ExtensionRegistry((session,))
    session.normalize_interface()
    specs = {type_id.name: spec for type_id, spec in session.type_specs().items()}
    classes = {name: registry.python_class_for(spec.id) for name, spec in specs.items()}
    return session, registry, specs, classes


def test_record_grammar_and_nominal_inheritance_are_canonical(tmp_path):
    """Record fields normalize once into the shared recursive TypeSpec vocabulary."""
    _session, registry, specs, _classes = _fixture(tmp_path)
    part = specs["Part"]
    angle = specs["Angle"]
    container = specs["Container"]
    assert dict(part.fields)["value"].kind == "NF_SET"
    assert dict(part.fields)["value"].nf_types == frozenset({NFType.FLOAT, NFType.INT})
    assert angle.bases == (part.id,)
    assert registry.is_nominal_subtype(angle.id, part.id)
    fields = dict(container.fields)
    assert fields["part"].kind == "RECORD"
    assert fields["values"].kind == "LIST" and fields["values"].item.kind == "NF_SET"
    assert fields["fixed"].kind == "TUPLE_FIXED"
    assert [item.python_scalar_kind for item in fields["fixed"].items] == [PythonScalarKind.INT, PythonScalarKind.FLOAT]
    assert fields["vectors"].kind == "TUPLE_VAR"
    assert fields["by_name"].kind == "DICT_STR"
    assert fields["maybe"].kind == "OPTIONAL"
    assert specs["_Private"].is_public is False


def test_pack_runtime_ref_preserves_actual_type_and_unpack_uses_exact_session_class(tmp_path):
    """Runtime dependency storage preserves actual NFType while nominal reconstruction keeps class identity."""
    _session, registry, specs, classes = _fixture(tmp_path)
    token = object()
    ref = RuntimeRef(token, NFType.INT)
    value = classes["Angle"](ref, "a")
    packed = pack_value(
        type_spec_for(specs["Part"]),
        value,
        registry=registry,
        active_runtime_refs={token: (0, NFType.INT)},
    )
    assert isinstance(packed, ExtensionValue)
    assert packed.type_id == specs["Angle"].id
    assert packed.storage[0] == ExtensionDependencySlot(0, NFType.INT)
    reconstructed = unpack_value(
        type_spec_for(specs["Part"]),
        packed,
        registry=registry,
        runtime_leaf_resolver=lambda slot: (slot.typ, (slot.dependency_index, slot.typ)) if isinstance(slot, ExtensionDependencySlot) else None,
    )
    assert type(reconstructed) is classes["Angle"]
    assert reconstructed.value == (0, NFType.INT)
    assert reconstructed.label == "a"


def type_spec_for(record_spec):
    """Return a RECORD TypeSpec for one normalized record schema."""
    return TypeSpec("RECORD", record_type=record_spec.id)


def test_nf_set_helpers_are_the_single_static_type_and_compatibility_authority():
    """Shared NF_SET helpers own detached inference, exact matching, and source compatibility."""
    float_spec = TypeSpec("NF_SET", frozenset({NFType.FLOAT}))
    int_or_float = TypeSpec("NF_SET", frozenset({NFType.INT, NFType.FLOAT}))

    assert static_nf_type(True) is NFType.BOOL
    assert static_nf_type(2) is NFType.INT
    assert static_nf_type(2.0) is NFType.FLOAT
    assert static_nf_type("x") is NFType.STRING
    assert static_nf_type((1.0, 2.0, 3.0)) is NFType.VECTOR

    assert type_spec_accepts_nf(float_spec, NFType.INT)
    assert not type_spec_accepts_nf(float_spec, NFType.INT, exact=True)
    assert select_declared_nf_type(float_spec, NFType.INT) is NFType.FLOAT
    assert type_spec_accepts_nf(int_or_float, NFType.INT, exact=True)
    assert select_declared_nf_type(int_or_float, NFType.INT) is NFType.INT


def test_runtime_ref_float_field_accepts_int_but_int_field_rejects_float(tmp_path):
    """RuntimeRef packing uses canonical NodeForge source compatibility without retagging actual type."""
    _session, registry, specs, classes = _fixture(tmp_path)
    # Part.value accepts Float|Int, proving actual INT survives rather than being retagged.
    token = object()
    packed = pack_value(
        type_spec_for(specs["Part"]),
        classes["Part"](RuntimeRef(token, NFType.INT)),
        registry=registry,
        active_runtime_refs={token: (0, NFType.INT)},
    )
    assert packed.storage[0].typ is NFType.INT

    private_field = dict(specs["_Private"].fields)["value"]
    float_token = object()
    with pytest.raises(CompileError, match="not uniquely accepted"):
        pack_value(
            private_field,
            RuntimeRef(float_token, NFType.FLOAT),
            registry=registry,
            active_runtime_refs={float_token: (0, NFType.FLOAT)},
        )


def test_pack_detaches_mutable_containers_and_preserves_dict_order(tmp_path):
    """Compiler storage is deep-detached and dict entries keep package insertion order."""
    _session, registry, specs, classes = _fixture(tmp_path)
    part_a = classes["Part"](1.0)
    part_b = classes["Part"](2.0)
    values = [1.0, 2.0]
    mapping = {"b": part_b, "a": part_a}
    record = classes["Container"](part_a, values, (1, 2.5), (), mapping, None)
    packed = pack_value(
        type_spec_for(specs["Container"]),
        record,
        registry=registry,
        active_runtime_refs={},
    )
    values.append(9.0)
    mapping["c"] = part_a
    fields = dict(zip((name for name, _ in specs["Container"].fields), packed.storage))
    assert fields["values"] == (1.0, 2.0)
    assert tuple(key for key, _value in fields["by_name"]) == ("b", "a")
    reconstructed = unpack_value(
        type_spec_for(specs["Container"]),
        packed,
        registry=registry,
        runtime_leaf_resolver=lambda _stored: None,
    )
    assert reconstructed.values == [1.0, 2.0]
    assert list(reconstructed.by_name) == ["b", "a"]
    assert reconstructed.maybe is None


def test_persistent_extension_values_compaction_preserves_complete_detached_record_grammar(tmp_path):
    """Persistence normalization is grammar-agnostic across the complete package-defined semantic-value detached storage model."""
    _session, registry, specs, classes = _fixture(tmp_path)
    part_token = object()
    list_token = object()
    vector_token = object()
    refs = {
        part_token: (0, NFType.INT),
        list_token: (1, NFType.FLOAT),
        vector_token: (2, NFType.VECTOR),
    }
    part_ref = RuntimeRef(part_token, NFType.INT)
    record = classes["Container"](
        classes["Part"](part_ref),
        [RuntimeRef(list_token, NFType.FLOAT)],
        (7, 2.5),
        (RuntimeRef(vector_token, NFType.VECTOR),),
        {"left": classes["Part"](part_ref)},
        classes["Part"](part_ref),
    )
    spec = type_spec_for(specs["Container"])
    packed = pack_value(spec, record, registry=registry, active_runtime_refs=refs)
    sources = (
        ExtensionDependencySource(ast.Name(id="i", ctx=ast.Load()), NFType.INT),
        ExtensionDependencySource(ast.Name(id="f", ctx=ast.Load()), NFType.FLOAT),
        ExtensionDependencySource(ast.Name(id="v", ctx=ast.Load()), NFType.VECTOR),
    )
    payload = compact_extension_semantic_payload(spec, packed, sources)
    assert payload.value == packed
    assert payload.dependencies == sources

    persistent = ExtensionSemanticPayload(
        spec,
        payload.value,
        tuple(
            ExtensionDependencySource(BindingId("scope", index), source.typ)
            for index, source in enumerate(payload.dependencies)
        ),
    )
    reconstructed = unpack_value(
        spec,
        persistent.value,
        registry=registry,
        runtime_leaf_resolver=lambda stored: (stored.typ, (stored.dependency_index, stored.typ))
        if isinstance(stored, ExtensionDependencySlot)
        else None,
    )
    assert reconstructed.part.value == (0, NFType.INT)
    assert reconstructed.values == [(1, NFType.FLOAT)]
    assert reconstructed.fixed == (7, 2.5)
    assert reconstructed.vectors == ((2, NFType.VECTOR),)
    assert reconstructed.by_name["left"].value == (0, NFType.INT)
    assert reconstructed.maybe.value == (0, NFType.INT)


def test_persistent_extension_values_payload_compaction_rejects_out_of_range_or_mismatched_nested_slot(tmp_path):
    """Malformed runtime slots fail at canonical payload construction instead of later persistence/lowering."""
    _session, _registry, specs, _classes = _fixture(tmp_path)
    spec = type_spec_for(specs["Part"])
    source = ExtensionDependencySource(ast.Name(id="x", ctx=ast.Load()), NFType.FLOAT)
    out_of_range = ExtensionValue(spec.record_type, (ExtensionDependencySlot(1, NFType.FLOAT),))
    with pytest.raises(CompileError, match="out of range"):
        compact_extension_semantic_payload(spec, out_of_range, (source,))
    mismatched = ExtensionValue(spec.record_type, (ExtensionDependencySlot(0, NFType.INT),))
    with pytest.raises(CompileError, match="actual type does not match"):
        compact_extension_semantic_payload(spec, mismatched, (source,))


def test_stale_or_fabricated_runtime_ref_is_rejected(tmp_path):
    """Only tokens issued for the active semantic invocation can enter detached storage."""
    _session, registry, specs, _classes = _fixture(tmp_path)
    field = dict(specs["Part"].fields)["value"]
    with pytest.raises(CompileError, match="stale, fabricated"):
        pack_value(field, RuntimeRef(object(), NFType.FLOAT), registry=registry, active_runtime_refs={})


def test_list_cycle_is_rejected_deterministically(tmp_path):
    """Identity cycles in package containers never become retained compiler state."""
    _session, registry, specs, _classes = _fixture(tmp_path)
    list_spec = dict(specs["Container"].fields)["values"]
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(CompileError):
        pack_value(list_spec, cyclic, registry=registry, active_runtime_refs={})


def test_custom_init_and_post_init_are_package_construction_only(tmp_path):
    """Package constructors may normalize fresh objects; compiler reconstruction writes stored fields directly."""
    root = tmp_path / "custom-construction"
    root.mkdir()
    root.joinpath("interface.py").write_text(
        '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2

@dataclass(frozen=True)
class PostPart:
    value: Float
    def __post_init__(self):
        object.__setattr__(self, "value", self.value + 1.0)

@dataclass(frozen=True)
class InitPart:
    value: Float
    def __init__(self, value):
        object.__setattr__(self, "value", value + 2.0)

EXTENSIONS={"make": None}
def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> PostPart: ...
''',
        encoding="utf-8",
    )
    session = ExtensionOwnerSession(capture_owner_code_snapshot(("system", "vendor.values", "custom"), root))
    registry = ExtensionRegistry((session,))
    session.normalize_interface()
    specs = {type_id.name: spec for type_id, spec in session.type_specs().items()}

    for name, expected in (("PostPart", 3.0), ("InitPart", 4.0)):
        spec = specs[name]
        cls = registry.python_class_for(spec.id)
        package_value = cls(2.0)
        assert package_value.value == expected
        packed = pack_value(
            type_spec_for(spec),
            package_value,
            registry=registry,
            active_runtime_refs={},
        )
        reconstructed = unpack_value(
            type_spec_for(spec),
            packed,
            registry=registry,
            runtime_leaf_resolver=lambda _stored: None,
        )
        assert type(reconstructed) is cls
        assert reconstructed.value == expected


def test_private_record_cannot_appear_in_public_parameter_or_result(tmp_path):
    """Owner-private record schemas remain implementation state rather than public callable ABI."""
    for suffix, signature in {
        "param": "def foo(value: _Private) -> Float: ...",
        "result": "def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> _Private: ...",
    }.items():
        root = tmp_path / suffix
        root.mkdir()
        root.joinpath("interface.py").write_text(
            f'''\nfrom dataclasses import dataclass\nfrom typing import Annotated\nfrom NodeForge import EvaluationMode, Float\nEXTENSION_API = 2\n@dataclass(frozen=True)\nclass _Private:\n    value: float\nEXTENSIONS = {{"foo": None}}\n{signature}\n''',
            encoding="utf-8",
        )
        session = ExtensionOwnerSession(capture_owner_code_snapshot(("system", "vendor.values", suffix), root))
        with pytest.raises(CompileError, match="private"):
            session.normalize_interface()


def test_same_record_name_in_different_owners_has_distinct_type_identity(tmp_path):
    """Semantic type identity is owner/name, never transient Python class identity alone."""
    ids = []
    for suffix in ("a", "b"):
        root = tmp_path / suffix
        root.mkdir()
        root.joinpath("interface.py").write_text(
            '''\nfrom dataclasses import dataclass\nfrom typing import Annotated\nfrom NodeForge import EvaluationMode, Float\nEXTENSION_API = 2\n@dataclass(frozen=True)\nclass Part:\n    value: Float\nEXTENSIONS={"make": None}\ndef make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...\n''',
            encoding="utf-8",
        )
        session = ExtensionOwnerSession(capture_owner_code_snapshot(("system", "vendor.values", suffix), root))
        session.normalize_interface()
        ids.append(next(type_id for type_id in session.type_specs() if type_id.name == "Part"))
    assert ids[0].name == ids[1].name == "Part"
    assert ids[0] != ids[1]


def test_record_packing_resolves_exact_class_only_in_expected_owner(tmp_path):
    """RECORD packing uses the expected type owner and rejects a foreign-owner class deterministically."""
    sessions = []
    specs_by_owner = {}
    classes_by_owner = {}
    for suffix in ("a", "b"):
        root = tmp_path / f"owner-{suffix}"
        root.mkdir()
        root.joinpath("interface.py").write_text(
            '''\nfrom dataclasses import dataclass\nfrom typing import Annotated\nfrom NodeForge import EvaluationMode, Float\nEXTENSION_API = 2\n@dataclass(frozen=True)\nclass Part:\n    value: Float\n@dataclass(frozen=True)\nclass Detail(Part):\n    label: str\nEXTENSIONS={"make": None}\ndef make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...\n''',
            encoding="utf-8",
        )
        owner = ("system", "vendor.values", f"owner_{suffix}")
        session = ExtensionOwnerSession(capture_owner_code_snapshot(owner, root))
        session.normalize_interface()
        sessions.append(session)
        specs_by_owner[owner] = {type_id.name: spec for type_id, spec in session.type_specs().items()}

    registry = ExtensionRegistry(tuple(sessions))
    for owner, specs in specs_by_owner.items():
        classes_by_owner[owner] = {name: registry.python_class_for(spec.id) for name, spec in specs.items()}

    owner_a, owner_b = specs_by_owner
    expected = type_spec_for(specs_by_owner[owner_a]["Part"])
    same_owner_subtype = classes_by_owner[owner_a]["Detail"](1.0, "ok")
    packed = pack_value(expected, same_owner_subtype, registry=registry, active_runtime_refs={})
    assert packed.type_id == specs_by_owner[owner_a]["Detail"].id

    foreign = classes_by_owner[owner_b]["Part"](1.0)
    with pytest.raises(CompileError, match="registered current-session class"):
        pack_value(expected, foreign, registry=registry, active_runtime_refs={})


def test_same_runtime_ref_reused_in_container_keeps_one_dependency_index(tmp_path):
    """Repeated uses of one active RuntimeRef produce repeated slots for one dependency source."""
    _session, registry, specs, _classes = _fixture(tmp_path)
    list_spec = dict(specs["Container"].fields)["values"]
    token = object()
    ref = RuntimeRef(token, NFType.FLOAT)
    packed = pack_value(
        list_spec,
        [ref, ref],
        registry=registry,
        active_runtime_refs={token: (0, NFType.FLOAT)},
    )
    assert packed == (
        ExtensionDependencySlot(0, NFType.FLOAT),
        ExtensionDependencySlot(0, NFType.FLOAT),
    )

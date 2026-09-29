"""Pure contracts for source-backed callable semantic preparation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from NodeForge.callable_contracts import (
    GroupInputContract,
    GroupOutputContract,
    SourceCallableParameter,
    bind_imported_source_arguments,
    canonicalize_group_input_default,
    normalize_callable_keyword,
    project_group_input_layout,
    source_argument_type_matches,
)
from NodeForge.compiler_identities import (
    BindingId,
    GroupCompilationIdentity,
    InputDeclarationId,
    library_function_id,
)
from NodeForge.consteval import _preprocess_compile_time
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.function_instances import normalized_statements
from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
from NodeForge.semantic_group import analyze_group_source
from NodeForge.semantic_ir import IRPanelDeclaration
from NodeForge.source_callables import SourceCallablePreparationKey, SourceCallableSession


@dataclass(frozen=True)
class _Record:
    """Minimal resolved pure-source catalog record used by semantic tests."""

    namespace: str
    name: str
    source_path: Path
    package_id: str = "vendor.pkg"
    package_version: str = "1.0.0"
    module_path: Path | None = None


def _environment(*, functions=None, examples=None, local=None):
    """Return one complete immutable resolved-environment fixture."""
    return ResolvedEnvironment(
        {
            "functions": ResolvedCatalog("functions", functions or {}),
            "examples": ResolvedCatalog("examples", examples or {}),
            "local": ResolvedCatalog("local", local or {}),
        },
    )


def _identity(owner_scope: str, declaration_owner: str = "decl") -> GroupCompilationIdentity:
    """Return one detached non-root compilation identity."""
    return GroupCompilationIdentity(None, owner_scope, "definition", declaration_owner)


def test_semantic_group_is_blender_independent_and_preserves_lowered_source_payload():
    source = 'x = input_float("X", default=2.0)\ny = x + 1\noutput(y)\n'
    environment = _environment()
    compilation = analyze_group_source(source, compilation_identity=_identity("ROOT/test"), resolved_environment=environment)

    residual = _preprocess_compile_time(__import__("ast").parse(source, mode="exec").body)
    assert compilation.source == source
    assert compilation.normalized_lowered_source == normalized_statements(list(residual.statements))
    assert [item.display_name for item in compilation.interface.inputs] == ["X"]
    assert [item.display_name for item in compilation.interface.outputs] == ["out"]


def test_panel_projection_uses_canonical_origin_not_alias_binding():
    source = 'x = input_float("X")\ny = x\npanel([y], name="Alias")\noutput(x)\n'
    compilation = analyze_group_source(
        source,
        compilation_identity=_identity("ROOT/panel", "panel-declarations"),
        resolved_environment=_environment(),
    )

    assert [item.display_name for item in compilation.interface.inputs] == ["X"]
    panel = next(statement for statement in compilation.body.statements if isinstance(statement, IRPanelDeclaration))
    assert len(panel.member_origins) == 1
    assert isinstance(panel.member_origins[0], InputDeclarationId)


def test_pure_panel_projection_reorders_final_callable_positions():
    loose_origin = BindingId("scope", 0)
    a_origin = InputDeclarationId("decl", "a", 0)
    b_origin = InputDeclarationId("decl", "b", 0)
    c_origin = InputDeclarationId("decl", "c", 0)
    base = tuple(
        GroupInputContract(name.lower(), name, NFType.FLOAT, 0.0, True, origin)
        for name, origin in (("Loose", loose_origin), ("A", a_origin), ("B", b_origin), ("C", c_origin))
    )
    panels = (
        IRPanelDeclaration((b_origin, a_origin), "First", False),
        IRPanelDeclaration((c_origin,), "Second", True),
    )

    projected = project_group_input_layout(base, panels)
    assert [item.display_name for item in projected] == ["Loose", "B", "A", "C"]
    assert projected == (base[0], base[2], base[1], base[3])


def test_callable_keyword_normalization_is_unicode_aware_and_optional():
    """Imported label aliases preserve Unicode letters and may be absent."""
    assert normalize_callable_keyword("Scale XY") == "scalexy"
    assert normalize_callable_keyword("scale_xy") == "scalexy"
    assert normalize_callable_keyword("Привет Мир") == "приветмир"
    assert normalize_callable_keyword("привет_мир") == "приветмир"
    assert normalize_callable_keyword("ESPAÑA") == "españa"
    assert normalize_callable_keyword("España") == "españa"
    assert normalize_callable_keyword("L'échelle") == "léchelle"
    assert normalize_callable_keyword("L’échelle") == "léchelle"
    assert normalize_callable_keyword("Value.01") == "value01"
    assert normalize_callable_keyword("!!!") is None


def test_imported_parameter_without_keyword_alias_remains_positional():
    """A public input without a keyword alias remains callable by final position."""
    parameter = SourceCallableParameter(0, "value", "!!!", None, NFType.FLOAT, True)

    bound = bind_imported_source_arguments((parameter,), ("value",), (), "demo")

    assert bound == ((parameter, "value"),)


def test_imported_unicode_keyword_aliases_and_nonempty_collisions():
    """Unicode keyword aliases bind normally while non-empty collisions stay ambiguous."""
    unicode_parameter = SourceCallableParameter(0, "value", "Привет Мир", "приветмир", NFType.FLOAT, True)
    unicode_bound = bind_imported_source_arguments(
        (unicode_parameter,),
        (),
        (("привет_мир", "unicode-value"),),
        "demo",
    )
    assert unicode_bound == ((unicode_parameter, "unicode-value"),)

    colliding = (
        SourceCallableParameter(0, "a", "Scale XY", "scalexy", NFType.FLOAT, True),
        SourceCallableParameter(1, "b", "Scale-XY", "scalexy", NFType.FLOAT, True),
    )
    positional = bind_imported_source_arguments(colliding, ("left", "right"), (), "demo")
    assert [(parameter.input_index, value) for parameter, value in positional] == [(0, "left"), (1, "right")]
    with pytest.raises(CompileError, match="ambiguous"):
        bind_imported_source_arguments(colliding, (), (("scale_xy", "value"),), "demo")

    no_alias = (
        SourceCallableParameter(0, "a", "!!!", None, NFType.FLOAT, True),
        SourceCallableParameter(1, "b", "???", None, NFType.FLOAT, True),
    )
    positional = bind_imported_source_arguments(no_alias, ("left", "right"), (), "demo")
    assert [(parameter.input_index, value) for parameter, value in positional] == [(0, "left"), (1, "right")]


def test_imported_argument_binding_uses_final_positions_and_detects_ambiguity():
    parameters = (
        SourceCallableParameter(0, "a", "Scale X", "scalex", NFType.FLOAT, True),
        SourceCallableParameter(1, "b", "Scale-X", "scalex", NFType.FLOAT, True),
        SourceCallableParameter(2, "count", "Count", "count", NFType.INT, True),
    )
    bound = bind_imported_source_arguments(parameters, ("first",), (("Count", "count-value"),), "demo")
    assert [(parameter.input_index, value) for parameter, value in bound] == [(0, "first"), (2, "count-value")]

    with pytest.raises(CompileError, match="ambiguous"):
        bind_imported_source_arguments(parameters, (), (("scale_x", "x"),), "demo")
    unique_parameters = (
        SourceCallableParameter(0, "a", "A", "a", NFType.FLOAT, True),
        SourceCallableParameter(1, "count", "Count", "count", NFType.INT, True),
    )
    with pytest.raises(CompileError, match="multiple values"):
        bind_imported_source_arguments(unique_parameters, ("first", "second"), (("Count", "again"),), "demo")


def test_source_argument_type_compatibility_matches_existing_numeric_rule():
    assert source_argument_type_matches(NFType.FLOAT, NFType.INT)
    assert source_argument_type_matches(NFType.FLOAT, NFType.FLOAT)
    assert source_argument_type_matches(NFType.INT, NFType.INT)
    assert not source_argument_type_matches(NFType.INT, NFType.FLOAT)
    assert not source_argument_type_matches(NFType.BOOL, NFType.INT)


def test_canonical_group_defaults_cover_public_scalar_and_vector_types():
    assert canonicalize_group_input_default(NFType.FLOAT, 2) == 2.0
    assert canonicalize_group_input_default(NFType.INT, 2) == 2
    assert canonicalize_group_input_default(NFType.BOOL, True) is True
    assert canonicalize_group_input_default(NFType.VECTOR, (1, 2, 3)) == (1.0, 2.0, 3.0)
    assert canonicalize_group_input_default(NFType.STRING, "x") == "x"


def test_source_session_caches_snapshot_by_function_and_preparation_by_owner(tmp_path):
    source_path = tmp_path / "demo.nf"
    source_path.write_text('x = input_float("X", default=1.0)\noutput(x)\n', encoding="utf-8")
    record = _Record("functions", "demo", source_path)
    function_id = library_function_id("functions", record.package_id, record.name)
    session = SourceCallableSession(resolved_environment=_environment())

    first = session.prepare_library(function_id=function_id, identity=_identity("LIBRARY/one"), record=record)
    same = session.prepare_library(function_id=function_id, identity=_identity("LIBRARY/one"), record=record)
    source_path.write_text('x = input_float("Changed", default=9.0)\noutput(x)\n', encoding="utf-8")
    second_owner = session.prepare_library(function_id=function_id, identity=_identity("LIBRARY/two"), record=record)

    assert first is same
    assert first is not second_owner
    assert first.group.source == second_owner.group.source
    assert '"X"' in second_owner.group.source
    assert '"Changed"' not in second_owner.group.source
    assert set(session.prepared) == {
        SourceCallablePreparationKey(function_id, "LIBRARY/one"),
        SourceCallablePreparationKey(function_id, "LIBRARY/two"),
    }
    assert session.snapshot_view()[function_id] == first.group.source


def test_source_session_cycle_detection_is_function_id_based(tmp_path):
    source_path = tmp_path / "recurse.nf"
    source_path.write_text(
        'from functions import recurse\nx = recurse(1.0, __unique__=True)\noutput(x)\n',
        encoding="utf-8",
    )
    record = _Record("functions", "recurse", source_path)
    environment = _environment(functions={"recurse": record})
    function_id = library_function_id("functions", record.package_id, record.name)
    session = SourceCallableSession(resolved_environment=environment)

    with pytest.raises(CompileError, match="Recursive source function calls are not supported"):
        session.prepare_library(
            function_id=function_id,
            identity=_identity("LIBRARY/root-owner", function_id.stable_key()),
            record=record,
        )
    assert session.prepared == {}


def test_imported_semantic_contract_uses_panel_projected_public_order(tmp_path):
    source_path = tmp_path / "panelled.nf"
    source_path.write_text(
        'loose = input_float("Loose")\na = input_float("A")\nb = input_float("B")\nc = input_float("C")\n'
        'panel([b, a], name="First")\npanel([c], name="Second", collapsed=True)\noutput(loose)\n',
        encoding="utf-8",
    )
    record = _Record("functions", "panelled", source_path)
    function_id = library_function_id("functions", record.package_id, record.name)
    prepared = SourceCallableSession(resolved_environment=_environment()).prepare_library(
        function_id=function_id,
        identity=_identity("LIBRARY/panelled", function_id.stable_key()),
        record=record,
    )

    assert [parameter.display_name for parameter in prepared.contract.parameters] == ["Loose", "B", "A", "C"]
    assert [parameter.input_index for parameter in prepared.contract.parameters] == [0, 1, 2, 3]


def test_imported_semantic_contract_derives_optional_unicode_keyword_aliases(tmp_path):
    """Imported semantic preparation derives optional aliases from public display labels."""
    source_path = tmp_path / "labels.nf"
    source_path.write_text(
        'a = input_float("!!!")\n'
        'b = input_float("Привет Мир")\n'
        'c = input_float("Scale XY")\n'
        'd = input_float("Scale-XY")\n'
        'output(a)\n',
        encoding="utf-8",
    )
    record = _Record("functions", "labels", source_path)
    function_id = library_function_id("functions", record.package_id, record.name)
    prepared = SourceCallableSession(resolved_environment=_environment()).prepare_library(
        function_id=function_id,
        identity=_identity("LIBRARY/labels", function_id.stable_key()),
        record=record,
    )

    assert [(parameter.display_name, parameter.keyword_key) for parameter in prepared.contract.parameters] == [
        ("!!!", None),
        ("Привет Мир", "приветмир"),
        ("Scale XY", "scalexy"),
        ("Scale-XY", "scalexy"),
    ]


def _prepared_group(source: str, identity: GroupCompilationIdentity):
    """Return a minimal immutable semantic artifact for materializer-boundary tests."""
    from NodeForge.callable_contracts import GroupInterfaceContract
    from NodeForge.semantic_group import SemanticGroupCompilation
    from NodeForge.semantic_ir import IRBody

    return SemanticGroupCompilation(
        source=source,
        identity=identity,
        normalized_lowered_source=normalized_statements(__import__("ast").parse(source).body),
        body=IRBody(()),
        interface=GroupInterfaceContract((), ()),
        geometry_mode=False,
    )


def test_materialization_specs_have_one_prepared_source_authority():
    """Prepared semantic artifacts replace duplicate raw-source fields on every permanent spec."""
    from dataclasses import fields
    from NodeForge.function_materializer import (
        LibraryFunctionMaterializationSpec,
        LibraryFunctionUpdateSpec,
        LocalFunctionMaterializationSpec,
    )

    for spec_type in (
        LocalFunctionMaterializationSpec,
        LibraryFunctionMaterializationSpec,
        LibraryFunctionUpdateSpec,
    ):
        names = {field.name for field in fields(spec_type)}
        assert "prepared_compilation" in names
        assert "source" not in names


def test_local_catalog_cache_miss_supplies_real_fingerprint_inputs_and_hit_reuses_group():
    """Local physical reuse is build-local and observes the child's persisted fingerprint."""
    from types import SimpleNamespace
    from NodeForge.function_instances import (
        FUNCTION_COMPILATION_FINGERPRINT_PROP,
        FunctionCompilationTrace,
        function_group_owner_scope,
    )
    from NodeForge.function_materializer import (
        FunctionMaterializationContext,
        FunctionMaterializer,
        LibraryFunctionMaterializationSpec,
    )

    class FakeGroup(dict):
        """Dictionary-like node-group stand-in carrying the metadata API used by the materializer."""

        def __init__(self):
            super().__init__()
            self.name = "Local Demo"
            self.interface = SimpleNamespace(items_tree=[])

    class FakeBackend:
        """Prepared-only backend double that persists a deterministic child fingerprint."""

        def __init__(self):
            self.calls = []

        def create_or_update(self, request, *, finalize_before_commit=None):
            self.calls.append(request)
            inputs = request.function_compilation_inputs
            assert inputs["kind"] == "library"
            assert inputs["namespace"] == "local"
            assert inputs["source"]
            group = FakeGroup()
            group[FUNCTION_COMPILATION_FINGERPRINT_PROP] = "child-fingerprint"
            finalize_before_commit(group)
            return group

    source = 'x = input_float("X")\noutput(x)\n'
    function_id = library_function_id("local", "", "demo")
    owner = function_group_owner_scope("LIBRARY", "local", "", "demo")
    prepared = _prepared_group(
        source,
        GroupCompilationIdentity(None, owner, owner, function_id.stable_key()),
    )
    record = _Record("local", "demo", Path("demo.nf"), package_id="", package_version="")
    spec = LibraryFunctionMaterializationSpec(
        namespace="local",
        name="demo",
        record=record,
        prepared_compilation=prepared,
        function_id=function_id,
        source_callable_session=None,
        materialization=None,
        group_name="Local Demo",
        find_existing=lambda *args, **kwargs: None,
        write_package_metadata=lambda group, record: None,
    )
    backend = FakeBackend()
    materializer = FunctionMaterializer(group_backend=backend)
    trace = FunctionCompilationTrace()
    cache = {}
    context = FunctionMaterializationContext(cache, object(), trace, None)

    with trace.group("parent", {}) as frame:
        first = materializer.materialize_library(spec, context).group
        second = materializer.materialize_library(spec, context).group
        assert first is second
        assert len(backend.calls) == 1
        assert frame.child_rows == [
            {"owner": owner, "fingerprint": "child-fingerprint"},
            {"owner": owner, "fingerprint": "child-fingerprint"},
        ]
        assert not frame.freshness_unproven


def test_selected_library_update_forwards_prepared_semantics_only():
    """Selected-root reload cannot reintroduce a raw-source backend authority."""
    from types import SimpleNamespace
    from NodeForge.function_instances import function_group_owner_scope
    from NodeForge.function_materializer import FunctionMaterializer, LibraryFunctionUpdateSpec

    class Backend:
        """Capture one selected-root backend publication request."""

        def __init__(self):
            self.request = None

        def create_or_update(self, request, *, finalize_before_commit=None):
            self.request = request
            group = request.existing_group
            finalize_before_commit(group)
            return group

    class Group(dict):
        """Minimal selected-root group stand-in."""

        def __init__(self):
            super().__init__()
            self.name = "Demo"

    function_id = library_function_id("functions", "vendor.pkg", "demo")
    owner = function_group_owner_scope("LIBRARY", "functions", function_id.package_id, "demo")
    prepared = _prepared_group(
        'x = input_float("X")\noutput(x)\n',
        GroupCompilationIdentity(None, owner, function_id.stable_key(), function_id.stable_key()),
    )
    record = _Record("functions", "demo", Path("demo.nf"))
    group = Group()
    spec = LibraryFunctionUpdateSpec(
        namespace="functions",
        name="demo",
        record=record,
        prepared_compilation=prepared,
        function_id=function_id,
        source_callable_session=None,
        group=group,
        group_name="Demo",
        write_package_metadata=lambda target, rec: None,
    )
    backend = Backend()
    result = FunctionMaterializer(group_backend=backend).update_library_group(spec)

    assert result is group
    assert backend.request.prepared_compilation is prepared
    assert backend.request.function_compilation_inputs["source"]
    assert group["nodeforge_library_source"] == prepared.source


def test_source_callable_contract_rejects_duplicate_positions_and_hidden_keywords():
    from NodeForge.callable_contracts import SourceCallableContract

    function_id = library_function_id("functions", "vendor.pkg", "demo")
    first = SourceCallableParameter(0, "a", "A", "a", NFType.FLOAT, True)
    duplicate = SourceCallableParameter(0, "b", "B", "b", NFType.FLOAT, True)
    with pytest.raises(ValueError, match="positions must be unique"):
        SourceCallableContract(function_id, (first, duplicate), ())
    with pytest.raises(ValueError, match="Hidden source-call parameter"):
        SourceCallableParameter(1, "capture", "Capture", "capture", NFType.FLOAT, False)


def test_group_interface_contract_rejects_duplicate_canonical_origins():
    from NodeForge.callable_contracts import GroupInterfaceContract

    origin = BindingId("scope", 0)
    inputs = (
        GroupInputContract("a", "A", NFType.FLOAT, 0.0, True, origin),
        GroupInputContract("b", "B", NFType.FLOAT, 0.0, True, origin),
    )
    with pytest.raises(ValueError, match="interface origins must be unique"):
        GroupInterfaceContract(inputs, ())


def test_source_callable_records_do_not_duplicate_derivable_or_unused_state():
    """Keep one authority for whole-group state, interface origin, and preparation keys."""
    from dataclasses import fields
    from NodeForge.semantic_group import SemanticGroupCompilation
    from NodeForge.source_callables import PreparedSourceCallable

    assert {field.name for field in fields(SemanticGroupCompilation)} == {
        "source",
        "identity",
        "normalized_lowered_source",
        "body",
        "interface",
        "geometry_mode",
        "extension_dependencies",
    }
    assert "origin_kind" not in {field.name for field in fields(GroupInputContract)}
    assert "index" not in {field.name for field in fields(GroupInputContract)}
    assert "index" in {field.name for field in fields(GroupOutputContract)}
    assert "required" not in {field.name for field in fields(SourceCallableParameter)}
    assert tuple(field.name for field in fields(PreparedSourceCallable)) == ("contract", "group")


def test_local_source_call_ir_owns_monotonic_shared_and_unique_identity():
    """Semantic traversal allocates stable unique ordinals without backend participation."""
    from NodeForge.semantic_ir import IRCall, IRCallableKind, IRFunctionMaterializationMode
    from NodeForge.source_callables import SourceCallableSession

    environment = _environment()
    session = SourceCallableSession(resolved_environment=environment)
    source = (
        'def f(x):\n'
        '    return x + 1\n'
        'a = input_int("A")\n'
        'shared = f(a)\n'
        'first = f(a, __unique__=True)\n'
        'second = f(a, __unique__=True)\n'
        'output(shared)\n'
    )
    compilation = analyze_group_source(
        source,
        compilation_identity=_identity("ROOT/calls", "ROOT/calls"),
        resolved_environment=environment,
        source_callable_session=session,
    )
    calls = []
    for statement in compilation.body.statements:
        program = getattr(statement, "value", None)
        if program is None:
            continue
        calls.extend(
            operation
            for operation in program.operations
            if isinstance(operation, IRCall) and operation.target.kind is IRCallableKind.SOURCE_FUNCTION
        )

    assert [call.materialization.mode for call in calls] == [
        IRFunctionMaterializationMode.SHARED,
        IRFunctionMaterializationMode.UNIQUE,
        IRFunctionMaterializationMode.UNIQUE,
    ]
    assert calls[0].materialization.call_site is None
    assert [call.materialization.call_site.ordinal for call in calls[1:]] == [0, 1]
    assert calls[1].target.function_id == calls[2].target.function_id == calls[0].target.function_id
    assert len(session.source_snapshots) == 1
    assert len(session.prepared) == 3


@pytest.mark.parametrize("namespace", ["functions", "examples", "local"])
def test_pure_source_catalog_namespaces_route_through_source_function_ir(tmp_path, namespace):
    """Functions, Examples, and Local pure-source calls all use permanent source Call IR."""
    from NodeForge.semantic_ir import IRCall, IRCallableKind, IRFunctionMaterializationMode
    from NodeForge.source_callables import SourceCallableSession

    source_path = tmp_path / f"{namespace}_demo.nf"
    source_path.write_text('x = input_float("X", default=1.0)\noutput(x)\n', encoding="utf-8")
    package_id = "" if namespace == "local" else "vendor.pkg"
    record = _Record(namespace, "demo", source_path, package_id=package_id)
    catalogs = {"functions": {}, "examples": {}, "local": {}}
    catalogs[namespace] = {"demo": record}
    environment = _environment(**catalogs)
    session = SourceCallableSession(resolved_environment=environment)
    root_source = f"from {namespace} import demo\ny = demo(2.0)\noutput(y)\n"
    compilation = analyze_group_source(
        root_source,
        compilation_identity=_identity(f"ROOT/{namespace}", f"ROOT/{namespace}"),
        resolved_environment=environment,
        source_callable_session=session,
    )
    call = next(
        operation
        for statement in compilation.body.statements
        for operation in getattr(getattr(statement, "value", None), "operations", ())
        if isinstance(operation, IRCall) and operation.target.kind is IRCallableKind.SOURCE_FUNCTION
    )
    assert call.target.function_id.namespace == namespace
    if namespace == "local":
        assert call.materialization is None
    else:
        assert call.materialization.mode is IRFunctionMaterializationMode.SHARED
    assert len(session.source_snapshots) == 1
    assert len(session.prepared) == 1


def test_source_call_known_but_not_foldable_argument_remains_runtime(tmp_path):
    """Compile-time knowledge never erases a NOT_FOLDABLE runtime source-call operand."""
    from NodeForge.semantic_ir import IRBinary, IRCall, IRCallableKind
    from NodeForge.source_callables import SourceCallableSession

    source_path = tmp_path / "demo.nf"
    source_path.write_text('x = input_float("X")\noutput(x)\n', encoding="utf-8")
    record = _Record("functions", "demo", source_path)
    environment = _environment(functions={"demo": record})
    session = SourceCallableSession(resolved_environment=environment)
    compilation = analyze_group_source(
        'from functions import demo\ny = demo(1 / 2)\noutput(y)\n',
        compilation_identity=_identity("ROOT/runtime-arg", "ROOT/runtime-arg"),
        resolved_environment=environment,
        source_callable_session=session,
    )
    operations = next(statement.value.operations for statement in compilation.body.statements if getattr(statement, "source_name", None) == "y")
    call = next(operation for operation in operations if isinstance(operation, IRCall) and operation.target.kind is IRCallableKind.SOURCE_FUNCTION)
    assert any(isinstance(operation, IRBinary) and operation.op == "DIVIDE" for operation in operations)
    assert len(call.arguments) == 1
    assert call.static_arguments == ()


def test_legacy_source_call_entrypoints_are_absent():
    """Permanent source calls have no fallback adapter or legacy dispatch entrypoint."""
    root = Path(__file__).resolve().parents[2]
    local_source = (root / "local_functions.py").read_text(encoding="utf-8")
    semantic_source = (root / "semantic_analysis.py").read_text(encoding="utf-8")
    lowering_source = (root / "semantic_lowering.py").read_text(encoding="utf-8")

    assert "def compile_local_function_call" not in local_source
    assert not (root / "library_calls.py").exists()
    assert not (root / "expression_compiler.py").exists()
    assert 'CallableKind.LOCAL_FUNCTION' in semantic_source
    assert 'CallableKind.LIBRARY' in semantic_source
    assert 'IRCallableKind.SOURCE_FUNCTION' in lowering_source

"""qualified package-callable end-to-end contracts for owner-qualified package callables."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from NodeForge.compiler_identities import GroupCompilationIdentity, ModuleId, module_definition_function_id
from NodeForge.errors import CompileError
from NodeForge.extensions.registry import ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot
from NodeForge.resolved_environment import (
    PackageCallableExport,
    ResolvedCatalog,
    ResolvedEnvironment,
    ResolvedPackageNamespace,
)
from NodeForge.semantic.group import analyze_group_source
from NodeForge.semantic.ir import IRCall, IRCallableKind
from NodeForge.semantic.source_callable_session import SourceCallableSession
from NodeForge.semantic.source_modules import parse_source_module

pytestmark = pytest.mark.unit


def _identity(label: str = "ROOT/package_namespaces") -> GroupCompilationIdentity:
    """Return one detached root semantic identity."""
    return GroupCompilationIdentity(None, label, label, label)


def _source_record(tmp_path: Path, package_id: str, name: str, *, result_expr: str = "x"):
    """Create one pure source-backed package callable record."""
    path = tmp_path / f"{package_id.replace('.', '_')}_{name}.nf"
    path.write_text(f'def {name}(x: Float) -> Float:\n    return {result_expr}\n', encoding="utf-8")
    return SimpleNamespace(
        namespace="functions",
        name=name,
        path=path,
        source_path=path,
        module_path=None,
        interface_path=None,
        package_id=package_id,
        package_name=package_id,
        package_version="1.0.0",
    )


def _environment(tmp_path: Path, specs, *, source_overrides=None):
    """Build one immutable owner-qualified environment from source-callable specs."""
    namespaces = {}
    records = {}
    for package_id, import_name, names in specs:
        exports = {}
        for name in names:
            record = _source_record(tmp_path, package_id, name)
            if source_overrides and (package_id, name) in source_overrides:
                record.path.write_text(source_overrides[(package_id, name)], encoding='utf-8')
            records[(package_id, name)] = record
            snapshot = parse_source_module(
                record.path.read_text(encoding='utf-8'),
                ModuleId('PACKAGE', f'functions/{record.path.name}', package_id=package_id),
            )
            exports[name] = PackageCallableExport(
                package_id, name, record=record,
                definition=snapshot.definition_ref(name), source_snapshot=snapshot,
            )
        namespaces[package_id] = ResolvedPackageNamespace(
            package_id,
            import_name,
            package_id,
            "1.0.0",
            exports,
        )
    environment = ResolvedEnvironment(
        {
            "functions": ResolvedCatalog("functions", {}),
            "examples": ResolvedCatalog("examples", {}),
            "local": ResolvedCatalog("local", {}),
        },
        package_namespaces=namespaces,
    )
    return environment, records


def _semantic_environment(tmp_path: Path) -> ResolvedEnvironment:
    """Build one semantic extension owner with a callable name usable for collision tests."""
    owner = tmp_path / "semantic_owner"
    owner.mkdir(parents=True)
    (owner / "interface.py").write_text(
        """
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
@dataclass(frozen=True)
class Part:
    value: Float
EXTENSIONS = {"make": None, "part": None}
def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
def part(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
""",
        encoding="utf-8",
    )
    (owner / "semantic.py").write_text(
        """
from .interface import Part
def make(value) -> Part: return Part(value)
def part(value) -> Part: return Part(value)
""",
        encoding="utf-8",
    )
    owner_key = ("system", "vendor.semantic", "values")
    session = ExtensionOwnerSession(capture_owner_code_snapshot(owner_key, owner))
    registry = ExtensionRegistry((session,))
    families, _types = session.normalize_interface()
    exports = {
        callable_id.name: PackageCallableExport(
            "vendor.semantic",
            callable_id.name,
            extension_callable_id=callable_id,
        )
        for callable_id in families
    }
    namespace = ResolvedPackageNamespace(
        "vendor.semantic",
        "semantic",
        "Semantic",
        "1.0.0",
        exports,
    )
    return ResolvedEnvironment(
        {
            "functions": ResolvedCatalog("functions", {}),
            "examples": ResolvedCatalog("examples", {}),
            "local": ResolvedCatalog("local", {}),
        },
        package_namespaces={"vendor.semantic": namespace},
        extension_registry=registry,
    )


def _compile(source: str, environment: ResolvedEnvironment, *, session=None, label="ROOT/package_namespaces"):
    """Compile one qualified package-callable fixture through the production semantic group path."""
    session = session or SourceCallableSession(resolved_environment=environment)
    return analyze_group_source(
        source,
        compilation_identity=_identity(label),
        resolved_environment=environment,
        source_callable_session=session,
    ), session


def _source_calls(compilation):
    """Return source-backed calls from all straight-line expression programs."""
    calls = []
    for statement in compilation.body.statements:
        program = getattr(statement, "value", None)
        for operation in getattr(program, "operations", ()):
            if isinstance(operation, IRCall) and operation.target.kind is IRCallableKind.SOURCE_FUNCTION:
                calls.append(operation)
    return calls


def test_core_reserved_bare_points_and_qualified_package_points_coexist(tmp_path):
    """Core bare reservation does not invalidate or shadow owner-qualified package points."""
    environment, _ = _environment(tmp_path, [("nodeforge.lsystem", "lsystem", ("rings",))])
    compilation, _session = _compile(
        "from packages import lsystem\n"
        "count = 1\n"
        "core_geo = points(count)\n"
        "x = 1.0\n"
        "pkg_value = lsystem.rings(x)\n"
        "display = core_geo\n",
        environment,
    )
    calls = _source_calls(compilation)
    assert [call.target.function_id for call in calls] == [
        next(iter(_session.prepared)).function_id
    ]


def test_qualified_missing_member_never_falls_back_to_core(tmp_path):
    """An imported package namespace owns qualified lookup failure completely."""
    environment, _ = _environment(tmp_path, [("nodeforge.lsystem", "lsystem", ("other",))])
    with pytest.raises(CompileError, match="has no callable member 'points'"):
        _compile("from packages import lsystem\ng = lsystem.points(1)\nresult = g\n", environment)


def test_same_member_from_two_packages_is_qualified_and_bare_is_ambiguous(tmp_path):
    """Cross-owner duplicate members retain canonical owner identity."""
    environment, _ = _environment(
        tmp_path,
        [("vendor.a", "a", ("foo",)), ("vendor.b", "b", ("foo",))],
    )
    with pytest.raises(CompileError, match=r"Ambiguous callable 'foo'.*a\.foo.*b\.foo"):
        _compile("from packages import a, b\nx = 1.0\ny = foo(x)\nresult = y\n", environment)

    first, _ = _compile("from packages import a, b\nx = 1.0\ny = a.foo(x)\nresult = y\n", environment, label="ROOT/a")
    second, _ = _compile("from packages import a, b\nx = 1.0\ny = b.foo(x)\nresult = y\n", environment, label="ROOT/b")
    assert _source_calls(first)[0].target.function_id.module.package_id == "vendor.a"
    assert _source_calls(second)[0].target.function_id.module.package_id == "vendor.b"


def test_alias_spelling_does_not_change_canonical_callable_identity(tmp_path):
    """Source alias is scope syntax only, never semantic identity."""
    environment, _ = _environment(tmp_path, [("nodeforge.math", "math", ("sin",))])
    first, _ = _compile("from packages import math\nx=1.0\ny=math.sin(x)\nresult = y\n", environment, label="ROOT/math")
    second, _ = _compile("from packages import math as m\nx=1.0\ny=m.sin(x)\nresult = y\n", environment, label="ROOT/m")
    assert _source_calls(first)[0].target.function_id == _source_calls(second)[0].target.function_id


def test_runtime_source_value_blocks_bare_package_call_but_qualified_call_works(tmp_path):
    """A live source value owns its bare name while package qualification remains explicit."""
    environment, _ = _environment(tmp_path, [("nodeforge.math", "math", ("sin",))])
    source = "from packages import math\nsin=math.sin(1.0)\nx=1.0\ny=sin(x)\nresult = y\n"
    with pytest.raises(CompileError, match=r"Cannot call 'sin'.*source value.*math\.sin"):
        _compile(source, environment)
    qualified = "from packages import math\nsin=math.sin(1.0)\nx=1.0\ny=math.sin(x)\nresult = y\n"
    compilation, _ = _compile(qualified, environment)
    assert _source_calls(compilation)[0].target.function_id.module.package_id == "nodeforge.math"


def test_compile_time_source_value_blocks_bare_package_call_but_qualified_call_works(tmp_path):
    """Compile-time source values own bare names without hiding qualified package members."""
    environment, _ = _environment(tmp_path, [("nodeforge.math", "math", ("sin",))])
    with pytest.raises(CompileError, match=r"Cannot call 'sin'.*source value.*math\.sin"):
        _compile("from packages import math\nsin = 2.0\nx=1.0\ny=sin(x)\nresult = y\n", environment)

    compilation, _ = _compile(
        "from packages import math\nsin = 2.0\nx=1.0\ny=math.sin(x)\nresult = y\n",
        environment,
    )
    assert _source_calls(compilation)[0].target.function_id.module.package_id == "nodeforge.math"


def test_structural_array_source_value_blocks_bare_package_call_but_qualified_call_works(tmp_path):
    """Structural arrays own bare names while explicit owner qualification remains available."""
    environment, _ = _environment(tmp_path, [("vendor.arr", "arr", ("items",))])
    with pytest.raises(CompileError, match=r"Cannot call 'items'.*source value.*arr\.items"):
        _compile(
            "from packages import arr\n"
            "x=1.0\n"
            "items=[x]\n"
            "y=items(x)\n"
            "result = y\n",
            environment,
        )

    compilation, _ = _compile(
        "from packages import arr\n"
        "x=1.0\n"
        "items=[x]\n"
        "y=arr.items(x)\n"
        "result = y\n",
        environment,
    )
    assert _source_calls(compilation)[0].target.function_id.module.package_id == "vendor.arr"



def test_plain_structural_binding_blocks_bare_package_call_and_qualified_call_works(tmp_path):
    """Tuple-backed structural state owns its bare name without hiding explicit qualification."""
    environment, _ = _environment(tmp_path, [("vendor.struct", "struct", ("pair",))])
    bad = (
        "from packages import struct\n"
        "x=1.0\n"
        "pair=(x, x)\n"
        "y=pair(x)\n"
        "result = y\n"
    )
    with pytest.raises(CompileError, match=r"Cannot call 'pair'.*source value.*struct\.pair"):
        _compile(bad, environment)

    good = (
        "from packages import struct\n"
        "x=1.0\n"
        "pair=(x, x)\n"
        "y=struct.pair(x)\n"
        "result = y\n"
    )
    compilation, _ = _compile(good, environment)
    assert _source_calls(compilation)[0].target.function_id.module.package_id == "vendor.struct"


def test_geometry_builder_binding_blocks_bare_package_call_and_qualified_call_works(tmp_path):
    """Frontend-only GeometryBuilder bindings participate in the shared source-value rule."""
    environment, _ = _environment(tmp_path, [("vendor.builder", "pkg", ("builder",))])
    bad = (
        "from packages import pkg\n"
        "builder=geometry_builder()\n"
        "x=1.0\n"
        "y=builder(x)\n"
        "result = y\n"
    )
    with pytest.raises(CompileError, match=r"Cannot call 'builder'.*source value.*pkg\.builder"):
        _compile(bad, environment)

    good = (
        "from packages import pkg\n"
        "builder=geometry_builder()\n"
        "x=1.0\n"
        "y=pkg.builder(x)\n"
        "result = y\n"
    )
    compilation, _ = _compile(good, environment)
    assert _source_calls(compilation)[0].target.function_id.module.package_id == "vendor.builder"


def test_persistent_extension_value_blocks_bare_package_call_and_qualified_call_works(tmp_path):
    """Persistent package semantic state owns its source name without changing package export identity."""
    environment = _semantic_environment(tmp_path)
    bad = (
        "from packages import semantic\n"
        "x=1.0\n"
        "part=make(x)\n"
        "other=part(x)\n"
        "result = x\n"
    )
    with pytest.raises(CompileError, match=r"Cannot call 'part'.*source value.*semantic\.part"):
        _compile(bad, environment)

    good = (
        "from packages import semantic\n"
        "x=1.0\n"
        "part=make(x)\n"
        "other=semantic.part(x)\n"
        "result = x\n"
    )
    compilation, _ = _compile(good, environment)
    assert compilation.extension_dependencies











def test_local_parameter_named_like_package_export_remains_non_callable(tmp_path):
    """A lexical parameter owns its spelling and is not replaced by outer package lookup."""
    environment, _ = _environment(tmp_path, [("nodeforge.math", "math", ("sin",))])
    source = (
        "from packages import math\n"
        "x=1.0\n"
        "def helper(sin: Float, x: Float):\n"
        "    return sin(x)\n"
        "result = helper(x, x)\n"
    )
    with pytest.raises(CompileError, match=r"Cannot call 'sin'.*source value"):
        _compile(source, environment)


def test_package_source_callable_keeps_its_own_explicit_package_scope(tmp_path):
    """Caller source values do not leak into independently prepared package source files."""
    environment, records = _environment(
        tmp_path,
        [
            ("nodeforge.math", "math", ("sin",)),
            ("vendor.helpers", "helpers", ("apply_sin",)),
        ],
        source_overrides={
            ("vendor.helpers", "apply_sin"):
                "from packages import math\ndef apply_sin(x: Float) -> Float:\n"
                "    return math.sin(x)\n",
        },
    )
    source = (
        "from packages import helpers, math\n"
        "sin=1.0\n"
        "x=1.0\n"
        "y=helpers.apply_sin(x)\n"
        "result = y\n"
    )
    compilation, session = _compile(source, environment)
    assert _source_calls(compilation)[0].target.function_id.module.package_id == "vendor.helpers"
    helper_group = next(
        prepared.group
        for key, prepared in session.prepared.items()
        if key.function_id.kind == "MODULE_DEF" and key.function_id.module.package_id == "vendor.helpers"
    )
    assert _source_calls(helper_group)[0].target.function_id.module.package_id == "nodeforge.math"






@pytest.mark.parametrize('case', ['selected', 'value_collision', 'ambiguous'])
def test_package_target_is_constructed_only_after_selection(tmp_path, monkeypatch, case):
    from NodeForge.semantic import call_resolution
    specs = [('nodeforge.math', 'math', ('sin',))]
    if case == 'ambiguous':
        specs.append(('vendor.other', 'other', ('sin',)))
    environment, _ = _environment(tmp_path, specs)
    original = call_resolution._resolved_from_package_export
    conversions = []
    def convert(name, export):
        conversions.append(export.package_id)
        return original(name, export)
    monkeypatch.setattr(call_resolution, '_resolved_from_package_export', convert)
    source = 'from packages import math\nx=1.0\n'
    if case == 'ambiguous':
        source += 'from packages import other\n'
    elif case == 'value_collision':
        source += 'sin=2.0\n'
    source += 'result = sin(x)\n'
    if case == 'selected':
        _compile(source, environment)
        assert conversions == ['nodeforge.math']
    else:
        message = 'Ambiguous callable' if case == 'ambiguous' else "Cannot call 'sin'"
        with pytest.raises(CompileError, match=message):
            _compile(source, environment)
        assert conversions == []

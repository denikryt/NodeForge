"""Focused contracts for the physical reusable-function materializer."""

from dataclasses import fields
from types import SimpleNamespace

import pytest

from NodeForge.compiler_identities import (
    CallSiteId,
    GroupCompilationIdentity,
    library_function_id,
    local_function_id,
)
from NodeForge.errors import CompileError
from NodeForge.function_instances import (
    FUNCTION_COMPILATION_FINGERPRINT_PROP,
    direct_library_owner_scope,
    function_group_owner_scope,
    function_materialization_owner_scope,
    instance_key_for,
)
from NodeForge.function_materializer import (
    FunctionMaterializationContext,
    FunctionMaterializer,
    LibraryFunctionMaterializationSpec,
    LibraryFunctionUpdateSpec,
    LocalFunctionMaterializationSpec,
)
from NodeForge.callable_contracts import GroupInterfaceContract
from NodeForge.semantic_group import SemanticGroupCompilation
from NodeForge.semantic_ir import IRBody, IRFunctionMaterialization, IRFunctionMaterializationMode


class FakeGroup(dict):
    """Minimal Blender-like group used by pure materializer tests."""

    def __init__(self, name="Group", fingerprint="fingerprint"):
        super().__init__()
        self.name = name
        self.interface = SimpleNamespace(items_tree=[])
        if fingerprint:
            self[FUNCTION_COMPILATION_FINGERPRINT_PROP] = fingerprint




class FakeBackend:
    """Adapt legacy callback-shaped fakes to the explicit backend test contract."""

    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def create_or_update(self, request, *, finalize_before_commit=None):
        kwargs = {}
        for field_name in (
            "existing_group",
            "helper_namespace",
            "function_group_cache",
            "function_group_transaction",
            "function_compilation_trace",
            "function_compilation_inputs",
            "function_instance_key",
            "source_callable_session",
        ):
            value = getattr(request, field_name)
            if value is not None:
                kwargs[field_name] = value
        if request.preserve_if_equivalent:
            kwargs["preserve_if_equivalent"] = True
        source = request.prepared_compilation.source
        self.calls.append(request)
        group = self.callback(source, request.name, **kwargs)
        if finalize_before_commit is not None:
            finalize_before_commit(group)
        return group


def make_materializer(callback):
    """Return a FunctionMaterializer backed by one callback-shaped fake backend."""
    return FunctionMaterializer(group_backend=FakeBackend(callback))


class Frame:
    """Record dependency-trace calls without requiring the full compiler trace."""

    def __init__(self):
        self.children = []
        self.identity_children = []
        self.unproven = []

    def record_dependency(self, materialization, fingerprint):
        self.children.append((materialization, fingerprint))

    def record_dependency_identity(self, owner_scope, fingerprint):
        self.identity_children.append((owner_scope, fingerprint))

    def mark_unproven(self, reason):
        self.unproven.append(reason)


class Trace:
    """Expose one current trace frame."""

    def __init__(self, frame=None):
        self.current = frame


def _prepared(source, *, owner_scope, definition_owner, declaration_owner):
    """Build a minimal immutable semantic group artifact for materializer tests."""
    return SemanticGroupCompilation(
        source=source,
        identity=GroupCompilationIdentity(None, owner_scope, definition_owner, declaration_owner),
        normalized_lowered_source="normalized-body",
        body=IRBody(()),
        interface=GroupInterfaceContract((), ()),
        geometry_mode=False,
    )


def local_request(*, materialization, existing=None, live=True, finalize=None):
    """Build a controlled local materialization request for policy tests."""
    callee = materialization.callee

    def find_existing(**kwargs):
        find_existing.calls.append(kwargs)
        return existing

    find_existing.calls = []

    def default_finalize(group, fingerprint, instance_key):
        group["finalized"] = (fingerprint, instance_key)
        group.name = "Helper"

    owner_scope = function_materialization_owner_scope(materialization)
    prepared = _prepared(
        "output(value=1)\n",
        owner_scope=owner_scope,
        definition_owner=callee.definition_owner,
        declaration_owner=callee.stable_key(),
    )
    return LocalFunctionMaterializationSpec(
        materialization=materialization,
        logical_namespace="Root",
        group_name="Helper",
        prepared_compilation=prepared,
        definition_owner=callee.definition_owner,
        own_inputs={
            "kind": "local-def",
            "definition_owner": callee.definition_owner,
            "name": callee.name,
            "signature": callee.signature,
            "source": "output(value=1)\n",
        },
        helper_namespace="Root",
        find_existing=find_existing,
        is_live_group=lambda group: live,
        finalize_group=finalize or default_finalize,
    )


def library_record(namespace="functions", package_id="vendor.pkg", name="demo"):
    """Return one resolved record-shaped object for materializer unit tests."""
    return SimpleNamespace(
        namespace=namespace,
        package_id=package_id,
        package_version="1.2.3",
        package_name="Vendor",
        name=name,
        kind="source",
    )


def library_request(*, namespace="functions", materialization=None, write_metadata=None, existing=None):
    """Build a controlled editable catalog materialization request."""
    record = library_record(namespace=namespace)
    function_id = library_function_id(namespace, record.package_id, record.name)

    def find_existing(record_arg, *, instance_key=None, transaction=None):
        find_existing.calls.append((record_arg, instance_key, transaction))
        return existing

    find_existing.calls = []

    def default_write(group, record_arg):
        group["package"] = record_arg.package_id

    if materialization is None:
        owner_scope = direct_library_owner_scope(namespace, function_id.package_id, function_id.name)
    else:
        owner_scope = function_materialization_owner_scope(materialization)
    definition_owner = owner_scope if namespace == "local" else function_id.stable_key()
    prepared = _prepared(
        "output(value=1)\n",
        owner_scope=owner_scope,
        definition_owner=definition_owner,
        declaration_owner=function_id.stable_key(),
    )
    return LibraryFunctionMaterializationSpec(
        namespace=namespace,
        name=record.name,
        record=record,
        prepared_compilation=prepared,
        function_id=function_id,
        source_callable_session=None,
        materialization=materialization,
        group_name="Demo",
        find_existing=find_existing,
        write_package_metadata=write_metadata or default_write,
    )


def test_materializer_constructor_and_context_are_minimal_and_borrowed():
    """Keep compilation-owned lifecycle state out of materializer instance state."""
    callback = object()
    materializer = make_materializer(callback)
    assert vars(materializer) == {"_group_backend": materializer._group_backend}
    assert tuple(field.name for field in fields(FunctionMaterializationContext)) == (
        "function_group_cache",
        "function_group_transaction",
        "function_compilation_trace",
        "source_callable_session",
    )


def test_shared_and_unique_local_identity_cache_and_owner_scope():
    """Derive the exact existing local owner/cache protocol without allocating ordinals."""
    function_id = local_function_id("root-owner", "helper", "x:FLOAT")
    shared = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    unique_site = CallSiteId("root-owner", function_id, 3)
    unique = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.UNIQUE, unique_site)

    for materialization, expected_key in ((shared, ""), (unique, instance_key_for(unique_site))):
        cache = {}
        frame = Frame()
        calls = []

        def compile_group(source, group_name, **kwargs):
            calls.append(kwargs)
            return FakeGroup(group_name)

        result = make_materializer(compile_group).materialize_local(
            local_request(materialization=materialization),
            FunctionMaterializationContext(cache, object(), Trace(frame)),
        )
        owner = function_group_owner_scope(
            "LOCAL_DEF", "root-owner", "helper", "x:FLOAT", instance_key=expected_key
        )
        assert result.instance_key == expected_key
        assert result.owner_scope == owner
        assert cache[("local-def", function_id, expected_key or "SHARED")] is result.group
        assert calls[0]["function_instance_key"] == expected_key
        assert frame.children == [(materialization, "fingerprint")]
        assert frame.children[0][0] is materialization


def test_local_live_cache_hit_records_trace_without_lookup_or_compile():
    """Preserve the local liveness check and cache-hit child registration."""
    function_id = local_function_id("root", "helper", "x:FLOAT")
    materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    group = FakeGroup("Helper")
    cache = {("local-def", function_id, "SHARED"): group}
    frame = Frame()
    spec = local_request(materialization=materialization, existing=object(), live=True)

    def fail_compile(*args, **kwargs):
        raise AssertionError("live cache hit must not compile")

    result = make_materializer(fail_compile).materialize_local(
        spec, FunctionMaterializationContext(cache, object(), Trace(frame))
    )
    owner = function_group_owner_scope("LOCAL_DEF", "root", "helper", "x:FLOAT")
    assert result.group is group
    assert spec.find_existing.calls == []
    assert frame.children == [(materialization, "fingerprint")]
    assert frame.children[0][0] is materialization


def test_local_stale_cache_updates_existing_and_preserves_exact_context_kwargs():
    """Ignore stale cached groups and keep nested transaction/cache/trace identity."""
    function_id = local_function_id("root", "helper", "x:FLOAT")
    materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    stale = FakeGroup("Stale")
    existing = FakeGroup("Existing")
    cache = {("local-def", function_id, "SHARED"): stale}
    transaction = object()
    trace = Trace(Frame())
    calls = []

    def compile_group(source, group_name, **kwargs):
        calls.append(kwargs)
        assert kwargs["existing_group"] is existing
        return existing

    spec = local_request(materialization=materialization, existing=existing, live=False)
    result = make_materializer(compile_group).materialize_local(
        spec, FunctionMaterializationContext(cache, transaction, trace)
    )
    assert result.group is existing
    assert calls[0]["function_group_cache"] is cache
    assert calls[0]["function_group_transaction"] is transaction
    assert calls[0]["function_compilation_trace"] is trace
    assert calls[0]["preserve_if_equivalent"] is True
    assert spec.find_existing.calls[0]["transaction"] is transaction


def test_local_failure_order_does_not_publish_before_required_finalization():
    """Keep local callback/finalization failure ahead of cache publication."""
    function_id = local_function_id("root", "helper", "x:FLOAT")
    materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    cache = {}

    def compile_fail(*args, **kwargs):
        raise RuntimeError("compile failed")

    with pytest.raises(RuntimeError, match="compile failed"):
        make_materializer(compile_fail).materialize_local(
            local_request(materialization=materialization),
            FunctionMaterializationContext(cache, None, None),
        )
    assert cache == {}

    def finalize_fail(*args):
        raise RuntimeError("metadata failed")

    with pytest.raises(RuntimeError, match="metadata failed"):
        make_materializer(lambda *args, **kwargs: FakeGroup()).materialize_local(
            local_request(materialization=materialization, finalize=finalize_fail),
            FunctionMaterializationContext(cache, None, None),
        )
    assert cache == {}


def test_imported_cache_hit_records_canonical_dependency_without_lookup():
    """An imported cache hit records actual use in the current parent frame."""
    function_id = library_function_id("functions", "vendor.pkg", "demo")
    materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    spec = library_request(materialization=materialization)
    assert spec.function_id == function_id
    cached = FakeGroup("Cached")
    cache = {("library", function_id, "SHARED"): cached}
    frame = Frame()

    result = make_materializer(lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())).materialize_library(
        spec, FunctionMaterializationContext(cache, object(), Trace(frame))
    )
    assert result.group is cached
    assert spec.find_existing.calls == []
    assert frame.children == [(materialization, "fingerprint")]
    assert frame.children[0][0] is spec.materialization


def test_imported_cached_child_is_observed_by_each_parent_and_each_access():
    """Shared cache population order cannot omit another parent's dependency."""
    function_id = library_function_id("functions", "vendor.pkg", "demo")
    materialization = IRFunctionMaterialization(
        function_id, IRFunctionMaterializationMode.SHARED
    )
    spec = library_request(materialization=materialization)
    cached = FakeGroup("Cached", fingerprint="child-v1")
    cache = {("library", function_id, "SHARED"): cached}
    materializer = make_materializer(
        lambda *args, **kwargs: pytest.fail("cache hit must not compile")
    )
    first = Frame()
    second = Frame()

    context = FunctionMaterializationContext(cache, None, Trace(first))
    materializer.materialize_library(spec, context)
    materializer.materialize_library(spec, context)
    materializer.materialize_library(
        spec, FunctionMaterializationContext(cache, None, Trace(second))
    )

    assert first.children == [
        (materialization, "child-v1"),
        (materialization, "child-v1"),
    ]
    assert second.children == [(materialization, "child-v1")]
    assert all(observed is materialization for observed, _ in first.children)
    assert second.children[0][0] is materialization


def test_imported_cached_child_fingerprint_changes_every_parent_fingerprint():
    """Every parent digest follows the realized fingerprint of a cached child."""
    from NodeForge.function_instances import FunctionCompilationTrace

    function_id = library_function_id("functions", "vendor.pkg", "demo")
    materialization = IRFunctionMaterialization(
        function_id, IRFunctionMaterializationMode.SHARED
    )
    spec = library_request(materialization=materialization)
    cached = FakeGroup("Cached", fingerprint="child-v1")
    cache = {("library", function_id, "SHARED"): cached}
    materializer = make_materializer(
        lambda *args, **kwargs: pytest.fail("cache hit must not compile")
    )

    def parent_fingerprint(owner):
        trace = FunctionCompilationTrace()
        with trace.group(owner, {"source": owner}) as frame:
            materializer.materialize_library(
                spec, FunctionMaterializationContext(cache, None, trace)
            )
            return frame.finish("contract").fingerprint

    first_v1 = parent_fingerprint("parent-1")
    second_v1 = parent_fingerprint("parent-2")
    cached[FUNCTION_COMPILATION_FINGERPRINT_PROP] = "child-v2"
    first_v2 = parent_fingerprint("parent-1")
    second_v2 = parent_fingerprint("parent-2")

    assert first_v1 != first_v2
    assert second_v1 != second_v2


def test_imported_cache_hit_without_fingerprint_marks_current_parent_unproven():
    """Missing metadata on an imported cache hit prevents equivalent reuse."""
    from NodeForge.function_instances import FunctionCompilationTrace

    function_id = library_function_id("functions", "vendor.pkg", "demo")
    materialization = IRFunctionMaterialization(
        function_id, IRFunctionMaterializationMode.SHARED
    )
    spec = library_request(materialization=materialization)
    cached = FakeGroup("Cached", fingerprint="")
    cache = {("library", function_id, "SHARED"): cached}
    trace = FunctionCompilationTrace()

    with trace.group("parent", {}) as frame:
        make_materializer(
            lambda *args, **kwargs: pytest.fail("cache hit must not compile")
        ).materialize_library(
            spec, FunctionMaterializationContext(cache, None, trace)
        )
        assert frame.freshness_unproven is True
        assert frame.finish("contract").freshness_unproven is True


def test_imported_metadata_failure_prevents_cache_and_trace_publication():
    """Required editable metadata failure prevents cache/trace publication."""
    function_id = library_function_id("functions", "vendor.pkg", "demo")
    materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)

    def write_fail(group, record):
        raise RuntimeError("metadata failed")

    spec = library_request(materialization=materialization, write_metadata=write_fail)
    cache = {}
    frame = Frame()
    group = FakeGroup("Demo")
    with pytest.raises(RuntimeError, match="metadata failed"):
        make_materializer(lambda *args, **kwargs: group).materialize_library(
            spec, FunctionMaterializationContext(cache, object(), Trace(frame))
        )
    assert cache == {}
    assert frame.children == []


def test_direct_catalog_uses_no_compilation_context_and_no_reusable_cache():
    """Keep standalone direct catalog materialization independent of compiler lifecycle state."""
    spec = library_request(materialization=None)
    calls = []

    def compile_group(source, group_name, **kwargs):
        calls.append(kwargs)
        return FakeGroup(group_name)

    result = make_materializer(compile_group).materialize_library(spec, context=None)
    assert result.instance_key == ""
    assert "function_group_cache" not in calls[0]
    assert "function_group_transaction" not in calls[0]
    assert "function_compilation_trace" not in calls[0]
    assert calls[0].get("preserve_if_equivalent") is None


def test_local_catalog_uses_build_local_transaction_tracked_cache_and_fingerprint():
    """Local catalog children use one build-local cache and canonical fingerprint dependency rows."""
    spec = library_request(namespace="local", materialization=None)
    transaction = object()
    frame = Frame()
    trace = Trace(frame)
    cache = {}
    calls = []

    def compile_group(source, group_name, **kwargs):
        calls.append(kwargs)
        return FakeGroup(group_name, fingerprint="local-fingerprint")

    materializer = make_materializer(compile_group)
    context = FunctionMaterializationContext(cache, transaction, trace)
    first = materializer.materialize_library(spec, context)
    second = materializer.materialize_library(spec, context)

    cache_key = ("local-catalog", spec.function_id)
    owner = direct_library_owner_scope("local", spec.function_id.package_id, spec.name)
    assert first.group is second.group
    assert cache[cache_key] is first.group
    assert len(calls) == 1
    assert calls[0]["function_group_transaction"] is transaction
    assert calls[0]["function_compilation_trace"] is trace
    assert calls[0]["function_compilation_inputs"]["kind"] == "library"
    assert calls[0]["function_compilation_inputs"]["source"]
    assert frame.identity_children == [
        (owner, "local-fingerprint"),
        (owner, "local-fingerprint"),
    ]
    assert frame.unproven == []



def test_selected_root_reload_targets_exact_group_and_metadata_failure_propagates():
    """Keep reload as selected-root update with required unsuppressed metadata."""
    record = library_record()
    function_id = library_function_id("functions", record.package_id, record.name)
    group = FakeGroup("Readable Demo")
    calls = []

    def compile_group(source, group_name, **kwargs):
        calls.append(kwargs)
        return group

    def metadata_fail(updated, record_arg):
        raise RuntimeError("reload metadata failed")

    owner_scope = direct_library_owner_scope("functions", function_id.package_id, function_id.name)
    spec = LibraryFunctionUpdateSpec(
        namespace="functions",
        name="demo",
        record=record,
        prepared_compilation=_prepared(
            "output(value=1)\n",
            owner_scope=owner_scope,
            definition_owner=function_id.stable_key(),
            declaration_owner=function_id.stable_key(),
        ),
        function_id=function_id,
        source_callable_session=None,
        group=group,
        group_name=group.name,
        write_package_metadata=metadata_fail,
    )
    with pytest.raises(RuntimeError, match="reload metadata failed"):
        make_materializer(compile_group).update_library_group(spec)
    assert calls[0]["existing_group"] is group
    assert calls[0]["preserve_if_equivalent"] is True
    assert calls[0]["function_compilation_inputs"]["kind"] == "library-root"
    assert "function_group_cache" not in calls[0]
    assert "function_compilation_trace" not in calls[0]


def test_reusable_library_identity_mismatch_fails_before_mutation():
    """Reject a materialization whose canonical callee differs from its request identity."""
    spec = library_request(materialization=None)
    other = library_function_id("functions", "vendor.other", "demo")
    wrong = IRFunctionMaterialization(other, IRFunctionMaterializationMode.SHARED)
    bad_spec = LibraryFunctionMaterializationSpec(
        **{**spec.__dict__, "materialization": wrong}
    )
    with pytest.raises(CompileError, match="identity mismatch"):
        make_materializer(lambda *args, **kwargs: pytest.fail("must not compile")).materialize_library(
            bad_spec, FunctionMaterializationContext({}, None, None)
        )


def test_imported_unique_identity_uses_existing_call_site_key_and_owner_scope():
    """Keep imported unique instance identity derived only from upstream CallSiteId."""
    function_id = library_function_id("functions", "vendor.pkg", "demo")
    call_site = CallSiteId("root-owner", function_id, 4)
    materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.UNIQUE, call_site)
    spec = library_request(materialization=materialization)
    cache = {}
    calls = []

    def compile_group(source, group_name, **kwargs):
        calls.append(kwargs)
        return FakeGroup(group_name)

    frame = Frame()
    result = make_materializer(compile_group).materialize_library(
        spec, FunctionMaterializationContext(cache, None, Trace(frame))
    )
    key = instance_key_for(call_site)
    owner = function_group_owner_scope("LIBRARY", "functions", "vendor.pkg", "demo", instance_key=key)
    assert result.instance_key == key
    assert result.owner_scope == owner
    assert cache[("library", function_id, key)] is result.group
    assert calls[0]["function_instance_key"] == key
    assert frame.children == [(materialization, "fingerprint")]
    assert frame.children[0][0] is spec.materialization


def test_direct_library_materialization_retains_separate_owner_construction():
    """A direct catalog build has physical ownership without reusable-call IR."""
    spec = library_request(materialization=None)
    calls = []

    def compile_group(source, group_name, **kwargs):
        calls.append(kwargs)
        return FakeGroup(group_name)

    result = make_materializer(compile_group).materialize_library(spec, context=None)
    expected = function_group_owner_scope(
        "LIBRARY", "functions", "vendor.pkg", "demo"
    )
    assert result.owner_scope == expected


def test_imported_compile_failure_does_not_publish_cache_or_trace():
    """Propagate physical build failure before imported cache/trace publication."""
    function_id = library_function_id("functions", "vendor.pkg", "demo")
    materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    spec = library_request(materialization=materialization)
    cache = {}
    frame = Frame()

    def compile_fail(*args, **kwargs):
        raise RuntimeError("build failed")

    with pytest.raises(RuntimeError, match="build failed"):
        make_materializer(compile_fail).materialize_library(
            spec, FunctionMaterializationContext(cache, None, Trace(frame))
        )
    assert cache == {}
    assert frame.children == []


def test_direct_and_local_catalog_metadata_failures_are_required():
    """Required editable catalog metadata failures propagate before publication."""
    def write_fail(group, record):
        raise RuntimeError("metadata failed")

    direct_group = FakeGroup("Direct")
    with pytest.raises(RuntimeError, match="metadata failed"):
        make_materializer(lambda *args, **kwargs: direct_group).materialize_library(
            library_request(materialization=None, write_metadata=write_fail), context=None
        )

    local_group = FakeGroup("Local")
    with pytest.raises(RuntimeError, match="metadata failed"):
        make_materializer(lambda *args, **kwargs: local_group).materialize_library(
            library_request(namespace="local", materialization=None, write_metadata=write_fail),
            FunctionMaterializationContext({}, None, Trace(Frame())),
        )

def test_materializer_source_has_no_compiler_backchannel_and_specs_are_ast_independent():
    """Keep the physical authority independent of Compiler objects and raw AST calls."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "function_materializer.py").read_text(encoding="utf-8")
    assert "__self__" not in source
    assert "__closure__" not in source
    assert "from .compiler import" not in source
    assert "import inspect" not in source
    assert "IR_DEPENDENCY_LOCAL_CATALOG_MIGRATION" not in source
    for spec_type in (LocalFunctionMaterializationSpec, LibraryFunctionMaterializationSpec, LibraryFunctionUpdateSpec):
        names = {field.name for field in fields(spec_type)}
        assert "source" not in names
        assert "prepared_compilation" in names
        assert "comp" not in names
        assert "compiler" not in names
        assert "expr" not in names
        assert "call" not in names


def test_missing_child_fingerprint_preserves_unproven_trace_semantics():
    """A materialized child without a stored fingerprint still makes freshness unproven."""
    from NodeForge.function_instances import FunctionCompilationTrace

    function_id = local_function_id("root", "helper", "x:FLOAT")
    materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    group = FakeGroup("Helper", fingerprint="")
    cache = {("local-def", function_id, "SHARED"): group}
    trace = FunctionCompilationTrace()

    with trace.group("parent", {}) as frame:
        result = make_materializer(
            lambda *args, **kwargs: pytest.fail("cache hit must not compile")
        ).materialize_local(
            local_request(materialization=materialization, live=True),
            FunctionMaterializationContext(cache, None, trace),
        )
        assert result.group is group
        assert frame.freshness_unproven is True

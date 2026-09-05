import ast

import pytest

from NodeForge.errors import CompileError
from NodeForge.compiler_identities import CallSiteId, FunctionId, library_function_id, local_function_id
from NodeForge.semantic_ir import IRFunctionMaterialization, IRFunctionMaterializationMode
from NodeForge.function_instances import (
    FUNCTION_ROOT_OWNER_ID_PROP,
    FunctionCallModifiers,
    FunctionCompilationTrace,
    extract_function_call_modifiers,
    function_group_owner_scope,
    function_materialization_owner_scope,
    instance_key_for,
    instance_key_for_materialization,
    validate_root_owner_id,
)


class DummyCompiler:
    def __init__(self, consts=None):
        self.consts = dict(consts or {})

    def _const_eval_macro_arg(self, expr):
        from NodeForge.consteval import _const_eval

        return _const_eval(expr, self.consts)


def _call(source):
    return ast.parse(source, mode="eval").body



def test_instance_key_for_materialization_preserves_existing_hash_protocol():
    """Semantic materialization records do not alter persisted instance-key hashing."""
    owner = function_group_owner_scope("ROOT", "0123456789abcdef0123456789abcdef")
    function_id = local_function_id(owner, "helper", "x:FLOAT")
    call_site = CallSiteId(owner, function_id, 0)
    shared = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    unique = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.UNIQUE, call_site)

    assert instance_key_for_materialization(shared) == ""
    assert instance_key_for_materialization(unique) == instance_key_for(call_site)
    assert instance_key_for_materialization(unique) == "41b0b35ed1633716105f59a9881b3d52"


def test_materialization_owner_scope_preserves_all_legacy_owner_bytes():
    """One serializer preserves local/imported shared/unique persistence bytes."""
    local_id = local_function_id("root-owner", "helper", "x:FLOAT")
    library_id = library_function_id("functions", "vendor.pkg", "demo")
    cases = (
        IRFunctionMaterialization(local_id, IRFunctionMaterializationMode.SHARED),
        IRFunctionMaterialization(
            local_id,
            IRFunctionMaterializationMode.UNIQUE,
            CallSiteId("root-owner", local_id, 3),
        ),
        IRFunctionMaterialization(library_id, IRFunctionMaterializationMode.SHARED),
        IRFunctionMaterialization(
            library_id,
            IRFunctionMaterializationMode.UNIQUE,
            CallSiteId("root-owner", library_id, 4),
        ),
    )

    for materialization in cases:
        callee = materialization.callee
        key = instance_key_for_materialization(materialization)
        if callee.kind == "LOCAL_DEF":
            expected = function_group_owner_scope(
                "LOCAL_DEF",
                callee.definition_owner,
                callee.name,
                callee.signature,
                instance_key=key,
            )
        else:
            expected = function_group_owner_scope(
                "LIBRARY",
                callee.namespace,
                callee.package_id,
                callee.name,
                instance_key=key or None,
            )
        assert function_materialization_owner_scope(materialization) == expected


def test_materialization_owner_scope_rejects_invalid_input_and_callee_kind():
    """Typed dependency serialization fails closed on unsupported identities."""
    with pytest.raises(TypeError, match="IRFunctionMaterialization"):
        function_materialization_owner_scope("raw-owner")

    rogue = object.__new__(FunctionId)
    object.__setattr__(rogue, "kind", "ROGUE")
    object.__setattr__(rogue, "definition_owner", "")
    object.__setattr__(rogue, "namespace", "")
    object.__setattr__(rogue, "package_id", "")
    object.__setattr__(rogue, "name", "rogue")
    object.__setattr__(rogue, "signature", "")
    materialization = IRFunctionMaterialization(
        rogue, IRFunctionMaterializationMode.SHARED
    )
    with pytest.raises(ValueError, match="Unsupported reusable FunctionId kind"):
        function_materialization_owner_scope(materialization)

def test_unique_modifier_is_removed_and_tracks_explicit_false():
    cleaned, modifiers = extract_function_call_modifiers(_call("helper(x, __unique__=False)"), "helper", {})
    assert isinstance(modifiers, FunctionCallModifiers)
    assert modifiers.unique is False
    assert modifiers.unique_was_explicit is True
    assert [kw.arg for kw in cleaned.keywords] == []


def test_unique_modifier_accepts_only_exact_compile_time_bool():
    _, modifiers = extract_function_call_modifiers(_call("helper(__unique__=flag)"), "helper", {"flag": True})
    assert modifiers.unique is True
    for expr in ("helper(__unique__=1)", "helper(__unique__='yes')", "helper(__unique__=x)"):
        with pytest.raises(CompileError):
            extract_function_call_modifiers(_call(expr), "helper", {})


def test_unique_spelling_without_double_underscores_is_user_keyword():
    cleaned, modifiers = extract_function_call_modifiers(_call("helper(unique=True)"), "helper", {})
    assert modifiers == FunctionCallModifiers()
    assert [kw.arg for kw in cleaned.keywords] == ["unique"]


def test_trace_child_fingerprint_changes_parent_fingerprint():
    trace = FunctionCompilationTrace()
    owner = function_group_owner_scope("ROOT", "r")
    function_id = local_function_id(owner, "leaf", "x:FLOAT")
    materialization = IRFunctionMaterialization(
        function_id, IRFunctionMaterializationMode.SHARED
    )
    with trace.group(owner, {"source": "out = x"}) as frame:
        frame.record_dependency(materialization, "abc")
        assert frame.child_rows == [{
            "owner": function_group_owner_scope(
                "LOCAL_DEF", owner, "leaf", "x:FLOAT"
            ),
            "fingerprint": "abc",
        }]
        first = frame.finish("contract")
    with trace.group(owner, {"source": "out = x"}) as frame:
        frame.record_dependency(materialization, "def")
        second = frame.finish("contract")
    assert first.fingerprint == "552f3cf08c1a25d771bb9cdfb003d40b964377efe451a677cfcef18e7a8c9522"
    assert second.fingerprint
    assert first.fingerprint != second.fingerprint


def test_trace_dependency_requires_materialization_and_missing_fingerprint_is_unproven():
    """The trace accepts no raw owner strings and preserves missing metadata behavior."""
    trace = FunctionCompilationTrace()
    function_id = local_function_id("root", "leaf", "x:FLOAT")
    materialization = IRFunctionMaterialization(
        function_id, IRFunctionMaterializationMode.SHARED
    )
    with trace.group("parent", {}) as frame:
        with pytest.raises(TypeError, match="IRFunctionMaterialization"):
            frame.record_dependency("raw-owner", "abc")
        frame.record_dependency(materialization, "")
        assert frame.freshness_unproven is True
        assert frame.child_rows == []
        assert frame.finish("contract").freshness_unproven is True


def test_trace_marks_unproven_and_detects_cycles():
    trace = FunctionCompilationTrace()
    owner = function_group_owner_scope("ROOT", "r")
    with trace.group(owner, {"source": "x"}) as frame:
        frame.mark_unproven("local")
        assert frame.finish("contract").freshness_unproven is True
    with pytest.raises(CompileError):
        with trace.group(owner, {}):
            with trace.group(owner, {}):
                pass


def test_root_owner_id_validation():
    valid = "0123456789abcdef0123456789abcdef"
    assert validate_root_owner_id(valid) == valid
    for invalid in ("", "not-a-uuid", 123):
        with pytest.raises(CompileError):
            validate_root_owner_id(invalid)
    assert FUNCTION_ROOT_OWNER_ID_PROP == "nodeforge_function_root_owner_id"

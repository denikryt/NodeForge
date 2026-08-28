import ast

import pytest

from NodeForge.errors import CompileError
from NodeForge.function_instances import (
    FUNCTION_ROOT_OWNER_ID_PROP,
    FunctionCallModifiers,
    FunctionCompilationTrace,
    extract_function_call_modifiers,
    function_group_owner_scope,
    instance_key_for,
    library_callee_identity,
    local_callee_identity,
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


def test_unique_modifier_is_removed_and_tracks_explicit_false():
    cleaned, modifiers = extract_function_call_modifiers(DummyCompiler(), _call("helper(x, __unique__=False)"), "helper")
    assert isinstance(modifiers, FunctionCallModifiers)
    assert modifiers.unique is False
    assert modifiers.unique_was_explicit is True
    assert [kw.arg for kw in cleaned.keywords] == []


def test_unique_modifier_accepts_only_exact_compile_time_bool():
    _, modifiers = extract_function_call_modifiers(DummyCompiler({"flag": True}), _call("helper(__unique__=flag)"), "helper")
    assert modifiers.unique is True
    for expr in ("helper(__unique__=1)", "helper(__unique__='yes')", "helper(__unique__=x)"):
        with pytest.raises(CompileError):
            extract_function_call_modifiers(DummyCompiler(), _call(expr), "helper")


def test_unique_spelling_without_double_underscores_is_user_keyword():
    cleaned, modifiers = extract_function_call_modifiers(DummyCompiler(), _call("helper(unique=True)"), "helper")
    assert modifiers == FunctionCallModifiers()
    assert [kw.arg for kw in cleaned.keywords] == ["unique"]


def test_owner_scope_and_occurrence_key_are_deterministic_and_owner_sensitive():
    owner_a = function_group_owner_scope("ROOT", "root-a")
    owner_b = function_group_owner_scope("ROOT", "root-b")
    callee = local_callee_identity(owner_a, "helper", "x:FLOAT")
    assert instance_key_for(owner_a, callee, 0) == instance_key_for(owner_a, callee, 0)
    assert instance_key_for(owner_a, callee, 0) != instance_key_for(owner_a, callee, 1)
    assert instance_key_for(owner_a, callee, 0) != instance_key_for(owner_b, callee, 0)


def test_library_callee_identity_uses_core_sentinel_for_unpacked_entries():
    assert library_callee_identity("functions", "", "helper") == library_callee_identity("functions", None, "helper")


def test_trace_child_fingerprint_changes_parent_fingerprint():
    trace = FunctionCompilationTrace()
    owner = function_group_owner_scope("ROOT", "r")
    with trace.group(owner, {"source": "out = x"}) as frame:
        frame.record_child(function_group_owner_scope("LOCAL_DEF", owner, "leaf", "x:FLOAT"), "abc")
        first = frame.finish("contract")
    with trace.group(owner, {"source": "out = x"}) as frame:
        frame.record_child(function_group_owner_scope("LOCAL_DEF", owner, "leaf", "x:FLOAT"), "def")
        second = frame.finish("contract")
    assert first.fingerprint
    assert second.fingerprint
    assert first.fingerprint != second.fingerprint


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

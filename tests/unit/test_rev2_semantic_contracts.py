"""Regression contracts for Issue 48 semantic fixes Rev2.

Tests marked strict xfail document defects on baseline 75f75ef.  Remove the
marker at the checkpoint which implements the corresponding contract.
"""

import pytest

from NodeForge.compiler_identities import ModuleId
from NodeForge.errors import CompileError
from NodeForge.semantic.source_modules import (
    parse_source_module, resolve_source_module, resolve_source_call_graph,
)


def _resolved(source):
    return resolve_source_module(parse_source_module(source, ModuleId("ROOT_TEXT", "rev2-contract")))


def _edges(source):
    return {(a.name, b.name) for a, b in resolve_source_call_graph(_resolved(source)).edges}


def test_graph_scan_does_not_mutate_module_mutable_constant():
    resolved = _resolved('''L = [1]
def a():
    L.append(2)
    return 1
def b():
    return 2''')
    before = list(resolved.preprocessed.final_compile_time.values["L"])
    first = resolve_source_call_graph(resolved)
    second = resolve_source_call_graph(resolved)
    assert first == second
    assert resolved.preprocessed.final_compile_time.values["L"] == before


def test_unrelated_function_does_not_influence_other_function_edges():
    source = '''L = [1]
def a():
    L.append(2)
    return 1
def b():
    if len(L) == 2:
        return g()
    return 2
def g():
    return 3'''
    assert ("b", "g") not in _edges(source)


def test_runtime_if_sibling_arm_cannot_see_other_arm_append():
    source = '''L = [1]
def f(flag: Bool):
    if flag:
        L.append(2)
    else:
        if len(L) == 2:
            return g()
    return 1
def g():
    return 2'''
    assert ("f", "g") not in _edges(source)


def test_runtime_loop_multiple_iterations_preserve_possible_edges():
    source = '''L = [1]
def f(count: Int):
    for i in repeat_range(count):
        if len(L) == 3:
            g()
        L.append(2)
    return 1
def g():
    return 2'''
    assert ("f", "g") in _edges(source)


def test_read_only_runtime_branch_does_not_add_false_edge():
    source = '''L = [1]
def f(flag: Bool):
    if flag:
        value = 2
    else:
        if len(L) == 2:
            return g()
    return 1
def g():
    return 2'''
    assert ("f", "g") not in _edges(source)


def test_detached_function_state_preserves_aliases_and_nested_cycles():
    from NodeForge.semantic.compile_time import detached_compile_time_mapping
    inner = [1]
    outer = [inner]
    outer.append(outer)
    source = {"A": outer, "B": outer, "C": inner}
    detached = detached_compile_time_mapping(source)
    assert detached["A"] is detached["B"]
    assert detached["A"] is not source["A"]
    assert detached["A"][0] is detached["C"]
    assert detached["A"][1] is detached["A"]
    detached["C"].append(7)
    assert inner == [1]


def test_foreign_constants_are_not_deepcopied_or_shared_with_function():
    from NodeForge.semantic.compile_time import detached_compile_time_mapping
    class ForeignResource:
        def __deepcopy__(self, memo):
            raise AssertionError("must never invoke external deepcopy")
    original = ForeignResource()
    detached = detached_compile_time_mapping({"foreign": original})
    assert detached["foreign"] is not original
    assert type(detached["foreign"]).__name__ == "_UnsupportedConstEvalValue"


def test_post_join_possible_append_keeps_reachable_call():
    source = """L = [1]
def f(flag: Bool):
    if flag:
        L.append(2)
    if len(L) == 2:
        return g()
    return 1
def g():
    return 2"""
    assert ("f", "g") in _edges(source)


def test_runtime_if_sibling_order_cannot_create_false_recursion():
    first = """L = [1]
def unused(flag: Bool):
    if flag:
        L.append(2)
    else:
        if len(L) == 2:
            return unused(flag)
    return 0
def ok():
    return 1"""
    reversed_arms = first.replace(
        "if flag:\n        L.append(2)\n    else:\n        if len(L) == 2:\n            return unused(flag)",
        "if flag:\n        if len(L) == 2:\n            return unused(flag)\n    else:\n        L.append(2)",
    )
    assert not _edges(first)
    assert not _edges(reversed_arms)


def test_speculative_alias_append_invalidates_outer_mutable_ancestor():
    source = """L = [[1]]
def f(flag: Bool):
    A = L[0]
    if flag:
        A.append(2)
    if len(L[0]) == 2:
        return g()
    return 1
def g():
    return 2"""
    assert ("f", "g") in _edges(source)


def test_read_only_if_join_retains_inherited_list_fact():
    source = """L = [1]
def f(flag: Bool):
    if flag:
        x = 1
    else:
        x = 2
    if len(L) == 2:
        return g()
    return 1
def g():
    return 2"""
    assert ("f", "g") not in _edges(source)


def test_runtime_loop_can_mutate_scalar_before_reaching_call():
    source = """V = 0
def f(n: Int):
    for i in repeat_range(n):
        if V == 2:
            return g()
        V = V + 1
    return 1
def g():
    return 2"""
    assert ("f", "g") in _edges(source)


def test_runtime_loop_read_only_does_not_invent_mutable_dependencies():
    source = """L = [1]
def f(n: Int):
    for i in repeat_range(n):
        x = i + 1
    if len(L) == 2:
        return g()
    return 1
def g():
    return 2"""
    assert ("f", "g") not in _edges(source)


def test_runtime_loop_may_have_zero_iterations_without_publishing_append():
    source = """L = [1]
def f(n: Int):
    for i in repeat_range(n):
        L.append(2)
    if len(L) == 2:
        return g()
    return 1
def g():
    return 2"""
    assert ("f", "g") in _edges(source)


def _compiled_defaults(source):
    from NodeForge.compiler_identities import GroupCompilationIdentity
    from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
    from NodeForge.semantic.source_callable_session import SourceCallableSession
    from NodeForge.semantic.group import analyze_group_source
    environment = ResolvedEnvironment({
        name: ResolvedCatalog(name, {})
        for name in ("functions", "examples", "local")
    })
    session = SourceCallableSession(resolved_environment=environment)
    analyze_group_source(
        source,
        compilation_identity=GroupCompilationIdentity(
            None, "ROOT/ct-default", "ROOT/ct-default", "ROOT/ct-default",
        ),
        resolved_environment=environment,
        source_callable_session=session,
    )
    return {
        item.contract.function_id.name: item.group.interface.inputs[0].default
        for item in session.prepared.values()
        if item.group.interface.inputs
    }


def test_function_default_uses_final_module_constant_before_and_after_call():
    before = """K = 1
x = f()
def f(value: Int = K) -> Int:
    return value
K = 9"""
    after = """K = 1
def f(value: Int = K) -> Int:
    return value
K = 9
x = f()"""
    assert _compiled_defaults(before) == {"f": 9}
    assert _compiled_defaults(after) == {"f": 9}


def test_module_final_default_is_not_changed_by_local_write_in_caller():
    source = """K = 1
def caller() -> Int:
    K = 100
    return f()
def f(value: Int = K) -> Int:
    return value
K = 9
x = caller()"""
    assert _compiled_defaults(source)["f"] == 9


def test_each_compilation_attempt_uses_its_own_final_module_values():
    original = """K = 9
def f(value: Int = K) -> Int:
    return value
x = f()"""
    revised = original.replace("K = 9", "K = 7")
    assert _compiled_defaults(original) == {"f": 9}
    assert _compiled_defaults(revised) == {"f": 7}


@pytest.mark.parametrize("body,expected", [
    ("def broken(x):\n    return x", "requires an explicit type"),
    ("def broken(x: Int = 2.5):\n    return x", "invalid default"),
    ("def broken(x: Int):\n    x = x + 1", "final top-level return"),
    ("def broken(x: Int):\n    return ()", "empty tuple"),
    ("def broken(x: Int):\n    output('Value', x)\n    return x", "obsolete group interface"),
    ("def broken(x: Int):\n    x = x + 1\n    panel([x], name='P')\n    return x", "precede executable statements"),
    ("def broken(x: Int) -> Vector['Label']:\n    return x", "unlabelled runtime type"),
])
def test_pure_declaration_contract_rejects_invalid_source_without_backend(body, expected):
    import ast
    from NodeForge.semantic.source_definition_contract import validate_source_definition_contract
    fn = ast.parse(body).body[0]
    with pytest.raises(CompileError, match=expected) as caught:
        validate_source_definition_contract(
            fn, owner_scope="source", declaration_owner="source",
            module_origin="some/module.nf",
        )
    assert "broken()" in str(caught.value)
    assert "line" in str(caught.value)
    assert "some/module.nf" in str(caught.value)


def test_pure_contract_does_not_eagerly_check_runtime_body_operations():
    import ast
    from NodeForge.semantic.source_definition_contract import validate_source_definition_contract
    fn = ast.parse("def f(x: Float = 1.0) -> Float:\n    return unknown_runtime_function(x)").body[0]
    signature, result = validate_source_definition_contract(
        fn, owner_scope="s", declaration_owner="d",
    )
    assert signature.logical_inputs[0].has_default
    assert len(result.elements) == 1


def test_unused_invalid_default_fails_before_any_function_preparation():
    from NodeForge.compiler_identities import GroupCompilationIdentity
    from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
    from NodeForge.semantic.group import analyze_group_source
    from NodeForge.semantic.source_callable_session import SourceCallableSession
    env = ResolvedEnvironment({
        name: ResolvedCatalog(name, {}) for name in ("functions", "examples", "local")
    })
    session = SourceCallableSession(resolved_environment=env)
    source = """def ok() -> Int:
    return 1
def unused(value: Int = 1.5) -> Int:
    return value
x = ok()"""
    with pytest.raises(CompileError, match="unused.*invalid default"):
        analyze_group_source(
            source,
            compilation_identity=GroupCompilationIdentity(
                None, "ROOT/invalid", "ROOT/invalid", "ROOT/invalid",
            ),
            resolved_environment=env, source_callable_session=session,
        )
    assert not session.prepared


def test_valid_unused_definition_does_not_prepare_runtime_or_extensions():
    from NodeForge.compiler_identities import GroupCompilationIdentity
    from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
    from NodeForge.semantic.group import analyze_group_source
    from NodeForge.semantic.source_callable_session import SourceCallableSession
    env = ResolvedEnvironment({
        name: ResolvedCatalog(name, {}) for name in ("functions", "examples", "local")
    })
    session = SourceCallableSession(resolved_environment=env)
    source = """def ok() -> Int:
    return 1
def unused(value: Float) -> Float:
    return no_such_extension(value)
x = ok()"""
    analyze_group_source(
        source,
        compilation_identity=GroupCompilationIdentity(
            None, "ROOT/lazy", "ROOT/lazy", "ROOT/lazy",
        ),
        resolved_environment=env, source_callable_session=session,
    )
    assert {item.contract.function_id.name for item in session.prepared.values()} == {"ok"}


def test_imported_module_preflight_validates_unselected_definition():
    from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
    from NodeForge.semantic.source_callable_session import SourceCallableSession
    env = ResolvedEnvironment({
        name: ResolvedCatalog(name, {}) for name in ("functions", "examples", "local")
    })
    snapshot = parse_source_module(
        "def good() -> Int:\n    return 1\ndef bad(x: Int = 1.5) -> Int:\n    return x",
        ModuleId("LOCAL", "/module/selected.nf"),
    )
    session = SourceCallableSession(resolved_environment=env)
    with pytest.raises(CompileError, match="bad.*invalid default"):
        session.preflight_module_contracts(snapshot)
    assert not session.prepared

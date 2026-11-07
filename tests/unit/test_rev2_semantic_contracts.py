"""Regression contracts for Issue 48 semantic fixes Rev2.

Tests marked strict xfail document defects on baseline 75f75ef.  Remove the
marker at the checkpoint which implements the corresponding contract.
"""

import pytest

from NodeForge.compiler_identities import ModuleId
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


@pytest.mark.xfail(strict=True, reason="F02: reachable later loop iterations must be considered")
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

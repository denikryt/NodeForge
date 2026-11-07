"""Parsed source-module snapshots shared by compilation and declaration resolution.

A snapshot represents one exact text revision. It retains original AST nodes
(including source offsets) and separates declaration collection from subsequent
compile-time residualization and physical Blender publication.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from ..compiler_identities import ModuleId, SourceDefinitionRef
from ..errors import CompileError
from .parsing import _parse_source_tree
from .compile_time import (
    CompileTimeSnapshot, CompileTimeState, detached_compile_time_mapping,
    merge_runtime_if_compile_time,
)
from .residualization import PreprocessedBody, _preprocess_compile_time, _repeat_range_state_names
from .source_bindings import collect_function_lexical_inventory


@dataclass(frozen=True)
class SourceModuleSnapshot:
    """One validated text revision with its source declarations and statements.

    ``compile_time_candidates`` identifies syntactic assignment candidates;
    the residualizer alone decides which statements have no runtime effect.
    All other AST collections refer to nodes in the same ``tree`` object.
    """

    module_id: ModuleId
    source: str
    tree: ast.Module
    imports: tuple[ast.ImportFrom, ...]
    definitions: Mapping[str, ast.FunctionDef]
    compile_time_candidates: tuple[ast.Assign, ...]
    statements: tuple[ast.stmt, ...]

    def __post_init__(self) -> None:
        """Reject snapshots assembled from inconsistent declaration references."""
        if not isinstance(self.module_id, ModuleId):
            raise TypeError("SourceModuleSnapshot.module_id must be ModuleId")
        if not isinstance(self.source, str) or not isinstance(self.tree, ast.Module):
            raise TypeError("SourceModuleSnapshot needs source text and its AST module")
        if any(fn.name != name or fn not in self.tree.body for name, fn in self.definitions.items()):
            raise ValueError("Snapshot definitions must reference declared AST nodes")
        object.__setattr__(self, "definitions", MappingProxyType(dict(self.definitions)))

    def definition_ref(self, name: str) -> SourceDefinitionRef:
        """Return the stable identity of a declaration in this snapshot."""
        if name not in self.definitions:
            raise CompileError(f"Unknown function {name!r} in module {self.module_id.origin!r}")
        return SourceDefinitionRef(self.module_id, name)



@dataclass(frozen=True)
class ResolvedSourceModule:
    """Declarations and effective runtime statements from one source revision.

    A module becomes executable only when residualization retains statements.
    Function declarations and imports never count as executable statements.
    """

    snapshot: SourceModuleSnapshot
    definition_refs: Mapping[str, SourceDefinitionRef]
    preprocessed: PreprocessedBody
    definition_aliases: Mapping[str, SourceDefinitionRef] = None

    def __post_init__(self) -> None:
        """Freeze resolved identities and reject mixed-source bindings."""
        for name, ref in self.definition_refs.items():
            if not isinstance(ref, SourceDefinitionRef) or ref.module != self.snapshot.module_id or (ref.name != name and name not in (self.definition_aliases or {})):
                raise ValueError("Definition references must use their source module identity")
        object.__setattr__(self, "definition_refs", MappingProxyType(dict(self.definition_refs)))
        object.__setattr__(self, "definition_aliases", MappingProxyType(dict(self.definition_aliases or {})))
        if not isinstance(self.preprocessed, PreprocessedBody):
            raise TypeError("Resolved module requires a preprocessed source body")

    @property
    def has_executable_statements(self) -> bool:
        """Return whether any runtime statements remain after preprocessing."""
        return bool(self.preprocessed.statements)


def collect_source_definition_aliases(
    statements: tuple[ast.stmt, ...] | list[ast.stmt],
    definitions: Mapping[str, ast.FunctionDef],
    *, require_local_assignment_order: bool = False,
) -> tuple[tuple[ast.stmt, ...], Mapping[str, str]]:
    """Collect lexical ``alias = definition`` declarations without runtime effects.

    The definitions are already collected, so aliases can refer to declarations
    later in the source. Only single-name assignments are treated as aliases.
    An ordinary runtime assignment remains in the executable statement stream.
    """
    canonical = {name: fn.name for name, fn in definitions.items()}
    aliases = {}
    alias_declarations = {}
    runtime = []
    for statement in statements:
        if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name) and isinstance(statement.value, ast.Name)
                and statement.value.id in canonical):
            alias = statement.targets[0].id
            if alias in canonical or alias in aliases:
                raise CompileError(f"Duplicate function alias {alias!r} at line {statement.lineno}")
            aliases[alias] = canonical[statement.value.id]
            alias_declarations[alias] = (statement.lineno, statement.col_offset)
            canonical[alias] = canonical[statement.value.id]
        else:
            runtime.append(statement)
    if require_local_assignment_order:
        # Source-definition aliases are static declarations, but they remain
        # local *bindings*.  Removing their CT-only assignments must not make
        # a read/call that occurs before the declaration look initialized.
        for statement in runtime:
            for node in ast.walk(statement):
                if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
                    continue
                assigned_at = alias_declarations.get(node.id)
                if assigned_at is not None and (node.lineno, node.col_offset) < assigned_at:
                    raise CompileError(
                        f"Local alias {node.id!r} used before assignment "
                        f"at line {node.lineno}, column {node.col_offset + 1}"
                    )
    return tuple(runtime), MappingProxyType(aliases)


@dataclass(frozen=True)
class SourceCallGraph:
    """Resolved intra-module call edges with deterministic entry selection."""

    vertices: tuple[SourceDefinitionRef, ...]
    edges: tuple[tuple[SourceDefinitionRef, SourceDefinitionRef], ...]

    @property
    def entries(self) -> tuple[SourceDefinitionRef, ...]:
        """Return only definitions without incoming intra-module references."""
        incoming = {target for _, target in self.edges}
        return tuple(vertex for vertex in self.vertices if vertex not in incoming)


def resolve_source_call_graph(resolved: ResolvedSourceModule) -> SourceCallGraph:
    """Resolve function edges through the same CallableEnvironment name resolver.

    Imports and top-level calls are not edges. Locally declared aliases share
    the canonical SourceDefinitionRef and known compile-time branches are pruned.
    """
    from .call_resolution import CallableEnvironment, CallableKind, UNRESOLVED, resolve_simple_callable
    from .builtin_calls import INPUT_DECLARATION_BUILTIN_NAMES, IR_CAPABLE_BUILTIN_NAMES
    from .consteval import ConstEvalUnavailable, _const_eval
    from .source_bindings import collect_function_lexical_inventory

    snapshot = resolved.snapshot
    definitions = dict(snapshot.definitions)
    definitions.update({alias: snapshot.definitions[target.name] for alias, target in resolved.definition_aliases.items()})
    base = CallableEnvironment(
        callable_builtins=frozenset(INPUT_DECLARATION_BUILTIN_NAMES | IR_CAPABLE_BUILTIN_NAMES),
        local_functions=definitions, imported_functions={},
        local_definition_refs=resolved.definition_refs,
    )
    edges = set()

    def container_ids(value, seen=None):
        """Collect inherited mutable object origins, including nested aliases."""
        seen = set() if seen is None else seen
        if type(value) not in (list, tuple) or id(value) in seen:
            return seen
        seen.add(id(value))
        for child in value:
            container_ids(child, seen)
        return seen

    def clone_path(values, provenance):
        """Fork one speculative CT path without sharing mutable containers."""
        memo = {}
        copy_values = detached_compile_time_mapping(values, copy_memo=memo)
        child_provenance = {}
        for parent_id, cloned in memo.items():
            if type(cloned) is list:
                child_provenance[id(cloned)] = provenance.get(parent_id, parent_id)
        for value in copy_values.values():
            for candidate in container_ids(value):
                child_provenance.setdefault(candidate, candidate)
        return copy_values, child_provenance, memo

    def join_paths(before, left, right, left_memo, right_memo, changed):
        """Keep only proved facts after alternative execution paths.

        Mutable facts survive only if both exits retain the exact cloned
        binding from entry and no observed CT append affected any descendant.
        Comparing cloned values is insufficient: equality loses mutation
        provenance and could make an impossible edge appear safe.
        """
        merged = dict(merge_runtime_if_compile_time(
            CompileTimeSnapshot(before), CompileTimeState(left), CompileTimeState(right),
        ).values)
        for name, original in before.items():
            if type(original) not in (list, tuple):
                continue
            origin = id(original)
            if name not in left or name not in right:
                continue
            if left.get(name) is not left_memo.get(origin):
                continue
            if right.get(name) is not right_memo.get(origin):
                continue
            if not container_ids(original).isdisjoint(changed):
                continue
            merged[name] = original
        return CompileTimeSnapshot(merged)

    def visit(node, owner, environment, values, runtime_range_names, provenance, changes, lexical_owned, callable_aliases):
        """Record resolved calls in reachable expression/statement subtrees."""
        if isinstance(node, ast.FunctionDef):
            return None
        if isinstance(node, ast.If):
            visit(node.test, owner, environment, values, runtime_range_names, provenance, changes, lexical_owned, callable_aliases)
            try:
                branch = _const_eval(node.test, values)
            except (ConstEvalUnavailable, CompileError):
                branch = None
            if type(branch) is bool:
                # A known branch executes with a sequential CT state; its writes
                # remain visible to later statements of the same lexical block.
                return visit_block(
                    node.body if branch else node.orelse,
                    owner, environment, values, runtime_range_names, provenance, changes, lexical_owned, callable_aliases,
                )
            # An unknown condition forks *both* arms from one entry state.
            # Neither arm may mutate the other's snapshot or module state.
            left, left_prov, left_memo = clone_path(values, provenance)
            right, right_prov, right_memo = clone_path(values, provenance)
            left_changed, right_changed = set(), set()
            left_exit = visit_block(
                node.body, owner, environment, left, runtime_range_names,
                left_prov, left_changed, lexical_owned, callable_aliases,
            )
            right_exit = visit_block(
                node.orelse, owner, environment, right, runtime_range_names,
                right_prov, right_changed, lexical_owned, callable_aliases,
            )
            changed = left_changed | right_changed
            changes.update(changed)
            return join_paths(
                values, left_exit.values, right_exit.values,
                left_memo, right_memo, changed,
            )
        if isinstance(node, ast.For):
            visit(node.iter, owner, environment, values, runtime_range_names, provenance, changes, lexical_owned, callable_aliases)
            # A for-target is a lexical binding, never a read of an outer
            # same-named module constant. For a known compile-time iterable,
            # evaluate the retained body with each actual item, as the body
            # lowerer does when unrolling. Runtime iteration has no constant
            # item and must conservatively retain either branch of an if.
            runtime_repeat = (
                isinstance(node.iter, ast.Call)
                and isinstance(node.iter.func, ast.Name)
                and node.iter.func.id == "repeat_range"
            )
            items = None
            if not runtime_repeat:
                try:
                    items = _const_eval(node.iter, values)
                except ConstEvalUnavailable:
                    pass
            target_names = {
                target.id for target in ast.walk(node.target)
                if isinstance(target, ast.Name)
            }
            scoped_owned = frozenset(lexical_owned) | target_names
            scoped_aliases = frozenset(callable_aliases) - target_names
            if isinstance(node.target, ast.Name) and isinstance(items, (tuple, list)):
                # Static unrolling uses one evolving CT state, not an
                # independent copy of the module state per iteration. The
                # function-body lowerer likewise executes the iterations in
                # order and publishes assignments made by earlier iterations.
                iteration_values = dict(values)
                loop_name = node.target.id
                original_binding = iteration_values.get(loop_name)
                had_original_binding = loop_name in iteration_values
                for item in items:
                    iteration_values[loop_name] = item
                    iteration_values = dict(visit_block(
                        node.body, owner, environment, iteration_values,
                        runtime_range_names, provenance, changes, scoped_owned, scoped_aliases,
                    ).values)
                # The loop index is a lexical binding, not a replacement for
                # a surrounding module constant. Other CT assignments are
                # deliberately retained for the next statement after the loop.
                if had_original_binding:
                    iteration_values[loop_name] = original_binding
                else:
                    iteration_values.pop(loop_name, None)
                if node.orelse:
                    iteration_values = dict(visit_block(
                        node.orelse, owner, environment, iteration_values,
                        runtime_range_names, provenance, changes, lexical_owned, callable_aliases,
                    ).values)
                return CompileTimeSnapshot(iteration_values)
            else:
                # Runtime-unknown loops can execute zero, one, or arbitrarily
                # many times. A single concrete visit misses dependencies that
                # become reachable after subsequent iterations. Widen by
                # intersecting the zero-iteration facts with one speculative
                # body execution, then revisit while CT facts can still change.
                # Facts only disappear; no finite unrolling bound is assumed.
                abstract_values = dict(values)
                while True:
                    zero, _, zero_memo = clone_path(abstract_values, provenance)
                    once, once_prov, once_memo = clone_path(abstract_values, provenance)
                    for target_name in target_names:
                        once.pop(target_name, None)
                    changed = set()
                    once_exit = dict(visit_block(
                        node.body, owner, environment, once, runtime_range_names,
                        once_prov, changed, scoped_owned, scoped_aliases,
                    ).values)
                    for target_name in target_names:
                        if target_name in abstract_values:
                            once_exit[target_name] = once_memo.get(
                                id(abstract_values[target_name]),
                                abstract_values[target_name],
                            )
                        else:
                            once_exit.pop(target_name, None)
                    changes.update(changed)
                    joined = join_paths(
                        abstract_values, zero, once_exit,
                        zero_memo, once_memo, changed,
                    )
                    next_values = dict(joined.values)
                    if len(next_values) == len(abstract_values) and all(
                        (next_values[name] is value)
                        or (type(value) in (int, bool, str)
                            and type(next_values[name]) is type(value)
                            and next_values[name] == value)
                        for name, value in abstract_values.items()
                    ):
                        abstract_values = next_values
                        break
                    abstract_values = next_values
                if node.orelse:
                    zero, _, zero_memo = clone_path(abstract_values, provenance)
                    one, one_prov, one_memo = clone_path(abstract_values, provenance)
                    else_changed = set()
                    else_exit = visit_block(
                        node.orelse, owner, environment, one,
                        runtime_range_names, one_prov, else_changed, lexical_owned, callable_aliases,
                    )
                    changes.update(else_changed)
                    abstract_values = dict(join_paths(
                        abstract_values, zero, else_exit.values,
                        zero_memo, one_memo, else_changed,
                    ).values)
                return CompileTimeSnapshot(abstract_values)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            # A local runtime variable (including parameters) blocks outer
            # callables.  Local source aliases retain their canonical identity.
            if (node.func.id in lexical_owned
                    and node.func.id not in callable_aliases):
                return None
            target = resolve_simple_callable(node.func.id, environment)
            if target is not UNRESOLVED and target.kind is CallableKind.LOCAL_FUNCTION:
                if not isinstance(target.target, SourceDefinitionRef):
                    raise CompileError("Internal error: graph callable lost its canonical source identity")
                edges.add((owner, target.target))
        for child in ast.iter_child_nodes(node):
            visit(child, owner, environment, values, runtime_range_names, provenance, changes, lexical_owned, callable_aliases)
        return None

    def visit_block(
        statements, owner, environment, initial_values, runtime_range_names,
        provenance, changes, lexical_owned, callable_aliases,
    ):
        """Visit statements in order using the established CT residualizer.

        Residualization of an entire block conservatively invalidates writes
        inside a retained ``if``. A selected compile-time branch, however,
        publishes its writes to subsequent statements. Process each statement
        against the preceding state and recurse through that selected branch;
        this is the same CT evaluator, not a second expression interpreter.
        """
        values = (initial_values if isinstance(initial_values, CompileTimeSnapshot)
                  else CompileTimeSnapshot(initial_values))
        # Repeat-zone state assignments have to remain runtime values even
        # when their initializer is a compile-time literal. The residualizer
        # normally computes this over the whole block, not each statement.
        preserve_names = _repeat_range_state_names(
            statements, runtime_range_names=runtime_range_names,
        )
        for statement in statements:
            append_journal = []
            residual = _preprocess_compile_time(
                (statement,), initial_compile_time=values,
                runtime_range_names=runtime_range_names,
                preserve_names=preserve_names,
                append_journal=append_journal,
            )
            for target, _old_length in append_journal:
                changes.add(provenance.get(id(target), id(target)))
            branch_exit = None
            for retained, before in zip(residual.statements, residual.compile_time_before):
                branch_exit = visit(
                    retained, owner, environment, before.values, runtime_range_names,
                    provenance, changes, lexical_owned, callable_aliases,
                )
            values = branch_exit if branch_exit is not None else residual.final_compile_time
        return values

    for name, fn in snapshot.definitions.items():
        body, aliases = collect_source_definition_aliases(
            fn.body, definitions, require_local_assignment_order=True,
        )
        local_functions = dict(definitions)
        local_refs = dict(resolved.definition_refs)
        for alias, canonical in aliases.items():
            local_functions[alias] = snapshot.definitions[canonical]
            local_refs[alias] = snapshot.definition_ref(canonical)
        env = CallableEnvironment(base.callable_builtins, local_functions, {},
                                  local_definition_refs=local_refs)
        inventory = collect_function_lexical_inventory(fn, local_refs)
        initial = detached_compile_time_mapping(resolved.preprocessed.final_compile_time.values)
        runtime_range_names = tuple(arg.arg for arg in fn.args.args)
        for local in inventory.owned_names:
            initial.pop(local, None)
        provenance = {}
        for value in initial.values():
            for origin in container_ids(value):
                provenance[origin] = origin
        visit_block(
            body, snapshot.definition_ref(name), env, initial, runtime_range_names,
            provenance, set(), inventory.owned_names,
            frozenset(inventory.source_aliases),
        )
    ordered_edges = tuple(sorted(edges, key=lambda pair: (pair[0].name, pair[1].name)))
    vertices = tuple(snapshot.definition_ref(name) for name in snapshot.definitions)
    graph = SourceCallGraph(vertices, ordered_edges)
    adj = {node: [] for node in vertices}
    for src, dst in ordered_edges:
        adj[src].append(dst)
    active = set()
    finished = set()

    def walk(node, chain):
        """Reject both direct and mutual recursion with the resolved chain."""
        if node in active:
            cycle = chain[chain.index(node):]
            raise CompileError("Recursive module definitions: " + " -> ".join(x.name for x in cycle))
        if node in finished:
            return
        active.add(node)
        for child in adj[node]:
            walk(child, chain + [child])
        active.remove(node)
        finished.add(node)

    for vertex in vertices:
        walk(vertex, [vertex])
    return graph


def resolve_source_module(
    snapshot: SourceModuleSnapshot,
    *,
    inherited_definitions: Mapping[str, ast.FunctionDef] | None = None,
) -> ResolvedSourceModule:
    """Collect all declarations, then preprocess only the module's statements.

    Collision checks against imported and reserved names remain with the
    frontend's existing authoritative binding validation before this call.
    """
    definitions = dict(inherited_definitions or {})
    for name, fn in snapshot.definitions.items():
        if name in definitions and definitions[name] is not fn:
            raise CompileError(
                f"Duplicate function {name!r} at line {fn.lineno}, "
                f"column {fn.col_offset + 1}"
            )
        definitions[name] = fn
    runtime, aliases = collect_source_definition_aliases(snapshot.statements, definitions)
    refs = {name: SourceDefinitionRef(snapshot.module_id, name) for name in definitions}
    for alias, canonical in aliases.items():
        if alias in refs:
            raise CompileError(f"Source alias {alias!r} conflicts with a declared function")
        refs[alias] = SourceDefinitionRef(snapshot.module_id, canonical)
    preprocessed = _preprocess_compile_time(runtime)
    return ResolvedSourceModule(snapshot, refs, preprocessed, {alias: refs[alias] for alias in aliases})


def parse_source_module(source: str, module_id: ModuleId) -> SourceModuleSnapshot:
    """Parse one exact source revision and collect all declarations before lowering.

    Imports and definitions are separated from statements without relocating
    them or creating synthetic source. Duplicate declarations fail with the
    second declaration's original source position.
    """
    if not isinstance(module_id, ModuleId):
        raise TypeError("module_id must be ModuleId")
    tree = _parse_source_tree(source)
    from .function_signatures import validate_function_parameters
    # Interface declarations were specific to the removed generated-root DSL.
    # Validate once at source capture, before imports, residualization or IR.
    from .builtin_calls import INPUT_DECLARATION_BUILTIN_NAMES
    obsolete = set(INPUT_DECLARATION_BUILTIN_NAMES) | {'output'}
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.ImportFrom)):
            continue
        for node in ast.walk(statement):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in obsolete):
                raise CompileError(
                    f"Unsupported legacy .nf interface {node.func.id}() "
                    f"at line {node.lineno}, column {node.col_offset + 1}"
                )
    imports = []
    functions = {}
    assignments = []
    statements = []
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom):
            imports.append(statement)
        elif isinstance(statement, ast.FunctionDef):
            validate_function_parameters(statement)
            if statement.name in functions:
                raise CompileError(
                    f"Duplicate function {statement.name!r} at "
                    f"line {statement.lineno}, column {statement.col_offset + 1}"
                )
            functions[statement.name] = statement
        else:
            statements.append(statement)
            if isinstance(statement, ast.Assign):
                assignments.append(statement)
    return SourceModuleSnapshot(
        module_id=module_id, source=source, tree=tree,
        imports=tuple(imports), definitions=functions,
        compile_time_candidates=tuple(assignments), statements=tuple(statements),
    )


__all__ = [
    "ResolvedSourceModule", "SourceModuleSnapshot", "SourceCallGraph",
    "collect_source_definition_aliases", "resolve_source_call_graph",
    "parse_source_module", "resolve_source_module",
]

"""Incremental executable dependency checks for NodeForge module ownership."""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_NAME = "NodeForge"
_IGNORED_TOP_LEVEL = {"tests", "dev", "plans", "examples", "erosion_study", "local"}


def _production_python_files(root: Path = PACKAGE_ROOT) -> list[Path]:
    """Return production Python sources that participate in internal dependencies."""
    files = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if any(part in {".git", "__pycache__", ".pytest_cache"} for part in relative.parts):
            continue
        if relative.parts and relative.parts[0] in _IGNORED_TOP_LEVEL:
            continue
        files.append(path)
    return sorted(files)


def _module_name(path: Path, root: Path = PACKAGE_ROOT) -> str:
    """Map one package source path to its fully qualified module name."""
    relative = path.relative_to(root)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = path.stem
    suffix = ".".join(parts)
    return PACKAGE_NAME if not suffix else f"{PACKAGE_NAME}.{suffix}"


def _module_scope_import_nodes(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """Collect imports executed at module initialization, excluding function/class bodies."""
    imports: list[ast.Import | ast.ImportFrom] = []

    def visit_statements(statements: list[ast.stmt]) -> None:
        for statement in statements:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                imports.append(statement)
                continue
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(statement, field, None)
                if isinstance(nested, list):
                    visit_statements(nested)
            if isinstance(statement, ast.Try):
                for handler in statement.handlers:
                    visit_statements(handler.body)
            if isinstance(statement, ast.Match):
                for case in statement.cases:
                    visit_statements(case.body)

    visit_statements(tree.body)
    return imports


def _resolve_import_targets(
    node: ast.Import | ast.ImportFrom,
    *,
    current_module: str,
    is_package: bool,
    known_modules: set[str],
) -> set[str]:
    """Resolve statically named internal import targets for one AST import node."""
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names if alias.name in known_modules}

    current_package = current_module if is_package else current_module.rsplit(".", 1)[0]
    if node.level:
        parts = current_package.split(".")
        parent_hops = node.level - 1
        if parent_hops >= len(parts):
            return set()
        base = ".".join(parts[: len(parts) - parent_hops])
        target = f"{base}.{node.module}" if node.module else base
    else:
        target = node.module or ""

    submodules = {
        f"{target}.{alias.name}" if target else alias.name
        for alias in node.names
        if (f"{target}.{alias.name}" if target else alias.name) in known_modules
    }
    if submodules:
        return submodules
    if node.module is not None and target in known_modules:
        return {target}
    return set()


def _build_import_graph(*, module_scope_only: bool) -> dict[str, set[str]]:
    """Build a static graph of resolvable NodeForge imports."""
    files = _production_python_files()
    paths = {_module_name(path): path for path in files}
    known_modules = set(paths)
    graph = {module: set() for module in paths}
    for module, path in paths.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        nodes = _module_scope_import_nodes(tree) if module_scope_only else [
            node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        for node in nodes:
            graph[module].update(
                _resolve_import_targets(
                    node,
                    current_module=module,
                    is_package=path.name == "__init__.py",
                    known_modules=known_modules,
                )
            )
    return graph


def _find_path(graph: dict[str, set[str]], start: str, forbidden_prefix: str) -> list[str] | None:
    """Return one dependency path from start into a forbidden module prefix."""
    queue = deque([(start, [start])])
    visited = {start}
    while queue:
        module, path = queue.popleft()
        for target in graph.get(module, ()):
            next_path = [*path, target]
            if target == forbidden_prefix or target.startswith(f"{forbidden_prefix}."):
                return next_path
            if target not in visited:
                visited.add(target)
                queue.append((target, next_path))
    return None


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return non-trivial strongly connected components for a directed graph."""
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(module: str) -> None:
        nonlocal index
        indices[module] = index
        lowlinks[module] = index
        index += 1
        stack.append(module)
        on_stack.add(module)
        for target in graph.get(module, ()):
            if target not in graph:
                continue
            if target not in indices:
                visit(target)
                lowlinks[module] = min(lowlinks[module], lowlinks[target])
            elif target in on_stack:
                lowlinks[module] = min(lowlinks[module], indices[target])
        if lowlinks[module] != indices[module]:
            return
        component = []
        while True:
            target = stack.pop()
            on_stack.remove(target)
            component.append(target)
            if target == module:
                break
        if len(component) > 1:
            components.append(sorted(component))

    for module in graph:
        if module not in indices:
            visit(module)
    return sorted(components)


def test_dependency_path_helper_accepts_disconnected_layers():
    """Positive: disconnected semantic and Blender nodes have no forbidden path."""
    graph = {"semantic.a": {"root.types"}, "root.types": set(), "blender.x": set()}
    assert _find_path(graph, "semantic.a", "blender") is None


def test_dependency_path_helper_reports_indirect_boundary_violation():
    """Negative: an indirect physical dependency reports the whole path."""
    graph = {"semantic.a": {"root.bridge"}, "root.bridge": {"blender.x"}, "blender.x": set()}
    assert _find_path(graph, "semantic.a", "blender") == ["semantic.a", "root.bridge", "blender.x"]


def test_cycle_helper_accepts_acyclic_graph():
    """Positive: a one-way dependency graph has no SCC violation."""
    assert _strongly_connected_components({"a": {"b"}, "b": {"c"}, "c": set()}) == []


def test_cycle_helper_detects_module_scope_cycle():
    """Negative: a cycle between production modules is detected."""
    assert _strongly_connected_components({"a": {"b"}, "b": {"a"}}) == [["a", "b"]]


def test_new_owner_packages_do_not_introduce_module_scope_cycles():
    """Owner-package scaffolding itself is acyclic before production moves begin."""
    graph = _build_import_graph(module_scope_only=True)
    prefixes = tuple(f"{PACKAGE_NAME}.{name}" for name in ("semantic", "extensions", "blender"))
    modules = {
        module for module in graph
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)
    }
    owner_graph = {
        module: {target for target in targets if target in modules}
        for module, targets in graph.items() if module in modules
    }
    assert _strongly_connected_components(owner_graph) == []


def test_catalog_owner_does_not_depend_on_local_source_or_blender_owners():
    """Positive: pure catalog discovery consumes paths rather than Local/platform state."""
    graph = _build_import_graph(module_scope_only=False)
    assert _find_path(graph, f"{PACKAGE_NAME}.catalog", f"{PACKAGE_NAME}.local_sources") is None
    assert _find_path(graph, f"{PACKAGE_NAME}.catalog", f"{PACKAGE_NAME}.blender") is None


def test_catalog_boundary_helper_detects_reverse_local_dependency():
    """Negative: the graph helper detects a forbidden catalog-to-Local dependency."""
    graph = {"NodeForge.catalog": {"NodeForge.local_sources"}, "NodeForge.local_sources": set()}
    assert _find_path(graph, "NodeForge.catalog", "NodeForge.local_sources") == [
        "NodeForge.catalog", "NodeForge.local_sources"
    ]


def test_resolved_environment_model_does_not_reach_discovery_or_blender():
    """Positive: immutable environment data is isolated from discovery and physical work."""
    graph = _build_import_graph(module_scope_only=False)
    for prefix in (f"{PACKAGE_NAME}.catalog", f"{PACKAGE_NAME}.local_sources", f"{PACKAGE_NAME}.blender"):
        path = _find_path(graph, f"{PACKAGE_NAME}.resolved_environment", prefix)
        assert path is None, " -> ".join(path or ())


def test_environment_model_boundary_helper_detects_discovery_leak():
    """Negative: a model-to-discovery dependency is detected by the architecture guard."""
    graph = {
        "NodeForge.resolved_environment": {"NodeForge.catalog"},
        "NodeForge.catalog": set(),
    }
    assert _find_path(graph, "NodeForge.resolved_environment", "NodeForge.catalog") == [
        "NodeForge.resolved_environment", "NodeForge.catalog"
    ]


def test_root_evaluation_vocabulary_has_no_semantic_ctfe_dependency():
    """Positive: public EvaluationMode vocabulary does not load semantic CTFE implementation."""
    graph = _build_import_graph(module_scope_only=False)
    for forbidden in (
        f"{PACKAGE_NAME}.semantic.consteval",
        f"{PACKAGE_NAME}.semantic.evaluation_resolution",
    ):
        path = _find_path(graph, f"{PACKAGE_NAME}.evaluation_modes", forbidden)
        assert path is None, " -> ".join(path or ())


def test_evaluation_boundary_helper_detects_semantic_implementation_leak():
    """Negative: transitive CTFE dependency from public evaluation vocabulary is detectable."""
    graph = {
        "NodeForge.evaluation_modes": {"NodeForge.bridge"},
        "NodeForge.bridge": {"NodeForge.semantic.consteval"},
        "NodeForge.semantic.consteval": set(),
    }
    assert _find_path(graph, "NodeForge.evaluation_modes", "NodeForge.semantic.consteval") == [
        "NodeForge.evaluation_modes", "NodeForge.bridge", "NodeForge.semantic.consteval"
    ]


def test_compile_time_carrier_owner_is_reusable_without_ctfe_evaluator_dependency():
    """Positive: physical geometry can consume carriers without importing the evaluator."""
    graph = _build_import_graph(module_scope_only=False)
    start = f"{PACKAGE_NAME}.blender.geometry"
    assert _find_path(graph, start, f"{PACKAGE_NAME}.semantic.compile_time") is not None
    path = _find_path(graph, start, f"{PACKAGE_NAME}.semantic.consteval")
    assert path is None, " -> ".join(path or ())


def test_compile_time_carrier_boundary_detects_backend_to_ctfe_leak():
    """Negative: routing physical helpers through the CTFE evaluator is detected."""
    graph = {
        "NodeForge.blender.geometry": {"NodeForge.semantic.consteval"},
        "NodeForge.semantic.consteval": {"NodeForge.semantic.compile_time"},
        "NodeForge.semantic.compile_time": set(),
    }
    assert _find_path(graph, "NodeForge.blender.geometry", "NodeForge.semantic.consteval") == [
        "NodeForge.blender.geometry", "NodeForge.semantic.consteval"
    ]


def test_ctfe_does_not_depend_on_residualization_owner():
    """Positive: value evaluation stays independent from source residualization."""
    graph = _build_import_graph(module_scope_only=False)
    path = _find_path(
        graph,
        f"{PACKAGE_NAME}.semantic.consteval",
        f"{PACKAGE_NAME}.semantic.residualization",
    )
    assert path is None, " -> ".join(path or ())


def test_ctfe_residualization_boundary_detects_reverse_ownership():
    """Negative: a CTFE-to-residualization dependency is an ownership violation."""
    graph = {
        "NodeForge.semantic.consteval": {"NodeForge.semantic.residualization"},
        "NodeForge.semantic.residualization": set(),
    }
    assert _find_path(
        graph, "NodeForge.semantic.consteval", "NodeForge.semantic.residualization"
    ) == ["NodeForge.semantic.consteval", "NodeForge.semantic.residualization"]


def test_pure_source_callable_analysis_does_not_reach_group_orchestration():
    """Positive: pure source-call analysis is independent from recursive group preparation."""
    graph = _build_import_graph(module_scope_only=False)
    path = _find_path(
        graph,
        f"{PACKAGE_NAME}.semantic.source_callables",
        f"{PACKAGE_NAME}.semantic.group",
    )
    assert path is None, " -> ".join(path or ())


def test_source_callable_boundary_helper_detects_group_orchestration_leak():
    """Negative: pure callable analysis reaching group orchestration is detectable."""
    graph = {
        "NodeForge.semantic.source_callables": {"NodeForge.semantic.group"},
        "NodeForge.semantic.group": set(),
    }
    assert _find_path(
        graph, "NodeForge.semantic.source_callables", "NodeForge.semantic.group"
    ) == ["NodeForge.semantic.source_callables", "NodeForge.semantic.group"]


def test_function_instance_identity_owner_does_not_reach_ctfe():
    """Positive: cross-phase function identity/freshness metadata is CTFE-independent."""
    graph = _build_import_graph(module_scope_only=False)
    path = _find_path(
        graph,
        f"{PACKAGE_NAME}.function_instances",
        f"{PACKAGE_NAME}.semantic.consteval",
    )
    assert path is None, " -> ".join(path or ())


def test_function_instance_boundary_helper_detects_ctfe_leak():
    """Negative: a function-instance metadata dependency on CTFE is detectable."""
    graph = {
        "NodeForge.function_instances": {"NodeForge.semantic.consteval"},
        "NodeForge.semantic.consteval": set(),
    }
    assert _find_path(
        graph, "NodeForge.function_instances", "NodeForge.semantic.consteval"
    ) == ["NodeForge.function_instances", "NodeForge.semantic.consteval"]


def test_compiler_facade_delegates_group_population_to_blender_owner():
    """Positive: compiler orchestration reaches physical assembly through the canonical Blender owner."""
    graph = _build_import_graph(module_scope_only=False)
    path = _find_path(
        graph,
        f"{PACKAGE_NAME}.compiler",
        f"{PACKAGE_NAME}.blender.group_assembly",
    )
    assert path is not None


def test_group_assembly_boundary_helper_detects_physical_logic_in_facade():
    """Negative: the dependency helper exposes a facade bypass around the Blender assembly owner."""
    graph = {
        "NodeForge.compiler": {"NodeForge.blender.ir_lowering"},
        "NodeForge.blender.ir_lowering": set(),
    }
    assert _find_path(graph, "NodeForge.compiler", "NodeForge.blender.ir_lowering") == [
        "NodeForge.compiler", "NodeForge.blender.ir_lowering"
    ]


def test_semantic_layer_has_no_transitive_blender_dependency():
    """Positive: frontend semantic modules cannot reach physical Blender owners indirectly."""
    graph = _build_import_graph(module_scope_only=False)
    semantic_prefix = f"{PACKAGE_NAME}.semantic"
    blender_prefix = f"{PACKAGE_NAME}.blender"
    offenders = []
    for module in sorted(graph):
        if module == semantic_prefix or module.startswith(f"{semantic_prefix}."):
            path = _find_path(graph, module, blender_prefix)
            if path is not None:
                offenders.append(" -> ".join(path))
    assert not offenders, "Semantic layer reaches Blender layer:\n" + "\n".join(offenders)


def test_semantic_to_blender_boundary_helper_detects_indirect_leak():
    """Negative: a semantic dependency routed through a root bridge is still rejected."""
    graph = {
        "NodeForge.semantic.analysis": {"NodeForge.bridge"},
        "NodeForge.bridge": {"NodeForge.blender.nodes"},
        "NodeForge.blender.nodes": set(),
    }
    assert _find_path(graph, "NodeForge.semantic.analysis", "NodeForge.blender") == [
        "NodeForge.semantic.analysis", "NodeForge.bridge", "NodeForge.blender.nodes"
    ]


def test_extensions_layer_has_no_transitive_blender_dependency():
    """Positive: package-facing Extension v2 internals cannot depend on physical Blender owners."""
    graph = _build_import_graph(module_scope_only=False)
    extension_prefix = f"{PACKAGE_NAME}.extensions"
    blender_prefix = f"{PACKAGE_NAME}.blender"
    offenders = []
    for module in sorted(graph):
        if module == extension_prefix or module.startswith(f"{extension_prefix}."):
            path = _find_path(graph, module, blender_prefix)
            if path is not None:
                offenders.append(" -> ".join(path))
    assert not offenders, "Extension layer reaches Blender layer:\n" + "\n".join(offenders)


def test_extension_to_blender_boundary_helper_detects_indirect_leak():
    """Negative: a package contract reaching Blender through any root bridge is rejected."""
    graph = {
        "NodeForge.extensions.contracts": {"NodeForge.bridge"},
        "NodeForge.bridge": {"NodeForge.blender.nodes"},
        "NodeForge.blender.nodes": set(),
    }
    assert _find_path(graph, "NodeForge.extensions.contracts", "NodeForge.blender") == [
        "NodeForge.extensions.contracts", "NodeForge.bridge", "NodeForge.blender.nodes"
    ]


def test_compiler_facade_reaches_group_backend_through_blender_owner():
    """Positive: root orchestration reaches physical publication through the Blender package."""
    graph = _build_import_graph(module_scope_only=False)
    path = _find_path(graph, f"{PACKAGE_NAME}.compiler", f"{PACKAGE_NAME}.blender.group_backend")
    assert path is not None


def test_lifecycle_boundary_helper_detects_root_physical_owner_leak():
    """Negative: a resurrected root physical backend is detectable as the wrong owner path."""
    graph = {
        "NodeForge.compiler": {"NodeForge.blender_group_backend"},
        "NodeForge.blender_group_backend": set(),
    }
    assert _find_path(graph, "NodeForge.compiler", "NodeForge.blender_group_backend") == [
        "NodeForge.compiler", "NodeForge.blender_group_backend"
    ]

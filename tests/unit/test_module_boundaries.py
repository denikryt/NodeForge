"""Executable dependency boundaries for the current NodeForge module architecture."""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_NAME = "NodeForge"
_IGNORED_TOP_LEVEL = {"tests", "dev", "plans", "examples", "erosion_study", "local"}

# These are ownership boundaries, not an inventory of implementation files. Internal
# modules may be merged, split, or removed as long as these dependency directions hold.
_FORBIDDEN_REACHABILITY = (
    (f"{PACKAGE_NAME}.semantic", f"{PACKAGE_NAME}.blender"),
    (f"{PACKAGE_NAME}.extensions", f"{PACKAGE_NAME}.blender"),
    (f"{PACKAGE_NAME}.catalog", f"{PACKAGE_NAME}.local_sources"),
    (f"{PACKAGE_NAME}.catalog", f"{PACKAGE_NAME}.blender"),
)


def _production_python_files(root: Path = PACKAGE_ROOT) -> list[Path]:
    """Return production Python sources that participate in internal dependencies."""
    files: list[Path] = []
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

    def visit(statements: list[ast.stmt]) -> None:
        for statement in statements:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                imports.append(statement)
                continue
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(statement, field, None)
                if isinstance(nested, list):
                    visit(nested)
            if isinstance(statement, ast.Try):
                for handler in statement.handlers:
                    visit(handler.body)
            if isinstance(statement, ast.Match):
                for case in statement.cases:
                    visit(case.body)

    visit(tree.body)
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

    targets = {
        f"{target}.{alias.name}" if target else alias.name
        for alias in node.names
        if (f"{target}.{alias.name}" if target else alias.name) in known_modules
    }
    if targets:
        return targets
    if node.module is not None and target in known_modules:
        return {target}
    return set()


def _build_import_graph(*, module_scope_only: bool) -> dict[str, set[str]]:
    """Build a static graph of resolvable NodeForge imports."""
    paths = {_module_name(path): path for path in _production_python_files()}
    known_modules = set(paths)
    graph = {module: set() for module in paths}

    for module, path in paths.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        nodes = (
            _module_scope_import_nodes(tree)
            if module_scope_only
            else [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        )
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


def _find_path(graph: dict[str, set[str]], start: str, target_prefix: str) -> list[str] | None:
    """Return one dependency path from start into a target module prefix."""
    queue = deque([(start, [start])])
    visited = {start}
    while queue:
        module, path = queue.popleft()
        for target in graph.get(module, ()):
            next_path = [*path, target]
            if target == target_prefix or target.startswith(f"{target_prefix}."):
                return next_path
            if target not in visited:
                visited.add(target)
                queue.append((target, next_path))
    return None


def _modules_with_prefix(graph: dict[str, set[str]], prefix: str) -> list[str]:
    """Return graph modules owned by one module/package prefix."""
    return sorted(
        module for module in graph if module == prefix or module.startswith(f"{prefix}.")
    )


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
        component: list[str] = []
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


def _relative_import_base(
    current_module: str,
    *,
    is_package: bool,
    level: int,
    module: str | None,
) -> str | None:
    """Resolve the module base named by one relative ImportFrom statement."""
    if level <= 0:
        return None
    package_parts = current_module.split(".") if is_package else current_module.split(".")[:-1]
    ascend = level - 1
    if ascend > len(package_parts):
        return ""
    base_parts = package_parts[: len(package_parts) - ascend]
    if module:
        base_parts.extend(module.split("."))
    return ".".join(base_parts)


def test_reachability_helper_distinguishes_allowed_and_forbidden_graphs():
    """The shared detector accepts separation and reports an indirect forbidden edge."""
    disconnected = {"semantic.a": {"root.types"}, "root.types": set(), "blender.x": set()}
    assert _find_path(disconnected, "semantic.a", "blender") is None

    connected = {"semantic.a": {"root.bridge"}, "root.bridge": {"blender.x"}, "blender.x": set()}
    assert _find_path(connected, "semantic.a", "blender") == [
        "semantic.a",
        "root.bridge",
        "blender.x",
    ]


def test_cycle_helper_distinguishes_acyclic_and_cyclic_graphs():
    """The shared detector accepts a DAG and reports a non-trivial SCC."""
    assert _strongly_connected_components({"a": {"b"}, "b": {"c"}, "c": set()}) == []
    assert _strongly_connected_components({"a": {"b"}, "b": {"a"}}) == [["a", "b"]]


@pytest.mark.parametrize(("source_prefix", "forbidden_prefix"), _FORBIDDEN_REACHABILITY)
def test_architecture_forbids_cross_owner_reachability(source_prefix: str, forbidden_prefix: str):
    """Ownership regions cannot acquire forbidden direct or indirect implementation dependencies."""
    graph = _build_import_graph(module_scope_only=False)
    violations = []
    for module in _modules_with_prefix(graph, source_prefix):
        path = _find_path(graph, module, forbidden_prefix)
        if path:
            violations.append(" -> ".join(path))
    assert not violations, "Forbidden dependency path(s):\n" + "\n".join(violations)


def test_production_module_scope_import_graph_is_acyclic():
    """Production initialization dependencies remain cycle-free."""
    graph = _build_import_graph(module_scope_only=True)
    assert _strongly_connected_components(graph) == []


def test_relative_import_resolution_distinguishes_valid_and_invalid_bases():
    """Relative import resolution exposes stale pre-move prefixes without pinning file layout."""
    assert _relative_import_base(
        "NodeForge.blender.ir_lowering",
        is_package=False,
        level=1,
        module="library_groups",
    ) == "NodeForge.blender.library_groups"
    assert _relative_import_base(
        "NodeForge.blender.ir_lowering",
        is_package=False,
        level=1,
        module="blender.library_groups",
    ) == "NodeForge.blender.blender.library_groups"


def test_all_relative_import_module_bases_exist():
    """Every explicit relative module base resolves to a production module/package."""
    paths = {_module_name(path): path for path in _production_python_files()}
    known_modules = set(paths)
    unresolved: list[str] = []

    for current_module, path in paths.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level <= 0 or node.module is None:
                continue
            base = _relative_import_base(
                current_module,
                is_package=path.name == "__init__.py",
                level=node.level,
                module=node.module,
            )
            if base not in known_modules:
                unresolved.append(f"{current_module}:{node.lineno} -> {base}")

    assert not unresolved, "Unresolved relative import base(s):\n" + "\n".join(unresolved)

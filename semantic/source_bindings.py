"""Semantic records for source import and package namespace bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..compiler_identities import SourceDefinitionRef
from ..errors import CompileError

from ..extensions.contracts import ExtensionCallableId
from ..resolved_environment import ResolvedPackageNamespace


@dataclass(frozen=True)
class LibraryBinding:
    """Resolve one source-local imported name to an exact catalog record."""

    namespace: str
    canonical_name: str
    record: object
    extension_callable_id: ExtensionCallableId | None = None

    def __post_init__(self) -> None:
        """Require the binding namespace/name to match its selected record."""
        if getattr(self.record, "namespace", None) != self.namespace or getattr(self.record, "name", None) != self.canonical_name:
            raise ValueError("Library binding does not match its resolved record")
        if self.extension_callable_id is not None and not isinstance(self.extension_callable_id, ExtensionCallableId):
            raise TypeError("LibraryBinding.extension_callable_id must be ExtensionCallableId or None")


@dataclass(frozen=True)
class SourceModuleNamespaceBinding:
    """Bind one selected physical .nf file as an importable definition namespace."""

    source_name: str
    record: object
    snapshot: object
    namespace: str = "local"

    def __post_init__(self) -> None:
        """Require a module snapshot of the exact selected canonical source owner."""
        from .source_modules import SourceModuleSnapshot
        if not self.source_name or not isinstance(self.snapshot, SourceModuleSnapshot):
            raise TypeError("Module namespace requires one captured .nf source snapshot")
        if self.record.source_origin != self.snapshot.module_id.origin:
            raise ValueError("Module binding snapshot does not match the selected source origin")

    def member(self, name: str) -> "SourceDefinitionBinding":
        """Resolve only definitions physically declared within this source file."""
        return SourceDefinitionBinding(self.namespace, name, self.record, self.snapshot.definition_ref(name), self.snapshot)


@dataclass(frozen=True)
class SourceDefinitionBinding:
    """Bind one exact source definition, independently of import alias spelling."""

    namespace: str
    canonical_name: str
    record: object
    definition: SourceDefinitionRef
    snapshot: object

    def __post_init__(self) -> None:
        """Ensure the source member and physical module snapshot agree."""
        from .source_modules import SourceModuleSnapshot
        if (not isinstance(self.snapshot, SourceModuleSnapshot)
                or not isinstance(self.definition, SourceDefinitionRef)
                or self.definition.module != self.snapshot.module_id
                or self.definition.name != self.canonical_name
                or self.definition.name not in self.snapshot.definitions):
            raise ValueError("Source definition binding needs a matching declared member")


@dataclass(frozen=True)
class PackageNamespaceBinding:
    """Bind one source-local alias to an exact resolved package namespace."""

    source_name: str
    namespace: object

    def __post_init__(self) -> None:
        """Require a source alias and canonical resolved package namespace."""
        if not isinstance(self.source_name, str) or not self.source_name:
            raise ValueError("Package namespace binding requires a non-empty source name")
        if not isinstance(self.namespace, ResolvedPackageNamespace):
            raise TypeError("Package namespace binding requires ResolvedPackageNamespace")

    @property
    def package_id(self) -> str:
        """Return canonical package identity independently from source alias spelling."""
        return self.namespace.package_id


@dataclass(frozen=True)
class LocalDirectoryNamespaceBinding:
    """Bind a source alias to a directory selected from one frozen Local index."""

    source_name: str
    index_snapshot: object
    directory: object
    expose_unqualified: bool = False
    namespace: str = "local"

    def __post_init__(self):
        """Reject descriptors from a different or ambiguous snapshot at binding time."""
        from ..local_index import DirectorySourceIndex, LocalDirectoryCandidate
        if type(self.expose_unqualified) is not bool:
            raise TypeError("Directory namespace bare-call capability must be Bool")
        if (not self.source_name or not isinstance(self.index_snapshot, DirectorySourceIndex)
                or self.namespace not in {"local", "examples"}
                or self.index_snapshot.namespace != self.namespace
                or not isinstance(self.directory, LocalDirectoryCandidate)):
            raise ValueError("Invalid directory namespace binding")
        if self.index_snapshot.resolve_directory(self.directory.import_path) != self.directory:
            raise ValueError("Local namespace directory does not belong to its snapshot")


__all__ = ["LibraryBinding", "PackageNamespaceBinding", "LocalDirectoryNamespaceBinding",
           "SourceModuleNamespaceBinding", "SourceDefinitionBinding"]


@dataclass(frozen=True)
class FunctionLexicalInventory:
    """AST-only ownership inventory, independent of execution-path assignment.

    Parameters and ordinary assignment targets are function-local from entry;
    loop-target names are temporary to their own `for` statement. Whether a
    local currently has a value is a separate definite-assignment question.
    """

    parameters: frozenset[str]
    locals: frozenset[str]
    loop_targets: Mapping[int, frozenset[str]]
    source_aliases: Mapping[str, SourceDefinitionRef]

    def __post_init__(self):
        from types import MappingProxyType
        object.__setattr__(self, "loop_targets", MappingProxyType(dict(self.loop_targets)))
        object.__setattr__(self, "source_aliases", MappingProxyType(dict(self.source_aliases)))

    @property
    def owned_names(self) -> frozenset[str]:
        return self.parameters | self.locals


def collect_function_lexical_inventory(
    fn, definition_refs: Mapping[str, SourceDefinitionRef] | None = None,
) -> FunctionLexicalInventory:
    """Collect lexical source bindings without executing code or resolving calls.

    Nested FunctionDefs own their bodies and are not traversed. A for-target
    receives a temporary nested scope; ordinary assignments in its body remain
    ordinary function-owned locals, subject to definite-assignment checks.
    """
    import ast

    if not isinstance(fn, ast.FunctionDef):
        raise TypeError("Lexical inventory requires one FunctionDef")
    refs = definition_refs or {}
    parameters = frozenset(arg.arg for arg in fn.args.args)
    ordinary_locals: set[str] = set()
    loops: dict[int, frozenset[str]] = {}
    # Only unconditional, simple assignments in the function body can declare
    # source-function aliases. Assignments in conditional/loop blocks are
    # ordinary locals, even when their RHS happens to spell a module function.
    candidate_aliases: list[tuple[str, str]] = []

    def names(target):
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, (ast.Tuple, ast.List)):
            return set().union(*(names(item) for item in target.elts))
        return set()

    def visit(stmt, *, direct=False):
        if isinstance(stmt, ast.FunctionDef):
            return
        if isinstance(stmt, ast.Assign):
            if (direct and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and isinstance(stmt.value, ast.Name)):
                candidate_aliases.append((stmt.targets[0].id, stmt.value.id))
            else:
                for target in stmt.targets:
                    ordinary_locals.update(names(target))
            return
        if isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
            ordinary_locals.update(names(stmt.target))
            return
        if isinstance(stmt, ast.If):
            for sub in (*stmt.body, *stmt.orelse):
                visit(sub)
            return
        if isinstance(stmt, ast.For):
            loops[id(stmt)] = frozenset(names(stmt.target))
            for sub in (*stmt.body, *stmt.orelse):
                visit(sub)
            return

    for statement in fn.body:
        visit(statement, direct=True)

    # Ownership must be decided before treating a textual RHS as a function.
    # A parameter or *any* ordinary assignment (even a later one) shadows an
    # outer source definition for the whole function. Alias chains, however,
    # may refer to earlier, verified aliases. Iterate when a candidate itself
    # proves to be an ordinary local, so aliases depending on it are invalidated
    # independent of the order in which statements were inspected.
    candidate_targets = {target for target, _ in candidate_aliases}
    non_alias_locals = set(parameters) | ordinary_locals
    while True:
        aliases: dict[str, SourceDefinitionRef] = {}
        previous = set(non_alias_locals)
        for target, source in candidate_aliases:
            if target in non_alias_locals:
                continue
            if source == target or source in non_alias_locals:
                non_alias_locals.add(target)
                continue
            if source in candidate_targets:
                ref = aliases.get(source)  # Only preceding valid aliases bind.
            else:
                ref = refs.get(source)
            if ref is None:
                non_alias_locals.add(target)
            else:
                aliases[target] = ref
        if non_alias_locals == previous:
            break

    return FunctionLexicalInventory(
        parameters,
        frozenset(ordinary_locals | candidate_targets), loops, aliases,
    )


__all__ += ["FunctionLexicalInventory", "collect_function_lexical_inventory"]

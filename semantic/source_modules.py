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


def parse_source_module(source: str, module_id: ModuleId) -> SourceModuleSnapshot:
    """Parse one exact source revision and collect all declarations before lowering.

    Imports and definitions are separated from statements without relocating
    them or creating synthetic source. Duplicate declarations fail with the
    second declaration's original source position.
    """
    if not isinstance(module_id, ModuleId):
        raise TypeError("module_id must be ModuleId")
    tree = _parse_source_tree(source)
    imports = []
    functions = {}
    assignments = []
    statements = []
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom):
            imports.append(statement)
        elif isinstance(statement, ast.FunctionDef):
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


__all__ = ["SourceModuleSnapshot", "parse_source_module"]

"""Pure declaration validation shared by demand and eager module preflight.

This module only inspects original AST and compile-time signature constants.
Runtime expression types, extension backends and Blender groups remain lazy.
"""

from __future__ import annotations

import ast
from typing import Mapping

from ..errors import CompileError
from .builtin_calls import INPUT_DECLARATION_BUILTIN_NAMES
from .function_signatures import compile_function_signature, resolve_parameter_annotation
from .source_callables import analyze_local_return_shape


def validate_source_definition_contract(
    fn: ast.FunctionDef, *, owner_scope: str, declaration_owner: str,
    compile_time_values: Mapping[str, object] | None = None,
    module_origin: str = "",
):
    """Validate a selected definition's source-only contract without lowering IR.

    Return the existing signature/return-shape records for the demanded body
    compiler. Unsupported runtime operations are intentionally not checked.
    """
    location = f"{fn.name}() at line {fn.lineno}, column {fn.col_offset + 1}"
    if module_origin:
        location += f" in {module_origin}"
    try:
        signature = compile_function_signature(
            fn, owner_scope=owner_scope, declaration_owner=declaration_owner,
            compile_time_values=compile_time_values,
        )
        if any(isinstance(node, ast.Name) and node.id == "__inline__"
               for statement in fn.body for node in ast.walk(statement)):
            raise CompileError(f"{fn.name}() cannot read compiler-only __inline__ in the function body")
        output_label = None
        if fn.returns is not None:
            declared_type, output_label = resolve_parameter_annotation(fn.returns)
            if declared_type is None:
                raise CompileError("scalar return annotation must be one runtime type")
        shape = analyze_local_return_shape(fn, output_label=output_label)
        if fn.returns is not None and len(shape.elements) != 1:
            raise CompileError("scalar return annotation cannot annotate tuple results")
        obsolete = set(INPUT_DECLARATION_BUILTIN_NAMES) | {"output"}
        for statement in fn.body:
            for node in ast.walk(statement):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in obsolete):
                    raise CompileError(
                        f"obsolete group interface declaration {node.func.id}() "
                        f"at line {node.lineno}, column {node.col_offset + 1}"
                    )
        return signature, shape
    except CompileError as exc:
        raise CompileError(f"{location}: {exc}") from exc

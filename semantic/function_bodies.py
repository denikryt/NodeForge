"""Compile FunctionDef bodies directly from AST to typed nested-group IR.

This is the function-body counterpart to unified module declaration collection.
It consumes original AST nodes, not generated ``input_*`` / ``output()`` source.
"""

from __future__ import annotations

import ast
from typing import Mapping

from ..compiler_identities import GroupCompilationIdentity
from ..errors import CompileError
from ..nf_types import NFType
from .call_resolution import CallableEnvironment
from .callable_contracts import GroupInterfaceContract, GroupOutputContract, source_argument_type_matches
from .compile_time import CompileTimeSnapshot
from .function_signatures import compile_function_signature
from .ir import IRBody
from .parsing import normalized_statements
from .residualization import _preprocess_compile_time
from .runtime_bindings import RuntimeBindingSymbol
from .source_callables import analyze_local_return_shape
from .body import lower_basic_body


def compile_function_definition(
    fn: ast.FunctionDef,
    *,
    compilation_identity: GroupCompilationIdentity,
    callable_environment: CallableEnvironment,
    resolved_environment,
    inferred_parameter_types: Mapping[str, NFType] | None = None,
    compile_time_values: Mapping[str, object] | None = None,
    source_callable_session=None,
    helper_namespace: str = "Function",
):
    """Compile one nested function body without synthesizing textual DSL.

    Parameters enter the semantic engine as typed runtime bindings. Their
    physical sockets are emitted from the same IRInputDeclaration objects as
    the signature contract. A terminal return publishes only declared outputs.
    """
    from .group import SemanticGroupCompilation

    if not isinstance(compilation_identity, GroupCompilationIdentity):
        raise TypeError("Function compilation requires GroupCompilationIdentity")
    if not isinstance(callable_environment, CallableEnvironment):
        raise TypeError("Function compilation requires CallableEnvironment")
    if not isinstance(fn, ast.FunctionDef):
        raise TypeError("Function compilation requires FunctionDef")
    shape = analyze_local_return_shape(fn)
    values = dict(compile_time_values or {})
    signature = compile_function_signature(
        fn,
        owner_scope=compilation_identity.owner_scope,
        declaration_owner=compilation_identity.declaration_owner,
        inferred_types=inferred_parameter_types,
        compile_time_values=values,
    )
    for statement in fn.body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and (
                node.func.id == "output" or node.func.id.startswith("input_")
            ):
                raise CompileError(
                    f"{fn.name}() uses obsolete group interface declaration {node.func.id}(); "
                    "define inputs in parameters and outputs with return"
                )
    preprocessed = _preprocess_compile_time(fn.body)
    if not preprocessed.statements or preprocessed.statements[-1] is not fn.body[-1]:
        raise CompileError(f"{fn.name}() return must remain terminal after preprocessing")
    bindings = {
        declaration.target_name: RuntimeBindingSymbol(declaration.target_binding_id, declaration.typ)
        for declaration in signature.declarations
    }
    origins = {declaration.target_binding_id: declaration.declaration_id for declaration in signature.declarations}
    # The body analyzer uses its existing source-bindings/lexical rules; a function
    # neither adds implicit geometry I/O nor inherits external runtime sockets.
    reserved = {
        name: "local function" for name in callable_environment.local_functions
    }
    reserved.update({name: "imported function" for name in callable_environment.imported_functions})
    reserved.update({name: "imported package" for name in callable_environment.package_namespaces})
    reserved.update({name: "local directory" for name in callable_environment.local_namespaces})
    dependencies: dict[tuple[str, ...], str] = {}
    body = lower_basic_body(
        list(preprocessed.statements),
        initial_runtime_bindings=bindings,
        initial_compile_time=CompileTimeSnapshot(values),
        reserved_name_labels=reserved,
        callable_environment=callable_environment,
        owner_scope=compilation_identity.owner_scope,
        declaration_owner=compilation_identity.declaration_owner,
        initial_interface_input_origins=origins,
        compile_time_effects_before=preprocessed.effects_before,
        trailing_compile_time_effects=preprocessed.trailing_effects,
        source_callable_session=source_callable_session,
        source_definition_owner=compilation_identity.definition_owner,
        helper_namespace=helper_namespace,
        extension_registry=resolved_environment.extension_registry,
        extension_dependency_sink=dependencies,
        terminal_return_shape=shape,
        geometry_mode=False,
    )
    outputs = tuple(
        GroupOutputContract(index, name, typ)
        for index, (name, typ) in enumerate(body.output_summary.explicit_outputs)
    )
    if len(outputs) != len(shape.elements):
        raise CompileError(f"{fn.name}() return output count changed during semantic preparation")
    if fn.returns is not None:
        if len(outputs) != 1:
            raise CompileError(f"{fn.name}() scalar return annotation is incompatible with tuple return")
        from .function_signatures import resolve_parameter_annotation
        declared, label = resolve_parameter_annotation(fn.returns)
        if declared is None or label is not None:
            raise CompileError(f"{fn.name}() return annotation must be an unlabelled runtime type")
        if not source_argument_type_matches(declared, outputs[0].typ):
            raise CompileError(f"{fn.name}() returns {outputs[0].typ}, not declared {declared}")
    # Parameter declarations are body IR so the normal Blender group lowering
    # constructs sockets and validates the matching provenance without special paths.
    result_body = IRBody(tuple(signature.declarations) + body.body.statements)
    return SemanticGroupCompilation(
        source=ast.unparse(fn),
        identity=compilation_identity,
        normalized_lowered_source=normalized_statements(fn.body),
        body=result_body,
        interface=GroupInterfaceContract(signature.physical_inputs, outputs),
        geometry_mode=False,
        extension_dependencies=tuple(sorted(dependencies.items(), key=lambda item: item[0])),
    )


__all__ = ["compile_function_definition"]

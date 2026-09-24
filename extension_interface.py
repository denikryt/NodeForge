"""Normalize captured ``interface.py`` modules into extension contracts."""

from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Callable, Mapping

from .callable_contracts import canonicalize_group_input_default, source_argument_type_matches
from .errors import CompileError
from .evaluation_modes import EvaluationMode
from .extension_annotations import MARKER_TO_NF_TYPE
from .extension_contracts import (
    ExtensionCallableId,
    ExtensionCallableSpec,
    ExtensionImplementationRef,
    ExtensionParameterSpec,
    TypeSpec,
)
from .nf_types import NFType


EXTENSION_API_VERSION = 2


def _public_identifier(value: object, context: str) -> str:
    """Return one validated public extension identifier."""
    if not isinstance(value, str) or not value or not value.isidentifier() or value.startswith("_"):
        raise CompileError(f"{context} must be a public Python identifier")
    return value


def normalize_implementation_ref(
    raw: object,
    *,
    module_exists: Callable[[str], bool],
) -> ExtensionImplementationRef:
    """Normalize one symbolic owner-relative implementation reference."""
    if not isinstance(raw, str):
        raise CompileError("EXTENSIONS implementation references must be strings")
    if raw.count(":") != 1:
        raise CompileError(f"Invalid extension implementation reference: {raw!r}")
    module, attribute = raw.split(":", 1)
    if not module.startswith(".") or module.startswith(".."):
        raise CompileError(f"Extension implementation module must be owner-relative: {raw!r}")
    module_parts = module[1:].split(".")
    if not module_parts or any(not part or not part.isidentifier() for part in module_parts):
        raise CompileError(f"Invalid extension implementation module: {module!r}")
    if module == ".semantic":
        raise CompileError("semantic.py is reserved and cannot be an extension implementation target")
    if not attribute.isidentifier():
        raise CompileError(f"Invalid extension implementation attribute: {attribute!r}")
    if not module_exists(module):
        raise CompileError(f"Extension implementation module does not exist in owner snapshot: {module}")
    return ExtensionImplementationRef(module, attribute)


def _marker_nf_type(annotation: object) -> NFType | None:
    """Return the canonical type for one exact exported annotation marker."""
    return MARKER_TO_NF_TYPE.get(annotation)


def _normalize_nf_set(annotation: object, *, context: str) -> TypeSpec:
    """Normalize one direct marker or finite marker union to ``NF_SET``."""
    direct = _marker_nf_type(annotation)
    if direct is not None:
        return TypeSpec("NF_SET", frozenset({direct}))

    origin = typing.get_origin(annotation)
    if origin in {typing.Union, types.UnionType}:
        nf_types: set[NFType] = set()
        for item in typing.get_args(annotation):
            typ = _marker_nf_type(item)
            if typ is None:
                raise CompileError(f"{context} union members must be exact NodeForge annotation markers")
            nf_types.add(typ)
        if not nf_types:
            raise CompileError(f"{context} union must contain at least one NodeForge type")
        return TypeSpec("NF_SET", frozenset(nf_types))

    if isinstance(annotation, NFType):
        raise CompileError(f"{context} must use NodeForge annotation markers, not NFType values")
    if annotation is typing.Any:
        raise CompileError(f"{context} does not support Any")
    raise CompileError(f"Unsupported {context} annotation: {annotation!r}")


def _normalize_parameter_annotation(annotation: object, *, context: str) -> tuple[TypeSpec, EvaluationMode]:
    """Normalize one ``Annotated[direct-NF-set, EvaluationMode]`` parameter."""
    if typing.get_origin(annotation) is not typing.Annotated:
        raise CompileError(f"{context} must declare exactly one EvaluationMode with typing.Annotated")
    base, *metadata = typing.get_args(annotation)
    modes = [item for item in metadata if isinstance(item, EvaluationMode)]
    if len(modes) != 1:
        raise CompileError(f"{context} must declare exactly one EvaluationMode")
    return _normalize_nf_set(base, context=context), modes[0]


def _normalize_result_annotation(annotation: object, *, context: str) -> TypeSpec:
    """Normalize one exact runtime result or fixed exact runtime tuple."""
    origin = typing.get_origin(annotation)
    if origin is tuple:
        args = typing.get_args(annotation)
        if not args or len(args) == 2 and args[1] is Ellipsis:
            raise CompileError(f"{context} does not support variadic tuple results")
        items = tuple(_normalize_nf_set(item, context=f"{context} tuple item") for item in args)
        if any(len(item.nf_types) != 1 for item in items):
            raise CompileError(f"{context} tuple result items must each resolve to one exact NFType")
        return TypeSpec("TUPLE_FIXED", items=items)
    result = _normalize_nf_set(annotation, context=context)
    if len(result.nf_types) != 1:
        raise CompileError(f"{context} must resolve to one exact NFType; use finite overloads for polymorphic results")
    return result


def _infer_detached_default_type(value: object) -> NFType:
    """Infer the canonical source type of one supported detached Python default."""
    if type(value) is bool:
        return NFType.BOOL
    if type(value) is int:
        canonicalize_group_input_default(NFType.INT, value)
        return NFType.INT
    if type(value) is float:
        canonicalize_group_input_default(NFType.FLOAT, value)
        return NFType.FLOAT
    if isinstance(value, str):
        return NFType.STRING
    if isinstance(value, (tuple, list)):
        canonicalize_group_input_default(NFType.VECTOR, value)
        return NFType.VECTOR
    raise CompileError(f"Unsupported extension parameter default: {value!r}")


def _normalize_default(value: object, type_spec: TypeSpec, *, context: str) -> tuple[object, NFType]:
    """Canonicalize one detached default against a direct ``NF_SET`` declaration."""
    actual = _infer_detached_default_type(value)
    if actual in type_spec.nf_types:
        selected = actual
    else:
        compatible = [
            expected
            for expected in sorted(type_spec.nf_types, key=lambda item: item.value)
            if source_argument_type_matches(expected, actual)
        ]
        if len(compatible) != 1:
            raise CompileError(f"{context} default is not uniquely representable by its declared NodeForge type set")
        selected = compatible[0]
    return canonicalize_group_input_default(selected, value), selected


def _evaluated_annotations(function: object) -> dict[str, object]:
    """Resolve one declaration's postponed annotations inside its captured globals."""
    try:
        return typing.get_type_hints(function, globalns=function.__globals__, localns=function.__globals__, include_extras=True)
    except Exception as exc:
        raise CompileError(f"Could not resolve extension annotations for {function.__name__}(): {exc}") from exc


def _normalize_declaration(
    function: object,
    callable_id: ExtensionCallableId,
) -> ExtensionCallableSpec:
    """Normalize one declaration function or overload stub into a semantic contract."""
    if not inspect.isfunction(function):
        raise CompileError(f"Extension declaration {callable_id.name!r} must be a Python function")
    signature = inspect.signature(function)
    annotations = _evaluated_annotations(function)
    parameters: list[ExtensionParameterSpec] = []
    for parameter in signature.parameters.values():
        if parameter.name == "__unique__":
            raise CompileError(f"Extension parameter {parameter.name!r} is reserved by the compiler")
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            raise CompileError(f"{callable_id.name}() extension declarations do not support **kwargs")
        annotation = annotations.get(parameter.name, parameter.annotation)
        if annotation is inspect.Parameter.empty:
            raise CompileError(f"{callable_id.name}() parameter {parameter.name!r} requires a NodeForge annotation")
        type_spec, mode = _normalize_parameter_annotation(
            annotation,
            context=f"{callable_id.name}() parameter {parameter.name!r}",
        )
        default = parameter.default
        default_type = None
        if default is not inspect.Parameter.empty:
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                raise CompileError(f"{callable_id.name}() variadic parameter cannot declare a default")
            if mode is EvaluationMode.RUNTIME_ONLY:
                raise CompileError(f"{callable_id.name}() RUNTIME_ONLY parameter {parameter.name!r} cannot declare a default")
            default, default_type = _normalize_default(
                default,
                type_spec,
                context=f"{callable_id.name}() parameter {parameter.name!r}",
            )
        parameters.append(
            ExtensionParameterSpec(
                parameter.name,
                parameter.kind,
                type_spec,
                mode,
                default,
                default_type,
            )
        )
    return_annotation = annotations.get("return", signature.return_annotation)
    if return_annotation is inspect.Signature.empty:
        raise CompileError(f"{callable_id.name}() extension declaration requires an exact return annotation")
    result = _normalize_result_annotation(return_annotation, context=f"{callable_id.name}() result")
    return ExtensionCallableSpec(callable_id, tuple(parameters), result)


def _defined_in_interface(function: object, module_globals: dict[str, object]) -> bool:
    """Return whether one declaration function belongs to the current fresh interface globals."""
    return inspect.isfunction(function) and getattr(function, "__globals__", None) is module_globals


def normalize_interface_module(
    module: object,
    *,
    owner_key: tuple[str, ...],
    module_exists: Callable[[str], bool],
) -> tuple[
    Mapping[ExtensionCallableId, tuple[ExtensionCallableSpec, ...]],
    Mapping[ExtensionCallableId, ExtensionImplementationRef],
]:
    """Normalize one executed captured interface module into immutable callable contracts."""
    module_globals = getattr(module, "__dict__", None)
    if not isinstance(module_globals, dict):
        raise CompileError("Extension interface must execute as a Python module")
    if module_globals.get("EXTENSION_API") != EXTENSION_API_VERSION:
        raise CompileError(f"Extension interface must declare EXTENSION_API = {EXTENSION_API_VERSION}")
    raw_extensions = module_globals.get("EXTENSIONS")
    if not isinstance(raw_extensions, dict) or not raw_extensions:
        raise CompileError("Extension interface EXTENSIONS must be a non-empty dict[str, str]")

    families: dict[ExtensionCallableId, tuple[ExtensionCallableSpec, ...]] = {}
    refs: dict[ExtensionCallableId, ExtensionImplementationRef] = {}
    for raw_name, raw_ref in raw_extensions.items():
        name = _public_identifier(raw_name, "EXTENSIONS key")
        declaration = module_globals.get(name)
        if declaration is None:
            raise CompileError(f"EXTENSIONS key {name!r} has no declaration in interface globals")
        if not _defined_in_interface(declaration, module_globals):
            raise CompileError(f"Extension declaration {name!r} must be defined in the current interface module")
        callable_id = ExtensionCallableId(tuple(owner_key), name)
        implementation_ref = normalize_implementation_ref(raw_ref, module_exists=module_exists)
        if implementation_ref.module == ".interface" and implementation_ref.attribute == name:
            raise CompileError(f"Extension declaration {name!r} cannot be its own physical implementation")

        overloads = [
            stub
            for stub in typing.get_overloads(declaration)
            if getattr(stub, "__globals__", None) is module_globals
        ]
        overloads.sort(key=lambda stub: getattr(getattr(stub, "__code__", None), "co_firstlineno", 0))
        declarations = overloads or [declaration]
        specs = tuple(_normalize_declaration(function, callable_id) for function in declarations)
        families[callable_id] = specs
        refs[callable_id] = implementation_ref
    return families, refs


__all__ = [
    "EXTENSION_API_VERSION",
    "normalize_implementation_ref",
    "normalize_interface_module",
]

"""Normalize captured ``interface.py`` modules into extension contracts."""

from __future__ import annotations

import dataclasses
import inspect
import types
import typing
from collections.abc import Callable, Mapping

from .semantic.callable_contracts import canonicalize_group_input_default, source_argument_type_matches
from .errors import CompileError
from .evaluation_modes import EvaluationMode
from .extension_annotations import MARKER_TO_NF_TYPE
from .extension_contracts import (
    ExtensionCallableId,
    ExtensionCallableSpec,
    ExtensionImplementationRef,
    ExtensionParameterSpec,
    ExtensionTypeId,
    ExtensionTypeSpec,
    PythonScalarKind,
    TypeSpec,
    is_executable_result_type_spec,
    is_frontend_semantic_type_spec,
)
from .nf_types import NFType


EXTENSION_API_VERSION = 2


_PYTHON_SCALAR_KINDS = {
    bool: PythonScalarKind.BOOL,
    int: PythonScalarKind.INT,
    float: PythonScalarKind.FLOAT,
    str: PythonScalarKind.STRING,
}


def _public_identifier(value: object, context: str) -> str:
    """Return one validated public extension identifier."""
    if not isinstance(value, str) or not value or not value.isidentifier() or value.startswith("_"):
        raise CompileError(f"{context} must be a public Python identifier")
    return value


def normalize_implementation_ref(
    raw: object,
    *,
    module_exists: Callable[[str], bool],
) -> ExtensionImplementationRef | None:
    """Normalize one optional symbolic owner-relative implementation reference."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise CompileError("EXTENSIONS implementation references must be strings or None")
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
    if module == ".interface":
        raise CompileError("interface.py is declaration-only and cannot be an extension implementation target")
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


def normalize_semantic_type_annotation(
    annotation: object,
    *,
    class_type_ids: Mapping[type, ExtensionTypeId],
    context: str,
) -> TypeSpec:
    """Normalize one record/private-state annotation into the canonical recursive grammar."""
    direct = _marker_nf_type(annotation)
    if direct is not None:
        return TypeSpec("NF_SET", frozenset({direct}))
    scalar_kind = _PYTHON_SCALAR_KINDS.get(annotation)
    if scalar_kind is not None:
        return TypeSpec("PY_SCALAR", python_scalar_kind=scalar_kind)
    if isinstance(annotation, type) and annotation in class_type_ids:
        return TypeSpec("RECORD", record_type=class_type_ids[annotation])
    if isinstance(annotation, NFType):
        raise CompileError(f"{context} must use NodeForge annotation markers, not NFType values")
    if annotation is typing.Any:
        raise CompileError(f"{context} does not support Any")

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in {typing.Union, types.UnionType}:
        non_none = tuple(item for item in args if item is not type(None))
        if len(non_none) == 1 and len(non_none) + 1 == len(args):
            return TypeSpec(
                "OPTIONAL",
                item=normalize_semantic_type_annotation(
                    non_none[0], class_type_ids=class_type_ids, context=f"{context} optional item"
                ),
            )
        # A union composed entirely of NodeForge markers remains one NF_SET.
        if args and all(_marker_nf_type(item) is not None for item in args):
            return TypeSpec("NF_SET", frozenset(_marker_nf_type(item) for item in args))
        raise CompileError(f"{context} supports only NodeForge marker unions or T | None")
    if origin is list:
        if len(args) != 1:
            raise CompileError(f"{context} list annotation must contain exactly one item type")
        return TypeSpec(
            "LIST",
            item=normalize_semantic_type_annotation(args[0], class_type_ids=class_type_ids, context=f"{context} list item"),
        )
    if origin is tuple:
        if not args:
            raise CompileError(f"{context} tuple annotation must declare item types")
        if len(args) == 2 and args[1] is Ellipsis:
            return TypeSpec(
                "TUPLE_VAR",
                item=normalize_semantic_type_annotation(args[0], class_type_ids=class_type_ids, context=f"{context} tuple item"),
            )
        return TypeSpec(
            "TUPLE_FIXED",
            items=tuple(
                normalize_semantic_type_annotation(item, class_type_ids=class_type_ids, context=f"{context} tuple item")
                for item in args
            ),
        )
    if origin is dict:
        if len(args) != 2 or args[0] is not str:
            raise CompileError(f"{context} supports only dict[str, T]")
        return TypeSpec(
            "DICT_STR",
            item=normalize_semantic_type_annotation(args[1], class_type_ids=class_type_ids, context=f"{context} dict value"),
        )
    raise CompileError(f"Unsupported {context} annotation: {annotation!r}")


def _type_spec_contains_private(
    spec: TypeSpec,
    type_specs: Mapping[ExtensionTypeId, ExtensionTypeSpec],
    active: frozenset[ExtensionTypeId] = frozenset(),
) -> bool:
    """Return whether a normalized type graph exposes an owner-private record."""
    if spec.kind == "RECORD":
        type_id = spec.record_type
        record = type_specs[type_id]
        if not record.is_public:
            return True
        if type_id in active:
            return False
        next_active = active | {type_id}
        return any(_type_spec_contains_private(field_spec, type_specs, next_active) for _name, field_spec in record.fields)
    if spec.kind in {"LIST", "TUPLE_VAR", "DICT_STR", "OPTIONAL"}:
        return _type_spec_contains_private(spec.item, type_specs, active)
    if spec.kind == "TUPLE_FIXED":
        return any(_type_spec_contains_private(item, type_specs, active) for item in spec.items)
    return False


def normalize_interface_records(
    module: object,
    *,
    owner_key: tuple[str, ...],
) -> tuple[
    Mapping[ExtensionTypeId, ExtensionTypeSpec],
    Mapping[ExtensionTypeId, type],
    Mapping[type, ExtensionTypeId],
]:
    """Discover direct frozen dataclasses and normalize one same-owner nominal schema graph."""
    module_globals = getattr(module, "__dict__", None)
    if not isinstance(module_globals, dict):
        raise CompileError("Extension interface must execute as a Python module")
    module_name = getattr(module, "__name__", None)
    class_bindings: list[tuple[str, type]] = []
    for binding_name, value in module_globals.items():
        if not (
            isinstance(value, type)
            and getattr(value, "__module__", None) == module_name
            and dataclasses.is_dataclass(value)
        ):
            continue
        if binding_name != value.__name__:
            raise CompileError(
                f"Extension semantic record {value.__name__!r} must be bound only under its declared class name; "
                f"found owner-local binding {binding_name!r}"
            )
        class_bindings.append((binding_name, value))

    class_type_ids: dict[type, ExtensionTypeId] = {}
    classes_by_candidate_id: dict[ExtensionTypeId, type] = {}
    for binding_name, cls in class_bindings:
        params = getattr(cls, "__dataclass_params__", None)
        if params is None or not params.frozen:
            raise CompileError(f"Extension semantic record {cls.__name__!r} must be a frozen dataclass")
        if not cls.__name__.isidentifier():
            raise CompileError("Extension semantic record name must be a Python identifier")
        type_id = ExtensionTypeId(tuple(owner_key), binding_name)
        previous = classes_by_candidate_id.get(type_id)
        if previous is not None and previous is not cls:
            raise CompileError(
                f"Multiple extension semantic record classes claim the same type identity {type_id.name!r}"
            )
        classes_by_candidate_id[type_id] = cls
        class_type_ids[cls] = type_id

    specs: dict[ExtensionTypeId, ExtensionTypeSpec] = {}
    classes_by_id: dict[ExtensionTypeId, type] = {}
    for _binding_name, cls in class_bindings:
        type_id = class_type_ids[cls]
        direct_bases: list[ExtensionTypeId] = []
        for base in cls.__bases__:
            if base is object:
                continue
            if dataclasses.is_dataclass(base):
                base_id = class_type_ids.get(base)
                if base_id is None:
                    raise CompileError(
                        f"Extension semantic record {cls.__name__!r} cannot inherit imported/cross-owner dataclass {base.__name__!r}"
                    )
                direct_bases.append(base_id)
            else:
                raise CompileError(
                    f"Extension semantic record {cls.__name__!r} cannot inherit non-record base {base.__name__!r}"
                )
        try:
            hints = typing.get_type_hints(cls, globalns=module_globals, localns=module_globals, include_extras=True)
        except Exception as exc:
            raise CompileError(f"Could not resolve extension record annotations for {cls.__name__}: {exc}") from exc
        fields: list[tuple[str, TypeSpec]] = []
        for field in dataclasses.fields(cls):
            annotation = hints.get(field.name, field.type)
            fields.append(
                (
                    field.name,
                    normalize_semantic_type_annotation(
                        annotation,
                        class_type_ids=class_type_ids,
                        context=f"record {cls.__name__}.{field.name}",
                    ),
                )
            )
        spec = ExtensionTypeSpec(type_id, tuple(fields), tuple(direct_bases), not cls.__name__.startswith("_"))
        specs[type_id] = spec
        classes_by_id[type_id] = cls

    for type_id, spec in specs.items():
        if not spec.is_public:
            continue
        if any(not specs[base].is_public for base in spec.bases):
            raise CompileError(f"Public extension record {type_id.name!r} cannot inherit a private record")
        if any(_type_spec_contains_private(field_spec, specs, frozenset({type_id})) for _name, field_spec in spec.fields):
            raise CompileError(f"Public extension record {type_id.name!r} cannot expose a private record")

    return specs, classes_by_id, {cls: type_id for cls, type_id in class_type_ids.items()}


def _normalize_parameter_annotation(
    annotation: object,
    *,
    context: str,
    class_type_ids: Mapping[type, ExtensionTypeId],
) -> tuple[TypeSpec, EvaluationMode | None]:
    """Normalize one public direct-NF or semantic RECORD/LIST parameter."""
    if typing.get_origin(annotation) is typing.Annotated:
        base, *metadata = typing.get_args(annotation)
        modes = [item for item in metadata if isinstance(item, EvaluationMode)]
        if len(modes) != 1:
            raise CompileError(f"{context} must declare exactly one EvaluationMode")
        spec = _normalize_nf_set(base, context=context)
        return spec, modes[0]
    spec = normalize_semantic_type_annotation(annotation, class_type_ids=class_type_ids, context=context)
    if not is_frontend_semantic_type_spec(spec):
        raise CompileError(f"{context} must declare EvaluationMode for direct NodeForge values")
    return spec, None


def _normalize_result_annotation(
    annotation: object,
    *,
    context: str,
    class_type_ids: Mapping[type, ExtensionTypeId],
) -> TypeSpec:
    """Normalize one executable or frontend-semantic public result."""
    try:
        semantic = normalize_semantic_type_annotation(annotation, class_type_ids=class_type_ids, context=context)
    except CompileError:
        semantic = None
    if semantic is not None and is_frontend_semantic_type_spec(semantic):
        return semantic

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
    *,
    class_type_ids: Mapping[type, ExtensionTypeId],
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
            class_type_ids=class_type_ids,
        )
        default = parameter.default
        default_type = None
        if default is not inspect.Parameter.empty:
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                raise CompileError(f"{callable_id.name}() variadic parameter cannot declare a default")
            if type_spec.kind != "NF_SET":
                raise CompileError(f"{callable_id.name}() semantic parameter {parameter.name!r} cannot declare a default")
            if mode is EvaluationMode.RUNTIME_ONLY:
                raise CompileError(f"{callable_id.name}() RUNTIME_ONLY parameter {parameter.name!r} cannot declare a default")
            default, default_type = _normalize_default(default, type_spec, context=f"{callable_id.name}() parameter {parameter.name!r}")
        parameters.append(ExtensionParameterSpec(parameter.name, parameter.kind, type_spec, mode, default, default_type))
    return_annotation = annotations.get("return", signature.return_annotation)
    if return_annotation is inspect.Signature.empty:
        raise CompileError(f"{callable_id.name}() extension declaration requires an exact return annotation")
    result = _normalize_result_annotation(
        return_annotation,
        context=f"{callable_id.name}() result",
        class_type_ids=class_type_ids,
    )
    return ExtensionCallableSpec(callable_id, tuple(parameters), result)



def _require_public_semantic_contract(
    spec: TypeSpec,
    type_specs: Mapping[ExtensionTypeId, ExtensionTypeSpec],
    *,
    context: str,
) -> None:
    """Reject owner-private record types from one public RECORD/semantic LIST position."""
    if spec.kind == "RECORD":
        record = type_specs.get(spec.record_type)
        if record is None or not record.is_public:
            raise CompileError(f"{context} cannot expose a private extension semantic record")
        return
    if spec.kind == "LIST":
        _require_public_semantic_contract(spec.item, type_specs, context=context)
        return
    raise CompileError(f"Internal error: {context} requested public semantic validation for non-semantic TypeSpec")

def _defined_in_interface(function: object, module_globals: dict[str, object]) -> bool:
    """Return whether one declaration function belongs to the current fresh interface globals."""
    return inspect.isfunction(function) and getattr(function, "__globals__", None) is module_globals


def normalize_interface_module(
    module: object,
    *,
    owner_key: tuple[str, ...],
    module_exists: Callable[[str], bool],
    class_type_ids: Mapping[type, ExtensionTypeId] | None = None,
    record_type_specs: Mapping[ExtensionTypeId, ExtensionTypeSpec] | None = None,
) -> tuple[
    Mapping[ExtensionCallableId, tuple[ExtensionCallableSpec, ...]],
    Mapping[ExtensionCallableId, ExtensionImplementationRef | None],
]:
    """Normalize one executed captured interface module into immutable callable contracts."""
    module_globals = getattr(module, "__dict__", None)
    if not isinstance(module_globals, dict):
        raise CompileError("Extension interface must execute as a Python module")
    if module_globals.get("EXTENSION_API") != EXTENSION_API_VERSION:
        raise CompileError(f"Extension interface must declare EXTENSION_API = {EXTENSION_API_VERSION}")
    raw_extensions = module_globals.get("EXTENSIONS")
    if not isinstance(raw_extensions, dict) or not raw_extensions:
        raise CompileError("Extension interface EXTENSIONS must be a non-empty dict[str, str | None]")
    class_type_ids = {} if class_type_ids is None else dict(class_type_ids)
    record_type_specs = {} if record_type_specs is None else dict(record_type_specs)

    families: dict[ExtensionCallableId, tuple[ExtensionCallableSpec, ...]] = {}
    refs: dict[ExtensionCallableId, ExtensionImplementationRef | None] = {}
    for raw_name, raw_ref in raw_extensions.items():
        name = _public_identifier(raw_name, "EXTENSIONS key")
        declaration = module_globals.get(name)
        if declaration is None:
            raise CompileError(f"EXTENSIONS key {name!r} has no declaration in interface globals")
        if not _defined_in_interface(declaration, module_globals):
            raise CompileError(f"Extension declaration {name!r} must be defined in the current interface module")
        callable_id = ExtensionCallableId(tuple(owner_key), name)
        implementation_ref = normalize_implementation_ref(raw_ref, module_exists=module_exists)

        overloads = [stub for stub in typing.get_overloads(declaration) if getattr(stub, "__globals__", None) is module_globals]
        overloads.sort(key=lambda stub: getattr(getattr(stub, "__code__", None), "co_firstlineno", 0))
        declarations = overloads or [declaration]
        specs = tuple(
            _normalize_declaration(function, callable_id, class_type_ids=class_type_ids)
            for function in declarations
        )
        for spec in specs:
            for parameter in spec.parameters:
                if is_frontend_semantic_type_spec(parameter.type_spec):
                    _require_public_semantic_contract(
                        parameter.type_spec,
                        record_type_specs,
                        context=f"{name}() parameter {parameter.name!r}",
                    )
            if is_frontend_semantic_type_spec(spec.result):
                _require_public_semantic_contract(
                    spec.result,
                    record_type_specs,
                    context=f"{name}() result",
                )
        if overloads:
            for spec in specs:
                if any(parameter.type_spec.kind != "NF_SET" for parameter in spec.parameters):
                    raise CompileError(f"{name}() overloaded families support only direct NodeForge parameters")
                if not is_executable_result_type_spec(spec.result):
                    raise CompileError(f"{name}() overloaded families support only executable results")
        families[callable_id] = specs
        refs[callable_id] = implementation_ref
    return families, refs


__all__ = [
    "EXTENSION_API_VERSION",
    "normalize_implementation_ref",
    "normalize_interface_module",
    "normalize_interface_records",
    "normalize_semantic_type_annotation",
]

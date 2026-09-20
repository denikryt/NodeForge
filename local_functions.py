"""Physical helpers for prepared local functions and retained v1 backend helpers."""

import ast
import json
import re

import bpy

from .blender_group_authority import is_authority_ineligible_group
from .errors import CompileError
from .function_instances import (
    FUNCTION_DEFINITION_OWNER_PROP,
    FUNCTION_INSTANCE_KEY_PROP,
    stamp_function_metadata,
)
from .function_materializer import LocalFunctionMaterializationSpec
from .nf_types import serialize_nf_type
from .source_callables import LocalReturnElement, LocalReturnShape


LOCAL_HELPER_KIND_PROP = "nodeforge_generated_kind"
LOCAL_HELPER_KIND = "local_function_helper"
LOCAL_HELPER_NAMESPACE_PROP = "nodeforge_local_function_namespace"
LOCAL_HELPER_NAME_PROP = "nodeforge_local_function_name"
LOCAL_HELPER_SIGNATURE_PROP = "nodeforge_local_function_signature"
LOCAL_HELPER_SOURCE_PROP = "nodeforge_local_function_source"
LOCAL_HELPER_RETURN_PROP = "nodeforge_local_function_return_shape"

def _logical_namespace(name):
    """Return the stable, non-lossy parent identity used for helper ownership."""
    return str(name or "Group")

def _local_helper_group_name(namespace, function_name, signature):
    """Return the readable Blender datablock name for a local helper.

    Helper identity is stored in metadata, not encoded in the datablock name.
    Blender may append a numeric suffix when several helpers share the same
    function name; reuse remains deterministic because lookup uses metadata.
    """
    parts = [part for part in re.split(r"[_\s]+", function_name or "") if part]
    return " ".join(part[:1].upper() + part[1:] for part in parts) or "Function"


def _find_local_helper(*, namespace, function_name, signature, definition_owner=None, instance_key=None, transaction=None):
    """Return the unique helper matching the exact metadata identity."""
    matches = []
    for group in bpy.data.node_groups:
        if transaction is not None and hasattr(transaction, "owns_group") and transaction.owns_group(group):
            continue
        if is_authority_ineligible_group(group):
            continue
        if _helper_metadata_matches(
            group,
            namespace=namespace,
            function_name=function_name,
            signature=signature,
            return_shape=None,
            definition_owner=definition_owner,
            instance_key=instance_key,
        ):
            matches.append(group)
    if len(matches) > 1:
        raise CompileError(
            f"Multiple local function helpers match {function_name}() with signature {signature}"
        )
    return matches[0] if matches else None

def _helper_metadata_matches(group, *, namespace, function_name, signature, return_shape=None, definition_owner=None, instance_key=None):
    """Return True when an existing group is an owned matching local-function helper."""
    try:
        if group.get(LOCAL_HELPER_KIND_PROP) != LOCAL_HELPER_KIND:
            return False
        if group.get(LOCAL_HELPER_NAME_PROP) != function_name:
            return False
        if group.get(LOCAL_HELPER_SIGNATURE_PROP) != signature:
            return False
        if return_shape is not None and group.get(LOCAL_HELPER_RETURN_PROP) != _serialize_return_shape(return_shape):
            return False
        stored_owner = group.get(FUNCTION_DEFINITION_OWNER_PROP)
        stored_key = str(group.get(FUNCTION_INSTANCE_KEY_PROP) or "")
        wanted_key = str(instance_key or "")
        if definition_owner is not None and stored_owner:
            return stored_owner == definition_owner and stored_key == wanted_key
        return (
            not stored_owner
            and not wanted_key
            and group.get(LOCAL_HELPER_NAMESPACE_PROP) == namespace
        )
    except Exception:
        return False


def _serialize_return_shape(shape, types=None):
    """Serialize ordered local return metadata for Blender custom properties."""
    payload = [{"key": e.key, "name": e.socket_name} for e in shape.elements]
    if types is not None:
        for item, typ in zip(payload, types):
            item["type"] = serialize_nf_type(typ)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _write_helper_metadata(group, *, namespace, function_name, signature, source, return_shape, return_types, definition_owner=None, instance_key=None, fingerprint=None):
    """Persist local-helper ownership metadata used by future reuse checks."""
    group[LOCAL_HELPER_KIND_PROP] = LOCAL_HELPER_KIND
    group[LOCAL_HELPER_NAMESPACE_PROP] = namespace
    group[LOCAL_HELPER_NAME_PROP] = function_name
    group[LOCAL_HELPER_SIGNATURE_PROP] = signature
    group[LOCAL_HELPER_SOURCE_PROP] = source
    group[LOCAL_HELPER_RETURN_PROP] = _serialize_return_shape(return_shape, return_types)
    stamp_function_metadata(
        group,
        instance_key=instance_key,
        definition_owner=definition_owner,
        fingerprint=fingerprint,
    )



def build_prepared_local_materialization_spec(
    prepared_callable,
    materialization,
    *,
    helper_namespace: str,
):
    """Build physical local-helper metadata from an already-prepared source contract."""
    function_id = prepared_callable.contract.function_id
    if materialization.callee != function_id:
        raise CompileError("Internal error: prepared local callable FunctionId mismatch")
    logical_namespace = _logical_namespace(helper_namespace)
    group_name = _local_helper_group_name(logical_namespace, function_id.name, function_id.signature)
    prepared = prepared_callable.group
    return_shape = LocalReturnShape(
        tuple(
            LocalReturnElement(index, output.display_name, f"return:{index}", ast.Constant(value=None))
            for index, output in enumerate(prepared_callable.contract.outputs)
        )
    )
    own_inputs = {
        "kind": "local-def",
        "definition_owner": function_id.definition_owner,
        "name": function_id.name,
        "signature": function_id.signature,
        "source": prepared.source,
    }

    def finalize_local_group(group, fingerprint, instance_key):
        """Persist existing local-helper metadata from semantic contract facts."""
        _write_helper_metadata(
            group,
            namespace=logical_namespace,
            function_name=function_id.name,
            signature=function_id.signature,
            source=prepared.source,
            return_shape=return_shape,
            return_types=[output.typ for output in prepared_callable.contract.outputs],
            definition_owner=function_id.definition_owner,
            instance_key=instance_key,
            fingerprint=fingerprint,
        )
        group.name = group_name

    return LocalFunctionMaterializationSpec(
        materialization=materialization,
        logical_namespace=logical_namespace,
        group_name=group_name,
        prepared_compilation=prepared,
        definition_owner=function_id.definition_owner,
        own_inputs=own_inputs,
        helper_namespace=logical_namespace,
        find_existing=_find_local_helper,
        is_live_group=lambda group: any(candidate is group for candidate in bpy.data.node_groups),
        finalize_group=finalize_local_group,
    )

def compile_backend_builtin_call(comp, expr, depth=0):
    """Compile a package-local Python helper exposed only while compiling source.nf."""
    name = expr.func.id
    helper = comp.backend_builtins.get(name)
    if not callable(helper):
        raise CompileError(f"Local backend helper {name} is not callable")
    return helper(comp, expr, depth)

__all__ = [
    "build_prepared_local_materialization_spec",
    "compile_backend_builtin_call",
]

"""Materialize one typed Python extension occurrence in its own editable group."""

from __future__ import annotations

import hashlib
import re
import bpy
from dataclasses import replace

from ..errors import CompileError
from ..function_instances import extension_instance_key_for
from ..semantic.ir import IRExtensionMaterializationMode
from .extension_backend import _selected_spec, lower_extension_call
from .group_authority import find_unique_authoritative_group
from .socket_types import socket_type_for_nf_type, validate_runtime_value_socket
from .values import make_value


EXTENSION_AUTHORITY_PROP = 'nodeforge_extension_call_authority_v1'
EXTENSION_FINGERPRINT_PROP = 'nodeforge_extension_call_fingerprint_v1'


def _input_names(operation, spec):
    """Provide one ordered physical input per retained IR runtime operand."""
    if operation.extension_state_type is not None:
        return tuple(f'Dependency_{index}' for index in range(len(operation.arguments)))
    params = tuple(spec.python_signature().parameters)
    names = []
    for argument in operation.arguments:
        if argument.parameter_index is None or argument.parameter_index >= len(params):
            raise CompileError('Invalid physical extension argument index')
        name = params[argument.parameter_index]
        if argument.variadic_index is not None:
            name = f'{name}_{argument.variadic_index}'
        names.append(name)
    if len(names) != len(set(names)):
        raise CompileError('Extension runtime inputs must have distinct physical names')
    return tuple(names)


def _output_names(operation, spec):
    """Use the selected result carrier, never its coincidental output count."""
    if spec.result.kind == 'NF_SET':
        if len(operation.results) != 1:
            raise CompileError('Extension scalar result has invalid arity')
        return ('Result',)
    return tuple(f'Result_{index}' for index in range(len(operation.results)))


def _fingerprint(operation, spec, registry):
    """Fingerprint effective call shape, state and admitted owner implementation."""
    payload = (
        operation.target.extension_callable_id, operation.target.extension_overload_index,
        spec, tuple((a.parameter_index, a.variadic_index, a.value.typ) for a in operation.arguments),
        tuple((a.parameter_index, a.variadic_index, a.value) for a in operation.static_arguments),
        operation.extension_state_type, operation.extension_state,
        tuple(r.typ for r in operation.results), spec.result.kind,
        registry.owner_fingerprint(operation.target.extension_callable_id.owner),
    )
    return hashlib.sha256(repr(payload).encode('utf-8')).hexdigest()


def _socket_at(sockets, position, expected_type, group, *, output):
    if position >= len(sockets):
        raise CompileError('Extension group socket count does not match typed Call IR')
    socket = sockets[position]
    validate_runtime_value_socket(socket, expected_type, group,
                                  context='extension group interface', require_output=output)
    return socket


def materialize_extension_call(context, operation, operands, x, y):
    """Publish the canonical child and create its single calling GeometryNodeGroup."""
    materialization = operation.extension_materialization
    if materialization is None or materialization.mode is not IRExtensionMaterializationMode.GROUPED:
        raise CompileError('Internal error: grouped extension has no grouped materialization')
    site = materialization.call_site
    if site is None:
        raise CompileError('Grouped extension requires a canonical physical call-site')
    fc = context.function_materialization_context
    fm = context.function_materializer
    if fc is None or fm is None or fc.function_group_transaction is None:
        raise CompileError('Grouped extension requires a shared group publication transaction')
    registry = context.extension_registry
    if registry is None:
        raise CompileError('Grouped extension requires a resolved extension registry')
    spec = _selected_spec(registry, operation)
    key = extension_instance_key_for(site)
    input_names = _input_names(operation, spec)
    output_names = _output_names(operation, spec)
    identity = site.owner_scope + ':' + repr((site.callee.owner, site.callee.name, site.ordinal))
    cache = fc.function_group_cache
    cache_key = ('extension', identity)
    group = cache.get(cache_key)
    if group is None:
        existing = find_unique_authoritative_group(
            lambda candidate: candidate.get(EXTENSION_AUTHORITY_PROP) == identity,
            description=f'extension {site.callee.owner}::{site.callee.name} occurrence {site.ordinal}',
            transaction=fc.function_group_transaction,
        )
        fingerprint = _fingerprint(operation, spec, registry)

        def populate(candidate, resources):
            from .ir_lowering import BlenderIRLoweringContext

            for name, argument in zip(input_names, operation.arguments):
                candidate.interface.new_socket(
                    name=name, in_out='INPUT', socket_type=socket_type_for_nf_type(argument.value.typ),
                )
            for name, result in zip(output_names, operation.results):
                candidate.interface.new_socket(
                    name=name, in_out='OUTPUT', socket_type=socket_type_for_nf_type(result.typ),
                )
            entry = candidate.nodes.new('NodeGroupInput')
            outlet = candidate.nodes.new('NodeGroupOutput')
            outlet.is_active_output = True
            if len(entry.outputs) < len(operands) or len(outlet.inputs) < len(output_names):
                raise CompileError('Extension group interface is incomplete')
            bound = []
            for index, argument in enumerate(operation.arguments):
                socket = _socket_at(entry.outputs, index, argument.value.typ, candidate, output=True)
                if socket.name != input_names[index]:
                    raise CompileError('Extension group input order has changed')
                bound.append(make_value(socket, argument.value.typ))
            sub_context = replace(context, group=candidate, generated_resource_transaction=resources,
                                  root_input_endpoint=None)
            result = lower_extension_call(sub_context, operation, bound, 0, -300)
            result_values = result if isinstance(result, tuple) else (result,)
            if len(result_values) != len(output_names):
                raise CompileError('Extension backend returned unexpected output arity')
            for index, (value, expected) in enumerate(zip(result_values, operation.results)):
                validate_runtime_value_socket(value.socket, expected.typ, candidate,
                                              context='extension grouped result', require_output=True)
                socket = _socket_at(outlet.inputs, index, expected.typ, candidate, output=False)
                if socket.name != output_names[index]:
                    raise CompileError('Extension group output order has changed')
                candidate.links.new(value.socket, socket)
            candidate[EXTENSION_AUTHORITY_PROP] = identity
            candidate[EXTENSION_FINGERPRINT_PROP] = fingerprint

        label = re.sub(r'[^A-Za-z0-9_.-]', '_', '.'.join((*site.callee.owner[-1:], site.callee.name)))
        group = fm._group_backend.publish_candidate(
            name=f'NodeForge.ext.{label}', existing_group=existing,
            build_tx=fc.function_group_transaction, populate=populate,
        )
        cache[cache_key] = group

    node = context.group.nodes.new('GeometryNodeGroup')
    node.location = (x, y)
    node.node_tree = group
    inputs = [s for s in node.inputs if getattr(s, 'enabled', True) and not getattr(s, 'hide', False)]
    outputs = [s for s in node.outputs if getattr(s, 'enabled', True) and not getattr(s, 'hide', False)]
    if len(inputs) != len(operands) or len(outputs) != len(operation.results):
        raise CompileError('Extension group physical interface has changed')
    for index, (operand, argument) in enumerate(zip(operands, operation.arguments)):
        validate_runtime_value_socket(operand.socket, argument.value.typ, context.group,
                                      context='extension caller input', require_output=True)
        if inputs[index].name != input_names[index] or inputs[index].is_output:
            raise CompileError('Extension group caller input layout does not match IR')
        if inputs[index].bl_idname != socket_type_for_nf_type(argument.value.typ):
            raise CompileError('Extension group caller input has wrong physical type')
        context.group.links.new(operand.socket, inputs[index])
    results = []
    for index, expected in enumerate(operation.results):
        socket = validate_runtime_value_socket(outputs[index], expected.typ, context.group,
                                               context='extension caller output', require_output=True)
        if socket.name != output_names[index]:
            raise CompileError('Extension group caller output layout does not match IR')
        results.append(make_value(socket, expected.typ))
    return results[0] if spec.result.kind == 'NF_SET' else tuple(results)


def retire_unused_extension_groups():
    """Retire exact session-created extension groups after the transaction PONR.

    Authority metadata by itself cannot authorize deletion: .blend reloads and
    copied datablocks retain those strings. Only the backend's session-published
    registry proves this exact Blender ID was created by a committed build.
    """
    from .generated_resources import (
        build_manifest, capture_owned_resource_ids, cleanup_previous_after_commit,
        read_group_manifest,
    )
    from .group_authority import forget, is_authority_ineligible_group, session_published_groups

    # Removing one child can make its own nested child eligible, so repeat
    # until no additional exact unreferenced extension group remains.
    while True:
        retired = False
        for group in session_published_groups():
            if (not group.get(EXTENSION_AUTHORITY_PROP)
                    or not group.get(EXTENSION_FINGERPRINT_PROP)
                    or is_authority_ineligible_group(group)
                    or group.users != 0
                    or group.use_fake_user
                    or getattr(group, 'asset_data', None) is not None):
                continue
            # A copied group can retain identical authority metadata. With
            # more than one claimant, neither physical ID may be retired.
            authority = group.get(EXTENSION_AUTHORITY_PROP)
            if any(other != group and other.get(EXTENSION_AUTHORITY_PROP) == authority
                   for other in bpy.data.node_groups):
                continue
            # Blender's user count and physical references must both agree.
            if any(getattr(node, 'node_tree', None) is group
                   for owner in bpy.data.node_groups for node in owner.nodes):
                continue
            if any(getattr(modifier, 'node_group', None) is group
                   for obj in bpy.data.objects for modifier in obj.modifiers):
                continue
            old_manifest = read_group_manifest(group)
            captured = capture_owned_resource_ids(group, old_manifest) if old_manifest else ()
            bpy.data.node_groups.remove(group)
            forget(group)
            retired = True
            if old_manifest:
                cleanup_previous_after_commit(
                    old_manifest, build_manifest(old_manifest['owner_group_uuid'], []),
                    captured_ids=captured,
                )
        if not retired:
            return


__all__ = ['materialize_extension_call', 'retire_unused_extension_groups']

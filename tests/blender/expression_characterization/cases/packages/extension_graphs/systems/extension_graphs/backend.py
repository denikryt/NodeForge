"""Intentionally simple physical owners; topology, not numerical evaluation, is pinned."""

from NodeForge.nf_types import NFType


def scale(context, value):
    """Create a multiply node in the current physical caller."""
    node = context.group.nodes.new("ShaderNodeMath")
    node.operation = "MULTIPLY"
    node.inputs[1].default_value = 2.0
    context.group.links.new(value.socket, node.inputs[0])
    return context.value(node.outputs[0], NFType.FLOAT)


def mixed(context, value):
    """Return independently typed Float and Vector sockets."""
    scaled = scale(context, value)
    vector = context.group.nodes.new("ShaderNodeCombineXYZ")
    context.group.links.new(value.socket, vector.inputs[0])
    return {"scalar": scaled, "vector": context.value(vector.outputs[0], NFType.VECTOR)}


def choose(context, value, form="FLOAT"):
    """Return a singleton named result selected by form."""
    if form == "VECTOR":
        vector = context.group.nodes.new("ShaderNodeCombineXYZ")
        context.group.links.new(value.socket, vector.inputs[0])
        return {"value": context.value(vector.outputs[0], NFType.VECTOR)}
    return {"value": value}


def capture(context, geometry):
    """Materialize typed Capture Items, including Rotation."""
    group = context.group
    cap = group.nodes.new("GeometryNodeCaptureAttribute")
    cap.capture_items.clear()
    cap.capture_items.new("ROTATION", "Spin")
    cap.capture_items.new("FLOAT", "Amount")
    cap.domain = "POINT"
    cap.inputs["Amount"].default_value = 0.5
    group.links.new(geometry.socket, cap.inputs["Geometry"])
    rotation = group.nodes.new("FunctionNodeEulerToRotation")
    group.links.new(rotation.outputs[0], cap.inputs["Spin"])
    return context.value(cap.outputs["Geometry"], NFType.GEOMETRY)


def consume_part(context, semantic_state):
    """Reconstruct one semantic dependency into an ADD node."""
    value = semantic_state.value
    node = context.group.nodes.new("ShaderNodeMath")
    node.operation = "ADD"
    node.inputs[1].default_value = 4.0
    context.group.links.new(value.socket, node.inputs[0])
    return context.value(node.outputs[0], NFType.FLOAT)



def named_one(context, value):
    """Materialize an explicit singleton dictionary."""
    return {"value": scale(context, value)}


def named_two(context, value):
    """Return leaves in reverse declaration order to test key matching."""
    return {"original": value, "scaled": scale(context, value)}


def positional_one(context, value):
    """Return an explicit tuple rather than a direct leaf."""
    return (scale(context, value),)


def shape(context, value, form="DIRECT"):
    """Materialize the explicitly selected result shape."""
    if form == "VECTOR":
        vector = context.group.nodes.new("ShaderNodeCombineXYZ")
        context.group.links.new(value.socket, vector.inputs[0])
        return context.value(vector.outputs[0], NFType.VECTOR)
    if form == "ONE" or form == "BAD":
        return named_one(context, value)
    if form == "MULTI":
        return named_two(context, value)
    return scale(context, value)


def shape_state(context, semantic_state):
    """Use the reconstructed state with the same physical operation."""
    return shape(context, semantic_state.value, semantic_state.form)


def structured(context, items, extra, labels):
    """Link runtime leaves and write static leaves into native math defaults."""
    from NodeForge.extension_api import ExtensionBackendValue
    value = items['x']
    for operand in (items['weight'], *extra):
        node = context.group.nodes.new('ShaderNodeMath')
        node.operation = 'ADD'
        for socket, argument in zip(node.inputs, (value, operand)):
            if isinstance(argument, ExtensionBackendValue):
                context.group.links.new(argument.socket, socket)
            else:
                socket.default_value = argument
        value = context.value(node.outputs[0], NFType.FLOAT)
    return {labels[0]: value} if labels else value

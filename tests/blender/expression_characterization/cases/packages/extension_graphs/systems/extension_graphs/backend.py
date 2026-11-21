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
    return (scaled, context.value(vector.outputs[0], NFType.VECTOR))


def choose(context, value, form="FLOAT"):
    """Return a singleton typed tuple selected by form."""
    if form == "VECTOR":
        vector = context.group.nodes.new("ShaderNodeCombineXYZ")
        context.group.links.new(value.socket, vector.inputs[0])
        return (context.value(vector.outputs[0], NFType.VECTOR),)
    return (value,)


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

"""Physical Blender operations shared by the generated wrappers.

No Inspector state tables are needed at runtime. Blender is the authority for
RNA properties, Menu sockets, and the resulting physical socket layout.
"""
import keyword
import re
from NodeForge.extension_api import ExtensionBackendValue, NFType


def sanitized(raw):
    v=re.sub('[^a-z0-9_]+','_',raw.lower()).strip('_')
    if not v or v[0].isdigit() or keyword.iskeyword(v):v='option_'+v
    return v

def capture_entries(elements, supported):
    """Parse capture collection entries, preserving exact item labels."""
    chunks = tuple(tuple(chunk.strip().split(':', 1)) for chunk in elements.split(',')) if elements else ()
    if any(len(pair) != 2 or pair[0] not in supported or not pair[1] for pair in chunks):
        raise ValueError('elements must be TYPE:Name entries with supported types')
    if len({pair[1] for pair in chunks}) != len(chunks):
        raise ValueError('Duplicate capture item names')
    return chunks

def menu_names(elements):
    """Parse ordered, unique Menu Switch labels."""
    names = tuple(chunk.strip() for chunk in elements.split(',')) if elements else ()
    if not all(names) or len(names) != len(set(names)):
        raise ValueError('elements must contain unique nonempty Menu names')
    return names

_TYPES = {
    'NodeSocketFloat': NFType.FLOAT, 'NodeSocketInt': NFType.INT,
    'NodeSocketBool': NFType.BOOL, 'NodeSocketVector': NFType.VECTOR,
    'NodeSocketColor': NFType.COLOR, 'NodeSocketRotation': NFType.ROTATION,
    'NodeSocketGeometry': NFType.GEOMETRY, 'NodeSocketString': NFType.STRING,
    'NodeSocketObject': NFType.OBJECT, 'NodeSocketMaterial': NFType.MATERIAL,
}
_EXCLUDED = {'NodeSocketIntVector3D', 'NodeSocketVector2D', 'NodeSocketVector4D',
             'NodeSocketVectorXYZ2D', 'NodeSocketBundle', 'NodeSocketMenu',
             'NodeSocketVirtual', 'NodeSocketMatrix', 'NodeSocketClosure',
             'NodeSocketImage', 'NodeSocketCollection', 'NodeSocketFont', 'NodeSocketSound'}


def socket_type(socket):
    name = socket.bl_idname
    if name in _EXCLUDED:
        return None
    return next((typ for prefix, typ in _TYPES.items() if name.startswith(prefix)), None)


def active_sockets(sockets, *, dynamic=False, menu=False):
    return [sock for sock in sockets
            if sock.bl_idname != 'NodeSocketVirtual'
            and (not menu or sock.bl_idname != 'NodeSocketMenu')
            and sock.enabled and not getattr(sock, 'is_unavailable', False)
            and (dynamic or not getattr(sock, 'is_inactive', False))]


def make_node(context, node_id, properties, orders):
    # Test the small set of observed RNA assignment orders on fresh nodes.
    # Changing an earlier property can invalidate later enum assignments.
    fallback = None
    last_error = None
    for order in (orders or ((),)):
        node = context.group.nodes.new(node_id)
        node.location = context.location
        try:
            for name in order:
                value = properties[name]
                if node.bl_rna.properties[name].is_enum_flag:
                    value = set(filter(None, value.split(',')))
                setattr(node, name, value)
        except (TypeError, ValueError) as exc:
            last_error = exc
            context.group.nodes.remove(node)
            continue
        if all(getattr(node, name) == (set(filter(None, value.split(',')))
                if node.bl_rna.properties[name].is_enum_flag else value)
               for name, value in properties.items()):
            if fallback is not None:
                context.group.nodes.remove(fallback)
            return node
        # Blender can silently normalize an impossible RNA combination.
        # Preserve it for independent comparison against the Inspector state.
        if fallback is not None:
            context.group.nodes.remove(fallback)
        fallback = node
    if fallback is not None:
        return fallback
    raise ValueError(f'Blender rejected RNA configuration {properties!r}: {last_error}')


def set_collection(node, kind, elements, capture_types):
    if kind == 'index':
        if type(elements) is not int or elements < 0:
            raise ValueError('elements must be a nonnegative integer')
        items = node.index_switch_items
        while len(items) > elements: items.remove(items[-1])
        while len(items) < elements: items.new()
    elif kind == 'menu':
        names = menu_names(elements)
        if tuple(x.name for x in node.enum_items) != names:
            node.enum_items.clear()
            for name in names: node.enum_items.new(name)
        return names
    elif kind == 'capture':
        entries = capture_entries(elements, capture_types)
        for token, name in entries:
            node.capture_items.new(token, name)
        return tuple(name for _, name in entries)
    return ()


def deliver(context, socket, value):
    typ = socket_type(socket)
    if typ is None:
        raise TypeError(f'Unsupported Blender input type: {socket.bl_idname}')
    if isinstance(value, ExtensionBackendValue):
        if value.typ is not typ:
            raise TypeError(f'{socket.identifier} expects {typ}, got {value.typ}')
        context.group.links.new(value.socket, socket)
    else:
        socket.default_value = value


def materialize(context, node_id, named, items=(), rna=None, menus=(), orders=(),
                kind=None, elements=None, capture_types=None,
                constant_rna=None, constant_outputs=None):
    node = make_node(context, node_id, rna or {}, orders)
    requested_names = set_collection(node, kind, elements, capture_types) if kind else ()
    for key, value in menus:
        candidates = [sock for sock in node.inputs if sock.identifier == key]
        if len(candidates) != 1:
            raise ValueError(f'Missing or ambiguous Menu socket: {key!r}')
        candidates[0].default_value = value
        if candidates[0].default_value != value:
            raise ValueError(f'Blender rejected Menu value {value!r}')
    available = active_sockets(node.inputs, dynamic=bool(kind), menu=True)
    # An explicitly supplied named input must exist in the selected configuration.
    for key, value in named.items():
        if value is None:
            continue
        matches = [sock for sock in available if sock.identifier == key]
        if len(matches) != 1:
            raise ValueError(f'{node_id}: input {key!r} is unavailable or ambiguous in this configuration')
        deliver(context, matches[0], value)
    # Only collection-created sockets remain positional.
    variable_inputs = [sock for sock in available if sock.identifier not in named]
    if len(items) > len(variable_inputs):
        raise ValueError(f'{node_id} has {len(variable_inputs)} variable inputs; got {len(items)}')
    for socket, value in zip(variable_inputs, items):
        deliver(context, socket, value)
    # A missing optional constant is intentionally not assigned: Blender owns
    # its defaults. These are compile-time-only knobs, never runtime links.
    for key, value in (constant_rna or {}).items():
        if value is not None:
            setattr(node, key, value)
    for key, value in (constant_outputs or {}).items():
        if value is None:
            continue
        matches = [sock for sock in node.outputs if sock.identifier == key]
        if len(matches) != 1 or not hasattr(matches[0], 'default_value'):
            raise ValueError(f'{node_id}: missing or ambiguous writable output {key!r}')
        matches[0].default_value = value
    outputs = active_sockets(node.outputs, dynamic=bool(kind))
    if len(outputs) != len(context.result_types) or len(requested_names) > len(outputs):
        raise ValueError(f'{node_id}: unexpected output count; '
                         f'physical identifiers {[socket.identifier for socket in outputs]!r}')
    base_count = len(outputs) - len(requested_names)
    observed_names = tuple(sanitized(socket.name) if index < base_count else socket.name
                           for index, socket in enumerate(outputs))
    if tuple(observed_names[base_count:]) != requested_names:
        raise ValueError(f'{node_id}: collection output labels differ from {requested_names!r}')
    observed = tuple(zip(observed_names, (socket_type(socket) for socket in outputs)))
    expected = tuple(zip(context.result_names, context.result_types))
    if observed != expected:
        raise ValueError(f'{node_id}: output schema {observed!r} does not match {expected!r}; '
                         f'physical identifiers {[socket.identifier for socket in outputs]!r}')
    return {key: context.value(socket, typ) for (key, typ), socket in zip(observed, outputs)}



def align_euler_to_vector(
    context,
    *,
    rotation=None,
    factor=None,
    vector=None,
    axis='X',
    pivot_axis='AUTO',
):
    return materialize(
        context,
        'FunctionNodeAlignEulerToVector',
        named={'Rotation': rotation, 'Factor': factor, 'Vector': vector},
        items=(),
        rna={'axis': axis, 'pivot_axis': pivot_axis},
        menus=(),
        orders=[('axis', 'pivot_axis')],
        constant_rna={},
        constant_outputs={},
    )

def align_rotation_to_vector(
    context,
    *,
    rotation=None,
    factor=None,
    vector=None,
    axis='Z',
    pivot_axis='AUTO',
):
    return materialize(
        context,
        'FunctionNodeAlignRotationToVector',
        named={'Rotation': rotation, 'Factor': factor, 'Vector': vector},
        items=(),
        rna={'axis': axis, 'pivot_axis': pivot_axis},
        menus=(),
        orders=[('axis', 'pivot_axis')],
        constant_rna={},
        constant_outputs={},
    )

def axes_to_rotation(
    context,
    *,
    primary_axis_vector=None,
    secondary_axis_vector=None,
    primary_axis='Z',
    secondary_axis='X',
):
    return materialize(
        context,
        'FunctionNodeAxesToRotation',
        named={'Primary Axis': primary_axis_vector, 'Secondary Axis': secondary_axis_vector},
        items=(),
        rna={'primary_axis': primary_axis, 'secondary_axis': secondary_axis},
        menus=(),
        orders=[('primary_axis', 'secondary_axis')],
        constant_rna={},
        constant_outputs={},
    )

def axis_angle_to_rotation(
    context,
    *,
    axis=None,
    angle=None,
):
    return materialize(
        context,
        'FunctionNodeAxisAngleToRotation',
        named={'Axis': axis, 'Angle': angle},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def bit_math(
    context,
    *,
    a=None,
    b=None,
    shift=None,
    operation='AND',
):
    return materialize(
        context,
        'FunctionNodeBitMath',
        named={'A': a, 'B': b, 'Shift': shift},
        items=(),
        rna={'operation': operation},
        menus=(),
        orders=[('operation',)],
        constant_rna={},
        constant_outputs={},
    )

def boolean_math(
    context,
    *,
    boolean=None,
    boolean_001=None,
    operation='AND',
):
    return materialize(
        context,
        'FunctionNodeBooleanMath',
        named={'Boolean': boolean, 'Boolean_001': boolean_001},
        items=(),
        rna={'operation': operation},
        menus=(),
        orders=[('operation',)],
        constant_rna={},
        constant_outputs={},
    )

def combine_color(
    context,
    *,
    red=None,
    green=None,
    blue=None,
    alpha=None,
    mode='RGB',
):
    return materialize(
        context,
        'FunctionNodeCombineColor',
        named={'Red': red, 'Green': green, 'Blue': blue, 'Alpha': alpha},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def compare(
    context,
    *,
    a=None,
    b=None,
    epsilon=None,
    c=None,
    angle=None,
    data_type='FLOAT',
    mode='ELEMENT',
    operation='GREATER_THAN',
):
    return materialize(
        context,
        'FunctionNodeCompare',
        named={'A': a, 'B': b, 'Epsilon': epsilon, 'C': c, 'Angle': angle},
        items=(),
        rna={'data_type': data_type, 'mode': mode, 'operation': operation},
        menus=(),
        orders=[('data_type', 'mode', 'operation'), ('operation', 'mode', 'data_type')],
        constant_rna={},
        constant_outputs={},
    )

def euler_to_rotation(
    context,
    *,
    euler=None,
):
    return materialize(
        context,
        'FunctionNodeEulerToRotation',
        named={'Euler': euler},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def find_in_string(
    context,
    *,
    string=None,
    search=None,
    mode='From Start',
):
    return materialize(
        context,
        'FunctionNodeFindInString',
        named={'String': string, 'Search': search},
        items=(),
        rna={},
        menus=(('Mode', mode),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def float_to_integer(
    context,
    *,
    float=None,
    rounding_mode='ROUND',
):
    return materialize(
        context,
        'FunctionNodeFloatToInt',
        named={'Float': float},
        items=(),
        rna={'rounding_mode': rounding_mode},
        menus=(),
        orders=[('rounding_mode',)],
        constant_rna={},
        constant_outputs={},
    )

def format_string(
    context,
    *,
    format=None,
):
    return materialize(
        context,
        'FunctionNodeFormatString',
        named={'Format': format},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def hash_value(
    context,
    *,
    value=None,
    seed=None,
    data_type='INT',
):
    return materialize(
        context,
        'FunctionNodeHashValue',
        named={'Value': value, 'Seed': seed},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def boolean(
    context,
    *,
    boolean_2=None,
    boolean=False,
):
    return materialize(
        context,
        'FunctionNodeInputBool',
        named={},
        items=(),
        rna={'boolean': boolean},
        menus=(),
        orders=[('boolean',)],
        constant_rna={'boolean': boolean_2},
        constant_outputs={},
    )

def color(
    context,
    *,
    value=None,
):
    return materialize(
        context,
        'FunctionNodeInputColor',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={'value': value},
        constant_outputs={},
    )

def integer(
    context,
    *,
    integer=None,
):
    return materialize(
        context,
        'FunctionNodeInputInt',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={'integer': integer},
        constant_outputs={},
    )

def rotation(
    context,
    *,
    rotation=None,
):
    return materialize(
        context,
        'FunctionNodeInputRotation',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={'rotation_euler': rotation},
        constant_outputs={},
    )

def special_characters(
    context,
):
    return materialize(
        context,
        'FunctionNodeInputSpecialCharacters',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def string(
    context,
    *,
    string=None,
):
    return materialize(
        context,
        'FunctionNodeInputString',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={'string': string},
        constant_outputs={},
    )

def vector(
    context,
    *,
    vector=None,
):
    return materialize(
        context,
        'FunctionNodeInputVector',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={'vector': vector},
        constant_outputs={},
    )

def integer_math(
    context,
    *,
    value=None,
    value_001=None,
    value_002=None,
    operation='ADD',
):
    return materialize(
        context,
        'FunctionNodeIntegerMath',
        named={'Value': value, 'Value_001': value_001, 'Value_002': value_002},
        items=(),
        rna={'operation': operation},
        menus=(),
        orders=[('operation',)],
        constant_rna={},
        constant_outputs={},
    )

def invert_rotation(
    context,
    *,
    rotation=None,
):
    return materialize(
        context,
        'FunctionNodeInvertRotation',
        named={'Rotation': rotation},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def match_string(
    context,
    *,
    string=None,
    key=None,
    operation='Starts With',
):
    return materialize(
        context,
        'FunctionNodeMatchString',
        named={'String': string, 'Key': key},
        items=(),
        rna={},
        menus=(('Operation', operation),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def quaternion_to_rotation(
    context,
    *,
    w=None,
    x=None,
    y=None,
    z=None,
):
    return materialize(
        context,
        'FunctionNodeQuaternionToRotation',
        named={'W': w, 'X': x, 'Y': y, 'Z': z},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def random_value(
    context,
    *,
    min=None,
    max=None,
    id=None,
    seed=None,
    probability=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'FunctionNodeRandomValue',
        named={'Min': min, 'Max': max, 'ID': id, 'Seed': seed, 'Probability': probability},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def replace_string(
    context,
    *,
    string=None,
    find=None,
    replace=None,
):
    return materialize(
        context,
        'FunctionNodeReplaceString',
        named={'String': string, 'Find': find, 'Replace': replace},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def reverse_string(
    context,
    *,
    string=None,
):
    return materialize(
        context,
        'FunctionNodeReverseString',
        named={'String': string},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def rotate_euler(
    context,
    *,
    rotation=None,
    axis=None,
    angle=None,
    rotate_by=None,
    rotation_type='EULER',
    space='OBJECT',
):
    return materialize(
        context,
        'FunctionNodeRotateEuler',
        named={'Rotation': rotation, 'Axis': axis, 'Angle': angle, 'Rotate By': rotate_by},
        items=(),
        rna={'rotation_type': rotation_type, 'space': space},
        menus=(),
        orders=[('rotation_type', 'space')],
        constant_rna={},
        constant_outputs={},
    )

def rotate_rotation(
    context,
    *,
    rotation=None,
    rotate_by=None,
    rotation_space='GLOBAL',
):
    return materialize(
        context,
        'FunctionNodeRotateRotation',
        named={'Rotation': rotation, 'Rotate By': rotate_by},
        items=(),
        rna={'rotation_space': rotation_space},
        menus=(),
        orders=[('rotation_space',)],
        constant_rna={},
        constant_outputs={},
    )

def rotate_vector(
    context,
    *,
    vector=None,
    rotation=None,
):
    return materialize(
        context,
        'FunctionNodeRotateVector',
        named={'Vector': vector, 'Rotation': rotation},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def rotation_to_axis_angle(
    context,
    *,
    rotation=None,
):
    return materialize(
        context,
        'FunctionNodeRotationToAxisAngle',
        named={'Rotation': rotation},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def rotation_to_euler(
    context,
    *,
    rotation=None,
):
    return materialize(
        context,
        'FunctionNodeRotationToEuler',
        named={'Rotation': rotation},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def rotation_to_quaternion(
    context,
    *,
    rotation=None,
):
    return materialize(
        context,
        'FunctionNodeRotationToQuaternion',
        named={'Rotation': rotation},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def separate_color(
    context,
    *,
    color=None,
    mode='RGB',
):
    return materialize(
        context,
        'FunctionNodeSeparateColor',
        named={'Color': color},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def set_string_case(
    context,
    *,
    string=None,
    case='Uppercase',
):
    return materialize(
        context,
        'FunctionNodeSetStringCase',
        named={'String': string},
        items=(),
        rna={},
        menus=(('Case', case),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def slice_string(
    context,
    *,
    string=None,
    position=None,
    length=None,
):
    return materialize(
        context,
        'FunctionNodeSliceString',
        named={'String': string, 'Position': position, 'Length': length},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def split_string(
    context,
    *,
    string=None,
    separator=None,
):
    return materialize(
        context,
        'FunctionNodeSplitString',
        named={'String': string, 'Separator': separator},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def string_length(
    context,
    *,
    string=None,
):
    return materialize(
        context,
        'FunctionNodeStringLength',
        named={'String': string},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def string_to_value(
    context,
    *,
    string=None,
    base=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'FunctionNodeStringToValue',
        named={'String': string, 'Base': base},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def trim_string(
    context,
    *,
    string=None,
    characters=None,
    whitespace=None,
    start=None,
    end=None,
):
    return materialize(
        context,
        'FunctionNodeTrimString',
        named={'String': string, 'Characters': characters, 'Whitespace': whitespace, 'Start': start, 'End': end},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def value_to_string(
    context,
    *,
    value=None,
    decimals=None,
    base=None,
    padding=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'FunctionNodeValueToString',
        named={'Value': value, 'Decimals': decimals, 'Base': base, 'Padding': padding},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def accumulate_field(
    context,
    *,
    value=None,
    group_index=None,
    data_type='FLOAT',
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeAccumulateField',
        named={'Value': value, 'Group Index': group_index},
        items=(),
        rna={'data_type': data_type, 'domain': domain},
        menus=(),
        orders=[('data_type', 'domain')],
        constant_rna={},
        constant_outputs={},
    )

def domain_size(
    context,
    *,
    geometry=None,
    component='MESH',
):
    return materialize(
        context,
        'GeometryNodeAttributeDomainSize',
        named={'Geometry': geometry},
        items=(),
        rna={'component': component},
        menus=(),
        orders=[('component',)],
        constant_rna={},
        constant_outputs={},
    )

def attribute_statistic(
    context,
    *,
    geometry=None,
    selection=None,
    attribute=None,
    data_type='FLOAT',
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeAttributeStatistic',
        named={'Geometry': geometry, 'Selection': selection, 'Attribute': attribute},
        items=(),
        rna={'data_type': data_type, 'domain': domain},
        menus=(),
        orders=[('data_type', 'domain')],
        constant_rna={},
        constant_outputs={},
    )

def blur_attribute(
    context,
    *,
    value=None,
    iterations=None,
    weight=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeBlurAttribute',
        named={'Value': value, 'Iterations': iterations, 'Weight': weight},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def bounding_box(
    context,
    *,
    geometry=None,
    use_radius=None,
):
    return materialize(
        context,
        'GeometryNodeBoundBox',
        named={'Geometry': geometry, 'Use Radius': use_radius},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def capture_attribute(
    context,
    *items,
    geometry=None,
    selection=None,
    domain='POINT',
    elements='',
):
    return materialize(
        context,
        'GeometryNodeCaptureAttribute',
        named={'Geometry': geometry, 'Selection': selection},
        items=items,
        rna={'domain': domain},
        menus=(),
        orders=[('domain',)],
        constant_rna={},
        constant_outputs={},
        kind='capture',
        elements=elements,
        capture_types=('BOOLEAN', 'FLOAT', 'INT', 'RGBA', 'ROTATION', 'VECTOR'),
    )

def cluster_by_connected(
    context,
    *,
    selection=None,
    position=None,
    distance=None,
):
    return materialize(
        context,
        'GeometryNodeClusterByConnected',
        named={'Selection': selection, 'Position': position, 'Distance': distance},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def cluster_by_distance(
    context,
    *,
    selection=None,
    group_id=None,
    position=None,
    distance=None,
):
    return materialize(
        context,
        'GeometryNodeClusterByDistance',
        named={'Selection': selection, 'Group ID': group_id, 'Position': position, 'Distance': distance},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def convex_hull(
    context,
    *,
    geometry=None,
):
    return materialize(
        context,
        'GeometryNodeConvexHull',
        named={'Geometry': geometry},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def corners_of_edge(
    context,
    *,
    edge_index=None,
    weights=None,
    sort_index=None,
):
    return materialize(
        context,
        'GeometryNodeCornersOfEdge',
        named={'Edge Index': edge_index, 'Weights': weights, 'Sort Index': sort_index},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def corners_of_face(
    context,
    *,
    face_index=None,
    weights=None,
    sort_index=None,
):
    return materialize(
        context,
        'GeometryNodeCornersOfFace',
        named={'Face Index': face_index, 'Weights': weights, 'Sort Index': sort_index},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def corners_of_vertex(
    context,
    *,
    vertex_index=None,
    weights=None,
    sort_index=None,
):
    return materialize(
        context,
        'GeometryNodeCornersOfVertex',
        named={'Vertex Index': vertex_index, 'Weights': weights, 'Sort Index': sort_index},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def cube_grid_topology(
    context,
    *,
    bounds_min=None,
    bounds_max=None,
    resolution_x=None,
    resolution_y=None,
    resolution_z=None,
    min_x=None,
    min_y=None,
    min_z=None,
):
    return materialize(
        context,
        'GeometryNodeCubeGridTopology',
        named={'Bounds Min': bounds_min, 'Bounds Max': bounds_max, 'Resolution X': resolution_x, 'Resolution Y': resolution_y, 'Resolution Z': resolution_z, 'Min X': min_x, 'Min Y': min_y, 'Min Z': min_z},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def arc(
    context,
    *,
    resolution=None,
    start=None,
    middle=None,
    end=None,
    offset_angle=None,
    connect_center=None,
    invert_arc=None,
    radius=None,
    start_angle=None,
    sweep_angle=None,
    mode='RADIUS',
):
    return materialize(
        context,
        'GeometryNodeCurveArc',
        named={'Resolution': resolution, 'Start': start, 'Middle': middle, 'End': end, 'Offset Angle': offset_angle, 'Connect Center': connect_center, 'Invert Arc': invert_arc, 'Radius': radius, 'Start Angle': start_angle, 'Sweep Angle': sweep_angle},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def endpoint_selection(
    context,
    *,
    start_size=None,
    end_size=None,
):
    return materialize(
        context,
        'GeometryNodeCurveEndpointSelection',
        named={'Start Size': start_size, 'End Size': end_size},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def handle_type_selection(
    context,
    *,
    handle_type='AUTO',
    mode='LEFT,RIGHT',
):
    return materialize(
        context,
        'GeometryNodeCurveHandleTypeSelection',
        named={},
        items=(),
        rna={'handle_type': handle_type, 'mode': mode},
        menus=(),
        orders=[('handle_type', 'mode')],
        constant_rna={},
        constant_outputs={},
    )

def curve_length(
    context,
    *,
    curve=None,
):
    return materialize(
        context,
        'GeometryNodeCurveLength',
        named={'Curve': curve},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def curve_of_point(
    context,
    *,
    point_index=None,
):
    return materialize(
        context,
        'GeometryNodeCurveOfPoint',
        named={'Point Index': point_index},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def b_zier_segment(
    context,
    *,
    resolution=None,
    start=None,
    start_handle=None,
    end_handle=None,
    end=None,
    mode='POSITION',
):
    return materialize(
        context,
        'GeometryNodeCurvePrimitiveBezierSegment',
        named={'Resolution': resolution, 'Start': start, 'Start Handle': start_handle, 'End Handle': end_handle, 'End': end},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def curve_circle(
    context,
    *,
    resolution=None,
    point_1=None,
    point_2=None,
    point_3=None,
    radius=None,
    mode='RADIUS',
):
    return materialize(
        context,
        'GeometryNodeCurvePrimitiveCircle',
        named={'Resolution': resolution, 'Point 1': point_1, 'Point 2': point_2, 'Point 3': point_3, 'Radius': radius},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def curve_line(
    context,
    *,
    start=None,
    end=None,
    direction=None,
    length=None,
    mode='POINTS',
):
    return materialize(
        context,
        'GeometryNodeCurvePrimitiveLine',
        named={'Start': start, 'End': end, 'Direction': direction, 'Length': length},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def quadrilateral(
    context,
    *,
    width=None,
    height=None,
    offset=None,
    bottom_width=None,
    top_width=None,
    bottom_height=None,
    top_height=None,
    point_1=None,
    point_2=None,
    point_3=None,
    point_4=None,
    mode='RECTANGLE',
):
    return materialize(
        context,
        'GeometryNodeCurvePrimitiveQuadrilateral',
        named={'Width': width, 'Height': height, 'Offset': offset, 'Bottom Width': bottom_width, 'Top Width': top_width, 'Bottom Height': bottom_height, 'Top Height': top_height, 'Point 1': point_1, 'Point 2': point_2, 'Point 3': point_3, 'Point 4': point_4},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def quadratic_b_zier(
    context,
    *,
    resolution=None,
    start=None,
    middle=None,
    end=None,
):
    return materialize(
        context,
        'GeometryNodeCurveQuadraticBezier',
        named={'Resolution': resolution, 'Start': start, 'Middle': middle, 'End': end},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_handle_type(
    context,
    *,
    curve=None,
    selection=None,
    handle_type='AUTO',
    mode='LEFT,RIGHT',
):
    return materialize(
        context,
        'GeometryNodeCurveSetHandles',
        named={'Curve': curve, 'Selection': selection},
        items=(),
        rna={'handle_type': handle_type, 'mode': mode},
        menus=(),
        orders=[('handle_type', 'mode')],
        constant_rna={},
        constant_outputs={},
    )

def spiral(
    context,
    *,
    resolution=None,
    rotations=None,
    start_radius=None,
    end_radius=None,
    height=None,
    reverse=None,
):
    return materialize(
        context,
        'GeometryNodeCurveSpiral',
        named={'Resolution': resolution, 'Rotations': rotations, 'Start Radius': start_radius, 'End Radius': end_radius, 'Height': height, 'Reverse': reverse},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_spline_type(
    context,
    *,
    curve=None,
    selection=None,
    spline_type='POLY',
):
    return materialize(
        context,
        'GeometryNodeCurveSplineType',
        named={'Curve': curve, 'Selection': selection},
        items=(),
        rna={'spline_type': spline_type},
        menus=(),
        orders=[('spline_type',)],
        constant_rna={},
        constant_outputs={},
    )

def star(
    context,
    *,
    points=None,
    inner_radius=None,
    outer_radius=None,
    twist=None,
):
    return materialize(
        context,
        'GeometryNodeCurveStar',
        named={'Points': points, 'Inner Radius': inner_radius, 'Outer Radius': outer_radius, 'Twist': twist},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def curve_to_mesh(
    context,
    *,
    curve=None,
    profile_curve=None,
    scale=None,
    fill_caps=None,
):
    return materialize(
        context,
        'GeometryNodeCurveToMesh',
        named={'Curve': curve, 'Profile Curve': profile_curve, 'Scale': scale, 'Fill Caps': fill_caps},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def curve_to_points(
    context,
    *,
    curve=None,
    count=None,
    length=None,
    mode='COUNT',
):
    return materialize(
        context,
        'GeometryNodeCurveToPoints',
        named={'Curve': curve, 'Count': count, 'Length': length},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def curves_to_grease_pencil(
    context,
    *,
    curves=None,
    selection=None,
    instances_as_layers=None,
):
    return materialize(
        context,
        'GeometryNodeCurvesToGreasePencil',
        named={'Curves': curves, 'Selection': selection, 'Instances as Layers': instances_as_layers},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def deform_curves_on_surface(
    context,
    *,
    curves=None,
):
    return materialize(
        context,
        'GeometryNodeDeformCurvesOnSurface',
        named={'Curves': curves},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def delete_geometry(
    context,
    *,
    geometry=None,
    selection=None,
    domain='POINT',
    mode='ALL',
):
    return materialize(
        context,
        'GeometryNodeDeleteGeometry',
        named={'Geometry': geometry, 'Selection': selection},
        items=(),
        rna={'domain': domain, 'mode': mode},
        menus=(),
        orders=[('domain', 'mode')],
        constant_rna={},
        constant_outputs={},
    )

def distribute_points_in_grid(
    context,
    *,
    grid=None,
    density=None,
    seed=None,
    spacing=None,
    threshold=None,
    mode='DENSITY_RANDOM',
):
    return materialize(
        context,
        'GeometryNodeDistributePointsInGrid',
        named={'Grid': grid, 'Density': density, 'Seed': seed, 'Spacing': spacing, 'Threshold': threshold},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def distribute_points_in_volume(
    context,
    *,
    volume=None,
    density=None,
    seed=None,
    spacing=None,
    threshold=None,
    mode='Random',
):
    return materialize(
        context,
        'GeometryNodeDistributePointsInVolume',
        named={'Volume': volume, 'Density': density, 'Seed': seed, 'Spacing': spacing, 'Threshold': threshold},
        items=(),
        rna={},
        menus=(('Mode', mode),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def distribute_points_on_faces(
    context,
    *,
    mesh=None,
    selection=None,
    density=None,
    seed=None,
    distance_min=None,
    density_max=None,
    density_factor=None,
    distribute_method='RANDOM',
    use_legacy_normal=False,
):
    return materialize(
        context,
        'GeometryNodeDistributePointsOnFaces',
        named={'Mesh': mesh, 'Selection': selection, 'Density': density, 'Seed': seed, 'Distance Min': distance_min, 'Density Max': density_max, 'Density Factor': density_factor},
        items=(),
        rna={'distribute_method': distribute_method, 'use_legacy_normal': use_legacy_normal},
        menus=(),
        orders=[('distribute_method', 'use_legacy_normal')],
        constant_rna={},
        constant_outputs={},
    )

def dual_mesh(
    context,
    *,
    mesh=None,
    keep_boundaries=None,
):
    return materialize(
        context,
        'GeometryNodeDualMesh',
        named={'Mesh': mesh, 'Keep Boundaries': keep_boundaries},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def duplicate_elements(
    context,
    *,
    geometry=None,
    selection=None,
    amount=None,
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeDuplicateElements',
        named={'Geometry': geometry, 'Selection': selection, 'Amount': amount},
        items=(),
        rna={'domain': domain},
        menus=(),
        orders=[('domain',)],
        constant_rna={},
        constant_outputs={},
    )

def edge_paths_to_curves(
    context,
    *,
    mesh=None,
    start_vertices=None,
    next_vertex_index=None,
):
    return materialize(
        context,
        'GeometryNodeEdgePathsToCurves',
        named={'Mesh': mesh, 'Start Vertices': start_vertices, 'Next Vertex Index': next_vertex_index},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def edge_paths_to_selection(
    context,
    *,
    start_vertices=None,
    next_vertex_index=None,
):
    return materialize(
        context,
        'GeometryNodeEdgePathsToSelection',
        named={'Start Vertices': start_vertices, 'Next Vertex Index': next_vertex_index},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def edges_of_corner(
    context,
    *,
    corner_index=None,
):
    return materialize(
        context,
        'GeometryNodeEdgesOfCorner',
        named={'Corner Index': corner_index},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def edges_of_vertex(
    context,
    *,
    vertex_index=None,
    weights=None,
    sort_index=None,
):
    return materialize(
        context,
        'GeometryNodeEdgesOfVertex',
        named={'Vertex Index': vertex_index, 'Weights': weights, 'Sort Index': sort_index},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def edges_to_face_groups(
    context,
    *,
    boundary_edges=None,
):
    return materialize(
        context,
        'GeometryNodeEdgesToFaceGroups',
        named={'Boundary Edges': boundary_edges},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def extrude_mesh(
    context,
    *,
    mesh=None,
    selection=None,
    offset=None,
    offset_scale=None,
    individual=None,
    mode='FACES',
):
    return materialize(
        context,
        'GeometryNodeExtrudeMesh',
        named={'Mesh': mesh, 'Selection': selection, 'Offset': offset, 'Offset Scale': offset_scale, 'Individual': individual},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def face_of_corner(
    context,
    *,
    corner_index=None,
):
    return materialize(
        context,
        'GeometryNodeFaceOfCorner',
        named={'Corner Index': corner_index},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def evaluate_at_index(
    context,
    *,
    value=None,
    index=None,
    data_type='FLOAT',
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeFieldAtIndex',
        named={'Value': value, 'Index': index},
        items=(),
        rna={'data_type': data_type, 'domain': domain},
        menus=(),
        orders=[('data_type', 'domain')],
        constant_rna={},
        constant_outputs={},
    )

def field_average(
    context,
    *,
    value=None,
    group_index=None,
    data_type='FLOAT',
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeFieldAverage',
        named={'Value': value, 'Group Index': group_index},
        items=(),
        rna={'data_type': data_type, 'domain': domain},
        menus=(),
        orders=[('data_type', 'domain')],
        constant_rna={},
        constant_outputs={},
    )

def field_min_max(
    context,
    *,
    value=None,
    group_index=None,
    data_type='FLOAT',
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeFieldMinAndMax',
        named={'Value': value, 'Group Index': group_index},
        items=(),
        rna={'data_type': data_type, 'domain': domain},
        menus=(),
        orders=[('data_type', 'domain')],
        constant_rna={},
        constant_outputs={},
    )

def evaluate_on_domain(
    context,
    *,
    value=None,
    data_type='FLOAT',
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeFieldOnDomain',
        named={'Value': value},
        items=(),
        rna={'data_type': data_type, 'domain': domain},
        menus=(),
        orders=[('data_type', 'domain')],
        constant_rna={},
        constant_outputs={},
    )

def field_variance(
    context,
    *,
    value=None,
    group_index=None,
    data_type='FLOAT',
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeFieldVariance',
        named={'Value': value, 'Group Index': group_index},
        items=(),
        rna={'data_type': data_type, 'domain': domain},
        menus=(),
        orders=[('data_type', 'domain')],
        constant_rna={},
        constant_outputs={},
    )

def fill_curve(
    context,
    *,
    curve=None,
    group_id=None,
    fill_rule='Even-Odd',
    mode='Triangles',
):
    return materialize(
        context,
        'GeometryNodeFillCurve',
        named={'Curve': curve, 'Group ID': group_id},
        items=(),
        rna={},
        menus=(('Fill Rule', fill_rule), ('Mode', mode)),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def fillet_curve(
    context,
    *,
    curve=None,
    radius=None,
    limit_radius=None,
    count=None,
    mode='Bézier',
):
    return materialize(
        context,
        'GeometryNodeFilletCurve',
        named={'Curve': curve, 'Radius': radius, 'Limit Radius': limit_radius, 'Count': count},
        items=(),
        rna={},
        menus=(('Mode', mode),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def filter_list(
    context,
    *,
    list=None,
    selection=None,
    socket_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeFilterList',
        named={'List': list, 'Selection': selection},
        items=(),
        rna={'socket_type': socket_type},
        menus=(),
        orders=[('socket_type',)],
        constant_rna={},
        constant_outputs={},
    )

def flip_faces(
    context,
    *,
    mesh=None,
    selection=None,
):
    return materialize(
        context,
        'GeometryNodeFlipFaces',
        named={'Mesh': mesh, 'Selection': selection},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def geometry_to_instance(
    context,
    *,
    geometry=None,
):
    return materialize(
        context,
        'GeometryNodeGeometryToInstance',
        named={'Geometry': geometry},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def get_attribute_names(
    context,
    *,
    geometry=None,
    filter_data_type=None,
    filter_domain=None,
    data_type='Float',
    domain='Point',
):
    return materialize(
        context,
        'GeometryNodeGetAttributeNames',
        named={'Geometry': geometry, 'Filter Data Type': filter_data_type, 'Filter Domain': filter_domain},
        items=(),
        rna={},
        menus=(('Data Type', data_type), ('Domain', domain)),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def get_geometry_component(
    context,
    *,
    geometry=None,
    remove=None,
    type='Mesh',
):
    return materialize(
        context,
        'GeometryNodeGetGeometryComponent',
        named={'Geometry': geometry, 'Remove': remove},
        items=(),
        rna={},
        menus=(('Type', type),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def get_named_grid(
    context,
    *,
    volume=None,
    name=None,
    remove=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeGetNamedGrid',
        named={'Volume': volume, 'Name': name, 'Remove': remove},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def dial_gizmo(
    context,
    *,
    value=None,
    position=None,
    up=None,
    screen_space=None,
    radius=None,
    color_id='PRIMARY',
):
    return materialize(
        context,
        'GeometryNodeGizmoDial',
        named={'Value': value, 'Position': position, 'Up': up, 'Screen Space': screen_space, 'Radius': radius},
        items=(),
        rna={'color_id': color_id},
        menus=(),
        orders=[('color_id',)],
        constant_rna={},
        constant_outputs={},
    )

def linear_gizmo(
    context,
    *,
    value=None,
    position=None,
    direction=None,
    color_id='PRIMARY',
    draw_style='ARROW',
):
    return materialize(
        context,
        'GeometryNodeGizmoLinear',
        named={'Value': value, 'Position': position, 'Direction': direction},
        items=(),
        rna={'color_id': color_id, 'draw_style': draw_style},
        menus=(),
        orders=[('color_id', 'draw_style')],
        constant_rna={},
        constant_outputs={},
    )

def grease_pencil_to_curves(
    context,
    *,
    grease_pencil=None,
    selection=None,
    layers_as_instances=None,
):
    return materialize(
        context,
        'GeometryNodeGreasePencilToCurves',
        named={'Grease Pencil': grease_pencil, 'Selection': selection, 'Layers as Instances': layers_as_instances},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def advect_grid(
    context,
    *,
    grid=None,
    velocity=None,
    time_step=None,
    data_type='FLOAT',
    integration_scheme='Runge-Kutta 3',
    limiter='Clamp',
):
    return materialize(
        context,
        'GeometryNodeGridAdvect',
        named={'Grid': grid, 'Velocity': velocity, 'Time Step': time_step},
        items=(),
        rna={'data_type': data_type},
        menus=(('Integration Scheme', integration_scheme), ('Limiter', limiter)),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def clip_grid(
    context,
    *,
    grid=None,
    min_x=None,
    min_y=None,
    min_z=None,
    max_x=None,
    max_y=None,
    max_z=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeGridClip',
        named={'Grid': grid, 'Min X': min_x, 'Min Y': min_y, 'Min Z': min_z, 'Max X': max_x, 'Max Y': max_y, 'Max Z': max_z},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def grid_curl(
    context,
    *,
    grid=None,
):
    return materialize(
        context,
        'GeometryNodeGridCurl',
        named={'Grid': grid},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def grid_dilate_erode(
    context,
    *,
    grid=None,
    steps=None,
    data_type='FLOAT',
    connectivity='Face',
    tiles='Preserve',
):
    return materialize(
        context,
        'GeometryNodeGridDilateAndErode',
        named={'Grid': grid, 'Steps': steps},
        items=(),
        rna={'data_type': data_type},
        menus=(('Connectivity', connectivity), ('Tiles', tiles)),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def grid_divergence(
    context,
    *,
    grid=None,
):
    return materialize(
        context,
        'GeometryNodeGridDivergence',
        named={'Grid': grid},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def grid_gradient(
    context,
    *,
    grid=None,
):
    return materialize(
        context,
        'GeometryNodeGridGradient',
        named={'Grid': grid},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def grid_laplacian(
    context,
    *,
    grid=None,
):
    return materialize(
        context,
        'GeometryNodeGridLaplacian',
        named={'Grid': grid},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def grid_mean(
    context,
    *,
    grid=None,
    width=None,
    iterations=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeGridMean',
        named={'Grid': grid, 'Width': width, 'Iterations': iterations},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def grid_median(
    context,
    *,
    grid=None,
    width=None,
    iterations=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeGridMedian',
        named={'Grid': grid, 'Width': width, 'Iterations': iterations},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def prune_grid(
    context,
    *,
    grid=None,
    threshold=None,
    data_type='FLOAT',
    mode='Threshold',
):
    return materialize(
        context,
        'GeometryNodeGridPrune',
        named={'Grid': grid, 'Threshold': threshold},
        items=(),
        rna={'data_type': data_type},
        menus=(('Mode', mode),),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def grid_to_mesh(
    context,
    *,
    grid=None,
    threshold=None,
    adaptivity=None,
):
    return materialize(
        context,
        'GeometryNodeGridToMesh',
        named={'Grid': grid, 'Threshold': threshold, 'Adaptivity': adaptivity},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def grid_to_points(
    context,
    *,
    grid=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeGridToPoints',
        named={'Grid': grid},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def voxelize_grid(
    context,
    *,
    grid=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeGridVoxelize',
        named={'Grid': grid},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def import_csv(
    context,
    *,
    path=None,
    delimiter=None,
):
    return materialize(
        context,
        'GeometryNodeImportCSV',
        named={'Path': path, 'Delimiter': delimiter},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def import_obj(
    context,
    *,
    path=None,
):
    return materialize(
        context,
        'GeometryNodeImportOBJ',
        named={'Path': path},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def import_ply(
    context,
    *,
    path=None,
):
    return materialize(
        context,
        'GeometryNodeImportPLY',
        named={'Path': path},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def import_stl(
    context,
    *,
    path=None,
):
    return materialize(
        context,
        'GeometryNodeImportSTL',
        named={'Path': path},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def import_text(
    context,
    *,
    path=None,
):
    return materialize(
        context,
        'GeometryNodeImportText',
        named={'Path': path},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def import_vdb(
    context,
    *,
    path=None,
):
    return materialize(
        context,
        'GeometryNodeImportVDB',
        named={'Path': path},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def index_of_nearest(
    context,
    *,
    position=None,
    group_id=None,
):
    return materialize(
        context,
        'GeometryNodeIndexOfNearest',
        named={'Position': position, 'Group ID': group_id},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def index_switch(
    context,
    *items,
    index=None,
    data_type='FLOAT',
    elements=2,
):
    return materialize(
        context,
        'GeometryNodeIndexSwitch',
        named={'Index': index},
        items=items,
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
        kind='index',
        elements=elements,
    )

def active_camera(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputActiveCamera',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def curve_handle_positions(
    context,
    *,
    relative=None,
):
    return materialize(
        context,
        'GeometryNodeInputCurveHandlePositions',
        named={'Relative': relative},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def curve_tilt(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputCurveTilt',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def is_edge_smooth(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputEdgeSmooth',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def id(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputID',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def index(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputIndex',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def instance_bounds(
    context,
    *,
    use_radius=None,
):
    return materialize(
        context,
        'GeometryNodeInputInstanceBounds',
        named={'Use Radius': use_radius},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def instance_reference(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputInstanceReference',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def instance_rotation(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputInstanceRotation',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def instance_scale(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputInstanceScale',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def material(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputMaterial',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def material_index(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputMaterialIndex',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def edge_angle(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputMeshEdgeAngle',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def edge_neighbors(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputMeshEdgeNeighbors',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def edge_vertices(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputMeshEdgeVertices',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def face_area(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputMeshFaceArea',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def is_face_planar(
    context,
    *,
    threshold=None,
):
    return materialize(
        context,
        'GeometryNodeInputMeshFaceIsPlanar',
        named={'Threshold': threshold},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def face_neighbors(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputMeshFaceNeighbors',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def mesh_island(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputMeshIsland',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def vertex_neighbors(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputMeshVertexNeighbors',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def named_attribute(
    context,
    *,
    name=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeInputNamedAttribute',
        named={'Name': name},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def named_layer_selection(
    context,
    *,
    name=None,
):
    return materialize(
        context,
        'GeometryNodeInputNamedLayerSelection',
        named={'Name': name},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def normal(
    context,
    *,
    legacy_corner_normals=False,
):
    return materialize(
        context,
        'GeometryNodeInputNormal',
        named={},
        items=(),
        rna={'legacy_corner_normals': legacy_corner_normals},
        menus=(),
        orders=[('legacy_corner_normals',)],
        constant_rna={},
        constant_outputs={},
    )

def object(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputObject',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def position(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputPosition',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def radius(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputRadius',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def scene_time(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputSceneTime',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def is_face_smooth(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputShadeSmooth',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def shortest_edge_paths(
    context,
    *,
    end_vertex=None,
    edge_cost=None,
):
    return materialize(
        context,
        'GeometryNodeInputShortestEdgePaths',
        named={'End Vertex': end_vertex, 'Edge Cost': edge_cost},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def is_spline_cyclic(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputSplineCyclic',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def spline_resolution(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputSplineResolution',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def curve_tangent(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputTangent',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def voxel_index(
    context,
):
    return materialize(
        context,
        'GeometryNodeInputVoxelIndex',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def instance_on_points(
    context,
    *,
    points=None,
    selection=None,
    instance=None,
    pick_instance=None,
    instance_index=None,
    rotation=None,
    scale=None,
):
    return materialize(
        context,
        'GeometryNodeInstanceOnPoints',
        named={'Points': points, 'Selection': selection, 'Instance': instance, 'Pick Instance': pick_instance, 'Instance Index': instance_index, 'Rotation': rotation, 'Scale': scale},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def instances_to_points(
    context,
    *,
    instances=None,
    selection=None,
    position=None,
    radius=None,
):
    return materialize(
        context,
        'GeometryNodeInstancesToPoints',
        named={'Instances': instances, 'Selection': selection, 'Position': position, 'Radius': radius},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def interpolate_curves(
    context,
    *,
    guide_curves=None,
    guide_up=None,
    guide_group_id=None,
    points=None,
    point_up=None,
    point_group_id=None,
    max_neighbors=None,
):
    return materialize(
        context,
        'GeometryNodeInterpolateCurves',
        named={'Guide Curves': guide_curves, 'Guide Up': guide_up, 'Guide Group ID': guide_group_id, 'Points': points, 'Point Up': point_up, 'Point Group ID': point_group_id, 'Max Neighbors': max_neighbors},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def is_viewport(
    context,
):
    return materialize(
        context,
        'GeometryNodeIsViewport',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def join_geometry(
    context,
    *,
    geometry=None,
):
    return materialize(
        context,
        'GeometryNodeJoinGeometry',
        named={'Geometry': geometry},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def get_list_item(
    context,
    *,
    list=None,
    index=None,
    socket_type='FLOAT',
    structure_type='AUTO',
):
    return materialize(
        context,
        'GeometryNodeListGetItem',
        named={'List': list, 'Index': index},
        items=(),
        rna={'socket_type': socket_type, 'structure_type': structure_type},
        menus=(),
        orders=[('socket_type', 'structure_type')],
        constant_rna={},
        constant_outputs={},
    )

def material_selection(
    context,
    *,
    material=None,
):
    return materialize(
        context,
        'GeometryNodeMaterialSelection',
        named={'Material': material},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def menu_switch(
    context,
    *items,
    data_type='GEOMETRY',
    menu='A',
    elements='A,B',
):
    return materialize(
        context,
        'GeometryNodeMenuSwitch',
        named={},
        items=items,
        rna={'data_type': data_type},
        menus=(('Menu', menu),),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
        kind='menu',
        elements=elements,
    )

def merge_by_distance(
    context,
    *,
    geometry=None,
    selection=None,
    distance=None,
    mode='All',
):
    return materialize(
        context,
        'GeometryNodeMergeByDistance',
        named={'Geometry': geometry, 'Selection': selection, 'Distance': distance},
        items=(),
        rna={},
        menus=(('Mode', mode),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def merge_layers(
    context,
    *,
    grease_pencil=None,
    selection=None,
    group_id=None,
    mode='MERGE_BY_NAME',
):
    return materialize(
        context,
        'GeometryNodeMergeLayers',
        named={'Grease Pencil': grease_pencil, 'Selection': selection, 'Group ID': group_id},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def merge_points(
    context,
    *,
    geometry=None,
    selection=None,
    merge_id=None,
):
    return materialize(
        context,
        'GeometryNodeMergePoints',
        named={'Geometry': geometry, 'Selection': selection, 'Merge ID': merge_id},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def mesh_bevel(
    context,
    *,
    mesh=None,
    selection=None,
    offset=None,
    miter=None,
    segments=None,
    shape=None,
    profile=None,
    start_left_offset=None,
    start_right_offset=None,
    end_left_offset=None,
    end_right_offset=None,
    affect_kind='Edges',
):
    return materialize(
        context,
        'GeometryNodeMeshBevel',
        named={'Mesh': mesh, 'Selection': selection, 'Offset': offset, 'Miter': miter, 'Segments': segments, 'Shape': shape, 'Profile': profile, 'Start Left Offset': start_left_offset, 'Start Right Offset': start_right_offset, 'End Left Offset': end_left_offset, 'End Right Offset': end_right_offset},
        items=(),
        rna={},
        menus=(('Affect Kind', affect_kind),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def mesh_boolean(
    context,
    *,
    mesh_2=None,
    self_intersection=None,
    hole_tolerant=None,
    mesh_1=None,
    operation='DIFFERENCE',
    solver='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeMeshBoolean',
        named={'Mesh 2': mesh_2, 'Self Intersection': self_intersection, 'Hole Tolerant': hole_tolerant, 'Mesh 1': mesh_1},
        items=(),
        rna={'operation': operation, 'solver': solver},
        menus=(),
        orders=[('operation', 'solver')],
        constant_rna={},
        constant_outputs={},
    )

def mesh_circle(
    context,
    *,
    vertices=None,
    radius=None,
    fill_type='NONE',
):
    return materialize(
        context,
        'GeometryNodeMeshCircle',
        named={'Vertices': vertices, 'Radius': radius},
        items=(),
        rna={'fill_type': fill_type},
        menus=(),
        orders=[('fill_type',)],
        constant_rna={},
        constant_outputs={},
    )

def cone(
    context,
    *,
    vertices=None,
    side_segments=None,
    radius_top=None,
    radius_bottom=None,
    depth=None,
    fill_segments=None,
    fill_type='NGON',
):
    return materialize(
        context,
        'GeometryNodeMeshCone',
        named={'Vertices': vertices, 'Side Segments': side_segments, 'Radius Top': radius_top, 'Radius Bottom': radius_bottom, 'Depth': depth, 'Fill Segments': fill_segments},
        items=(),
        rna={'fill_type': fill_type},
        menus=(),
        orders=[('fill_type',)],
        constant_rna={},
        constant_outputs={},
    )

def cube(
    context,
    *,
    size=None,
    vertices_x=None,
    vertices_y=None,
    vertices_z=None,
):
    return materialize(
        context,
        'GeometryNodeMeshCube',
        named={'Size': size, 'Vertices X': vertices_x, 'Vertices Y': vertices_y, 'Vertices Z': vertices_z},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def cylinder(
    context,
    *,
    vertices=None,
    side_segments=None,
    radius=None,
    depth=None,
    fill_segments=None,
    fill_type='NGON',
):
    return materialize(
        context,
        'GeometryNodeMeshCylinder',
        named={'Vertices': vertices, 'Side Segments': side_segments, 'Radius': radius, 'Depth': depth, 'Fill Segments': fill_segments},
        items=(),
        rna={'fill_type': fill_type},
        menus=(),
        orders=[('fill_type',)],
        constant_rna={},
        constant_outputs={},
    )

def face_group_boundaries(
    context,
    *,
    face_set=None,
):
    return materialize(
        context,
        'GeometryNodeMeshFaceSetBoundaries',
        named={'Face Set': face_set},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def grid(
    context,
    *,
    size_x=None,
    size_y=None,
    vertices_x=None,
    vertices_y=None,
):
    return materialize(
        context,
        'GeometryNodeMeshGrid',
        named={'Size X': size_x, 'Size Y': size_y, 'Vertices X': vertices_x, 'Vertices Y': vertices_y},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def ico_sphere(
    context,
    *,
    radius=None,
    subdivisions=None,
):
    return materialize(
        context,
        'GeometryNodeMeshIcoSphere',
        named={'Radius': radius, 'Subdivisions': subdivisions},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def mesh_line(
    context,
    *,
    count=None,
    start_location=None,
    offset=None,
    resolution=None,
    count_mode='TOTAL',
    mode='OFFSET',
):
    return materialize(
        context,
        'GeometryNodeMeshLine',
        named={'Count': count, 'Start Location': start_location, 'Offset': offset, 'Resolution': resolution},
        items=(),
        rna={'count_mode': count_mode, 'mode': mode},
        menus=(),
        orders=[('count_mode', 'mode')],
        constant_rna={},
        constant_outputs={},
    )

def mesh_to_curve(
    context,
    *,
    mesh=None,
    selection=None,
    mode='EDGES',
):
    return materialize(
        context,
        'GeometryNodeMeshToCurve',
        named={'Mesh': mesh, 'Selection': selection},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def mesh_to_density_grid(
    context,
    *,
    mesh=None,
    density=None,
    voxel_size=None,
    gradient_width=None,
):
    return materialize(
        context,
        'GeometryNodeMeshToDensityGrid',
        named={'Mesh': mesh, 'Density': density, 'Voxel Size': voxel_size, 'Gradient Width': gradient_width},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def mesh_to_points(
    context,
    *,
    mesh=None,
    selection=None,
    position=None,
    radius=None,
    mode='VERTICES',
):
    return materialize(
        context,
        'GeometryNodeMeshToPoints',
        named={'Mesh': mesh, 'Selection': selection, 'Position': position, 'Radius': radius},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def mesh_to_sdf_grid(
    context,
    *,
    mesh=None,
    voxel_size=None,
    band_width=None,
):
    return materialize(
        context,
        'GeometryNodeMeshToSDFGrid',
        named={'Mesh': mesh, 'Voxel Size': voxel_size, 'Band Width': band_width},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def mesh_to_volume(
    context,
    *,
    mesh=None,
    density=None,
    voxel_amount=None,
    interior_band_width=None,
    voxel_size=None,
    resolution_mode='Amount',
):
    return materialize(
        context,
        'GeometryNodeMeshToVolume',
        named={'Mesh': mesh, 'Density': density, 'Voxel Amount': voxel_amount, 'Interior Band Width': interior_band_width, 'Voxel Size': voxel_size},
        items=(),
        rna={},
        menus=(('Resolution Mode', resolution_mode),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def uv_sphere(
    context,
    *,
    segments=None,
    rings=None,
    radius=None,
):
    return materialize(
        context,
        'GeometryNodeMeshUVSphere',
        named={'Segments': segments, 'Rings': rings, 'Radius': radius},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def offset_corner_in_face(
    context,
    *,
    corner_index=None,
    offset=None,
):
    return materialize(
        context,
        'GeometryNodeOffsetCornerInFace',
        named={'Corner Index': corner_index, 'Offset': offset},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def offset_point_in_curve(
    context,
    *,
    point_index=None,
    offset=None,
):
    return materialize(
        context,
        'GeometryNodeOffsetPointInCurve',
        named={'Point Index': point_index, 'Offset': offset},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def points(
    context,
    *,
    count=None,
    position=None,
    radius=None,
):
    return materialize(
        context,
        'GeometryNodePoints',
        named={'Count': count, 'Position': position, 'Radius': radius},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def points_of_curve(
    context,
    *,
    curve_index=None,
    weights=None,
    sort_index=None,
):
    return materialize(
        context,
        'GeometryNodePointsOfCurve',
        named={'Curve Index': curve_index, 'Weights': weights, 'Sort Index': sort_index},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def points_to_curves(
    context,
    *,
    points=None,
    curve_group_id=None,
    weight=None,
):
    return materialize(
        context,
        'GeometryNodePointsToCurves',
        named={'Points': points, 'Curve Group ID': curve_group_id, 'Weight': weight},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def points_to_sdf_grid(
    context,
    *,
    points=None,
    radius=None,
    voxel_size=None,
):
    return materialize(
        context,
        'GeometryNodePointsToSDFGrid',
        named={'Points': points, 'Radius': radius, 'Voxel Size': voxel_size},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def points_to_vertices(
    context,
    *,
    points=None,
    selection=None,
):
    return materialize(
        context,
        'GeometryNodePointsToVertices',
        named={'Points': points, 'Selection': selection},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def points_to_volume(
    context,
    *,
    points=None,
    density=None,
    voxel_amount=None,
    radius=None,
    voxel_size=None,
    resolution_mode='Amount',
):
    return materialize(
        context,
        'GeometryNodePointsToVolume',
        named={'Points': points, 'Density': density, 'Voxel Amount': voxel_amount, 'Radius': radius, 'Voxel Size': voxel_size},
        items=(),
        rna={},
        menus=(('Resolution Mode', resolution_mode),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def geometry_proximity(
    context,
    *,
    target=None,
    group_id=None,
    source_position=None,
    sample_group_id=None,
    target_element='FACES',
):
    return materialize(
        context,
        'GeometryNodeProximity',
        named={'Target': target, 'Group ID': group_id, 'Source Position': source_position, 'Sample Group ID': sample_group_id},
        items=(),
        rna={'target_element': target_element},
        menus=(),
        orders=[('target_element',)],
        constant_rna={},
        constant_outputs={},
    )

def raycast(
    context,
    *,
    target_geometry=None,
    attribute=None,
    source_position=None,
    ray_direction=None,
    ray_length=None,
    data_type='FLOAT',
    interpolation='Interpolated',
):
    return materialize(
        context,
        'GeometryNodeRaycast',
        named={'Target Geometry': target_geometry, 'Attribute': attribute, 'Source Position': source_position, 'Ray Direction': ray_direction, 'Ray Length': ray_length},
        items=(),
        rna={'data_type': data_type},
        menus=(('Interpolation', interpolation),),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def realize_instances(
    context,
    *,
    geometry=None,
    selection=None,
    realize_all=None,
    depth=None,
    realize_to_point_domain=False,
):
    return materialize(
        context,
        'GeometryNodeRealizeInstances',
        named={'Geometry': geometry, 'Selection': selection, 'Realize All': realize_all, 'Depth': depth},
        items=(),
        rna={'realize_to_point_domain': realize_to_point_domain},
        menus=(),
        orders=[('realize_to_point_domain',)],
        constant_rna={},
        constant_outputs={},
    )

def remove_named_attribute(
    context,
    *,
    geometry=None,
    name=None,
    pattern_mode='Exact',
):
    return materialize(
        context,
        'GeometryNodeRemoveAttribute',
        named={'Geometry': geometry, 'Name': name},
        items=(),
        rna={},
        menus=(('Pattern Mode', pattern_mode),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def rename_attribute(
    context,
    *,
    geometry=None,
    old=None,
    new=None,
    overwrite=None,
    mode='Single',
):
    return materialize(
        context,
        'GeometryNodeRenameAttribute',
        named={'Geometry': geometry, 'Old': old, 'New': new, 'Overwrite': overwrite},
        items=(),
        rna={},
        menus=(('Mode', mode),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def replace_material(
    context,
    *,
    geometry=None,
    old=None,
    new=None,
):
    return materialize(
        context,
        'GeometryNodeReplaceMaterial',
        named={'Geometry': geometry, 'Old': old, 'New': new},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def resample_curve(
    context,
    *,
    curve=None,
    selection=None,
    count=None,
    length=None,
    keep_last_segment=True,
    mode='Count',
):
    return materialize(
        context,
        'GeometryNodeResampleCurve',
        named={'Curve': curve, 'Selection': selection, 'Count': count, 'Length': length},
        items=(),
        rna={'keep_last_segment': keep_last_segment},
        menus=(('Mode', mode),),
        orders=[('keep_last_segment',)],
        constant_rna={},
        constant_outputs={},
    )

def reverse_curve(
    context,
    *,
    curve=None,
    selection=None,
):
    return materialize(
        context,
        'GeometryNodeReverseCurve',
        named={'Curve': curve, 'Selection': selection},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def rotate_instances(
    context,
    *,
    instances=None,
    selection=None,
    rotation=None,
    pivot_point=None,
    local_space=None,
):
    return materialize(
        context,
        'GeometryNodeRotateInstances',
        named={'Instances': instances, 'Selection': selection, 'Rotation': rotation, 'Pivot Point': pivot_point, 'Local Space': local_space},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def sdf_grid_boolean(
    context,
    *,
    grid_2=None,
    grid_1=None,
    operation='DIFFERENCE',
):
    return materialize(
        context,
        'GeometryNodeSDFGridBoolean',
        named={'Grid 2': grid_2, 'Grid 1': grid_1},
        items=(),
        rna={'operation': operation},
        menus=(),
        orders=[('operation',)],
        constant_rna={},
        constant_outputs={},
    )

def sdf_grid_fillet(
    context,
    *,
    grid=None,
    iterations=None,
):
    return materialize(
        context,
        'GeometryNodeSDFGridFillet',
        named={'Grid': grid, 'Iterations': iterations},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def sdf_grid_laplacian(
    context,
    *,
    grid=None,
    iterations=None,
):
    return materialize(
        context,
        'GeometryNodeSDFGridLaplacian',
        named={'Grid': grid, 'Iterations': iterations},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def sdf_grid_mean(
    context,
    *,
    grid=None,
    width=None,
    iterations=None,
):
    return materialize(
        context,
        'GeometryNodeSDFGridMean',
        named={'Grid': grid, 'Width': width, 'Iterations': iterations},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def sdf_grid_mean_curvature(
    context,
    *,
    grid=None,
    iterations=None,
):
    return materialize(
        context,
        'GeometryNodeSDFGridMeanCurvature',
        named={'Grid': grid, 'Iterations': iterations},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def sdf_grid_median(
    context,
    *,
    grid=None,
    width=None,
    iterations=None,
):
    return materialize(
        context,
        'GeometryNodeSDFGridMedian',
        named={'Grid': grid, 'Width': width, 'Iterations': iterations},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def sdf_grid_offset(
    context,
    *,
    grid=None,
    distance=None,
):
    return materialize(
        context,
        'GeometryNodeSDFGridOffset',
        named={'Grid': grid, 'Distance': distance},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def sample_curve(
    context,
    *,
    curves=None,
    value=None,
    factor=None,
    curve_index=None,
    length=None,
    data_type='FLOAT',
    mode='FACTOR',
    use_all_curves=False,
):
    return materialize(
        context,
        'GeometryNodeSampleCurve',
        named={'Curves': curves, 'Value': value, 'Factor': factor, 'Curve Index': curve_index, 'Length': length},
        items=(),
        rna={'data_type': data_type, 'mode': mode, 'use_all_curves': use_all_curves},
        menus=(),
        orders=[('data_type', 'mode', 'use_all_curves')],
        constant_rna={},
        constant_outputs={},
    )

def sample_grid(
    context,
    *,
    grid=None,
    position=None,
    data_type='FLOAT',
    interpolation='Trilinear',
):
    return materialize(
        context,
        'GeometryNodeSampleGrid',
        named={'Grid': grid, 'Position': position},
        items=(),
        rna={'data_type': data_type},
        menus=(('Interpolation', interpolation),),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def sample_grid_index(
    context,
    *,
    grid=None,
    x=None,
    y=None,
    z=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeSampleGridIndex',
        named={'Grid': grid, 'X': x, 'Y': y, 'Z': z},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def sample_index(
    context,
    *,
    geometry=None,
    value=None,
    index=None,
    clamp=False,
    data_type='FLOAT',
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeSampleIndex',
        named={'Geometry': geometry, 'Value': value, 'Index': index},
        items=(),
        rna={'clamp': clamp, 'data_type': data_type, 'domain': domain},
        menus=(),
        orders=[('clamp', 'data_type', 'domain')],
        constant_rna={},
        constant_outputs={},
    )

def sample_nearest(
    context,
    *,
    geometry=None,
    sample_position=None,
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeSampleNearest',
        named={'Geometry': geometry, 'Sample Position': sample_position},
        items=(),
        rna={'domain': domain},
        menus=(),
        orders=[('domain',)],
        constant_rna={},
        constant_outputs={},
    )

def sample_nearest_surface(
    context,
    *,
    mesh=None,
    value=None,
    group_id=None,
    sample_position=None,
    sample_group_id=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeSampleNearestSurface',
        named={'Mesh': mesh, 'Value': value, 'Group ID': group_id, 'Sample Position': sample_position, 'Sample Group ID': sample_group_id},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def sample_uv_surface(
    context,
    *,
    mesh=None,
    value=None,
    source_uv_map=None,
    sample_uv=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeSampleUVSurface',
        named={'Mesh': mesh, 'Value': value, 'Source UV Map': source_uv_map, 'Sample UV': sample_uv},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def scale_elements(
    context,
    *,
    geometry=None,
    selection=None,
    scale=None,
    center=None,
    axis=None,
    domain='FACE',
    scale_mode='Uniform',
):
    return materialize(
        context,
        'GeometryNodeScaleElements',
        named={'Geometry': geometry, 'Selection': selection, 'Scale': scale, 'Center': center, 'Axis': axis},
        items=(),
        rna={'domain': domain},
        menus=(('Scale Mode', scale_mode),),
        orders=[('domain',)],
        constant_rna={},
        constant_outputs={},
    )

def scale_instances(
    context,
    *,
    instances=None,
    selection=None,
    scale=None,
    center=None,
    local_space=None,
):
    return materialize(
        context,
        'GeometryNodeScaleInstances',
        named={'Instances': instances, 'Selection': selection, 'Scale': scale, 'Center': center, 'Local Space': local_space},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def self_object(
    context,
):
    return materialize(
        context,
        'GeometryNodeSelfObject',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def separate_components(
    context,
    *,
    geometry=None,
):
    return materialize(
        context,
        'GeometryNodeSeparateComponents',
        named={'Geometry': geometry},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def separate_geometry(
    context,
    *,
    geometry=None,
    selection=None,
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeSeparateGeometry',
        named={'Geometry': geometry, 'Selection': selection},
        items=(),
        rna={'domain': domain},
        menus=(),
        orders=[('domain',)],
        constant_rna={},
        constant_outputs={},
    )

def set_handle_positions(
    context,
    *,
    curve=None,
    selection=None,
    position=None,
    offset=None,
    mode='LEFT',
):
    return materialize(
        context,
        'GeometryNodeSetCurveHandlePositions',
        named={'Curve': curve, 'Selection': selection, 'Position': position, 'Offset': offset},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def set_curve_normal(
    context,
    *,
    curve=None,
    selection=None,
    normal=None,
    mode='Minimum Twist',
):
    return materialize(
        context,
        'GeometryNodeSetCurveNormal',
        named={'Curve': curve, 'Selection': selection, 'Normal': normal},
        items=(),
        rna={},
        menus=(('Mode', mode),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_curve_radius(
    context,
    *,
    curve=None,
    selection=None,
    radius=None,
):
    return materialize(
        context,
        'GeometryNodeSetCurveRadius',
        named={'Curve': curve, 'Selection': selection, 'Radius': radius},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_curve_tilt(
    context,
    *,
    curve=None,
    selection=None,
    tilt=None,
):
    return materialize(
        context,
        'GeometryNodeSetCurveTilt',
        named={'Curve': curve, 'Selection': selection, 'Tilt': tilt},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_geometry_name(
    context,
    *,
    geometry=None,
    name=None,
):
    return materialize(
        context,
        'GeometryNodeSetGeometryName',
        named={'Geometry': geometry, 'Name': name},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_grease_pencil_color(
    context,
    *,
    grease_pencil=None,
    selection=None,
    color=None,
    opacity=None,
    mode='STROKE',
):
    return materialize(
        context,
        'GeometryNodeSetGreasePencilColor',
        named={'Grease Pencil': grease_pencil, 'Selection': selection, 'Color': color, 'Opacity': opacity},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def set_grease_pencil_depth(
    context,
    *,
    grease_pencil=None,
    depth_order='2D',
):
    return materialize(
        context,
        'GeometryNodeSetGreasePencilDepth',
        named={'Grease Pencil': grease_pencil},
        items=(),
        rna={'depth_order': depth_order},
        menus=(),
        orders=[('depth_order',)],
        constant_rna={},
        constant_outputs={},
    )

def set_grease_pencil_softness(
    context,
    *,
    grease_pencil=None,
    selection=None,
    softness=None,
):
    return materialize(
        context,
        'GeometryNodeSetGreasePencilSoftness',
        named={'Grease Pencil': grease_pencil, 'Selection': selection, 'Softness': softness},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_grid_background(
    context,
    *,
    grid=None,
    background=None,
    update_inactive=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeSetGridBackground',
        named={'Grid': grid, 'Background': background, 'Update Inactive': update_inactive},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def set_id(
    context,
    *,
    geometry=None,
    selection=None,
    id=None,
):
    return materialize(
        context,
        'GeometryNodeSetID',
        named={'Geometry': geometry, 'Selection': selection, 'ID': id},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_material(
    context,
    *,
    geometry=None,
    selection=None,
    material=None,
):
    return materialize(
        context,
        'GeometryNodeSetMaterial',
        named={'Geometry': geometry, 'Selection': selection, 'Material': material},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_material_index(
    context,
    *,
    geometry=None,
    selection=None,
    material_index=None,
):
    return materialize(
        context,
        'GeometryNodeSetMaterialIndex',
        named={'Geometry': geometry, 'Selection': selection, 'Material Index': material_index},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_mesh_normal(
    context,
    *,
    mesh=None,
    remove_custom=None,
    edge_sharpness=None,
    face_sharpness=None,
    custom_normal=None,
    domain='POINT',
    mode='SHARPNESS',
):
    return materialize(
        context,
        'GeometryNodeSetMeshNormal',
        named={'Mesh': mesh, 'Remove Custom': remove_custom, 'Edge Sharpness': edge_sharpness, 'Face Sharpness': face_sharpness, 'Custom Normal': custom_normal},
        items=(),
        rna={'domain': domain, 'mode': mode},
        menus=(),
        orders=[('domain', 'mode')],
        constant_rna={},
        constant_outputs={},
    )

def set_nurbs_order(
    context,
    *,
    curves=None,
    selection=None,
    order=None,
):
    return materialize(
        context,
        'GeometryNodeSetNURBSOrder',
        named={'Curves': curves, 'Selection': selection, 'Order': order},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_nurbs_weight(
    context,
    *,
    curves=None,
    selection=None,
    weight=None,
):
    return materialize(
        context,
        'GeometryNodeSetNURBSWeight',
        named={'Curves': curves, 'Selection': selection, 'Weight': weight},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_point_radius(
    context,
    *,
    points=None,
    selection=None,
    radius=None,
):
    return materialize(
        context,
        'GeometryNodeSetPointRadius',
        named={'Points': points, 'Selection': selection, 'Radius': radius},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_position(
    context,
    *,
    geometry=None,
    selection=None,
    position=None,
    offset=None,
):
    return materialize(
        context,
        'GeometryNodeSetPosition',
        named={'Geometry': geometry, 'Selection': selection, 'Position': position, 'Offset': offset},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_shade_smooth(
    context,
    *,
    geometry=None,
    selection=None,
    shade_smooth=None,
    domain='FACE',
):
    return materialize(
        context,
        'GeometryNodeSetShadeSmooth',
        named={'Geometry': geometry, 'Selection': selection, 'Shade Smooth': shade_smooth},
        items=(),
        rna={'domain': domain},
        menus=(),
        orders=[('domain',)],
        constant_rna={},
        constant_outputs={},
    )

def set_spline_cyclic(
    context,
    *,
    geometry=None,
    selection=None,
    cyclic=None,
):
    return materialize(
        context,
        'GeometryNodeSetSplineCyclic',
        named={'Geometry': geometry, 'Selection': selection, 'Cyclic': cyclic},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_spline_resolution(
    context,
    *,
    geometry=None,
    selection=None,
    resolution=None,
):
    return materialize(
        context,
        'GeometryNodeSetSplineResolution',
        named={'Geometry': geometry, 'Selection': selection, 'Resolution': resolution},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def sort_elements(
    context,
    *,
    geometry=None,
    selection=None,
    group_id=None,
    sort_weight=None,
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeSortElements',
        named={'Geometry': geometry, 'Selection': selection, 'Group ID': group_id, 'Sort Weight': sort_weight},
        items=(),
        rna={'domain': domain},
        menus=(),
        orders=[('domain',)],
        constant_rna={},
        constant_outputs={},
    )

def sort_list(
    context,
    *,
    list=None,
    selection=None,
    group_id=None,
    sort_weight=None,
    socket_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeSortList',
        named={'List': list, 'Selection': selection, 'Group ID': group_id, 'Sort Weight': sort_weight},
        items=(),
        rna={'socket_type': socket_type},
        menus=(),
        orders=[('socket_type',)],
        constant_rna={},
        constant_outputs={},
    )

def spline_length(
    context,
):
    return materialize(
        context,
        'GeometryNodeSplineLength',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def spline_parameter(
    context,
):
    return materialize(
        context,
        'GeometryNodeSplineParameter',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def split_edges(
    context,
    *,
    mesh=None,
    selection=None,
):
    return materialize(
        context,
        'GeometryNodeSplitEdges',
        named={'Mesh': mesh, 'Selection': selection},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def split_to_instances(
    context,
    *,
    geometry=None,
    selection=None,
    group_id=None,
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeSplitToInstances',
        named={'Geometry': geometry, 'Selection': selection, 'Group ID': group_id},
        items=(),
        rna={'domain': domain},
        menus=(),
        orders=[('domain',)],
        constant_rna={},
        constant_outputs={},
    )

def store_named_attribute(
    context,
    *,
    geometry=None,
    selection=None,
    name=None,
    value=None,
    data_type='FLOAT',
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeStoreNamedAttribute',
        named={'Geometry': geometry, 'Selection': selection, 'Name': name, 'Value': value},
        items=(),
        rna={'data_type': data_type, 'domain': domain},
        menus=(),
        orders=[('data_type', 'domain')],
        constant_rna={},
        constant_outputs={},
    )

def store_named_grid(
    context,
    *,
    volume=None,
    name=None,
    grid=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeStoreNamedGrid',
        named={'Volume': volume, 'Name': name, 'Grid': grid},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def join_strings(
    context,
    *,
    delimiter=None,
    strings=None,
):
    return materialize(
        context,
        'GeometryNodeStringJoin',
        named={'Delimiter': delimiter, 'Strings': strings},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def subdivide_curve(
    context,
    *,
    curve=None,
    cuts=None,
):
    return materialize(
        context,
        'GeometryNodeSubdivideCurve',
        named={'Curve': curve, 'Cuts': cuts},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def subdivide_mesh(
    context,
    *,
    mesh=None,
    level=None,
):
    return materialize(
        context,
        'GeometryNodeSubdivideMesh',
        named={'Mesh': mesh, 'Level': level},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def subdivision_surface(
    context,
    *,
    mesh=None,
    level=None,
    edge_crease=None,
    vertex_crease=None,
    limit_surface=None,
    quality=None,
    boundary_smooth='All',
    uv_smooth='Keep Boundaries',
):
    return materialize(
        context,
        'GeometryNodeSubdivisionSurface',
        named={'Mesh': mesh, 'Level': level, 'Edge Crease': edge_crease, 'Vertex Crease': vertex_crease, 'Limit Surface': limit_surface, 'Quality': quality},
        items=(),
        rna={},
        menus=(('Boundary Smooth', boundary_smooth), ('UV Smooth', uv_smooth)),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def switch(
    context,
    *,
    switch=None,
    false=None,
    input_type='FLOAT',
):
    return materialize(
        context,
        'GeometryNodeSwitch',
        named={'Switch': switch, 'False': false},
        items=(),
        rna={'input_type': input_type},
        menus=(),
        orders=[('input_type',)],
        constant_rna={},
        constant_outputs={},
    )

def tag_filter(
    context,
    *,
    tag_filter=None,
    tags=None,
):
    return materialize(
        context,
        'GeometryNodeTagFilter',
        named={'Tag Filter': tag_filter, 'Tags': tags},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def option_3d_cursor(
    context,
):
    return materialize(
        context,
        'GeometryNodeTool3DCursor',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def active_element(
    context,
    *,
    domain='POINT',
):
    return materialize(
        context,
        'GeometryNodeToolActiveElement',
        named={},
        items=(),
        rna={'domain': domain},
        menus=(),
        orders=[('domain',)],
        constant_rna={},
        constant_outputs={},
    )

def face_set(
    context,
):
    return materialize(
        context,
        'GeometryNodeToolFaceSet',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def mouse_position(
    context,
):
    return materialize(
        context,
        'GeometryNodeToolMousePosition',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def selection(
    context,
):
    return materialize(
        context,
        'GeometryNodeToolSelection',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_face_set(
    context,
    *,
    mesh=None,
    selection=None,
    face_set=None,
):
    return materialize(
        context,
        'GeometryNodeToolSetFaceSet',
        named={'Mesh': mesh, 'Selection': selection, 'Face Set': face_set},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def set_selection(
    context,
    *,
    geometry=None,
    selection=None,
    domain='POINT',
    selection_type='BOOLEAN',
):
    return materialize(
        context,
        'GeometryNodeToolSetSelection',
        named={'Geometry': geometry, 'Selection': selection},
        items=(),
        rna={'domain': domain, 'selection_type': selection_type},
        menus=(),
        orders=[('domain', 'selection_type')],
        constant_rna={},
        constant_outputs={},
    )

def transfer_attributes(
    context,
    *,
    target=None,
    target_point_id=None,
    target_edge_id=None,
    target_face_id=None,
    target_corner_id=None,
    target_curve_id=None,
    target_instance_id=None,
    source=None,
    source_point_id=None,
    source_edge_id=None,
    source_face_id=None,
    source_corner_id=None,
    source_curve_id=None,
    source_instance_id=None,
    attribute_names=None,
    exclude_names=None,
    pattern_mode='Wildcard',
):
    return materialize(
        context,
        'GeometryNodeTransferAttributes',
        named={'Target': target, 'Target Point ID': target_point_id, 'Target Edge ID': target_edge_id, 'Target Face ID': target_face_id, 'Target Corner ID': target_corner_id, 'Target Curve ID': target_curve_id, 'Target Instance ID': target_instance_id, 'Source': source, 'Source Point ID': source_point_id, 'Source Edge ID': source_edge_id, 'Source Face ID': source_face_id, 'Source Corner ID': source_corner_id, 'Source Curve ID': source_curve_id, 'Source Instance ID': source_instance_id, 'Attribute Names': attribute_names, 'Exclude Names': exclude_names},
        items=(),
        rna={},
        menus=(('Pattern Mode', pattern_mode),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def transform_geometry(
    context,
    *,
    geometry=None,
    translation=None,
    rotation=None,
    scale=None,
    mode='Components',
):
    return materialize(
        context,
        'GeometryNodeTransform',
        named={'Geometry': geometry, 'Translation': translation, 'Rotation': rotation, 'Scale': scale},
        items=(),
        rna={},
        menus=(('Mode', mode),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def translate_instances(
    context,
    *,
    instances=None,
    selection=None,
    translation=None,
    local_space=None,
):
    return materialize(
        context,
        'GeometryNodeTranslateInstances',
        named={'Instances': instances, 'Selection': selection, 'Translation': translation, 'Local Space': local_space},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def triangulate(
    context,
    *,
    mesh=None,
    selection=None,
    n_gon_method='Beauty',
    quad_method='Shortest Diagonal',
):
    return materialize(
        context,
        'GeometryNodeTriangulate',
        named={'Mesh': mesh, 'Selection': selection},
        items=(),
        rna={},
        menus=(('N-gon Method', n_gon_method), ('Quad Method', quad_method)),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def trim_curve(
    context,
    *,
    curve=None,
    selection=None,
    start=None,
    end=None,
    start_001=None,
    end_001=None,
    mode='FACTOR',
):
    return materialize(
        context,
        'GeometryNodeTrimCurve',
        named={'Curve': curve, 'Selection': selection, 'Start': start, 'End': end, 'Start_001': start_001, 'End_001': end_001},
        items=(),
        rna={'mode': mode},
        menus=(),
        orders=[('mode',)],
        constant_rna={},
        constant_outputs={},
    )

def uv_unwrap(
    context,
    *,
    selection=None,
    seam=None,
    margin=None,
    fill_holes=None,
    iterations=None,
    no_flip=None,
    method='Angle Based',
):
    return materialize(
        context,
        'GeometryNodeUVUnwrap',
        named={'Selection': selection, 'Seam': seam, 'Margin': margin, 'Fill Holes': fill_holes, 'Iterations': iterations, 'No Flip': no_flip},
        items=(),
        rna={},
        menus=(('Method', method),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def vertex_of_corner(
    context,
    *,
    corner_index=None,
):
    return materialize(
        context,
        'GeometryNodeVertexOfCorner',
        named={'Corner Index': corner_index},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def volume_cube(
    context,
    *,
    density=None,
    background=None,
    min=None,
    max=None,
    resolution_x=None,
    resolution_y=None,
    resolution_z=None,
):
    return materialize(
        context,
        'GeometryNodeVolumeCube',
        named={'Density': density, 'Background': background, 'Min': min, 'Max': max, 'Resolution X': resolution_x, 'Resolution Y': resolution_y, 'Resolution Z': resolution_z},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def volume_to_mesh(
    context,
    *,
    volume=None,
    threshold=None,
    adaptivity=None,
    voxel_amount=None,
    voxel_size=None,
    resolution_mode='Grid',
):
    return materialize(
        context,
        'GeometryNodeVolumeToMesh',
        named={'Volume': volume, 'Threshold': threshold, 'Adaptivity': adaptivity, 'Voxel Amount': voxel_amount, 'Voxel Size': voxel_size},
        items=(),
        rna={},
        menus=(('Resolution Mode', resolution_mode),),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def warning(
    context,
    *,
    show=None,
    message=None,
    warning_type='ERROR',
):
    return materialize(
        context,
        'GeometryNodeWarning',
        named={'Show': show, 'Message': message},
        items=(),
        rna={'warning_type': warning_type},
        menus=(),
        orders=[('warning_type',)],
        constant_rna={},
        constant_outputs={},
    )

def enable_output(
    context,
    *,
    enable=None,
    data_type='FLOAT',
):
    return materialize(
        context,
        'NodeEnableOutput',
        named={'Enable': enable},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def implicit_conversion(
    context,
    *,
    value=None,
    data_type='RGBA',
):
    return materialize(
        context,
        'NodeImplicitConversion',
        named={'Value': value},
        items=(),
        rna={'data_type': data_type},
        menus=(),
        orders=[('data_type',)],
        constant_rna={},
        constant_outputs={},
    )

def reroute(
    context,
    *,
    input=None,
):
    return materialize(
        context,
        'NodeReroute',
        named={'Input': input},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def blackbody(
    context,
    *,
    temperature=None,
):
    return materialize(
        context,
        'ShaderNodeBlackbody',
        named={'Temperature': temperature},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def clamp(
    context,
    *,
    value=None,
    min=None,
    max=None,
    clamp_type='MINMAX',
):
    return materialize(
        context,
        'ShaderNodeClamp',
        named={'Value': value, 'Min': min, 'Max': max},
        items=(),
        rna={'clamp_type': clamp_type},
        menus=(),
        orders=[('clamp_type',)],
        constant_rna={},
        constant_outputs={},
    )

def combine_xyz(
    context,
    *,
    x=None,
    y=None,
    z=None,
):
    return materialize(
        context,
        'ShaderNodeCombineXYZ',
        named={'X': x, 'Y': y, 'Z': z},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def float_curve(
    context,
    *,
    factor=None,
    value=None,
):
    return materialize(
        context,
        'ShaderNodeFloatCurve',
        named={'Factor': factor, 'Value': value},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def gamma(
    context,
    *,
    color=None,
    gamma=None,
):
    return materialize(
        context,
        'ShaderNodeGamma',
        named={'Color': color, 'Gamma': gamma},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def map_range(
    context,
    *,
    value=None,
    from_min=None,
    from_max=None,
    to_min=None,
    to_max=None,
    steps=None,
    vector=None,
    from_min_float3=None,
    from_max_float3=None,
    to_min_float3=None,
    to_max_float3=None,
    steps_float3=None,
    clamp=True,
    data_type='FLOAT',
    interpolation_type='LINEAR',
):
    return materialize(
        context,
        'ShaderNodeMapRange',
        named={'Value': value, 'From Min': from_min, 'From Max': from_max, 'To Min': to_min, 'To Max': to_max, 'Steps': steps, 'Vector': vector, 'From_Min_FLOAT3': from_min_float3, 'From_Max_FLOAT3': from_max_float3, 'To_Min_FLOAT3': to_min_float3, 'To_Max_FLOAT3': to_max_float3, 'Steps_FLOAT3': steps_float3},
        items=(),
        rna={'clamp': clamp, 'data_type': data_type, 'interpolation_type': interpolation_type},
        menus=(),
        orders=[('clamp', 'data_type', 'interpolation_type')],
        constant_rna={},
        constant_outputs={},
    )

def math(
    context,
    *,
    value=None,
    value_001=None,
    value_002=None,
    operation='ADD',
    use_clamp=False,
):
    return materialize(
        context,
        'ShaderNodeMath',
        named={'Value': value, 'Value_001': value_001, 'Value_002': value_002},
        items=(),
        rna={'operation': operation, 'use_clamp': use_clamp},
        menus=(),
        orders=[('operation', 'use_clamp')],
        constant_rna={},
        constant_outputs={},
    )

def mix(
    context,
    *,
    factor_float=None,
    b_float=None,
    b_vector=None,
    factor_vector=None,
    a_vector=None,
    b_color=None,
    b_rotation=None,
    a_color=None,
    blend_type='MIX',
    clamp_factor=True,
    clamp_result=False,
    data_type='FLOAT',
    factor_mode='UNIFORM',
):
    return materialize(
        context,
        'ShaderNodeMix',
        named={'Factor_Float': factor_float, 'B_Float': b_float, 'B_Vector': b_vector, 'Factor_Vector': factor_vector, 'A_Vector': a_vector, 'B_Color': b_color, 'B_Rotation': b_rotation, 'A_Color': a_color},
        items=(),
        rna={'blend_type': blend_type, 'clamp_factor': clamp_factor, 'clamp_result': clamp_result, 'data_type': data_type, 'factor_mode': factor_mode},
        menus=(),
        orders=[('blend_type', 'clamp_factor', 'clamp_result', 'data_type', 'factor_mode')],
        constant_rna={},
        constant_outputs={},
    )

def mix_legacy(
    context,
    *,
    fac=None,
    color1=None,
    color2=None,
    blend_type='MIX',
    use_alpha=False,
    use_clamp=False,
):
    return materialize(
        context,
        'ShaderNodeMixRGB',
        named={'Fac': fac, 'Color1': color1, 'Color2': color2},
        items=(),
        rna={'blend_type': blend_type, 'use_alpha': use_alpha, 'use_clamp': use_clamp},
        menus=(),
        orders=[('blend_type', 'use_alpha', 'use_clamp')],
        constant_rna={},
        constant_outputs={},
    )

def rgb_curves(
    context,
    *,
    fac=None,
    color=None,
):
    return materialize(
        context,
        'ShaderNodeRGBCurve',
        named={'Fac': fac, 'Color': color},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def separate_xyz(
    context,
    *,
    vector=None,
):
    return materialize(
        context,
        'ShaderNodeSeparateXYZ',
        named={'Vector': vector},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def brick_texture(
    context,
    *,
    vector=None,
    color1=None,
    color2=None,
    mortar=None,
    scale=None,
    mortar_size=None,
    mortar_smooth=None,
    bias=None,
    brick_width=None,
    row_height=None,
):
    return materialize(
        context,
        'ShaderNodeTexBrick',
        named={'Vector': vector, 'Color1': color1, 'Color2': color2, 'Mortar': mortar, 'Scale': scale, 'Mortar Size': mortar_size, 'Mortar Smooth': mortar_smooth, 'Bias': bias, 'Brick Width': brick_width, 'Row Height': row_height},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def checker_texture(
    context,
    *,
    vector=None,
    color1=None,
    color2=None,
    scale=None,
):
    return materialize(
        context,
        'ShaderNodeTexChecker',
        named={'Vector': vector, 'Color1': color1, 'Color2': color2, 'Scale': scale},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def gabor_texture(
    context,
    *,
    vector=None,
    scale=None,
    frequency=None,
    anisotropy=None,
    orientation_2d=None,
    orientation_3d=None,
    gabor_type='2D',
):
    return materialize(
        context,
        'ShaderNodeTexGabor',
        named={'Vector': vector, 'Scale': scale, 'Frequency': frequency, 'Anisotropy': anisotropy, 'Orientation 2D': orientation_2d, 'Orientation 3D': orientation_3d},
        items=(),
        rna={'gabor_type': gabor_type},
        menus=(),
        orders=[('gabor_type',)],
        constant_rna={},
        constant_outputs={},
    )

def gradient_texture(
    context,
    *,
    vector=None,
    gradient_type='LINEAR',
):
    return materialize(
        context,
        'ShaderNodeTexGradient',
        named={'Vector': vector},
        items=(),
        rna={'gradient_type': gradient_type},
        menus=(),
        orders=[('gradient_type',)],
        constant_rna={},
        constant_outputs={},
    )

def magic_texture(
    context,
    *,
    vector=None,
    scale=None,
    distortion=None,
):
    return materialize(
        context,
        'ShaderNodeTexMagic',
        named={'Vector': vector, 'Scale': scale, 'Distortion': distortion},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def noise_texture(
    context,
    *,
    w=None,
    scale=None,
    detail=None,
    roughness=None,
    lacunarity=None,
    distortion=None,
    offset=None,
    gain=None,
    vector=None,
    noise_dimensions='3D',
    noise_type='FBM',
    normalize=True,
):
    return materialize(
        context,
        'ShaderNodeTexNoise',
        named={'W': w, 'Scale': scale, 'Detail': detail, 'Roughness': roughness, 'Lacunarity': lacunarity, 'Distortion': distortion, 'Offset': offset, 'Gain': gain, 'Vector': vector},
        items=(),
        rna={'noise_dimensions': noise_dimensions, 'noise_type': noise_type, 'normalize': normalize},
        menus=(),
        orders=[('noise_dimensions', 'noise_type', 'normalize')],
        constant_rna={},
        constant_outputs={},
    )

def voronoi_texture(
    context,
    *,
    w=None,
    scale=None,
    detail=None,
    roughness=None,
    lacunarity=None,
    randomness=None,
    vector=None,
    smoothness=None,
    exponent=None,
    distance='EUCLIDEAN',
    feature='F1',
    normalize=False,
    voronoi_dimensions='3D',
):
    return materialize(
        context,
        'ShaderNodeTexVoronoi',
        named={'W': w, 'Scale': scale, 'Detail': detail, 'Roughness': roughness, 'Lacunarity': lacunarity, 'Randomness': randomness, 'Vector': vector, 'Smoothness': smoothness, 'Exponent': exponent},
        items=(),
        rna={'distance': distance, 'feature': feature, 'normalize': normalize, 'voronoi_dimensions': voronoi_dimensions},
        menus=(),
        orders=[('distance', 'feature', 'normalize', 'voronoi_dimensions')],
        constant_rna={},
        constant_outputs={},
    )

def wave_texture(
    context,
    *,
    vector=None,
    scale=None,
    distortion=None,
    detail=None,
    detail_scale=None,
    detail_roughness=None,
    phase_offset=None,
    bands_direction='X',
    rings_direction='X',
    wave_profile='SIN',
    wave_type='BANDS',
):
    return materialize(
        context,
        'ShaderNodeTexWave',
        named={'Vector': vector, 'Scale': scale, 'Distortion': distortion, 'Detail': detail, 'Detail Scale': detail_scale, 'Detail Roughness': detail_roughness, 'Phase Offset': phase_offset},
        items=(),
        rna={'bands_direction': bands_direction, 'rings_direction': rings_direction, 'wave_profile': wave_profile, 'wave_type': wave_type},
        menus=(),
        orders=[('bands_direction', 'rings_direction', 'wave_profile', 'wave_type')],
        constant_rna={},
        constant_outputs={},
    )

def white_noise_texture(
    context,
    *,
    w=None,
    vector=None,
    noise_dimensions='3D',
):
    return materialize(
        context,
        'ShaderNodeTexWhiteNoise',
        named={'W': w, 'Vector': vector},
        items=(),
        rna={'noise_dimensions': noise_dimensions},
        menus=(),
        orders=[('noise_dimensions',)],
        constant_rna={},
        constant_outputs={},
    )

def color_ramp(
    context,
    *,
    fac=None,
):
    return materialize(
        context,
        'ShaderNodeValToRGB',
        named={'Fac': fac},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def value(
    context,
    *,
    value=None,
):
    return materialize(
        context,
        'ShaderNodeValue',
        named={},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={'Value': value},
    )

def vector_curves(
    context,
    *,
    fac=None,
    vector=None,
):
    return materialize(
        context,
        'ShaderNodeVectorCurve',
        named={'Fac': fac, 'Vector': vector},
        items=(),
        rna={},
        menus=(),
        orders=[()],
        constant_rna={},
        constant_outputs={},
    )

def vector_math(
    context,
    *,
    vector=None,
    vector_001=None,
    vector_002=None,
    scale=None,
    operation='ADD',
):
    return materialize(
        context,
        'ShaderNodeVectorMath',
        named={'Vector': vector, 'Vector_001': vector_001, 'Vector_002': vector_002, 'Scale': scale},
        items=(),
        rna={'operation': operation},
        menus=(),
        orders=[('operation',)],
        constant_rna={},
        constant_outputs={},
    )

def vector_rotate(
    context,
    *,
    vector=None,
    center=None,
    axis=None,
    angle=None,
    rotation=None,
    invert=False,
    rotation_type='AXIS_ANGLE',
):
    return materialize(
        context,
        'ShaderNodeVectorRotate',
        named={'Vector': vector, 'Center': center, 'Axis': axis, 'Angle': angle, 'Rotation': rotation},
        items=(),
        rna={'invert': invert, 'rotation_type': rotation_type},
        menus=(),
        orders=[('invert', 'rotation_type')],
        constant_rna={},
        constant_outputs={},
    )

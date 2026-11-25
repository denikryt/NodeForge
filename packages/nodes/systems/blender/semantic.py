"""Only invocation-dependent output schemas require semantic resolvers."""
from NodeForge.nf_types import NFType
from NodeForge.extension_result_api import ResultStaticArg, validate_named_result_key



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

def static_value(arguments, param):
    value = arguments[param]
    if not isinstance(value, ResultStaticArg):
        raise TypeError(f'{param} must be compile-time')
    return value.value


def output_types(arguments, parameters, signatures):
    key = tuple(static_value(arguments, name) for name in parameters)
    try:
        return signatures[key]
    except KeyError:
        raise ValueError(f'Unsupported result configuration: {key!r}') from None

def public_result(schema):
    """Choose the public Nodes carrier from the complete validated schema."""
    return next(iter(schema.values())) if len(schema) == 1 else schema


def with_collection_results(base, entries):
    """Append explicit collection keys without normalizing or overwriting them."""
    output = dict(base)
    for key, typ in entries:
        validate_named_result_key(key)
        if key in output:
            raise ValueError(f'Duplicate output key {key!r}')
        output[key] = typ
    return output



_SIGNATURE_random_value = {
    ('BOOLEAN',): {'value': NFType.BOOL},
    ('FLOAT',): {'value': NFType.FLOAT},
    ('FLOAT_VECTOR',): {'value': NFType.VECTOR},
    ('INT',): {'value': NFType.INT},
}

def _result_types_random_value(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_random_value)
    return public_result(base)

_SIGNATURE_string_to_value = {
    ('FLOAT',): {'value': NFType.FLOAT, 'length': NFType.INT},
    ('INT',): {'value': NFType.INT, 'length': NFType.INT},
}

def _result_types_string_to_value(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_string_to_value)
    return public_result(base)

_SIGNATURE_accumulate_field = {
    ('FLOAT',): {'leading': NFType.FLOAT, 'trailing': NFType.FLOAT, 'total': NFType.FLOAT},
    ('FLOAT_VECTOR',): {'leading': NFType.VECTOR, 'trailing': NFType.VECTOR, 'total': NFType.VECTOR},
    ('INT',): {'leading': NFType.INT, 'trailing': NFType.INT, 'total': NFType.INT},
}

def _result_types_accumulate_field(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_accumulate_field)
    return public_result(base)

_SIGNATURE_domain_size = {
    ('CURVE',): {'point_count': NFType.INT, 'spline_count': NFType.INT},
    ('GREASEPENCIL',): {'layer_count': NFType.INT},
    ('INSTANCES',): {'instance_count': NFType.INT},
    ('MESH',): {'point_count': NFType.INT, 'edge_count': NFType.INT, 'face_count': NFType.INT, 'face_corner_count': NFType.INT},
    ('POINTCLOUD',): {'point_count': NFType.INT},
}

def _result_types_domain_size(arguments):
    base = output_types(arguments, ['component'], _SIGNATURE_domain_size)
    return public_result(base)

_SIGNATURE_attribute_statistic = {
    ('FLOAT',): {'mean': NFType.FLOAT, 'median': NFType.FLOAT, 'sum': NFType.FLOAT, 'min': NFType.FLOAT, 'max': NFType.FLOAT, 'range': NFType.FLOAT, 'standard_deviation': NFType.FLOAT, 'variance': NFType.FLOAT},
    ('FLOAT_VECTOR',): {'mean': NFType.VECTOR, 'median': NFType.VECTOR, 'sum': NFType.VECTOR, 'min': NFType.VECTOR, 'max': NFType.VECTOR, 'range': NFType.VECTOR, 'standard_deviation': NFType.VECTOR, 'variance': NFType.VECTOR},
}

def _result_types_attribute_statistic(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_attribute_statistic)
    return public_result(base)

_SIGNATURE_blur_attribute = {
    ('FLOAT',): {'value': NFType.FLOAT},
    ('FLOAT_COLOR',): {'value': NFType.COLOR},
    ('FLOAT_VECTOR',): {'value': NFType.VECTOR},
    ('INT',): {'value': NFType.INT},
}

def _result_types_blur_attribute(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_blur_attribute)
    return public_result(base)

_SIGNATURE_capture_attribute = {
    (): {'geometry': NFType.GEOMETRY, 'selection': NFType.BOOL},
}

def _result_types_capture_attribute(arguments):
    base = output_types(arguments, [], _SIGNATURE_capture_attribute)
    types = {'BOOLEAN': NFType.BOOL, 'FLOAT': NFType.FLOAT, 'INT': NFType.INT, 'RGBA': NFType.COLOR, 'ROTATION': NFType.ROTATION, 'VECTOR': NFType.VECTOR}
    entries = capture_entries(static_value(arguments, 'elements'), types)
    return public_result(with_collection_results(base, ((name, types[token]) for token, name in entries)))

_SIGNATURE_arc = {
    ('POINTS',): {'curve': NFType.GEOMETRY, 'center': NFType.VECTOR, 'normal': NFType.VECTOR, 'radius': NFType.FLOAT},
    ('RADIUS',): {'curve': NFType.GEOMETRY},
}

def _result_types_arc(arguments):
    base = output_types(arguments, ['mode'], _SIGNATURE_arc)
    return public_result(base)

_SIGNATURE_curve_circle = {
    ('POINTS',): {'curve': NFType.GEOMETRY, 'center': NFType.VECTOR},
    ('RADIUS',): {'curve': NFType.GEOMETRY},
}

def _result_types_curve_circle(arguments):
    base = output_types(arguments, ['mode'], _SIGNATURE_curve_circle)
    return public_result(base)

_SIGNATURE_evaluate_at_index = {
    ('BOOLEAN',): {'value': NFType.BOOL},
    ('FLOAT',): {'value': NFType.FLOAT},
    ('FLOAT_COLOR',): {'value': NFType.COLOR},
    ('FLOAT_VECTOR',): {'value': NFType.VECTOR},
    ('INT',): {'value': NFType.INT},
    ('QUATERNION',): {'value': NFType.ROTATION},
}

def _result_types_evaluate_at_index(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_evaluate_at_index)
    return public_result(base)

_SIGNATURE_field_average = {
    ('FLOAT',): {'mean': NFType.FLOAT, 'median': NFType.FLOAT},
    ('FLOAT_VECTOR',): {'mean': NFType.VECTOR, 'median': NFType.VECTOR},
}

def _result_types_field_average(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_field_average)
    return public_result(base)

_SIGNATURE_field_min_max = {
    ('FLOAT',): {'min': NFType.FLOAT, 'max': NFType.FLOAT},
    ('FLOAT_VECTOR',): {'min': NFType.VECTOR, 'max': NFType.VECTOR},
    ('INT',): {'min': NFType.INT, 'max': NFType.INT},
}

def _result_types_field_min_max(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_field_min_max)
    return public_result(base)

_SIGNATURE_evaluate_on_domain = {
    ('BOOLEAN',): {'value': NFType.BOOL},
    ('FLOAT',): {'value': NFType.FLOAT},
    ('FLOAT_COLOR',): {'value': NFType.COLOR},
    ('FLOAT_VECTOR',): {'value': NFType.VECTOR},
    ('INT',): {'value': NFType.INT},
    ('QUATERNION',): {'value': NFType.ROTATION},
}

def _result_types_evaluate_on_domain(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_evaluate_on_domain)
    return public_result(base)

_SIGNATURE_field_variance = {
    ('FLOAT',): {'standard_deviation': NFType.FLOAT, 'variance': NFType.FLOAT},
    ('FLOAT_VECTOR',): {'standard_deviation': NFType.VECTOR, 'variance': NFType.VECTOR},
}

def _result_types_field_variance(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_field_variance)
    return public_result(base)

_SIGNATURE_filter_list = {
    ('BOOLEAN',): {'selection': NFType.BOOL, 'inverted': NFType.BOOL},
    ('FLOAT',): {'selection': NFType.FLOAT, 'inverted': NFType.FLOAT},
    ('GEOMETRY',): {'selection': NFType.GEOMETRY, 'inverted': NFType.GEOMETRY},
    ('INT',): {'selection': NFType.INT, 'inverted': NFType.INT},
    ('MATERIAL',): {'selection': NFType.MATERIAL, 'inverted': NFType.MATERIAL},
    ('OBJECT',): {'selection': NFType.OBJECT, 'inverted': NFType.OBJECT},
    ('RGBA',): {'selection': NFType.COLOR, 'inverted': NFType.COLOR},
    ('ROTATION',): {'selection': NFType.ROTATION, 'inverted': NFType.ROTATION},
    ('STRING',): {'selection': NFType.STRING, 'inverted': NFType.STRING},
    ('VECTOR',): {'selection': NFType.VECTOR, 'inverted': NFType.VECTOR},
}

def _result_types_filter_list(arguments):
    base = output_types(arguments, ['socket_type'], _SIGNATURE_filter_list)
    return public_result(base)

_SIGNATURE_get_named_grid = {
    ('BOOLEAN',): {'volume': NFType.GEOMETRY, 'grid': NFType.BOOL},
    ('FLOAT',): {'volume': NFType.GEOMETRY, 'grid': NFType.FLOAT},
    ('INT',): {'volume': NFType.GEOMETRY, 'grid': NFType.INT},
    ('VECTOR',): {'volume': NFType.GEOMETRY, 'grid': NFType.VECTOR},
}

def _result_types_get_named_grid(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_get_named_grid)
    return public_result(base)

_SIGNATURE_advect_grid = {
    ('FLOAT',): {'grid': NFType.FLOAT},
    ('INT',): {'grid': NFType.INT},
    ('VECTOR',): {'grid': NFType.VECTOR},
}

def _result_types_advect_grid(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_advect_grid)
    return public_result(base)

_SIGNATURE_clip_grid = {
    ('BOOLEAN',): {'grid': NFType.BOOL},
    ('FLOAT',): {'grid': NFType.FLOAT},
    ('INT',): {'grid': NFType.INT},
    ('VECTOR',): {'grid': NFType.VECTOR},
}

def _result_types_clip_grid(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_clip_grid)
    return public_result(base)

_SIGNATURE_grid_dilate_erode = {
    ('BOOLEAN',): {'grid': NFType.BOOL},
    ('FLOAT',): {'grid': NFType.FLOAT},
    ('INT',): {'grid': NFType.INT},
    ('VECTOR',): {'grid': NFType.VECTOR},
}

def _result_types_grid_dilate_erode(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_grid_dilate_erode)
    return public_result(base)

_SIGNATURE_grid_mean = {
    ('FLOAT',): {'grid': NFType.FLOAT},
    ('INT',): {'grid': NFType.INT},
    ('VECTOR',): {'grid': NFType.VECTOR},
}

def _result_types_grid_mean(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_grid_mean)
    return public_result(base)

_SIGNATURE_grid_median = {
    ('FLOAT',): {'grid': NFType.FLOAT},
    ('INT',): {'grid': NFType.INT},
    ('VECTOR',): {'grid': NFType.VECTOR},
}

def _result_types_grid_median(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_grid_median)
    return public_result(base)

_SIGNATURE_prune_grid = {
    ('BOOLEAN',): {'grid': NFType.BOOL},
    ('FLOAT',): {'grid': NFType.FLOAT},
    ('INT',): {'grid': NFType.INT},
    ('VECTOR',): {'grid': NFType.VECTOR},
}

def _result_types_prune_grid(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_prune_grid)
    return public_result(base)

_SIGNATURE_grid_to_points = {
    ('BOOLEAN',): {'points': NFType.GEOMETRY, 'value': NFType.BOOL, 'x': NFType.INT, 'y': NFType.INT, 'z': NFType.INT, 'is_tile': NFType.BOOL, 'extent': NFType.INT},
    ('FLOAT',): {'points': NFType.GEOMETRY, 'value': NFType.FLOAT, 'x': NFType.INT, 'y': NFType.INT, 'z': NFType.INT, 'is_tile': NFType.BOOL, 'extent': NFType.INT},
    ('INT',): {'points': NFType.GEOMETRY, 'value': NFType.INT, 'x': NFType.INT, 'y': NFType.INT, 'z': NFType.INT, 'is_tile': NFType.BOOL, 'extent': NFType.INT},
    ('VECTOR',): {'points': NFType.GEOMETRY, 'value': NFType.VECTOR, 'x': NFType.INT, 'y': NFType.INT, 'z': NFType.INT, 'is_tile': NFType.BOOL, 'extent': NFType.INT},
}

def _result_types_grid_to_points(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_grid_to_points)
    return public_result(base)

_SIGNATURE_voxelize_grid = {
    ('BOOLEAN',): {'grid': NFType.BOOL},
    ('FLOAT',): {'grid': NFType.FLOAT},
    ('INT',): {'grid': NFType.INT},
    ('VECTOR',): {'grid': NFType.VECTOR},
}

def _result_types_voxelize_grid(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_voxelize_grid)
    return public_result(base)

_SIGNATURE_index_switch = {
    ('BOOLEAN',): {'output': NFType.BOOL},
    ('FLOAT',): {'output': NFType.FLOAT},
    ('GEOMETRY',): {'output': NFType.GEOMETRY},
    ('INT',): {'output': NFType.INT},
    ('MATERIAL',): {'output': NFType.MATERIAL},
    ('OBJECT',): {'output': NFType.OBJECT},
    ('RGBA',): {'output': NFType.COLOR},
    ('ROTATION',): {'output': NFType.ROTATION},
    ('STRING',): {'output': NFType.STRING},
    ('VECTOR',): {'output': NFType.VECTOR},
}

def _result_types_index_switch(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_index_switch)
    count = static_value(arguments, 'elements')
    if type(count) is not int or count < 0:
        raise ValueError('elements must be nonnegative')
    return public_result(base)

_SIGNATURE_named_attribute = {
    ('BOOLEAN',): {'attribute': NFType.BOOL, 'exists': NFType.BOOL},
    ('FLOAT',): {'attribute': NFType.FLOAT, 'exists': NFType.BOOL},
    ('FLOAT_COLOR',): {'attribute': NFType.COLOR, 'exists': NFType.BOOL},
    ('FLOAT_VECTOR',): {'attribute': NFType.VECTOR, 'exists': NFType.BOOL},
    ('INT',): {'attribute': NFType.INT, 'exists': NFType.BOOL},
    ('QUATERNION',): {'attribute': NFType.ROTATION, 'exists': NFType.BOOL},
}

def _result_types_named_attribute(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_named_attribute)
    return public_result(base)

_SIGNATURE_get_list_item = {
    ('BOOLEAN',): {'value': NFType.BOOL},
    ('FLOAT',): {'value': NFType.FLOAT},
    ('GEOMETRY',): {'value': NFType.GEOMETRY},
    ('INT',): {'value': NFType.INT},
    ('MATERIAL',): {'value': NFType.MATERIAL},
    ('OBJECT',): {'value': NFType.OBJECT},
    ('RGBA',): {'value': NFType.COLOR},
    ('ROTATION',): {'value': NFType.ROTATION},
    ('STRING',): {'value': NFType.STRING},
    ('VECTOR',): {'value': NFType.VECTOR},
}

def _result_types_get_list_item(arguments):
    base = output_types(arguments, ['socket_type'], _SIGNATURE_get_list_item)
    return public_result(base)

_SIGNATURE_menu_switch = {
    ('BOOLEAN',): {'output': NFType.BOOL},
    ('FLOAT',): {'output': NFType.FLOAT},
    ('GEOMETRY',): {'output': NFType.GEOMETRY},
    ('INT',): {'output': NFType.INT},
    ('MATERIAL',): {'output': NFType.MATERIAL},
    ('OBJECT',): {'output': NFType.OBJECT},
    ('RGBA',): {'output': NFType.COLOR},
    ('ROTATION',): {'output': NFType.ROTATION},
    ('STRING',): {'output': NFType.STRING},
    ('VECTOR',): {'output': NFType.VECTOR},
}

def _result_types_menu_switch(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_menu_switch)
    names = menu_names(static_value(arguments, 'elements'))
    selected = static_value(arguments, 'menu')
    if selected not in names:
        raise ValueError('Invalid Menu selection / elements')
    return public_result(with_collection_results(base, ((name, NFType.BOOL) for name in names)))

_SIGNATURE_mesh_boolean = {
    ('EXACT',): {'mesh': NFType.GEOMETRY, 'intersecting_edges': NFType.BOOL},
    ('FLOAT',): {'mesh': NFType.GEOMETRY},
    ('MANIFOLD',): {'mesh': NFType.GEOMETRY, 'intersecting_edges': NFType.BOOL},
}

def _result_types_mesh_boolean(arguments):
    base = output_types(arguments, ['solver'], _SIGNATURE_mesh_boolean)
    return public_result(base)

_SIGNATURE_raycast = {
    ('BOOLEAN',): {'is_hit': NFType.BOOL, 'hit_position': NFType.VECTOR, 'hit_normal': NFType.VECTOR, 'hit_distance': NFType.FLOAT, 'attribute': NFType.BOOL},
    ('FLOAT',): {'is_hit': NFType.BOOL, 'hit_position': NFType.VECTOR, 'hit_normal': NFType.VECTOR, 'hit_distance': NFType.FLOAT, 'attribute': NFType.FLOAT},
    ('FLOAT_COLOR',): {'is_hit': NFType.BOOL, 'hit_position': NFType.VECTOR, 'hit_normal': NFType.VECTOR, 'hit_distance': NFType.FLOAT, 'attribute': NFType.COLOR},
    ('FLOAT_VECTOR',): {'is_hit': NFType.BOOL, 'hit_position': NFType.VECTOR, 'hit_normal': NFType.VECTOR, 'hit_distance': NFType.FLOAT, 'attribute': NFType.VECTOR},
    ('INT',): {'is_hit': NFType.BOOL, 'hit_position': NFType.VECTOR, 'hit_normal': NFType.VECTOR, 'hit_distance': NFType.FLOAT, 'attribute': NFType.INT},
    ('QUATERNION',): {'is_hit': NFType.BOOL, 'hit_position': NFType.VECTOR, 'hit_normal': NFType.VECTOR, 'hit_distance': NFType.FLOAT, 'attribute': NFType.ROTATION},
}

def _result_types_raycast(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_raycast)
    return public_result(base)

_SIGNATURE_sample_curve = {
    ('BOOLEAN',): {'value': NFType.BOOL, 'position': NFType.VECTOR, 'tangent': NFType.VECTOR, 'normal': NFType.VECTOR},
    ('FLOAT',): {'value': NFType.FLOAT, 'position': NFType.VECTOR, 'tangent': NFType.VECTOR, 'normal': NFType.VECTOR},
    ('FLOAT_COLOR',): {'value': NFType.COLOR, 'position': NFType.VECTOR, 'tangent': NFType.VECTOR, 'normal': NFType.VECTOR},
    ('FLOAT_VECTOR',): {'value': NFType.VECTOR, 'position': NFType.VECTOR, 'tangent': NFType.VECTOR, 'normal': NFType.VECTOR},
    ('INT',): {'value': NFType.INT, 'position': NFType.VECTOR, 'tangent': NFType.VECTOR, 'normal': NFType.VECTOR},
    ('QUATERNION',): {'value': NFType.ROTATION, 'position': NFType.VECTOR, 'tangent': NFType.VECTOR, 'normal': NFType.VECTOR},
}

def _result_types_sample_curve(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_sample_curve)
    return public_result(base)

_SIGNATURE_sample_grid = {
    ('BOOLEAN',): {'value': NFType.BOOL},
    ('FLOAT',): {'value': NFType.FLOAT},
    ('INT',): {'value': NFType.INT},
    ('VECTOR',): {'value': NFType.VECTOR},
}

def _result_types_sample_grid(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_sample_grid)
    return public_result(base)

_SIGNATURE_sample_grid_index = {
    ('BOOLEAN',): {'value': NFType.BOOL},
    ('FLOAT',): {'value': NFType.FLOAT},
    ('INT',): {'value': NFType.INT},
    ('VECTOR',): {'value': NFType.VECTOR},
}

def _result_types_sample_grid_index(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_sample_grid_index)
    return public_result(base)

_SIGNATURE_sample_index = {
    ('BOOLEAN',): {'value': NFType.BOOL},
    ('FLOAT',): {'value': NFType.FLOAT},
    ('FLOAT_COLOR',): {'value': NFType.COLOR},
    ('FLOAT_VECTOR',): {'value': NFType.VECTOR},
    ('INT',): {'value': NFType.INT},
    ('QUATERNION',): {'value': NFType.ROTATION},
}

def _result_types_sample_index(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_sample_index)
    return public_result(base)

_SIGNATURE_sample_nearest_surface = {
    ('BOOLEAN',): {'value': NFType.BOOL, 'is_valid': NFType.BOOL},
    ('FLOAT',): {'value': NFType.FLOAT, 'is_valid': NFType.BOOL},
    ('FLOAT_COLOR',): {'value': NFType.COLOR, 'is_valid': NFType.BOOL},
    ('FLOAT_VECTOR',): {'value': NFType.VECTOR, 'is_valid': NFType.BOOL},
    ('INT',): {'value': NFType.INT, 'is_valid': NFType.BOOL},
    ('QUATERNION',): {'value': NFType.ROTATION, 'is_valid': NFType.BOOL},
}

def _result_types_sample_nearest_surface(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_sample_nearest_surface)
    return public_result(base)

_SIGNATURE_sample_uv_surface = {
    ('BOOLEAN',): {'value': NFType.BOOL, 'is_valid': NFType.BOOL},
    ('FLOAT',): {'value': NFType.FLOAT, 'is_valid': NFType.BOOL},
    ('FLOAT_COLOR',): {'value': NFType.COLOR, 'is_valid': NFType.BOOL},
    ('FLOAT_VECTOR',): {'value': NFType.VECTOR, 'is_valid': NFType.BOOL},
    ('INT',): {'value': NFType.INT, 'is_valid': NFType.BOOL},
    ('QUATERNION',): {'value': NFType.ROTATION, 'is_valid': NFType.BOOL},
}

def _result_types_sample_uv_surface(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_sample_uv_surface)
    return public_result(base)

_SIGNATURE_set_grid_background = {
    ('BOOLEAN',): {'grid': NFType.BOOL},
    ('FLOAT',): {'grid': NFType.FLOAT},
    ('INT',): {'grid': NFType.INT},
    ('VECTOR',): {'grid': NFType.VECTOR},
}

def _result_types_set_grid_background(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_set_grid_background)
    return public_result(base)

_SIGNATURE_sort_list = {
    ('BOOLEAN',): {'list': NFType.BOOL},
    ('FLOAT',): {'list': NFType.FLOAT},
    ('GEOMETRY',): {'list': NFType.GEOMETRY},
    ('INT',): {'list': NFType.INT},
    ('MATERIAL',): {'list': NFType.MATERIAL},
    ('OBJECT',): {'list': NFType.OBJECT},
    ('RGBA',): {'list': NFType.COLOR},
    ('ROTATION',): {'list': NFType.ROTATION},
    ('STRING',): {'list': NFType.STRING},
    ('VECTOR',): {'list': NFType.VECTOR},
}

def _result_types_sort_list(arguments):
    base = output_types(arguments, ['socket_type'], _SIGNATURE_sort_list)
    return public_result(base)

_SIGNATURE_switch = {
    ('BOOLEAN',): {'output': NFType.BOOL},
    ('FLOAT',): {'output': NFType.FLOAT},
    ('GEOMETRY',): {'output': NFType.GEOMETRY},
    ('INT',): {'output': NFType.INT},
    ('MATERIAL',): {'output': NFType.MATERIAL},
    ('OBJECT',): {'output': NFType.OBJECT},
    ('RGBA',): {'output': NFType.COLOR},
    ('ROTATION',): {'output': NFType.ROTATION},
    ('STRING',): {'output': NFType.STRING},
    ('VECTOR',): {'output': NFType.VECTOR},
}

def _result_types_switch(arguments):
    base = output_types(arguments, ['input_type'], _SIGNATURE_switch)
    return public_result(base)

_SIGNATURE_enable_output = {
    ('BOOLEAN',): {'value': NFType.BOOL},
    ('FLOAT',): {'value': NFType.FLOAT},
    ('GEOMETRY',): {'value': NFType.GEOMETRY},
    ('INT',): {'value': NFType.INT},
    ('MATERIAL',): {'value': NFType.MATERIAL},
    ('OBJECT',): {'value': NFType.OBJECT},
    ('RGBA',): {'value': NFType.COLOR},
    ('ROTATION',): {'value': NFType.ROTATION},
    ('STRING',): {'value': NFType.STRING},
    ('VECTOR',): {'value': NFType.VECTOR},
}

def _result_types_enable_output(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_enable_output)
    return public_result(base)

_SIGNATURE_implicit_conversion = {
    ('BOOLEAN',): {'value': NFType.BOOL},
    ('FLOAT',): {'value': NFType.FLOAT},
    ('GEOMETRY',): {'value': NFType.GEOMETRY},
    ('INT',): {'value': NFType.INT},
    ('MATERIAL',): {'value': NFType.MATERIAL},
    ('OBJECT',): {'value': NFType.OBJECT},
    ('RGBA',): {'value': NFType.COLOR},
    ('ROTATION',): {'value': NFType.ROTATION},
    ('STRING',): {'value': NFType.STRING},
    ('VECTOR',): {'value': NFType.VECTOR},
}

def _result_types_implicit_conversion(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_implicit_conversion)
    return public_result(base)

_SIGNATURE_map_range = {
    ('FLOAT',): {'result': NFType.FLOAT},
    ('FLOAT_VECTOR',): {'vector': NFType.VECTOR},
}

def _result_types_map_range(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_map_range)
    return public_result(base)

_SIGNATURE_mix = {
    ('FLOAT',): {'result': NFType.FLOAT},
    ('RGBA',): {'result': NFType.COLOR},
    ('ROTATION',): {'result': NFType.ROTATION},
    ('VECTOR',): {'result': NFType.VECTOR},
}

def _result_types_mix(arguments):
    base = output_types(arguments, ['data_type'], _SIGNATURE_mix)
    return public_result(base)

_SIGNATURE_voronoi_texture = {
    ('DISTANCE_TO_EDGE', '1D'): {'distance': NFType.FLOAT},
    ('DISTANCE_TO_EDGE', '2D'): {'distance': NFType.FLOAT},
    ('DISTANCE_TO_EDGE', '3D'): {'distance': NFType.FLOAT},
    ('DISTANCE_TO_EDGE', '4D'): {'distance': NFType.FLOAT},
    ('F1', '1D'): {'distance': NFType.FLOAT, 'color': NFType.COLOR, 'w': NFType.FLOAT},
    ('F1', '2D'): {'distance': NFType.FLOAT, 'color': NFType.COLOR, 'position': NFType.VECTOR},
    ('F1', '3D'): {'distance': NFType.FLOAT, 'color': NFType.COLOR, 'position': NFType.VECTOR},
    ('F1', '4D'): {'distance': NFType.FLOAT, 'color': NFType.COLOR, 'position': NFType.VECTOR, 'w': NFType.FLOAT},
    ('F2', '1D'): {'distance': NFType.FLOAT, 'color': NFType.COLOR, 'w': NFType.FLOAT},
    ('F2', '2D'): {'distance': NFType.FLOAT, 'color': NFType.COLOR, 'position': NFType.VECTOR},
    ('F2', '3D'): {'distance': NFType.FLOAT, 'color': NFType.COLOR, 'position': NFType.VECTOR},
    ('F2', '4D'): {'distance': NFType.FLOAT, 'color': NFType.COLOR, 'position': NFType.VECTOR, 'w': NFType.FLOAT},
    ('N_SPHERE_RADIUS', '1D'): {'radius': NFType.FLOAT},
    ('N_SPHERE_RADIUS', '2D'): {'radius': NFType.FLOAT},
    ('N_SPHERE_RADIUS', '3D'): {'radius': NFType.FLOAT},
    ('N_SPHERE_RADIUS', '4D'): {'radius': NFType.FLOAT},
    ('SMOOTH_F1', '1D'): {'distance': NFType.FLOAT, 'color': NFType.COLOR, 'w': NFType.FLOAT},
    ('SMOOTH_F1', '2D'): {'distance': NFType.FLOAT, 'color': NFType.COLOR, 'position': NFType.VECTOR},
    ('SMOOTH_F1', '3D'): {'distance': NFType.FLOAT, 'color': NFType.COLOR, 'position': NFType.VECTOR},
    ('SMOOTH_F1', '4D'): {'distance': NFType.FLOAT, 'color': NFType.COLOR, 'position': NFType.VECTOR, 'w': NFType.FLOAT},
}

def _result_types_voronoi_texture(arguments):
    base = output_types(arguments, ['feature', 'voronoi_dimensions'], _SIGNATURE_voronoi_texture)
    return public_result(base)

_SIGNATURE_vector_math = {
    ('ABSOLUTE',): {'vector': NFType.VECTOR},
    ('ADD',): {'vector': NFType.VECTOR},
    ('CEIL',): {'vector': NFType.VECTOR},
    ('COSINE',): {'vector': NFType.VECTOR},
    ('CROSS_PRODUCT',): {'vector': NFType.VECTOR},
    ('DISTANCE',): {'value': NFType.FLOAT},
    ('DIVIDE',): {'vector': NFType.VECTOR},
    ('DOT_PRODUCT',): {'value': NFType.FLOAT},
    ('FACEFORWARD',): {'vector': NFType.VECTOR},
    ('FLOOR',): {'vector': NFType.VECTOR},
    ('FRACTION',): {'vector': NFType.VECTOR},
    ('LENGTH',): {'value': NFType.FLOAT},
    ('MAXIMUM',): {'vector': NFType.VECTOR},
    ('MINIMUM',): {'vector': NFType.VECTOR},
    ('MODULO',): {'vector': NFType.VECTOR},
    ('MULTIPLY',): {'vector': NFType.VECTOR},
    ('MULTIPLY_ADD',): {'vector': NFType.VECTOR},
    ('NORMALIZE',): {'vector': NFType.VECTOR},
    ('POWER',): {'vector': NFType.VECTOR},
    ('PROJECT',): {'vector': NFType.VECTOR},
    ('REFLECT',): {'vector': NFType.VECTOR},
    ('REFRACT',): {'vector': NFType.VECTOR},
    ('ROUND',): {'vector': NFType.VECTOR},
    ('SCALE',): {'vector': NFType.VECTOR},
    ('SIGN',): {'vector': NFType.VECTOR},
    ('SINE',): {'vector': NFType.VECTOR},
    ('SNAP',): {'vector': NFType.VECTOR},
    ('SUBTRACT',): {'vector': NFType.VECTOR},
    ('TANGENT',): {'vector': NFType.VECTOR},
    ('WRAP',): {'vector': NFType.VECTOR},
}

def _result_types_vector_math(arguments):
    base = output_types(arguments, ['operation'], _SIGNATURE_vector_math)
    return public_result(base)


"""Only invocation-dependent output types require semantic resolvers."""
from NodeForge.nf_types import NFType
from NodeForge.extension_result_api import ResultStaticArg


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


_SIGNATURE_random_value = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('FLOAT_VECTOR',): (NFType.VECTOR,),
    ('INT',): (NFType.INT,),
}

def _result_types_random_value(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_random_value)
    return base

_SIGNATURE_string_to_value = {
    ('FLOAT',): (NFType.FLOAT, NFType.INT),
    ('INT',): (NFType.INT, NFType.INT),
}

def _result_types_string_to_value(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_string_to_value)
    return base

_SIGNATURE_accumulate_field = {
    ('FLOAT',): (NFType.FLOAT, NFType.FLOAT, NFType.FLOAT),
    ('FLOAT_VECTOR',): (NFType.VECTOR, NFType.VECTOR, NFType.VECTOR),
    ('INT',): (NFType.INT, NFType.INT, NFType.INT),
}

def _result_types_accumulate_field(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_accumulate_field)
    return base

_SIGNATURE_domain_size = {
    ('CURVE',): (NFType.INT, NFType.INT),
    ('GREASEPENCIL',): (NFType.INT,),
    ('INSTANCES',): (NFType.INT,),
    ('MESH',): (NFType.INT, NFType.INT, NFType.INT, NFType.INT),
    ('POINTCLOUD',): (NFType.INT,),
}

def _result_types_domain_size(arguments):
    base = output_types(arguments, ['rna_component'], _SIGNATURE_domain_size)
    return base

_SIGNATURE_attribute_statistic = {
    ('FLOAT',): (NFType.FLOAT, NFType.FLOAT, NFType.FLOAT, NFType.FLOAT, NFType.FLOAT, NFType.FLOAT, NFType.FLOAT, NFType.FLOAT),
    ('FLOAT_VECTOR',): (NFType.VECTOR, NFType.VECTOR, NFType.VECTOR, NFType.VECTOR, NFType.VECTOR, NFType.VECTOR, NFType.VECTOR, NFType.VECTOR),
}

def _result_types_attribute_statistic(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_attribute_statistic)
    return base

_SIGNATURE_blur_attribute = {
    ('FLOAT',): (NFType.FLOAT,),
    ('FLOAT_COLOR',): (NFType.COLOR,),
    ('FLOAT_VECTOR',): (NFType.VECTOR,),
    ('INT',): (NFType.INT,),
}

def _result_types_blur_attribute(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_blur_attribute)
    return base

_SIGNATURE_capture_attribute = {
    (): (NFType.GEOMETRY, NFType.BOOL),
}

def _result_types_capture_attribute(arguments):
    base = output_types(arguments, [], _SIGNATURE_capture_attribute)
    items = static_value(arguments, 'elements')
    if not items: return base
    names = set()
    output = list(base)
    for raw in items.split(','):
        pair = raw.strip().split(':', 1)
        if len(pair) != 2 or pair[0] not in ('BOOLEAN', 'FLOAT', 'INT', 'RGBA', 'ROTATION', 'VECTOR') or not pair[1] or pair[1] in names:
            raise ValueError('Invalid Capture Attribute elements')
        names.add(pair[1])
        output.append(NFType[{'BOOLEAN': 'BOOL', 'FLOAT': 'FLOAT', 'INT': 'INT', 'RGBA': 'COLOR', 'ROTATION': 'ROTATION', 'VECTOR': 'VECTOR'}[pair[0]]])
    return tuple(output)

_SIGNATURE_arc = {
    ('POINTS',): (NFType.GEOMETRY, NFType.VECTOR, NFType.VECTOR, NFType.FLOAT),
    ('RADIUS',): (NFType.GEOMETRY,),
}

def _result_types_arc(arguments):
    base = output_types(arguments, ['rna_mode'], _SIGNATURE_arc)
    return base

_SIGNATURE_curve_circle = {
    ('POINTS',): (NFType.GEOMETRY, NFType.VECTOR),
    ('RADIUS',): (NFType.GEOMETRY,),
}

def _result_types_curve_circle(arguments):
    base = output_types(arguments, ['rna_mode'], _SIGNATURE_curve_circle)
    return base

_SIGNATURE_evaluate_at_index = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('FLOAT_COLOR',): (NFType.COLOR,),
    ('FLOAT_VECTOR',): (NFType.VECTOR,),
    ('INT',): (NFType.INT,),
    ('QUATERNION',): (NFType.ROTATION,),
}

def _result_types_evaluate_at_index(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_evaluate_at_index)
    return base

_SIGNATURE_field_average = {
    ('FLOAT',): (NFType.FLOAT, NFType.FLOAT),
    ('FLOAT_VECTOR',): (NFType.VECTOR, NFType.VECTOR),
}

def _result_types_field_average(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_field_average)
    return base

_SIGNATURE_field_min_max = {
    ('FLOAT',): (NFType.FLOAT, NFType.FLOAT),
    ('FLOAT_VECTOR',): (NFType.VECTOR, NFType.VECTOR),
    ('INT',): (NFType.INT, NFType.INT),
}

def _result_types_field_min_max(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_field_min_max)
    return base

_SIGNATURE_evaluate_on_domain = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('FLOAT_COLOR',): (NFType.COLOR,),
    ('FLOAT_VECTOR',): (NFType.VECTOR,),
    ('INT',): (NFType.INT,),
    ('QUATERNION',): (NFType.ROTATION,),
}

def _result_types_evaluate_on_domain(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_evaluate_on_domain)
    return base

_SIGNATURE_field_variance = {
    ('FLOAT',): (NFType.FLOAT, NFType.FLOAT),
    ('FLOAT_VECTOR',): (NFType.VECTOR, NFType.VECTOR),
}

def _result_types_field_variance(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_field_variance)
    return base

_SIGNATURE_filter_list = {
    ('BOOLEAN',): (NFType.BOOL, NFType.BOOL),
    ('FLOAT',): (NFType.FLOAT, NFType.FLOAT),
    ('GEOMETRY',): (NFType.GEOMETRY, NFType.GEOMETRY),
    ('INT',): (NFType.INT, NFType.INT),
    ('MATERIAL',): (NFType.MATERIAL, NFType.MATERIAL),
    ('OBJECT',): (NFType.OBJECT, NFType.OBJECT),
    ('RGBA',): (NFType.COLOR, NFType.COLOR),
    ('ROTATION',): (NFType.ROTATION, NFType.ROTATION),
    ('STRING',): (NFType.STRING, NFType.STRING),
    ('VECTOR',): (NFType.VECTOR, NFType.VECTOR),
}

def _result_types_filter_list(arguments):
    base = output_types(arguments, ['rna_socket_type'], _SIGNATURE_filter_list)
    return base

_SIGNATURE_get_named_grid = {
    ('BOOLEAN',): (NFType.GEOMETRY, NFType.BOOL),
    ('FLOAT',): (NFType.GEOMETRY, NFType.FLOAT),
    ('INT',): (NFType.GEOMETRY, NFType.INT),
    ('VECTOR',): (NFType.GEOMETRY, NFType.VECTOR),
}

def _result_types_get_named_grid(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_get_named_grid)
    return base

_SIGNATURE_advect_grid = {
    ('FLOAT',): (NFType.FLOAT,),
    ('INT',): (NFType.INT,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_advect_grid(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_advect_grid)
    return base

_SIGNATURE_clip_grid = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('INT',): (NFType.INT,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_clip_grid(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_clip_grid)
    return base

_SIGNATURE_grid_dilate_erode = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('INT',): (NFType.INT,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_grid_dilate_erode(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_grid_dilate_erode)
    return base

_SIGNATURE_grid_mean = {
    ('FLOAT',): (NFType.FLOAT,),
    ('INT',): (NFType.INT,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_grid_mean(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_grid_mean)
    return base

_SIGNATURE_grid_median = {
    ('FLOAT',): (NFType.FLOAT,),
    ('INT',): (NFType.INT,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_grid_median(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_grid_median)
    return base

_SIGNATURE_prune_grid = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('INT',): (NFType.INT,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_prune_grid(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_prune_grid)
    return base

_SIGNATURE_grid_to_points = {
    ('BOOLEAN',): (NFType.GEOMETRY, NFType.BOOL, NFType.INT, NFType.INT, NFType.INT, NFType.BOOL, NFType.INT),
    ('FLOAT',): (NFType.GEOMETRY, NFType.FLOAT, NFType.INT, NFType.INT, NFType.INT, NFType.BOOL, NFType.INT),
    ('INT',): (NFType.GEOMETRY, NFType.INT, NFType.INT, NFType.INT, NFType.INT, NFType.BOOL, NFType.INT),
    ('VECTOR',): (NFType.GEOMETRY, NFType.VECTOR, NFType.INT, NFType.INT, NFType.INT, NFType.BOOL, NFType.INT),
}

def _result_types_grid_to_points(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_grid_to_points)
    return base

_SIGNATURE_voxelize_grid = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('INT',): (NFType.INT,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_voxelize_grid(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_voxelize_grid)
    return base

_SIGNATURE_index_switch = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('GEOMETRY',): (NFType.GEOMETRY,),
    ('INT',): (NFType.INT,),
    ('MATERIAL',): (NFType.MATERIAL,),
    ('OBJECT',): (NFType.OBJECT,),
    ('RGBA',): (NFType.COLOR,),
    ('ROTATION',): (NFType.ROTATION,),
    ('STRING',): (NFType.STRING,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_index_switch(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_index_switch)
    count = static_value(arguments, 'elements')
    if type(count) is not int or count < 0:
        raise ValueError('elements must be nonnegative')
    return base

_SIGNATURE_named_attribute = {
    ('BOOLEAN',): (NFType.BOOL, NFType.BOOL),
    ('FLOAT',): (NFType.FLOAT, NFType.BOOL),
    ('FLOAT_COLOR',): (NFType.COLOR, NFType.BOOL),
    ('FLOAT_VECTOR',): (NFType.VECTOR, NFType.BOOL),
    ('INT',): (NFType.INT, NFType.BOOL),
    ('QUATERNION',): (NFType.ROTATION, NFType.BOOL),
}

def _result_types_named_attribute(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_named_attribute)
    return base

_SIGNATURE_get_list_item = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('GEOMETRY',): (NFType.GEOMETRY,),
    ('INT',): (NFType.INT,),
    ('MATERIAL',): (NFType.MATERIAL,),
    ('OBJECT',): (NFType.OBJECT,),
    ('RGBA',): (NFType.COLOR,),
    ('ROTATION',): (NFType.ROTATION,),
    ('STRING',): (NFType.STRING,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_get_list_item(arguments):
    base = output_types(arguments, ['rna_socket_type'], _SIGNATURE_get_list_item)
    return base

_SIGNATURE_menu_switch = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('GEOMETRY',): (NFType.GEOMETRY,),
    ('INT',): (NFType.INT,),
    ('MATERIAL',): (NFType.MATERIAL,),
    ('OBJECT',): (NFType.OBJECT,),
    ('RGBA',): (NFType.COLOR,),
    ('ROTATION',): (NFType.ROTATION,),
    ('STRING',): (NFType.STRING,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_menu_switch(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_menu_switch)
    raw = static_value(arguments, 'elements')
    names = [s.strip() for s in raw.split(',')] if raw else []
    selected = static_value(arguments, 'menu_menu')
    if not all(names) or len(names) != len(set(names)) or selected not in names:
        raise ValueError('Invalid Menu selection / elements')
    return base + (NFType.BOOL,) * len(names)

_SIGNATURE_mesh_boolean = {
    ('EXACT',): (NFType.GEOMETRY, NFType.BOOL),
    ('FLOAT',): (NFType.GEOMETRY,),
    ('MANIFOLD',): (NFType.GEOMETRY, NFType.BOOL),
}

def _result_types_mesh_boolean(arguments):
    base = output_types(arguments, ['rna_solver'], _SIGNATURE_mesh_boolean)
    return base

_SIGNATURE_raycast = {
    ('BOOLEAN',): (NFType.BOOL, NFType.VECTOR, NFType.VECTOR, NFType.FLOAT, NFType.BOOL),
    ('FLOAT',): (NFType.BOOL, NFType.VECTOR, NFType.VECTOR, NFType.FLOAT, NFType.FLOAT),
    ('FLOAT_COLOR',): (NFType.BOOL, NFType.VECTOR, NFType.VECTOR, NFType.FLOAT, NFType.COLOR),
    ('FLOAT_VECTOR',): (NFType.BOOL, NFType.VECTOR, NFType.VECTOR, NFType.FLOAT, NFType.VECTOR),
    ('INT',): (NFType.BOOL, NFType.VECTOR, NFType.VECTOR, NFType.FLOAT, NFType.INT),
    ('QUATERNION',): (NFType.BOOL, NFType.VECTOR, NFType.VECTOR, NFType.FLOAT, NFType.ROTATION),
}

def _result_types_raycast(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_raycast)
    return base

_SIGNATURE_sample_curve = {
    ('BOOLEAN',): (NFType.BOOL, NFType.VECTOR, NFType.VECTOR, NFType.VECTOR),
    ('FLOAT',): (NFType.FLOAT, NFType.VECTOR, NFType.VECTOR, NFType.VECTOR),
    ('FLOAT_COLOR',): (NFType.COLOR, NFType.VECTOR, NFType.VECTOR, NFType.VECTOR),
    ('FLOAT_VECTOR',): (NFType.VECTOR, NFType.VECTOR, NFType.VECTOR, NFType.VECTOR),
    ('INT',): (NFType.INT, NFType.VECTOR, NFType.VECTOR, NFType.VECTOR),
    ('QUATERNION',): (NFType.ROTATION, NFType.VECTOR, NFType.VECTOR, NFType.VECTOR),
}

def _result_types_sample_curve(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_sample_curve)
    return base

_SIGNATURE_sample_grid = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('INT',): (NFType.INT,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_sample_grid(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_sample_grid)
    return base

_SIGNATURE_sample_grid_index = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('INT',): (NFType.INT,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_sample_grid_index(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_sample_grid_index)
    return base

_SIGNATURE_sample_index = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('FLOAT_COLOR',): (NFType.COLOR,),
    ('FLOAT_VECTOR',): (NFType.VECTOR,),
    ('INT',): (NFType.INT,),
    ('QUATERNION',): (NFType.ROTATION,),
}

def _result_types_sample_index(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_sample_index)
    return base

_SIGNATURE_sample_nearest_surface = {
    ('BOOLEAN',): (NFType.BOOL, NFType.BOOL),
    ('FLOAT',): (NFType.FLOAT, NFType.BOOL),
    ('FLOAT_COLOR',): (NFType.COLOR, NFType.BOOL),
    ('FLOAT_VECTOR',): (NFType.VECTOR, NFType.BOOL),
    ('INT',): (NFType.INT, NFType.BOOL),
    ('QUATERNION',): (NFType.ROTATION, NFType.BOOL),
}

def _result_types_sample_nearest_surface(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_sample_nearest_surface)
    return base

_SIGNATURE_sample_uv_surface = {
    ('BOOLEAN',): (NFType.BOOL, NFType.BOOL),
    ('FLOAT',): (NFType.FLOAT, NFType.BOOL),
    ('FLOAT_COLOR',): (NFType.COLOR, NFType.BOOL),
    ('FLOAT_VECTOR',): (NFType.VECTOR, NFType.BOOL),
    ('INT',): (NFType.INT, NFType.BOOL),
    ('QUATERNION',): (NFType.ROTATION, NFType.BOOL),
}

def _result_types_sample_uv_surface(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_sample_uv_surface)
    return base

_SIGNATURE_set_grid_background = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('INT',): (NFType.INT,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_set_grid_background(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_set_grid_background)
    return base

_SIGNATURE_sort_list = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('GEOMETRY',): (NFType.GEOMETRY,),
    ('INT',): (NFType.INT,),
    ('MATERIAL',): (NFType.MATERIAL,),
    ('OBJECT',): (NFType.OBJECT,),
    ('RGBA',): (NFType.COLOR,),
    ('ROTATION',): (NFType.ROTATION,),
    ('STRING',): (NFType.STRING,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_sort_list(arguments):
    base = output_types(arguments, ['rna_socket_type'], _SIGNATURE_sort_list)
    return base

_SIGNATURE_switch = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('GEOMETRY',): (NFType.GEOMETRY,),
    ('INT',): (NFType.INT,),
    ('MATERIAL',): (NFType.MATERIAL,),
    ('OBJECT',): (NFType.OBJECT,),
    ('RGBA',): (NFType.COLOR,),
    ('ROTATION',): (NFType.ROTATION,),
    ('STRING',): (NFType.STRING,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_switch(arguments):
    base = output_types(arguments, ['rna_input_type'], _SIGNATURE_switch)
    return base

_SIGNATURE_enable_output = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('GEOMETRY',): (NFType.GEOMETRY,),
    ('INT',): (NFType.INT,),
    ('MATERIAL',): (NFType.MATERIAL,),
    ('OBJECT',): (NFType.OBJECT,),
    ('RGBA',): (NFType.COLOR,),
    ('ROTATION',): (NFType.ROTATION,),
    ('STRING',): (NFType.STRING,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_enable_output(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_enable_output)
    return base

_SIGNATURE_implicit_conversion = {
    ('BOOLEAN',): (NFType.BOOL,),
    ('FLOAT',): (NFType.FLOAT,),
    ('GEOMETRY',): (NFType.GEOMETRY,),
    ('INT',): (NFType.INT,),
    ('MATERIAL',): (NFType.MATERIAL,),
    ('OBJECT',): (NFType.OBJECT,),
    ('RGBA',): (NFType.COLOR,),
    ('ROTATION',): (NFType.ROTATION,),
    ('STRING',): (NFType.STRING,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_implicit_conversion(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_implicit_conversion)
    return base

_SIGNATURE_map_range = {
    ('FLOAT',): (NFType.FLOAT,),
    ('FLOAT_VECTOR',): (NFType.VECTOR,),
}

def _result_types_map_range(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_map_range)
    return base

_SIGNATURE_mix = {
    ('FLOAT',): (NFType.FLOAT,),
    ('RGBA',): (NFType.COLOR,),
    ('ROTATION',): (NFType.ROTATION,),
    ('VECTOR',): (NFType.VECTOR,),
}

def _result_types_mix(arguments):
    base = output_types(arguments, ['rna_data_type'], _SIGNATURE_mix)
    return base

_SIGNATURE_voronoi_texture = {
    ('DISTANCE_TO_EDGE', '1D'): (NFType.FLOAT,),
    ('DISTANCE_TO_EDGE', '2D'): (NFType.FLOAT,),
    ('DISTANCE_TO_EDGE', '3D'): (NFType.FLOAT,),
    ('DISTANCE_TO_EDGE', '4D'): (NFType.FLOAT,),
    ('F1', '1D'): (NFType.FLOAT, NFType.COLOR, NFType.FLOAT),
    ('F1', '2D'): (NFType.FLOAT, NFType.COLOR, NFType.VECTOR),
    ('F1', '3D'): (NFType.FLOAT, NFType.COLOR, NFType.VECTOR),
    ('F1', '4D'): (NFType.FLOAT, NFType.COLOR, NFType.VECTOR, NFType.FLOAT),
    ('F2', '1D'): (NFType.FLOAT, NFType.COLOR, NFType.FLOAT),
    ('F2', '2D'): (NFType.FLOAT, NFType.COLOR, NFType.VECTOR),
    ('F2', '3D'): (NFType.FLOAT, NFType.COLOR, NFType.VECTOR),
    ('F2', '4D'): (NFType.FLOAT, NFType.COLOR, NFType.VECTOR, NFType.FLOAT),
    ('N_SPHERE_RADIUS', '1D'): (NFType.FLOAT,),
    ('N_SPHERE_RADIUS', '2D'): (NFType.FLOAT,),
    ('N_SPHERE_RADIUS', '3D'): (NFType.FLOAT,),
    ('N_SPHERE_RADIUS', '4D'): (NFType.FLOAT,),
    ('SMOOTH_F1', '1D'): (NFType.FLOAT, NFType.COLOR, NFType.FLOAT),
    ('SMOOTH_F1', '2D'): (NFType.FLOAT, NFType.COLOR, NFType.VECTOR),
    ('SMOOTH_F1', '3D'): (NFType.FLOAT, NFType.COLOR, NFType.VECTOR),
    ('SMOOTH_F1', '4D'): (NFType.FLOAT, NFType.COLOR, NFType.VECTOR, NFType.FLOAT),
}

def _result_types_voronoi_texture(arguments):
    base = output_types(arguments, ['rna_feature', 'rna_voronoi_dimensions'], _SIGNATURE_voronoi_texture)
    return base

_SIGNATURE_vector_math = {
    ('ABSOLUTE',): (NFType.VECTOR,),
    ('ADD',): (NFType.VECTOR,),
    ('CEIL',): (NFType.VECTOR,),
    ('COSINE',): (NFType.VECTOR,),
    ('CROSS_PRODUCT',): (NFType.VECTOR,),
    ('DISTANCE',): (NFType.FLOAT,),
    ('DIVIDE',): (NFType.VECTOR,),
    ('DOT_PRODUCT',): (NFType.FLOAT,),
    ('FACEFORWARD',): (NFType.VECTOR,),
    ('FLOOR',): (NFType.VECTOR,),
    ('FRACTION',): (NFType.VECTOR,),
    ('LENGTH',): (NFType.FLOAT,),
    ('MAXIMUM',): (NFType.VECTOR,),
    ('MINIMUM',): (NFType.VECTOR,),
    ('MODULO',): (NFType.VECTOR,),
    ('MULTIPLY',): (NFType.VECTOR,),
    ('MULTIPLY_ADD',): (NFType.VECTOR,),
    ('NORMALIZE',): (NFType.VECTOR,),
    ('POWER',): (NFType.VECTOR,),
    ('PROJECT',): (NFType.VECTOR,),
    ('REFLECT',): (NFType.VECTOR,),
    ('REFRACT',): (NFType.VECTOR,),
    ('ROUND',): (NFType.VECTOR,),
    ('SCALE',): (NFType.VECTOR,),
    ('SIGN',): (NFType.VECTOR,),
    ('SINE',): (NFType.VECTOR,),
    ('SNAP',): (NFType.VECTOR,),
    ('SUBTRACT',): (NFType.VECTOR,),
    ('TANGENT',): (NFType.VECTOR,),
    ('WRAP',): (NFType.VECTOR,),
}

def _result_types_vector_math(arguments):
    base = output_types(arguments, ['rna_operation'], _SIGNATURE_vector_math)
    return base


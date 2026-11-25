"""One wrapper per Blender node. Fixed sockets are named and optional."""
from typing import Annotated
from NodeForge import Bool, Color, DynamicResults, EvaluationMode, Float, Geometry, Int, Material, Object, Rotation, String, Vector
EXTENSION_API = 2
EXTENSIONS = {'align_euler_to_vector': '.backend:align_euler_to_vector', 'align_rotation_to_vector': '.backend:align_rotation_to_vector', 'axes_to_rotation': '.backend:axes_to_rotation', 'axis_angle_to_rotation': '.backend:axis_angle_to_rotation', 'bit_math': '.backend:bit_math', 'boolean_math': '.backend:boolean_math', 'combine_color': '.backend:combine_color', 'compare': '.backend:compare', 'euler_to_rotation': '.backend:euler_to_rotation', 'find_in_string': '.backend:find_in_string', 'float_to_integer': '.backend:float_to_integer', 'format_string': '.backend:format_string', 'hash_value': '.backend:hash_value', 'boolean': '.backend:boolean', 'color': '.backend:color', 'integer': '.backend:integer', 'rotation': '.backend:rotation', 'special_characters': '.backend:special_characters', 'string': '.backend:string', 'vector': '.backend:vector', 'integer_math': '.backend:integer_math', 'invert_rotation': '.backend:invert_rotation', 'match_string': '.backend:match_string', 'quaternion_to_rotation': '.backend:quaternion_to_rotation', 'random_value': '.backend:random_value', 'replace_string': '.backend:replace_string', 'reverse_string': '.backend:reverse_string', 'rotate_euler': '.backend:rotate_euler', 'rotate_rotation': '.backend:rotate_rotation', 'rotate_vector': '.backend:rotate_vector', 'rotation_to_axis_angle': '.backend:rotation_to_axis_angle', 'rotation_to_euler': '.backend:rotation_to_euler', 'rotation_to_quaternion': '.backend:rotation_to_quaternion', 'separate_color': '.backend:separate_color', 'set_string_case': '.backend:set_string_case', 'slice_string': '.backend:slice_string', 'split_string': '.backend:split_string', 'string_length': '.backend:string_length', 'string_to_value': '.backend:string_to_value', 'trim_string': '.backend:trim_string', 'value_to_string': '.backend:value_to_string', 'accumulate_field': '.backend:accumulate_field', 'domain_size': '.backend:domain_size', 'attribute_statistic': '.backend:attribute_statistic', 'blur_attribute': '.backend:blur_attribute', 'bounding_box': '.backend:bounding_box', 'capture_attribute': '.backend:capture_attribute', 'cluster_by_connected': '.backend:cluster_by_connected', 'cluster_by_distance': '.backend:cluster_by_distance', 'convex_hull': '.backend:convex_hull', 'corners_of_edge': '.backend:corners_of_edge', 'corners_of_face': '.backend:corners_of_face', 'corners_of_vertex': '.backend:corners_of_vertex', 'cube_grid_topology': '.backend:cube_grid_topology', 'arc': '.backend:arc', 'endpoint_selection': '.backend:endpoint_selection', 'handle_type_selection': '.backend:handle_type_selection', 'curve_length': '.backend:curve_length', 'curve_of_point': '.backend:curve_of_point', 'b_zier_segment': '.backend:b_zier_segment', 'curve_circle': '.backend:curve_circle', 'curve_line': '.backend:curve_line', 'quadrilateral': '.backend:quadrilateral', 'quadratic_b_zier': '.backend:quadratic_b_zier', 'set_handle_type': '.backend:set_handle_type', 'spiral': '.backend:spiral', 'set_spline_type': '.backend:set_spline_type', 'star': '.backend:star', 'curve_to_mesh': '.backend:curve_to_mesh', 'curve_to_points': '.backend:curve_to_points', 'curves_to_grease_pencil': '.backend:curves_to_grease_pencil', 'deform_curves_on_surface': '.backend:deform_curves_on_surface', 'delete_geometry': '.backend:delete_geometry', 'distribute_points_in_grid': '.backend:distribute_points_in_grid', 'distribute_points_in_volume': '.backend:distribute_points_in_volume', 'distribute_points_on_faces': '.backend:distribute_points_on_faces', 'dual_mesh': '.backend:dual_mesh', 'duplicate_elements': '.backend:duplicate_elements', 'edge_paths_to_curves': '.backend:edge_paths_to_curves', 'edge_paths_to_selection': '.backend:edge_paths_to_selection', 'edges_of_corner': '.backend:edges_of_corner', 'edges_of_vertex': '.backend:edges_of_vertex', 'edges_to_face_groups': '.backend:edges_to_face_groups', 'extrude_mesh': '.backend:extrude_mesh', 'face_of_corner': '.backend:face_of_corner', 'evaluate_at_index': '.backend:evaluate_at_index', 'field_average': '.backend:field_average', 'field_min_max': '.backend:field_min_max', 'evaluate_on_domain': '.backend:evaluate_on_domain', 'field_variance': '.backend:field_variance', 'fill_curve': '.backend:fill_curve', 'fillet_curve': '.backend:fillet_curve', 'filter_list': '.backend:filter_list', 'flip_faces': '.backend:flip_faces', 'geometry_to_instance': '.backend:geometry_to_instance', 'get_attribute_names': '.backend:get_attribute_names', 'get_geometry_component': '.backend:get_geometry_component', 'get_named_grid': '.backend:get_named_grid', 'dial_gizmo': '.backend:dial_gizmo', 'linear_gizmo': '.backend:linear_gizmo', 'grease_pencil_to_curves': '.backend:grease_pencil_to_curves', 'advect_grid': '.backend:advect_grid', 'clip_grid': '.backend:clip_grid', 'grid_curl': '.backend:grid_curl', 'grid_dilate_erode': '.backend:grid_dilate_erode', 'grid_divergence': '.backend:grid_divergence', 'grid_gradient': '.backend:grid_gradient', 'grid_laplacian': '.backend:grid_laplacian', 'grid_mean': '.backend:grid_mean', 'grid_median': '.backend:grid_median', 'prune_grid': '.backend:prune_grid', 'grid_to_mesh': '.backend:grid_to_mesh', 'grid_to_points': '.backend:grid_to_points', 'voxelize_grid': '.backend:voxelize_grid', 'import_csv': '.backend:import_csv', 'import_obj': '.backend:import_obj', 'import_ply': '.backend:import_ply', 'import_stl': '.backend:import_stl', 'import_text': '.backend:import_text', 'import_vdb': '.backend:import_vdb', 'index_of_nearest': '.backend:index_of_nearest', 'index_switch': '.backend:index_switch', 'active_camera': '.backend:active_camera', 'curve_handle_positions': '.backend:curve_handle_positions', 'curve_tilt': '.backend:curve_tilt', 'is_edge_smooth': '.backend:is_edge_smooth', 'id': '.backend:id', 'index': '.backend:index', 'instance_bounds': '.backend:instance_bounds', 'instance_reference': '.backend:instance_reference', 'instance_rotation': '.backend:instance_rotation', 'instance_scale': '.backend:instance_scale', 'material': '.backend:material', 'material_index': '.backend:material_index', 'edge_angle': '.backend:edge_angle', 'edge_neighbors': '.backend:edge_neighbors', 'edge_vertices': '.backend:edge_vertices', 'face_area': '.backend:face_area', 'is_face_planar': '.backend:is_face_planar', 'face_neighbors': '.backend:face_neighbors', 'mesh_island': '.backend:mesh_island', 'vertex_neighbors': '.backend:vertex_neighbors', 'named_attribute': '.backend:named_attribute', 'named_layer_selection': '.backend:named_layer_selection', 'normal': '.backend:normal', 'object': '.backend:object', 'position': '.backend:position', 'radius': '.backend:radius', 'scene_time': '.backend:scene_time', 'is_face_smooth': '.backend:is_face_smooth', 'shortest_edge_paths': '.backend:shortest_edge_paths', 'is_spline_cyclic': '.backend:is_spline_cyclic', 'spline_resolution': '.backend:spline_resolution', 'curve_tangent': '.backend:curve_tangent', 'voxel_index': '.backend:voxel_index', 'instance_on_points': '.backend:instance_on_points', 'instances_to_points': '.backend:instances_to_points', 'interpolate_curves': '.backend:interpolate_curves', 'is_viewport': '.backend:is_viewport', 'join_geometry': '.backend:join_geometry', 'get_list_item': '.backend:get_list_item', 'material_selection': '.backend:material_selection', 'menu_switch': '.backend:menu_switch', 'merge_by_distance': '.backend:merge_by_distance', 'merge_layers': '.backend:merge_layers', 'merge_points': '.backend:merge_points', 'mesh_bevel': '.backend:mesh_bevel', 'mesh_boolean': '.backend:mesh_boolean', 'mesh_circle': '.backend:mesh_circle', 'cone': '.backend:cone', 'cube': '.backend:cube', 'cylinder': '.backend:cylinder', 'face_group_boundaries': '.backend:face_group_boundaries', 'grid': '.backend:grid', 'ico_sphere': '.backend:ico_sphere', 'mesh_line': '.backend:mesh_line', 'mesh_to_curve': '.backend:mesh_to_curve', 'mesh_to_density_grid': '.backend:mesh_to_density_grid', 'mesh_to_points': '.backend:mesh_to_points', 'mesh_to_sdf_grid': '.backend:mesh_to_sdf_grid', 'mesh_to_volume': '.backend:mesh_to_volume', 'uv_sphere': '.backend:uv_sphere', 'offset_corner_in_face': '.backend:offset_corner_in_face', 'offset_point_in_curve': '.backend:offset_point_in_curve', 'points': '.backend:points', 'points_of_curve': '.backend:points_of_curve', 'points_to_curves': '.backend:points_to_curves', 'points_to_sdf_grid': '.backend:points_to_sdf_grid', 'points_to_vertices': '.backend:points_to_vertices', 'points_to_volume': '.backend:points_to_volume', 'geometry_proximity': '.backend:geometry_proximity', 'raycast': '.backend:raycast', 'realize_instances': '.backend:realize_instances', 'remove_named_attribute': '.backend:remove_named_attribute', 'rename_attribute': '.backend:rename_attribute', 'replace_material': '.backend:replace_material', 'resample_curve': '.backend:resample_curve', 'reverse_curve': '.backend:reverse_curve', 'rotate_instances': '.backend:rotate_instances', 'sdf_grid_boolean': '.backend:sdf_grid_boolean', 'sdf_grid_fillet': '.backend:sdf_grid_fillet', 'sdf_grid_laplacian': '.backend:sdf_grid_laplacian', 'sdf_grid_mean': '.backend:sdf_grid_mean', 'sdf_grid_mean_curvature': '.backend:sdf_grid_mean_curvature', 'sdf_grid_median': '.backend:sdf_grid_median', 'sdf_grid_offset': '.backend:sdf_grid_offset', 'sample_curve': '.backend:sample_curve', 'sample_grid': '.backend:sample_grid', 'sample_grid_index': '.backend:sample_grid_index', 'sample_index': '.backend:sample_index', 'sample_nearest': '.backend:sample_nearest', 'sample_nearest_surface': '.backend:sample_nearest_surface', 'sample_uv_surface': '.backend:sample_uv_surface', 'scale_elements': '.backend:scale_elements', 'scale_instances': '.backend:scale_instances', 'self_object': '.backend:self_object', 'separate_components': '.backend:separate_components', 'separate_geometry': '.backend:separate_geometry', 'set_handle_positions': '.backend:set_handle_positions', 'set_curve_normal': '.backend:set_curve_normal', 'set_curve_radius': '.backend:set_curve_radius', 'set_curve_tilt': '.backend:set_curve_tilt', 'set_geometry_name': '.backend:set_geometry_name', 'set_grease_pencil_color': '.backend:set_grease_pencil_color', 'set_grease_pencil_depth': '.backend:set_grease_pencil_depth', 'set_grease_pencil_softness': '.backend:set_grease_pencil_softness', 'set_grid_background': '.backend:set_grid_background', 'set_id': '.backend:set_id', 'set_material': '.backend:set_material', 'set_material_index': '.backend:set_material_index', 'set_mesh_normal': '.backend:set_mesh_normal', 'set_nurbs_order': '.backend:set_nurbs_order', 'set_nurbs_weight': '.backend:set_nurbs_weight', 'set_point_radius': '.backend:set_point_radius', 'set_position': '.backend:set_position', 'set_shade_smooth': '.backend:set_shade_smooth', 'set_spline_cyclic': '.backend:set_spline_cyclic', 'set_spline_resolution': '.backend:set_spline_resolution', 'sort_elements': '.backend:sort_elements', 'sort_list': '.backend:sort_list', 'spline_length': '.backend:spline_length', 'spline_parameter': '.backend:spline_parameter', 'split_edges': '.backend:split_edges', 'split_to_instances': '.backend:split_to_instances', 'store_named_attribute': '.backend:store_named_attribute', 'store_named_grid': '.backend:store_named_grid', 'join_strings': '.backend:join_strings', 'subdivide_curve': '.backend:subdivide_curve', 'subdivide_mesh': '.backend:subdivide_mesh', 'subdivision_surface': '.backend:subdivision_surface', 'switch': '.backend:switch', 'tag_filter': '.backend:tag_filter', 'option_3d_cursor': '.backend:option_3d_cursor', 'active_element': '.backend:active_element', 'face_set': '.backend:face_set', 'mouse_position': '.backend:mouse_position', 'selection': '.backend:selection', 'set_face_set': '.backend:set_face_set', 'set_selection': '.backend:set_selection', 'transfer_attributes': '.backend:transfer_attributes', 'transform_geometry': '.backend:transform_geometry', 'translate_instances': '.backend:translate_instances', 'triangulate': '.backend:triangulate', 'trim_curve': '.backend:trim_curve', 'uv_unwrap': '.backend:uv_unwrap', 'vertex_of_corner': '.backend:vertex_of_corner', 'volume_cube': '.backend:volume_cube', 'volume_to_mesh': '.backend:volume_to_mesh', 'warning': '.backend:warning', 'enable_output': '.backend:enable_output', 'implicit_conversion': '.backend:implicit_conversion', 'reroute': '.backend:reroute', 'blackbody': '.backend:blackbody', 'clamp': '.backend:clamp', 'combine_xyz': '.backend:combine_xyz', 'float_curve': '.backend:float_curve', 'gamma': '.backend:gamma', 'map_range': '.backend:map_range', 'math': '.backend:math', 'mix': '.backend:mix', 'mix_legacy': '.backend:mix_legacy', 'rgb_curves': '.backend:rgb_curves', 'separate_xyz': '.backend:separate_xyz', 'brick_texture': '.backend:brick_texture', 'checker_texture': '.backend:checker_texture', 'gabor_texture': '.backend:gabor_texture', 'gradient_texture': '.backend:gradient_texture', 'magic_texture': '.backend:magic_texture', 'noise_texture': '.backend:noise_texture', 'voronoi_texture': '.backend:voronoi_texture', 'wave_texture': '.backend:wave_texture', 'white_noise_texture': '.backend:white_noise_texture', 'color_ramp': '.backend:color_ramp', 'value': '.backend:value', 'vector_curves': '.backend:vector_curves', 'vector_math': '.backend:vector_math', 'vector_rotate': '.backend:vector_rotate'}

def align_euler_to_vector(
    *,
    rotation: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    factor: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    axis: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'X',
    pivot_axis: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'AUTO',
) -> {'rotation': Vector}:
    """Build a FunctionNodeAlignEulerToVector; omitted inputs keep Blender defaults."""
    ...

def align_rotation_to_vector(
    *,
    rotation: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    factor: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    axis: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Z',
    pivot_axis: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'AUTO',
) -> {'rotation': Rotation}:
    """Build a FunctionNodeAlignRotationToVector; omitted inputs keep Blender defaults."""
    ...

def axes_to_rotation(
    *,
    primary_axis_vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    secondary_axis_vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    primary_axis: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Z',
    secondary_axis: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'X',
) -> {'rotation': Rotation}:
    """Build a FunctionNodeAxesToRotation; omitted inputs keep Blender defaults."""
    ...

def axis_angle_to_rotation(
    *,
    axis: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    angle: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'rotation': Rotation}:
    """Build a FunctionNodeAxisAngleToRotation; omitted inputs keep Blender defaults."""
    ...

def bit_math(
    *,
    a: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    b: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    shift: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    operation: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'AND',
) -> {'value': Int}:
    """Build a FunctionNodeBitMath; omitted inputs keep Blender defaults."""
    ...

def boolean_math(
    *,
    boolean: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    boolean_001: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    operation: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'AND',
) -> {'boolean': Bool}:
    """Build a FunctionNodeBooleanMath; omitted inputs keep Blender defaults."""
    ...

def combine_color(
    *,
    red: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    green: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    blue: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    alpha: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'RGB',
) -> {'color': Color}:
    """Build a FunctionNodeCombineColor; omitted inputs keep Blender defaults."""
    ...

def compare(
    *,
    a: Annotated[Color | Float | Int | Material | Object | String | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    b: Annotated[Color | Float | Int | Material | Object | String | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    epsilon: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    c: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    angle: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'ELEMENT',
    operation: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'GREATER_THAN',
) -> {'result': Bool}:
    """Build a FunctionNodeCompare; omitted inputs keep Blender defaults."""
    ...

def euler_to_rotation(
    *,
    euler: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'rotation': Rotation}:
    """Build a FunctionNodeEulerToRotation; omitted inputs keep Blender defaults."""
    ...

def find_in_string(
    *,
    string: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    search: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'From Start',
) -> {'first_found': Int, 'count': Int}:
    """Build a FunctionNodeFindInString; omitted inputs keep Blender defaults."""
    ...

def float_to_integer(
    *,
    float: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    rounding_mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'ROUND',
) -> {'integer': Int}:
    """Build a FunctionNodeFloatToInt; omitted inputs keep Blender defaults."""
    ...

def format_string(
    *,
    format: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'string': String}:
    """Build a FunctionNodeFormatString; omitted inputs keep Blender defaults."""
    ...

def hash_value(
    *,
    value: Annotated[Color | Float | Int | Rotation | String | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    seed: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'INT',
) -> {'hash': Int}:
    """Build a FunctionNodeHashValue; omitted inputs keep Blender defaults."""
    ...

def boolean(
    *,
    boolean_2: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = None,
    boolean: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False,
) -> {'boolean': Bool}:
    """Build a FunctionNodeInputBool; omitted inputs keep Blender defaults."""
    ...

def color(
    *,
    value: Annotated[Color, EvaluationMode.COMPILE_TIME_ONLY] = None,
) -> {'color': Color}:
    """Build a FunctionNodeInputColor; omitted inputs keep Blender defaults."""
    ...

def integer(
    *,
    integer: Annotated[Int, EvaluationMode.COMPILE_TIME_ONLY] = None,
) -> {'integer': Int}:
    """Build a FunctionNodeInputInt; omitted inputs keep Blender defaults."""
    ...

def rotation(
    *,
    rotation: Annotated[Rotation, EvaluationMode.COMPILE_TIME_ONLY] = None,
) -> {'rotation': Rotation}:
    """Build a FunctionNodeInputRotation; omitted inputs keep Blender defaults."""
    ...

def special_characters(
) -> {'line_break': String, 'tab': String}:
    """Build a FunctionNodeInputSpecialCharacters; omitted inputs keep Blender defaults."""
    ...

def string(
    *,
    string: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = None,
) -> {'string': String}:
    """Build a FunctionNodeInputString; omitted inputs keep Blender defaults."""
    ...

def vector(
    *,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_ONLY] = None,
) -> {'vector': Vector}:
    """Build a FunctionNodeInputVector; omitted inputs keep Blender defaults."""
    ...

def integer_math(
    *,
    value: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    value_001: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    value_002: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    operation: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'ADD',
) -> {'value': Int}:
    """Build a FunctionNodeIntegerMath; omitted inputs keep Blender defaults."""
    ...

def invert_rotation(
    *,
    rotation: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'rotation': Rotation}:
    """Build a FunctionNodeInvertRotation; omitted inputs keep Blender defaults."""
    ...

def match_string(
    *,
    string: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    key: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    operation: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Starts With',
) -> {'result': Bool}:
    """Build a FunctionNodeMatchString; omitted inputs keep Blender defaults."""
    ...

def quaternion_to_rotation(
    *,
    w: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    x: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    y: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    z: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'rotation': Rotation}:
    """Build a FunctionNodeQuaternionToRotation; omitted inputs keep Blender defaults."""
    ...

def random_value(
    *,
    min: Annotated[Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    max: Annotated[Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    seed: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    probability: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a FunctionNodeRandomValue; omitted inputs keep Blender defaults."""
    ...

def replace_string(
    *,
    string: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    find: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    replace: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'string': String}:
    """Build a FunctionNodeReplaceString; omitted inputs keep Blender defaults."""
    ...

def reverse_string(
    *,
    string: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'string': String}:
    """Build a FunctionNodeReverseString; omitted inputs keep Blender defaults."""
    ...

def rotate_euler(
    *,
    rotation: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    axis: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    angle: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    rotate_by: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    rotation_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'EULER',
    space: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'OBJECT',
) -> {'rotation': Vector}:
    """Build a FunctionNodeRotateEuler; omitted inputs keep Blender defaults."""
    ...

def rotate_rotation(
    *,
    rotation: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    rotate_by: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    rotation_space: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'GLOBAL',
) -> {'rotation': Rotation}:
    """Build a FunctionNodeRotateRotation; omitted inputs keep Blender defaults."""
    ...

def rotate_vector(
    *,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    rotation: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'vector': Vector}:
    """Build a FunctionNodeRotateVector; omitted inputs keep Blender defaults."""
    ...

def rotation_to_axis_angle(
    *,
    rotation: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'axis': Vector, 'angle': Float}:
    """Build a FunctionNodeRotationToAxisAngle; omitted inputs keep Blender defaults."""
    ...

def rotation_to_euler(
    *,
    rotation: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'euler': Vector}:
    """Build a FunctionNodeRotationToEuler; omitted inputs keep Blender defaults."""
    ...

def rotation_to_quaternion(
    *,
    rotation: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'w': Float, 'x': Float, 'y': Float, 'z': Float}:
    """Build a FunctionNodeRotationToQuaternion; omitted inputs keep Blender defaults."""
    ...

def separate_color(
    *,
    color: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'RGB',
) -> {'red': Float, 'green': Float, 'blue': Float, 'alpha': Float}:
    """Build a FunctionNodeSeparateColor; omitted inputs keep Blender defaults."""
    ...

def set_string_case(
    *,
    string: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    case: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Uppercase',
) -> {'string': String}:
    """Build a FunctionNodeSetStringCase; omitted inputs keep Blender defaults."""
    ...

def slice_string(
    *,
    string: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    position: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    length: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'string': String}:
    """Build a FunctionNodeSliceString; omitted inputs keep Blender defaults."""
    ...

def split_string(
    *,
    string: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    separator: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'list': String}:
    """Build a FunctionNodeSplitString; omitted inputs keep Blender defaults."""
    ...

def string_length(
    *,
    string: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'length': Int}:
    """Build a FunctionNodeStringLength; omitted inputs keep Blender defaults."""
    ...

def string_to_value(
    *,
    string: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    base: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a FunctionNodeStringToValue; omitted inputs keep Blender defaults."""
    ...

def trim_string(
    *,
    string: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    characters: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    whitespace: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    end: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'string': String}:
    """Build a FunctionNodeTrimString; omitted inputs keep Blender defaults."""
    ...

def value_to_string(
    *,
    value: Annotated[Float | Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    decimals: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    base: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    padding: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> {'string': String}:
    """Build a FunctionNodeValueToString; omitted inputs keep Blender defaults."""
    ...

def accumulate_field(
    *,
    value: Annotated[Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> DynamicResults:
    """Build a GeometryNodeAccumulateField; omitted inputs keep Blender defaults."""
    ...

def domain_size(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    component: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'MESH',
) -> DynamicResults:
    """Build a GeometryNodeAttributeDomainSize; omitted inputs keep Blender defaults."""
    ...

def attribute_statistic(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    attribute: Annotated[Float | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> DynamicResults:
    """Build a GeometryNodeAttributeStatistic; omitted inputs keep Blender defaults."""
    ...

def blur_attribute(
    *,
    value: Annotated[Color | Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    iterations: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    weight: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeBlurAttribute; omitted inputs keep Blender defaults."""
    ...

def bounding_box(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    use_radius: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'bounding_box': Geometry, 'min': Vector, 'max': Vector}:
    """Build a GeometryNodeBoundBox; omitted inputs keep Blender defaults."""
    ...

def capture_attribute(
    *items: Annotated[Bool | Color | Float | Geometry | Int | Rotation | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME],
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
    elements: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = '',
) -> DynamicResults:
    """Build a GeometryNodeCaptureAttribute; omitted inputs keep Blender defaults."""
    ...

def cluster_by_connected(
    *,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    distance: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'cluster_id': Int}:
    """Build a GeometryNodeClusterByConnected; omitted inputs keep Blender defaults."""
    ...

def cluster_by_distance(
    *,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    distance: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'cluster_id': Int}:
    """Build a GeometryNodeClusterByDistance; omitted inputs keep Blender defaults."""
    ...

def convex_hull(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'convex_hull': Geometry}:
    """Build a GeometryNodeConvexHull; omitted inputs keep Blender defaults."""
    ...

def corners_of_edge(
    *,
    edge_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    weights: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sort_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'corner_index': Int, 'total': Int}:
    """Build a GeometryNodeCornersOfEdge; omitted inputs keep Blender defaults."""
    ...

def corners_of_face(
    *,
    face_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    weights: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sort_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'corner_index': Int, 'total': Int}:
    """Build a GeometryNodeCornersOfFace; omitted inputs keep Blender defaults."""
    ...

def corners_of_vertex(
    *,
    vertex_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    weights: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sort_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'corner_index': Int, 'total': Int}:
    """Build a GeometryNodeCornersOfVertex; omitted inputs keep Blender defaults."""
    ...

def cube_grid_topology(
    *,
    bounds_min: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    bounds_max: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    resolution_x: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    resolution_y: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    resolution_z: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    min_x: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    min_y: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    min_z: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'topology': Bool}:
    """Build a GeometryNodeCubeGridTopology; omitted inputs keep Blender defaults."""
    ...

def arc(
    *,
    resolution: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    middle: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    end: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    offset_angle: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    connect_center: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    invert_arc: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start_angle: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sweep_angle: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'RADIUS',
) -> DynamicResults:
    """Build a GeometryNodeCurveArc; omitted inputs keep Blender defaults."""
    ...

def endpoint_selection(
    *,
    start_size: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    end_size: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'selection': Bool}:
    """Build a GeometryNodeCurveEndpointSelection; omitted inputs keep Blender defaults."""
    ...

def handle_type_selection(
    *,
    handle_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'AUTO',
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'LEFT,RIGHT',
) -> {'selection': Bool}:
    """Build a GeometryNodeCurveHandleTypeSelection; omitted inputs keep Blender defaults."""
    ...

def curve_length(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'length': Float}:
    """Build a GeometryNodeCurveLength; omitted inputs keep Blender defaults."""
    ...

def curve_of_point(
    *,
    point_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curve_index': Int, 'index_in_curve': Int}:
    """Build a GeometryNodeCurveOfPoint; omitted inputs keep Blender defaults."""
    ...

def b_zier_segment(
    *,
    resolution: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start_handle: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    end_handle: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    end: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POSITION',
) -> {'curve': Geometry}:
    """Build a GeometryNodeCurvePrimitiveBezierSegment; omitted inputs keep Blender defaults."""
    ...

def curve_circle(
    *,
    resolution: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    point_1: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    point_2: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    point_3: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'RADIUS',
) -> DynamicResults:
    """Build a GeometryNodeCurvePrimitiveCircle; omitted inputs keep Blender defaults."""
    ...

def curve_line(
    *,
    start: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    end: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    direction: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    length: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINTS',
) -> {'curve': Geometry}:
    """Build a GeometryNodeCurvePrimitiveLine; omitted inputs keep Blender defaults."""
    ...

def quadrilateral(
    *,
    width: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    height: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    offset: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    bottom_width: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    top_width: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    bottom_height: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    top_height: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    point_1: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    point_2: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    point_3: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    point_4: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'RECTANGLE',
) -> {'curve': Geometry}:
    """Build a GeometryNodeCurvePrimitiveQuadrilateral; omitted inputs keep Blender defaults."""
    ...

def quadratic_b_zier(
    *,
    resolution: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    middle: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    end: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curve': Geometry}:
    """Build a GeometryNodeCurveQuadraticBezier; omitted inputs keep Blender defaults."""
    ...

def set_handle_type(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    handle_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'AUTO',
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'LEFT,RIGHT',
) -> {'curve': Geometry}:
    """Build a GeometryNodeCurveSetHandles; omitted inputs keep Blender defaults."""
    ...

def spiral(
    *,
    resolution: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    rotations: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start_radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    end_radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    height: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    reverse: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curve': Geometry}:
    """Build a GeometryNodeCurveSpiral; omitted inputs keep Blender defaults."""
    ...

def set_spline_type(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    spline_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POLY',
) -> {'curve': Geometry}:
    """Build a GeometryNodeCurveSplineType; omitted inputs keep Blender defaults."""
    ...

def star(
    *,
    points: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    inner_radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    outer_radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    twist: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curve': Geometry, 'outer_points': Bool}:
    """Build a GeometryNodeCurveStar; omitted inputs keep Blender defaults."""
    ...

def curve_to_mesh(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    profile_curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    fill_caps: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry}:
    """Build a GeometryNodeCurveToMesh; omitted inputs keep Blender defaults."""
    ...

def curve_to_points(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    count: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    length: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'COUNT',
) -> {'points': Geometry, 'tangent': Vector, 'normal': Vector, 'rotation': Rotation}:
    """Build a GeometryNodeCurveToPoints; omitted inputs keep Blender defaults."""
    ...

def curves_to_grease_pencil(
    *,
    curves: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    instances_as_layers: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'grease_pencil': Geometry}:
    """Build a GeometryNodeCurvesToGreasePencil; omitted inputs keep Blender defaults."""
    ...

def deform_curves_on_surface(
    *,
    curves: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curves': Geometry}:
    """Build a GeometryNodeDeformCurvesOnSurface; omitted inputs keep Blender defaults."""
    ...

def delete_geometry(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'ALL',
) -> {'geometry': Geometry}:
    """Build a GeometryNodeDeleteGeometry; omitted inputs keep Blender defaults."""
    ...

def distribute_points_in_grid(
    *,
    grid: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    density: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    seed: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    spacing: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    threshold: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'DENSITY_RANDOM',
) -> {'points': Geometry}:
    """Build a GeometryNodeDistributePointsInGrid; omitted inputs keep Blender defaults."""
    ...

def distribute_points_in_volume(
    *,
    volume: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    density: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    seed: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    spacing: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    threshold: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Random',
) -> {'points': Geometry}:
    """Build a GeometryNodeDistributePointsInVolume; omitted inputs keep Blender defaults."""
    ...

def distribute_points_on_faces(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    density: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    seed: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    distance_min: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    density_max: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    density_factor: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    distribute_method: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'RANDOM',
    use_legacy_normal: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False,
) -> {'points': Geometry, 'normal': Vector, 'rotation': Rotation}:
    """Build a GeometryNodeDistributePointsOnFaces; omitted inputs keep Blender defaults."""
    ...

def dual_mesh(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    keep_boundaries: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'dual_mesh': Geometry}:
    """Build a GeometryNodeDualMesh; omitted inputs keep Blender defaults."""
    ...

def duplicate_elements(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    amount: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> {'geometry': Geometry, 'duplicate_index': Int}:
    """Build a GeometryNodeDuplicateElements; omitted inputs keep Blender defaults."""
    ...

def edge_paths_to_curves(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start_vertices: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    next_vertex_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curves': Geometry}:
    """Build a GeometryNodeEdgePathsToCurves; omitted inputs keep Blender defaults."""
    ...

def edge_paths_to_selection(
    *,
    start_vertices: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    next_vertex_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'selection': Bool}:
    """Build a GeometryNodeEdgePathsToSelection; omitted inputs keep Blender defaults."""
    ...

def edges_of_corner(
    *,
    corner_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'next_edge_index': Int, 'previous_edge_index': Int}:
    """Build a GeometryNodeEdgesOfCorner; omitted inputs keep Blender defaults."""
    ...

def edges_of_vertex(
    *,
    vertex_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    weights: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sort_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'edge_index': Int, 'total': Int}:
    """Build a GeometryNodeEdgesOfVertex; omitted inputs keep Blender defaults."""
    ...

def edges_to_face_groups(
    *,
    boundary_edges: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'face_group_id': Int}:
    """Build a GeometryNodeEdgesToFaceGroups; omitted inputs keep Blender defaults."""
    ...

def extrude_mesh(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    offset: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    offset_scale: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    individual: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FACES',
) -> {'mesh': Geometry, 'top': Bool, 'side': Bool}:
    """Build a GeometryNodeExtrudeMesh; omitted inputs keep Blender defaults."""
    ...

def face_of_corner(
    *,
    corner_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'face_index': Int, 'index_in_face': Int}:
    """Build a GeometryNodeFaceOfCorner; omitted inputs keep Blender defaults."""
    ...

def evaluate_at_index(
    *,
    value: Annotated[Bool | Color | Float | Int | Rotation | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> DynamicResults:
    """Build a GeometryNodeFieldAtIndex; omitted inputs keep Blender defaults."""
    ...

def field_average(
    *,
    value: Annotated[Float | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> DynamicResults:
    """Build a GeometryNodeFieldAverage; omitted inputs keep Blender defaults."""
    ...

def field_min_max(
    *,
    value: Annotated[Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> DynamicResults:
    """Build a GeometryNodeFieldMinAndMax; omitted inputs keep Blender defaults."""
    ...

def evaluate_on_domain(
    *,
    value: Annotated[Bool | Color | Float | Int | Rotation | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> DynamicResults:
    """Build a GeometryNodeFieldOnDomain; omitted inputs keep Blender defaults."""
    ...

def field_variance(
    *,
    value: Annotated[Float | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> DynamicResults:
    """Build a GeometryNodeFieldVariance; omitted inputs keep Blender defaults."""
    ...

def fill_curve(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    fill_rule: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Even-Odd',
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Triangles',
) -> {'mesh': Geometry}:
    """Build a GeometryNodeFillCurve; omitted inputs keep Blender defaults."""
    ...

def fillet_curve(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    limit_radius: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    count: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Bézier',
) -> {'curve': Geometry}:
    """Build a GeometryNodeFilletCurve; omitted inputs keep Blender defaults."""
    ...

def filter_list(
    *,
    list: Annotated[Bool | Color | Float | Geometry | Int | Material | Object | Rotation | String | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    socket_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeFilterList; omitted inputs keep Blender defaults."""
    ...

def flip_faces(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry}:
    """Build a GeometryNodeFlipFaces; omitted inputs keep Blender defaults."""
    ...

def geometry_to_instance(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'instances': Geometry}:
    """Build a GeometryNodeGeometryToInstance; omitted inputs keep Blender defaults."""
    ...

def get_attribute_names(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    filter_data_type: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    filter_domain: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Float',
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Point',
) -> {'names': String}:
    """Build a GeometryNodeGetAttributeNames; omitted inputs keep Blender defaults."""
    ...

def get_geometry_component(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    remove: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Mesh',
) -> {'geometry': Geometry, 'component': Geometry, 'exists': Bool}:
    """Build a GeometryNodeGetGeometryComponent; omitted inputs keep Blender defaults."""
    ...

def get_named_grid(
    *,
    volume: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    name: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    remove: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeGetNamedGrid; omitted inputs keep Blender defaults."""
    ...

def dial_gizmo(
    *,
    value: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    up: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    screen_space: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    color_id: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'PRIMARY',
) -> {'transform': Geometry}:
    """Build a GeometryNodeGizmoDial; omitted inputs keep Blender defaults."""
    ...

def linear_gizmo(
    *,
    value: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    direction: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    color_id: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'PRIMARY',
    draw_style: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'ARROW',
) -> {'transform': Geometry}:
    """Build a GeometryNodeGizmoLinear; omitted inputs keep Blender defaults."""
    ...

def grease_pencil_to_curves(
    *,
    grease_pencil: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    layers_as_instances: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curves': Geometry}:
    """Build a GeometryNodeGreasePencilToCurves; omitted inputs keep Blender defaults."""
    ...

def advect_grid(
    *,
    grid: Annotated[Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    velocity: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    time_step: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    integration_scheme: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Runge-Kutta 3',
    limiter: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Clamp',
) -> DynamicResults:
    """Build a GeometryNodeGridAdvect; omitted inputs keep Blender defaults."""
    ...

def clip_grid(
    *,
    grid: Annotated[Bool | Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    min_x: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    min_y: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    min_z: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    max_x: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    max_y: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    max_z: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeGridClip; omitted inputs keep Blender defaults."""
    ...

def grid_curl(
    *,
    grid: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curl': Vector}:
    """Build a GeometryNodeGridCurl; omitted inputs keep Blender defaults."""
    ...

def grid_dilate_erode(
    *,
    grid: Annotated[Bool | Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    steps: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    connectivity: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Face',
    tiles: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Preserve',
) -> DynamicResults:
    """Build a GeometryNodeGridDilateAndErode; omitted inputs keep Blender defaults."""
    ...

def grid_divergence(
    *,
    grid: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'divergence': Float}:
    """Build a GeometryNodeGridDivergence; omitted inputs keep Blender defaults."""
    ...

def grid_gradient(
    *,
    grid: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'gradient': Vector}:
    """Build a GeometryNodeGridGradient; omitted inputs keep Blender defaults."""
    ...

def grid_laplacian(
    *,
    grid: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'laplacian': Float}:
    """Build a GeometryNodeGridLaplacian; omitted inputs keep Blender defaults."""
    ...

def grid_mean(
    *,
    grid: Annotated[Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    width: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    iterations: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeGridMean; omitted inputs keep Blender defaults."""
    ...

def grid_median(
    *,
    grid: Annotated[Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    width: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    iterations: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeGridMedian; omitted inputs keep Blender defaults."""
    ...

def prune_grid(
    *,
    grid: Annotated[Bool | Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    threshold: Annotated[Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Threshold',
) -> DynamicResults:
    """Build a GeometryNodeGridPrune; omitted inputs keep Blender defaults."""
    ...

def grid_to_mesh(
    *,
    grid: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    threshold: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    adaptivity: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry}:
    """Build a GeometryNodeGridToMesh; omitted inputs keep Blender defaults."""
    ...

def grid_to_points(
    *,
    grid: Annotated[Bool | Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeGridToPoints; omitted inputs keep Blender defaults."""
    ...

def voxelize_grid(
    *,
    grid: Annotated[Bool | Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeGridVoxelize; omitted inputs keep Blender defaults."""
    ...

def import_csv(
    *,
    path: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    delimiter: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'point_cloud': Geometry}:
    """Build a GeometryNodeImportCSV; omitted inputs keep Blender defaults."""
    ...

def import_obj(
    *,
    path: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'instances': Geometry}:
    """Build a GeometryNodeImportOBJ; omitted inputs keep Blender defaults."""
    ...

def import_ply(
    *,
    path: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry}:
    """Build a GeometryNodeImportPLY; omitted inputs keep Blender defaults."""
    ...

def import_stl(
    *,
    path: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry}:
    """Build a GeometryNodeImportSTL; omitted inputs keep Blender defaults."""
    ...

def import_text(
    *,
    path: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'string': String}:
    """Build a GeometryNodeImportText; omitted inputs keep Blender defaults."""
    ...

def import_vdb(
    *,
    path: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'volume': Geometry}:
    """Build a GeometryNodeImportVDB; omitted inputs keep Blender defaults."""
    ...

def index_of_nearest(
    *,
    position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'index': Int, 'has_neighbor': Bool}:
    """Build a GeometryNodeIndexOfNearest; omitted inputs keep Blender defaults."""
    ...

def index_switch(
    *items: Annotated[Bool | Color | Float | Geometry | Int | Material | Object | Rotation | String | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME],
    index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    elements: Annotated[Int, EvaluationMode.COMPILE_TIME_ONLY] = 2,
) -> DynamicResults:
    """Build a GeometryNodeIndexSwitch; omitted inputs keep Blender defaults."""
    ...

def active_camera(
) -> {'active_camera': Object}:
    """Build a GeometryNodeInputActiveCamera; omitted inputs keep Blender defaults."""
    ...

def curve_handle_positions(
    *,
    relative: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'left': Vector, 'right': Vector}:
    """Build a GeometryNodeInputCurveHandlePositions; omitted inputs keep Blender defaults."""
    ...

def curve_tilt(
) -> {'tilt': Float}:
    """Build a GeometryNodeInputCurveTilt; omitted inputs keep Blender defaults."""
    ...

def is_edge_smooth(
) -> {'smooth': Bool}:
    """Build a GeometryNodeInputEdgeSmooth; omitted inputs keep Blender defaults."""
    ...

def id(
) -> {'id': Int}:
    """Build a GeometryNodeInputID; omitted inputs keep Blender defaults."""
    ...

def index(
) -> {'index': Int}:
    """Build a GeometryNodeInputIndex; omitted inputs keep Blender defaults."""
    ...

def instance_bounds(
    *,
    use_radius: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'min': Vector, 'max': Vector}:
    """Build a GeometryNodeInputInstanceBounds; omitted inputs keep Blender defaults."""
    ...

def instance_reference(
) -> {'reference_index': Int}:
    """Build a GeometryNodeInputInstanceReference; omitted inputs keep Blender defaults."""
    ...

def instance_rotation(
) -> {'rotation': Rotation}:
    """Build a GeometryNodeInputInstanceRotation; omitted inputs keep Blender defaults."""
    ...

def instance_scale(
) -> {'scale': Vector}:
    """Build a GeometryNodeInputInstanceScale; omitted inputs keep Blender defaults."""
    ...

def material(
) -> {'material': Material}:
    """Build a GeometryNodeInputMaterial; omitted inputs keep Blender defaults."""
    ...

def material_index(
) -> {'material_index': Int}:
    """Build a GeometryNodeInputMaterialIndex; omitted inputs keep Blender defaults."""
    ...

def edge_angle(
) -> {'unsigned_angle': Float, 'signed_angle': Float}:
    """Build a GeometryNodeInputMeshEdgeAngle; omitted inputs keep Blender defaults."""
    ...

def edge_neighbors(
) -> {'face_count': Int}:
    """Build a GeometryNodeInputMeshEdgeNeighbors; omitted inputs keep Blender defaults."""
    ...

def edge_vertices(
) -> {'vertex_index_1': Int, 'vertex_index_2': Int, 'position_1': Vector, 'position_2': Vector}:
    """Build a GeometryNodeInputMeshEdgeVertices; omitted inputs keep Blender defaults."""
    ...

def face_area(
) -> {'area': Float}:
    """Build a GeometryNodeInputMeshFaceArea; omitted inputs keep Blender defaults."""
    ...

def is_face_planar(
    *,
    threshold: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'planar': Bool}:
    """Build a GeometryNodeInputMeshFaceIsPlanar; omitted inputs keep Blender defaults."""
    ...

def face_neighbors(
) -> {'vertex_count': Int, 'face_count': Int}:
    """Build a GeometryNodeInputMeshFaceNeighbors; omitted inputs keep Blender defaults."""
    ...

def mesh_island(
) -> {'island_index': Int, 'island_count': Int}:
    """Build a GeometryNodeInputMeshIsland; omitted inputs keep Blender defaults."""
    ...

def vertex_neighbors(
) -> {'vertex_count': Int, 'face_count': Int}:
    """Build a GeometryNodeInputMeshVertexNeighbors; omitted inputs keep Blender defaults."""
    ...

def named_attribute(
    *,
    name: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeInputNamedAttribute; omitted inputs keep Blender defaults."""
    ...

def named_layer_selection(
    *,
    name: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'selection': Bool}:
    """Build a GeometryNodeInputNamedLayerSelection; omitted inputs keep Blender defaults."""
    ...

def normal(
    *,
    legacy_corner_normals: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False,
) -> {'normal': Vector, 'true_normal': Vector}:
    """Build a GeometryNodeInputNormal; omitted inputs keep Blender defaults."""
    ...

def object(
) -> {'object': Object}:
    """Build a GeometryNodeInputObject; omitted inputs keep Blender defaults."""
    ...

def position(
) -> {'position': Vector}:
    """Build a GeometryNodeInputPosition; omitted inputs keep Blender defaults."""
    ...

def radius(
) -> {'radius': Float}:
    """Build a GeometryNodeInputRadius; omitted inputs keep Blender defaults."""
    ...

def scene_time(
) -> {'seconds': Float, 'frame': Float}:
    """Build a GeometryNodeInputSceneTime; omitted inputs keep Blender defaults."""
    ...

def is_face_smooth(
) -> {'smooth': Bool}:
    """Build a GeometryNodeInputShadeSmooth; omitted inputs keep Blender defaults."""
    ...

def shortest_edge_paths(
    *,
    end_vertex: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    edge_cost: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'next_vertex_index': Int, 'total_cost': Float}:
    """Build a GeometryNodeInputShortestEdgePaths; omitted inputs keep Blender defaults."""
    ...

def is_spline_cyclic(
) -> {'cyclic': Bool}:
    """Build a GeometryNodeInputSplineCyclic; omitted inputs keep Blender defaults."""
    ...

def spline_resolution(
) -> {'resolution': Int}:
    """Build a GeometryNodeInputSplineResolution; omitted inputs keep Blender defaults."""
    ...

def curve_tangent(
) -> {'tangent': Vector}:
    """Build a GeometryNodeInputTangent; omitted inputs keep Blender defaults."""
    ...

def voxel_index(
) -> {'x': Int, 'y': Int, 'z': Int, 'is_tile': Bool, 'extent_x': Int, 'extent_y': Int, 'extent_z': Int}:
    """Build a GeometryNodeInputVoxelIndex; omitted inputs keep Blender defaults."""
    ...

def instance_on_points(
    *,
    points: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    instance: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    pick_instance: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    instance_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    rotation: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'instances': Geometry}:
    """Build a GeometryNodeInstanceOnPoints; omitted inputs keep Blender defaults."""
    ...

def instances_to_points(
    *,
    instances: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'points': Geometry}:
    """Build a GeometryNodeInstancesToPoints; omitted inputs keep Blender defaults."""
    ...

def interpolate_curves(
    *,
    guide_curves: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    guide_up: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    guide_group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    points: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    point_up: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    point_group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    max_neighbors: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curves': Geometry, 'closest_index': Int, 'closest_weight': Float}:
    """Build a GeometryNodeInterpolateCurves; omitted inputs keep Blender defaults."""
    ...

def is_viewport(
) -> {'is_viewport': Bool}:
    """Build a GeometryNodeIsViewport; omitted inputs keep Blender defaults."""
    ...

def join_geometry(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'geometry': Geometry}:
    """Build a GeometryNodeJoinGeometry; omitted inputs keep Blender defaults."""
    ...

def get_list_item(
    *,
    list: Annotated[Bool | Color | Float | Geometry | Int | Material | Object | Rotation | String | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    socket_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    structure_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'AUTO',
) -> DynamicResults:
    """Build a GeometryNodeListGetItem; omitted inputs keep Blender defaults."""
    ...

def material_selection(
    *,
    material: Annotated[Material, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'selection': Bool}:
    """Build a GeometryNodeMaterialSelection; omitted inputs keep Blender defaults."""
    ...

def menu_switch(
    *items: Annotated[Bool | Color | Float | Geometry | Int | Material | Object | Rotation | String | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME],
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'GEOMETRY',
    menu: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'A',
    elements: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'A,B',
) -> DynamicResults:
    """Build a GeometryNodeMenuSwitch; omitted inputs keep Blender defaults."""
    ...

def merge_by_distance(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    distance: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'All',
) -> {'geometry': Geometry}:
    """Build a GeometryNodeMergeByDistance; omitted inputs keep Blender defaults."""
    ...

def merge_layers(
    *,
    grease_pencil: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'MERGE_BY_NAME',
) -> {'grease_pencil': Geometry}:
    """Build a GeometryNodeMergeLayers; omitted inputs keep Blender defaults."""
    ...

def merge_points(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    merge_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'geometry': Geometry}:
    """Build a GeometryNodeMergePoints; omitted inputs keep Blender defaults."""
    ...

def mesh_bevel(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    offset: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    miter: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    segments: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    shape: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    profile: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start_left_offset: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start_right_offset: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    end_left_offset: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    end_right_offset: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    affect_kind: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Edges',
) -> {'mesh': Geometry, 'vertex_face': Bool, 'edge_face': Bool, 'outer_edge': Bool, 'mid_edge': Bool}:
    """Build a GeometryNodeMeshBevel; omitted inputs keep Blender defaults."""
    ...

def mesh_boolean(
    *,
    mesh_2: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    self_intersection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    hole_tolerant: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mesh_1: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    operation: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'DIFFERENCE',
    solver: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeMeshBoolean; omitted inputs keep Blender defaults."""
    ...

def mesh_circle(
    *,
    vertices: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    fill_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'NONE',
) -> {'mesh': Geometry}:
    """Build a GeometryNodeMeshCircle; omitted inputs keep Blender defaults."""
    ...

def cone(
    *,
    vertices: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    side_segments: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius_top: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius_bottom: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    depth: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    fill_segments: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    fill_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'NGON',
) -> {'mesh': Geometry, 'top': Bool, 'bottom': Bool, 'side': Bool, 'uv_map': Vector}:
    """Build a GeometryNodeMeshCone; omitted inputs keep Blender defaults."""
    ...

def cube(
    *,
    size: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vertices_x: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vertices_y: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vertices_z: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry, 'uv_map': Vector}:
    """Build a GeometryNodeMeshCube; omitted inputs keep Blender defaults."""
    ...

def cylinder(
    *,
    vertices: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    side_segments: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    depth: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    fill_segments: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    fill_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'NGON',
) -> {'mesh': Geometry, 'top': Bool, 'side': Bool, 'bottom': Bool, 'uv_map': Vector}:
    """Build a GeometryNodeMeshCylinder; omitted inputs keep Blender defaults."""
    ...

def face_group_boundaries(
    *,
    face_set: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'boundary_edges': Bool}:
    """Build a GeometryNodeMeshFaceSetBoundaries; omitted inputs keep Blender defaults."""
    ...

def grid(
    *,
    size_x: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    size_y: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vertices_x: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vertices_y: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry, 'uv_map': Vector}:
    """Build a GeometryNodeMeshGrid; omitted inputs keep Blender defaults."""
    ...

def ico_sphere(
    *,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    subdivisions: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry, 'uv_map': Vector}:
    """Build a GeometryNodeMeshIcoSphere; omitted inputs keep Blender defaults."""
    ...

def mesh_line(
    *,
    count: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start_location: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    offset: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    resolution: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    count_mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'TOTAL',
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'OFFSET',
) -> {'mesh': Geometry}:
    """Build a GeometryNodeMeshLine; omitted inputs keep Blender defaults."""
    ...

def mesh_to_curve(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'EDGES',
) -> {'curve': Geometry}:
    """Build a GeometryNodeMeshToCurve; omitted inputs keep Blender defaults."""
    ...

def mesh_to_density_grid(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    density: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    voxel_size: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    gradient_width: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'density_grid': Float}:
    """Build a GeometryNodeMeshToDensityGrid; omitted inputs keep Blender defaults."""
    ...

def mesh_to_points(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'VERTICES',
) -> {'points': Geometry}:
    """Build a GeometryNodeMeshToPoints; omitted inputs keep Blender defaults."""
    ...

def mesh_to_sdf_grid(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    voxel_size: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    band_width: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'sdf_grid': Float}:
    """Build a GeometryNodeMeshToSDFGrid; omitted inputs keep Blender defaults."""
    ...

def mesh_to_volume(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    density: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    voxel_amount: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    interior_band_width: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    voxel_size: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    resolution_mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Amount',
) -> {'volume': Geometry}:
    """Build a GeometryNodeMeshToVolume; omitted inputs keep Blender defaults."""
    ...

def uv_sphere(
    *,
    segments: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    rings: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry, 'uv_map': Vector}:
    """Build a GeometryNodeMeshUVSphere; omitted inputs keep Blender defaults."""
    ...

def offset_corner_in_face(
    *,
    corner_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    offset: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'corner_index': Int}:
    """Build a GeometryNodeOffsetCornerInFace; omitted inputs keep Blender defaults."""
    ...

def offset_point_in_curve(
    *,
    point_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    offset: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'is_valid_offset': Bool, 'point_index': Int}:
    """Build a GeometryNodeOffsetPointInCurve; omitted inputs keep Blender defaults."""
    ...

def points(
    *,
    count: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'points': Geometry}:
    """Build a GeometryNodePoints; omitted inputs keep Blender defaults."""
    ...

def points_of_curve(
    *,
    curve_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    weights: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sort_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'point_index': Int, 'total': Int}:
    """Build a GeometryNodePointsOfCurve; omitted inputs keep Blender defaults."""
    ...

def points_to_curves(
    *,
    points: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    curve_group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    weight: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curves': Geometry}:
    """Build a GeometryNodePointsToCurves; omitted inputs keep Blender defaults."""
    ...

def points_to_sdf_grid(
    *,
    points: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    voxel_size: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'sdf_grid': Float}:
    """Build a GeometryNodePointsToSDFGrid; omitted inputs keep Blender defaults."""
    ...

def points_to_vertices(
    *,
    points: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry}:
    """Build a GeometryNodePointsToVertices; omitted inputs keep Blender defaults."""
    ...

def points_to_volume(
    *,
    points: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    density: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    voxel_amount: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    voxel_size: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    resolution_mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Amount',
) -> {'volume': Geometry}:
    """Build a GeometryNodePointsToVolume; omitted inputs keep Blender defaults."""
    ...

def geometry_proximity(
    *,
    target: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    source_position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sample_group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    target_element: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FACES',
) -> {'position': Vector, 'distance': Float, 'is_valid': Bool}:
    """Build a GeometryNodeProximity; omitted inputs keep Blender defaults."""
    ...

def raycast(
    *,
    target_geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    attribute: Annotated[Bool | Color | Float | Int | Rotation | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    source_position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    ray_direction: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    ray_length: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    interpolation: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Interpolated',
) -> DynamicResults:
    """Build a GeometryNodeRaycast; omitted inputs keep Blender defaults."""
    ...

def realize_instances(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    realize_all: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    depth: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    realize_to_point_domain: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False,
) -> {'geometry': Geometry}:
    """Build a GeometryNodeRealizeInstances; omitted inputs keep Blender defaults."""
    ...

def remove_named_attribute(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    name: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    pattern_mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Exact',
) -> {'geometry': Geometry}:
    """Build a GeometryNodeRemoveAttribute; omitted inputs keep Blender defaults."""
    ...

def rename_attribute(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    old: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    new: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    overwrite: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Single',
) -> {'geometry': Geometry}:
    """Build a GeometryNodeRenameAttribute; omitted inputs keep Blender defaults."""
    ...

def replace_material(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    old: Annotated[Material, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    new: Annotated[Material, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'geometry': Geometry}:
    """Build a GeometryNodeReplaceMaterial; omitted inputs keep Blender defaults."""
    ...

def resample_curve(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    count: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    length: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    keep_last_segment: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = True,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Count',
) -> {'curve': Geometry}:
    """Build a GeometryNodeResampleCurve; omitted inputs keep Blender defaults."""
    ...

def reverse_curve(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curve': Geometry}:
    """Build a GeometryNodeReverseCurve; omitted inputs keep Blender defaults."""
    ...

def rotate_instances(
    *,
    instances: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    rotation: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    pivot_point: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    local_space: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'instances': Geometry}:
    """Build a GeometryNodeRotateInstances; omitted inputs keep Blender defaults."""
    ...

def sdf_grid_boolean(
    *,
    grid_2: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    grid_1: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    operation: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'DIFFERENCE',
) -> {'grid': Float}:
    """Build a GeometryNodeSDFGridBoolean; omitted inputs keep Blender defaults."""
    ...

def sdf_grid_fillet(
    *,
    grid: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    iterations: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'grid': Float}:
    """Build a GeometryNodeSDFGridFillet; omitted inputs keep Blender defaults."""
    ...

def sdf_grid_laplacian(
    *,
    grid: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    iterations: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'grid': Float}:
    """Build a GeometryNodeSDFGridLaplacian; omitted inputs keep Blender defaults."""
    ...

def sdf_grid_mean(
    *,
    grid: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    width: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    iterations: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'grid': Float}:
    """Build a GeometryNodeSDFGridMean; omitted inputs keep Blender defaults."""
    ...

def sdf_grid_mean_curvature(
    *,
    grid: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    iterations: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'grid': Float}:
    """Build a GeometryNodeSDFGridMeanCurvature; omitted inputs keep Blender defaults."""
    ...

def sdf_grid_median(
    *,
    grid: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    width: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    iterations: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'grid': Float}:
    """Build a GeometryNodeSDFGridMedian; omitted inputs keep Blender defaults."""
    ...

def sdf_grid_offset(
    *,
    grid: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    distance: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'grid': Float}:
    """Build a GeometryNodeSDFGridOffset; omitted inputs keep Blender defaults."""
    ...

def sample_curve(
    *,
    curves: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    value: Annotated[Bool | Color | Float | Int | Rotation | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    factor: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    curve_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    length: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FACTOR',
    use_all_curves: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False,
) -> DynamicResults:
    """Build a GeometryNodeSampleCurve; omitted inputs keep Blender defaults."""
    ...

def sample_grid(
    *,
    grid: Annotated[Bool | Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    interpolation: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Trilinear',
) -> DynamicResults:
    """Build a GeometryNodeSampleGrid; omitted inputs keep Blender defaults."""
    ...

def sample_grid_index(
    *,
    grid: Annotated[Bool | Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    x: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    y: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    z: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeSampleGridIndex; omitted inputs keep Blender defaults."""
    ...

def sample_index(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    value: Annotated[Bool | Color | Float | Int | Rotation | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    clamp: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> DynamicResults:
    """Build a GeometryNodeSampleIndex; omitted inputs keep Blender defaults."""
    ...

def sample_nearest(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sample_position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> {'index': Int}:
    """Build a GeometryNodeSampleNearest; omitted inputs keep Blender defaults."""
    ...

def sample_nearest_surface(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    value: Annotated[Bool | Color | Float | Int | Rotation | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sample_position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sample_group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeSampleNearestSurface; omitted inputs keep Blender defaults."""
    ...

def sample_uv_surface(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    value: Annotated[Bool | Color | Float | Int | Rotation | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    source_uv_map: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sample_uv: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeSampleUVSurface; omitted inputs keep Blender defaults."""
    ...

def scale_elements(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    center: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    axis: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FACE',
    scale_mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Uniform',
) -> {'geometry': Geometry}:
    """Build a GeometryNodeScaleElements; omitted inputs keep Blender defaults."""
    ...

def scale_instances(
    *,
    instances: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    center: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    local_space: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'instances': Geometry}:
    """Build a GeometryNodeScaleInstances; omitted inputs keep Blender defaults."""
    ...

def self_object(
) -> {'self_object': Object}:
    """Build a GeometryNodeSelfObject; omitted inputs keep Blender defaults."""
    ...

def separate_components(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry, 'curve': Geometry, 'grease_pencil': Geometry, 'point_cloud': Geometry, 'volume': Geometry, 'instances': Geometry}:
    """Build a GeometryNodeSeparateComponents; omitted inputs keep Blender defaults."""
    ...

def separate_geometry(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> {'selection': Geometry, 'inverted': Geometry}:
    """Build a GeometryNodeSeparateGeometry; omitted inputs keep Blender defaults."""
    ...

def set_handle_positions(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    offset: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'LEFT',
) -> {'curve': Geometry}:
    """Build a GeometryNodeSetCurveHandlePositions; omitted inputs keep Blender defaults."""
    ...

def set_curve_normal(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    normal: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Minimum Twist',
) -> {'curve': Geometry}:
    """Build a GeometryNodeSetCurveNormal; omitted inputs keep Blender defaults."""
    ...

def set_curve_radius(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curve': Geometry}:
    """Build a GeometryNodeSetCurveRadius; omitted inputs keep Blender defaults."""
    ...

def set_curve_tilt(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    tilt: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curve': Geometry}:
    """Build a GeometryNodeSetCurveTilt; omitted inputs keep Blender defaults."""
    ...

def set_geometry_name(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    name: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'geometry': Geometry}:
    """Build a GeometryNodeSetGeometryName; omitted inputs keep Blender defaults."""
    ...

def set_grease_pencil_color(
    *,
    grease_pencil: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    color: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    opacity: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'STROKE',
) -> {'grease_pencil': Geometry}:
    """Build a GeometryNodeSetGreasePencilColor; omitted inputs keep Blender defaults."""
    ...

def set_grease_pencil_depth(
    *,
    grease_pencil: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    depth_order: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = '2D',
) -> {'grease_pencil': Geometry}:
    """Build a GeometryNodeSetGreasePencilDepth; omitted inputs keep Blender defaults."""
    ...

def set_grease_pencil_softness(
    *,
    grease_pencil: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    softness: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'grease_pencil': Geometry}:
    """Build a GeometryNodeSetGreasePencilSoftness; omitted inputs keep Blender defaults."""
    ...

def set_grid_background(
    *,
    grid: Annotated[Bool | Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    background: Annotated[Bool | Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    update_inactive: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeSetGridBackground; omitted inputs keep Blender defaults."""
    ...

def set_id(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'geometry': Geometry}:
    """Build a GeometryNodeSetID; omitted inputs keep Blender defaults."""
    ...

def set_material(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    material: Annotated[Material, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'geometry': Geometry}:
    """Build a GeometryNodeSetMaterial; omitted inputs keep Blender defaults."""
    ...

def set_material_index(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    material_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'geometry': Geometry}:
    """Build a GeometryNodeSetMaterialIndex; omitted inputs keep Blender defaults."""
    ...

def set_mesh_normal(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    remove_custom: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    edge_sharpness: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    face_sharpness: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    custom_normal: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'SHARPNESS',
) -> {'mesh': Geometry}:
    """Build a GeometryNodeSetMeshNormal; omitted inputs keep Blender defaults."""
    ...

def set_nurbs_order(
    *,
    curves: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    order: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curves': Geometry}:
    """Build a GeometryNodeSetNURBSOrder; omitted inputs keep Blender defaults."""
    ...

def set_nurbs_weight(
    *,
    curves: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    weight: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curves': Geometry}:
    """Build a GeometryNodeSetNURBSWeight; omitted inputs keep Blender defaults."""
    ...

def set_point_radius(
    *,
    points: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    radius: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'points': Geometry}:
    """Build a GeometryNodeSetPointRadius; omitted inputs keep Blender defaults."""
    ...

def set_position(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    position: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    offset: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'geometry': Geometry}:
    """Build a GeometryNodeSetPosition; omitted inputs keep Blender defaults."""
    ...

def set_shade_smooth(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    shade_smooth: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FACE',
) -> {'mesh': Geometry}:
    """Build a GeometryNodeSetShadeSmooth; omitted inputs keep Blender defaults."""
    ...

def set_spline_cyclic(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    cyclic: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curve': Geometry}:
    """Build a GeometryNodeSetSplineCyclic; omitted inputs keep Blender defaults."""
    ...

def set_spline_resolution(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    resolution: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curve': Geometry}:
    """Build a GeometryNodeSetSplineResolution; omitted inputs keep Blender defaults."""
    ...

def sort_elements(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sort_weight: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> {'geometry': Geometry}:
    """Build a GeometryNodeSortElements; omitted inputs keep Blender defaults."""
    ...

def sort_list(
    *,
    list: Annotated[Bool | Color | Float | Geometry | Int | Material | Object | Rotation | String | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    sort_weight: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    socket_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeSortList; omitted inputs keep Blender defaults."""
    ...

def spline_length(
) -> {'length': Float, 'point_count': Int}:
    """Build a GeometryNodeSplineLength; omitted inputs keep Blender defaults."""
    ...

def spline_parameter(
) -> {'factor': Float, 'length': Float, 'index': Int}:
    """Build a GeometryNodeSplineParameter; omitted inputs keep Blender defaults."""
    ...

def split_edges(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry}:
    """Build a GeometryNodeSplitEdges; omitted inputs keep Blender defaults."""
    ...

def split_to_instances(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    group_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> {'instances': Geometry, 'group_id': Int}:
    """Build a GeometryNodeSplitToInstances; omitted inputs keep Blender defaults."""
    ...

def store_named_attribute(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    name: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    value: Annotated[Bool | Color | Float | Int | Rotation | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> {'geometry': Geometry}:
    """Build a GeometryNodeStoreNamedAttribute; omitted inputs keep Blender defaults."""
    ...

def store_named_grid(
    *,
    volume: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    name: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    grid: Annotated[Bool | Float | Int | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> {'volume': Geometry}:
    """Build a GeometryNodeStoreNamedGrid; omitted inputs keep Blender defaults."""
    ...

def join_strings(
    *,
    delimiter: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    strings: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'string': String}:
    """Build a GeometryNodeStringJoin; omitted inputs keep Blender defaults."""
    ...

def subdivide_curve(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    cuts: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'curve': Geometry}:
    """Build a GeometryNodeSubdivideCurve; omitted inputs keep Blender defaults."""
    ...

def subdivide_mesh(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    level: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry}:
    """Build a GeometryNodeSubdivideMesh; omitted inputs keep Blender defaults."""
    ...

def subdivision_surface(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    level: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    edge_crease: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vertex_crease: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    limit_surface: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    quality: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    boundary_smooth: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'All',
    uv_smooth: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Keep Boundaries',
) -> {'mesh': Geometry}:
    """Build a GeometryNodeSubdivisionSurface; omitted inputs keep Blender defaults."""
    ...

def switch(
    *,
    switch: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    false: Annotated[Bool | Color | Float | Geometry | Int | Material | Object | Rotation | String | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    input_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a GeometryNodeSwitch; omitted inputs keep Blender defaults."""
    ...

def tag_filter(
    *,
    tag_filter: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    tags: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'match': Bool}:
    """Build a GeometryNodeTagFilter; omitted inputs keep Blender defaults."""
    ...

def option_3d_cursor(
) -> {'location': Vector, 'rotation': Rotation}:
    """Build a GeometryNodeTool3DCursor; omitted inputs keep Blender defaults."""
    ...

def active_element(
    *,
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
) -> {'index': Int, 'exists': Bool}:
    """Build a GeometryNodeToolActiveElement; omitted inputs keep Blender defaults."""
    ...

def face_set(
) -> {'face_set': Int, 'exists': Bool}:
    """Build a GeometryNodeToolFaceSet; omitted inputs keep Blender defaults."""
    ...

def mouse_position(
) -> {'mouse_x': Int, 'mouse_y': Int, 'region_width': Int, 'region_height': Int}:
    """Build a GeometryNodeToolMousePosition; omitted inputs keep Blender defaults."""
    ...

def selection(
) -> {'boolean': Bool, 'float': Float}:
    """Build a GeometryNodeToolSelection; omitted inputs keep Blender defaults."""
    ...

def set_face_set(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    face_set: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'mesh': Geometry}:
    """Build a GeometryNodeToolSetFaceSet; omitted inputs keep Blender defaults."""
    ...

def set_selection(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool | Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    domain: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'POINT',
    selection_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'BOOLEAN',
) -> {'geometry': Geometry}:
    """Build a GeometryNodeToolSetSelection; omitted inputs keep Blender defaults."""
    ...

def transfer_attributes(
    *,
    target: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    target_point_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    target_edge_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    target_face_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    target_corner_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    target_curve_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    target_instance_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    source: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    source_point_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    source_edge_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    source_face_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    source_corner_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    source_curve_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    source_instance_id: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    attribute_names: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    exclude_names: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    pattern_mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Wildcard',
) -> {'target': Geometry, 'transferred_names': String}:
    """Build a GeometryNodeTransferAttributes; omitted inputs keep Blender defaults."""
    ...

def transform_geometry(
    *,
    geometry: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    translation: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    rotation: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Components',
) -> {'geometry': Geometry}:
    """Build a GeometryNodeTransform; omitted inputs keep Blender defaults."""
    ...

def translate_instances(
    *,
    instances: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    translation: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    local_space: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'instances': Geometry}:
    """Build a GeometryNodeTranslateInstances; omitted inputs keep Blender defaults."""
    ...

def triangulate(
    *,
    mesh: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    n_gon_method: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Beauty',
    quad_method: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Shortest Diagonal',
) -> {'mesh': Geometry}:
    """Build a GeometryNodeTriangulate; omitted inputs keep Blender defaults."""
    ...

def trim_curve(
    *,
    curve: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    end: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    start_001: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    end_001: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FACTOR',
) -> {'curve': Geometry}:
    """Build a GeometryNodeTrimCurve; omitted inputs keep Blender defaults."""
    ...

def uv_unwrap(
    *,
    selection: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    seam: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    margin: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    fill_holes: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    iterations: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    no_flip: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    method: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Angle Based',
) -> {'uv': Vector}:
    """Build a GeometryNodeUVUnwrap; omitted inputs keep Blender defaults."""
    ...

def vertex_of_corner(
    *,
    corner_index: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'vertex_index': Int}:
    """Build a GeometryNodeVertexOfCorner; omitted inputs keep Blender defaults."""
    ...

def volume_cube(
    *,
    density: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    background: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    min: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    max: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    resolution_x: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    resolution_y: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    resolution_z: Annotated[Int, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'volume': Geometry}:
    """Build a GeometryNodeVolumeCube; omitted inputs keep Blender defaults."""
    ...

def volume_to_mesh(
    *,
    volume: Annotated[Geometry, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    threshold: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    adaptivity: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    voxel_amount: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    voxel_size: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    resolution_mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'Grid',
) -> {'mesh': Geometry}:
    """Build a GeometryNodeVolumeToMesh; omitted inputs keep Blender defaults."""
    ...

def warning(
    *,
    show: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    message: Annotated[String, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    warning_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'ERROR',
) -> {'show': Bool}:
    """Build a GeometryNodeWarning; omitted inputs keep Blender defaults."""
    ...

def enable_output(
    *,
    enable: Annotated[Bool, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
) -> DynamicResults:
    """Build a NodeEnableOutput; omitted inputs keep Blender defaults."""
    ...

def implicit_conversion(
    *,
    value: Annotated[Bool | Color | Float | Geometry | Int | Material | Object | Rotation | String | Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'RGBA',
) -> DynamicResults:
    """Build a NodeImplicitConversion; omitted inputs keep Blender defaults."""
    ...

def reroute(
    *,
    input: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'output': Color}:
    """Build a NodeReroute; omitted inputs keep Blender defaults."""
    ...

def blackbody(
    *,
    temperature: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'color': Color}:
    """Build a ShaderNodeBlackbody; omitted inputs keep Blender defaults."""
    ...

def clamp(
    *,
    value: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    min: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    max: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    clamp_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'MINMAX',
) -> {'result': Float}:
    """Build a ShaderNodeClamp; omitted inputs keep Blender defaults."""
    ...

def combine_xyz(
    *,
    x: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    y: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    z: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'vector': Vector}:
    """Build a ShaderNodeCombineXYZ; omitted inputs keep Blender defaults."""
    ...

def float_curve(
    *,
    factor: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    value: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'value': Float}:
    """Build a ShaderNodeFloatCurve; omitted inputs keep Blender defaults."""
    ...

def gamma(
    *,
    color: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    gamma: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'color': Color}:
    """Build a ShaderNodeGamma; omitted inputs keep Blender defaults."""
    ...

def map_range(
    *,
    value: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    from_min: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    from_max: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    to_min: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    to_max: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    steps: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    from_min_float3: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    from_max_float3: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    to_min_float3: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    to_max_float3: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    steps_float3: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    clamp: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = True,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    interpolation_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'LINEAR',
) -> DynamicResults:
    """Build a ShaderNodeMapRange; omitted inputs keep Blender defaults."""
    ...

def math(
    *,
    value: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    value_001: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    value_002: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    operation: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'ADD',
    use_clamp: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False,
) -> {'value': Float}:
    """Build a ShaderNodeMath; omitted inputs keep Blender defaults."""
    ...

def mix(
    *,
    factor_float: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    b_float: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    b_vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    factor_vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    a_vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    b_color: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    b_rotation: Annotated[Rotation, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    a_color: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    blend_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'MIX',
    clamp_factor: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = True,
    clamp_result: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False,
    data_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FLOAT',
    factor_mode: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'UNIFORM',
) -> DynamicResults:
    """Build a ShaderNodeMix; omitted inputs keep Blender defaults."""
    ...

def mix_legacy(
    *,
    fac: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    color1: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    color2: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    blend_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'MIX',
    use_alpha: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False,
    use_clamp: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False,
) -> {'color': Color}:
    """Build a ShaderNodeMixRGB; omitted inputs keep Blender defaults."""
    ...

def rgb_curves(
    *,
    fac: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    color: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'color': Color}:
    """Build a ShaderNodeRGBCurve; omitted inputs keep Blender defaults."""
    ...

def separate_xyz(
    *,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'x': Float, 'y': Float, 'z': Float}:
    """Build a ShaderNodeSeparateXYZ; omitted inputs keep Blender defaults."""
    ...

def brick_texture(
    *,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    color1: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    color2: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mortar: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mortar_size: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    mortar_smooth: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    bias: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    brick_width: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    row_height: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'color': Color, 'factor': Float}:
    """Build a ShaderNodeTexBrick; omitted inputs keep Blender defaults."""
    ...

def checker_texture(
    *,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    color1: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    color2: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'color': Color, 'factor': Float}:
    """Build a ShaderNodeTexChecker; omitted inputs keep Blender defaults."""
    ...

def gabor_texture(
    *,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    frequency: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    anisotropy: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    orientation_2d: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    orientation_3d: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    gabor_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = '2D',
) -> {'value': Float, 'phase': Float, 'intensity': Float}:
    """Build a ShaderNodeTexGabor; omitted inputs keep Blender defaults."""
    ...

def gradient_texture(
    *,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    gradient_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'LINEAR',
) -> {'color': Color, 'factor': Float}:
    """Build a ShaderNodeTexGradient; omitted inputs keep Blender defaults."""
    ...

def magic_texture(
    *,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    distortion: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'color': Color, 'factor': Float}:
    """Build a ShaderNodeTexMagic; omitted inputs keep Blender defaults."""
    ...

def noise_texture(
    *,
    w: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    detail: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    roughness: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    lacunarity: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    distortion: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    offset: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    gain: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    noise_dimensions: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = '3D',
    noise_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'FBM',
    normalize: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = True,
) -> {'factor': Float, 'color': Color}:
    """Build a ShaderNodeTexNoise; omitted inputs keep Blender defaults."""
    ...

def voronoi_texture(
    *,
    w: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    detail: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    roughness: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    lacunarity: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    randomness: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    smoothness: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    exponent: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    distance: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'EUCLIDEAN',
    feature: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'F1',
    normalize: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False,
    voronoi_dimensions: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = '3D',
) -> DynamicResults:
    """Build a ShaderNodeTexVoronoi; omitted inputs keep Blender defaults."""
    ...

def wave_texture(
    *,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    distortion: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    detail: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    detail_scale: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    detail_roughness: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    phase_offset: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    bands_direction: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'X',
    rings_direction: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'X',
    wave_profile: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'SIN',
    wave_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'BANDS',
) -> {'color': Color, 'factor': Float}:
    """Build a ShaderNodeTexWave; omitted inputs keep Blender defaults."""
    ...

def white_noise_texture(
    *,
    w: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    noise_dimensions: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = '3D',
) -> {'value': Float, 'color': Color}:
    """Build a ShaderNodeTexWhiteNoise; omitted inputs keep Blender defaults."""
    ...

def color_ramp(
    *,
    fac: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'color': Color, 'alpha': Float}:
    """Build a ShaderNodeValToRGB; omitted inputs keep Blender defaults."""
    ...

def value(
    *,
    value: Annotated[Float, EvaluationMode.COMPILE_TIME_ONLY] = None,
) -> {'value': Float}:
    """Build a ShaderNodeValue; omitted inputs keep Blender defaults."""
    ...

def vector_curves(
    *,
    fac: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
) -> {'vector': Vector}:
    """Build a ShaderNodeVectorCurve; omitted inputs keep Blender defaults."""
    ...

def vector_math(
    *,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vector_001: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    vector_002: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    scale: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    operation: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'ADD',
) -> DynamicResults:
    """Build a ShaderNodeVectorMath; omitted inputs keep Blender defaults."""
    ...

def vector_rotate(
    *,
    vector: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    center: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    axis: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    angle: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    rotation: Annotated[Vector, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None,
    invert: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False,
    rotation_type: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'AXIS_ANGLE',
) -> {'vector': Vector}:
    """Build a ShaderNodeVectorRotate; omitted inputs keep Blender defaults."""
    ...


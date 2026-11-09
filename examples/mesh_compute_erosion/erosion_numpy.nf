from examples.mesh_compute_erosion import erosion
from packages import math

def modifier(
    source_geo: Geometry['Geometry'],
    requested_n: Int['Resolution'] = 96,
    requested_steps: Int['Steps'] = 500,
    size: Float['Size'] = 10.0,
    base_level: Float['Base Level'] = 1.0,
    hill_height: Float['Hill Height'] = 4.0,
    stretch_x_requested: Float['Hill Stretch X'] = 1.0,
    stretch_y_requested: Float['Hill Stretch Y'] = 1.0,
    center_x: Float['Hill Center X'] = 0.0,
    center_y: Float['Hill Center Y'] = 0.0,
    rotation_degrees: Float['Terrain Rotation'] = 0.0,
    second_height: Float['Second Hill Height'] = 0.0,
    second_x: Float['Second Hill X'] = 2.5,
    second_y: Float['Second Hill Y'] = 1.5,
    slope_x: Float['Slope X'] = 0.0,
    slope_y: Float['Slope Y'] = 0.12,
    fine_height: Float['Fine Noise Height'] = 0.7,
    fine_scale: Float['Fine Noise Scale'] = 0.65,
    broad_height: Float['Broad Noise Height'] = 1.5,
    broad_scale: Float['Broad Noise Scale'] = 0.22,
    seed: Int['Terrain Seed'] = 0,
    use_source: Bool['Use Source Surface'] = False,
    source_offset: Float['Source Height Offset'] = 0.0,
    parameter_0: Float['Time Step'] = 0.02,
    parameter_1: Float['Rain'] = 0.03,
    parameter_2: Float['Evaporation'] = 0.03,
    parameter_3: Float['Capacity'] = 2.0,
    parameter_4: Float['Erosion Rate'] = 0.35,
    parameter_5: Float['Deposition Rate'] = 1.0,
    open_edges: Bool['Open Edges'] = True,
    parameter_6: Float['Talus Slope'] = 1.2,
    parameter_7: Float['Thermal Rate'] = 1.0,
    outlet_level: Bool['Legacy Outlet'] = False,
) -> Geometry:
    # Adjustable base shape; erosion equations from v2.
    # This bundled Python callback runs with Blender Python privileges.
    n = requested_n if requested_n >= 3 else 3
    steps = requested_steps if requested_steps >= 0 else 0
    dx = math.max(size, 0.01) / (n - 1)
    area = dx * dx
    geo = node("GeometryNodeMeshGrid", inputs={"Size X": size, "Size Y": size, "Vertices X": n, "Vertices Y": n}, output="Mesh", typ=Geometry)
    stretch_x = math.max(stretch_x_requested, 0.05)
    stretch_y = math.max(stretch_y_requested, 0.05)
    angle = math.radians(rotation_degrees)
    grid_position = position()
    p0 = vector(grid_position.x * math.cos(angle) - grid_position.y * math.sin(angle), grid_position.x * math.sin(angle) + grid_position.y * math.cos(angle), 0.0)
    px = (p0.x - center_x) / stretch_x
    py = (p0.y - center_y) / stretch_y
    px2 = (p0.x - second_x) / stretch_x
    py2 = (p0.y - second_y) / stretch_y
    seed_offset = vector(seed * 17.37, seed * 31.19, seed * 7.13)
    noise0 = math.noise(p0 + vector(37.13, -19.71, 13.7) + seed_offset, scale=fine_scale, detail=2.5, roughness=0.5, normalize=True)
    noise1 = math.noise(p0 + vector(-11.7, 83.2, 5.4) + seed_offset, scale=broad_scale, detail=2.0, roughness=0.5, normalize=True)
    generated_height = base_level + hill_height * math.exp(-(px * px + py * py) * 0.075) + slope_y * (size * 0.5 - p0.y) + slope_x * p0.x + (noise0 - 0.5) * fine_height + (noise1 - 0.5) * broad_height + second_height * math.exp(-(px2 * px2 + py2 * py2) * 0.075)
    source_mesh = node("GeometryNodeRealizeInstances", inputs={"Geometry": source_geo}, output="Geometry", typ=Geometry)
    bounds = node("GeometryNodeBoundBox", inputs={"Geometry": source_mesh}, outputs={"low": ("Min", Vector), "high": ("Max", Vector)})
    # Sample slightly inside grid edges: rays exactly on a polygon boundary can miss.
    ray = node("GeometryNodeRaycast", inputs={"Target Geometry": source_mesh, "Source Position": vector(grid_position.x * 0.999999, grid_position.y * 0.999999, bounds.high.z + 1.0), "Ray Direction": vector(0.0, 0.0, -1.0), "Ray Length": math.max(bounds.high.z - bounds.low.z + 2.0, 2.0)}, outputs={"hit": ("Is Hit", Bool), "location": ("Hit Position", Vector)})
    source_height = ray.location.z + source_offset if ray.hit else base_level
    h0 = source_height if use_source else generated_height
    geo = store_named_attribute(geo, "source_hit", (1.0 if ray.hit else 0.0) if use_source else 1.0, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "height", h0, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "initial_height", h0, domain="POINT", type="FLOAT")
    min_height = node("GeometryNodeAttributeStatistic", inputs={"Geometry": geo, "Attribute": node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float)}, props={"data_type": "FLOAT", "domain": "POINT"}, output="Min", typ=Float)
    height_datum = math.max(1.0 - min_height, 0.0) if min_height < 0.0 else 0.0
    geo = store_named_attribute(geo, "height", h0 + height_datum, domain="POINT", type="FLOAT")
    dt = math.clamp(parameter_0, 0.0001, 0.1)
    rain = math.max(parameter_1, 0.0)
    evap = math.max(parameter_2, 0.0)
    capacity_k = math.max(parameter_3, 0.0)
    erosion_k = math.max(parameter_4, 0.0)
    deposition_k = math.max(parameter_5, 0.0)
    talus = math.max(parameter_6, 0.0)
    thermal_rate = math.clamp(parameter_7, 0.0, 10.0)

    geo = erosion(geo, steps=steps, n=n, size=size, dt=dt, rain=rain, evap=evap, capacity_k=capacity_k, erosion_k=erosion_k, deposition_k=deposition_k, open_edges=open_edges, talus=talus, thermal_rate=thermal_rate, outlet_level=outlet_level)

    hfinal = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index())
    initial = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "initial_height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index())
    geo = store_named_attribute(geo, "height_change", hfinal - initial, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "height", hfinal, domain="POINT", type="FLOAT")
    pfinal = position()
    geo = set_position(geo, vector(pfinal.x, pfinal.y, hfinal))
    geo = node("GeometryNodeSetShadeSmooth", inputs={"Mesh": geo, "Shade Smooth": True}, props={"domain": "FACE"}, output="Mesh", typ=Geometry)
    geo = set_material(geo, "Erosion Terrain")
    return geo

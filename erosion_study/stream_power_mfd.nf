# Common initial terrain, identical between both approaches.
from packages import math
requested_n = input_int("Resolution", default=96)
requested_steps = input_int("Steps", default=24)
size = input_float("Size", default=10.0)
n = requested_n if requested_n >= 3 else 3
steps = requested_steps if requested_steps >= 0 else 0
dx = math.max(size, 0.01) / (n - 1)
area = dx * dx
geo = node("GeometryNodeMeshGrid", inputs={"Size X": size, "Size Y": size, "Vertices X": n, "Vertices Y": n}, output="Mesh", typ=Geometry)
p0 = position()
noise0 = math.noise(p0 + vector(37.13, -19.71, 13.7), scale=0.65, detail=2.5, roughness=0.5, normalize=True)
noise1 = math.noise(p0 + vector(-11.7, 83.2, 5.4), scale=0.22, detail=2.0, roughness=0.5, normalize=True)
h0 = 1.0 + 4.0 * math.exp(-(p0.x * p0.x + p0.y * p0.y) * 0.075) + 0.12 * (5.0 - p0.y) + (noise0 - 0.5) * 0.7 + (noise1 - 0.5) * 1.5
geo = store_named_attribute(geo, "height", h0, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "initial_height", h0, domain="POINT", type="FLOAT")
a0 = sample_index(geo, position().x, 0)
a1 = sample_index(geo, position().x, 1)
xs = 1 if a1 != a0 else n
ys = n if a1 != a0 else 1

routing_passes = input_int("Routing Passes", default=512)
parameter_0 = input_float("Time Step", default=0.2)
dt = math.max(parameter_0, 0.0)
parameter_1 = input_float("Erodibility", default=0.12)
k = math.max(parameter_1, 0.0)

for tick in repeat_range(steps):
    i = index()
    x = (i // xs) % n
    y = (i // ys) % n
    h = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    boundary = (x == 0) or (y == 0) or (x == n - 1) or (y == n - 1)
    hn_l = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (-1) * xs + (0) * ys, clamp=True)
    slope_l = math.max((h - hn_l) / (dx), 0.0) if ((x > 0)) and (not boundary) else 0.0
    hn_r = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (1) * xs + (0) * ys, clamp=True)
    slope_r = math.max((h - hn_r) / (dx), 0.0) if ((x < n - 1)) and (not boundary) else 0.0
    hn_d = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (0) * xs + (-1) * ys, clamp=True)
    slope_d = math.max((h - hn_d) / (dx), 0.0) if ((y > 0)) and (not boundary) else 0.0
    hn_u = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (0) * xs + (1) * ys, clamp=True)
    slope_u = math.max((h - hn_u) / (dx), 0.0) if ((y < n - 1)) and (not boundary) else 0.0
    hn_sw = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (-1) * xs + (-1) * ys, clamp=True)
    slope_sw = math.max((h - hn_sw) / (dx * 1.4142135623730951), 0.0) if ((x > 0) and (y > 0)) and (not boundary) else 0.0
    hn_se = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (1) * xs + (-1) * ys, clamp=True)
    slope_se = math.max((h - hn_se) / (dx * 1.4142135623730951), 0.0) if ((x < n - 1) and (y > 0)) and (not boundary) else 0.0
    hn_nw = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (-1) * xs + (1) * ys, clamp=True)
    slope_nw = math.max((h - hn_nw) / (dx * 1.4142135623730951), 0.0) if ((x > 0) and (y < n - 1)) and (not boundary) else 0.0
    hn_ne = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (1) * xs + (1) * ys, clamp=True)
    slope_ne = math.max((h - hn_ne) / (dx * 1.4142135623730951), 0.0) if ((x < n - 1) and (y < n - 1)) and (not boundary) else 0.0
    slope_sum = slope_l + slope_r + slope_d + slope_u + slope_sw + slope_se + slope_nw + slope_ne
    geo = store_named_attribute(geo, "p_l", slope_l / math.max(slope_sum, 0.000001), domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "p_r", slope_r / math.max(slope_sum, 0.000001), domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "p_d", slope_d / math.max(slope_sum, 0.000001), domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "p_u", slope_u / math.max(slope_sum, 0.000001), domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "p_sw", slope_sw / math.max(slope_sum, 0.000001), domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "p_se", slope_se / math.max(slope_sum, 0.000001), domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "p_nw", slope_nw / math.max(slope_sum, 0.000001), domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "p_ne", slope_ne / math.max(slope_sum, 0.000001), domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "slope", slope_l * slope_l / math.max(slope_sum, 0.000001) + slope_r * slope_r / math.max(slope_sum, 0.000001) + slope_d * slope_d / math.max(slope_sum, 0.000001) + slope_u * slope_u / math.max(slope_sum, 0.000001) + slope_sw * slope_sw / math.max(slope_sum, 0.000001) + slope_se * slope_se / math.max(slope_sum, 0.000001) + slope_nw * slope_nw / math.max(slope_sum, 0.000001) + slope_ne * slope_ne / math.max(slope_sum, 0.000001), domain="POINT", type="FLOAT")
    lowest = h
    lowest = math.min(lowest, hn_l) if (x > 0) else lowest
    lowest = math.min(lowest, hn_r) if (x < n - 1) else lowest
    lowest = math.min(lowest, hn_d) if (y > 0) else lowest
    lowest = math.min(lowest, hn_u) if (y < n - 1) else lowest
    lowest = math.min(lowest, hn_sw) if (x > 0) and (y > 0) else lowest
    lowest = math.min(lowest, hn_se) if (x < n - 1) and (y > 0) else lowest
    lowest = math.min(lowest, hn_nw) if (x > 0) and (y < n - 1) else lowest
    lowest = math.min(lowest, hn_ne) if (x < n - 1) and (y < n - 1) else lowest
    geo = store_named_attribute(geo, "drop_limit", math.max(h - lowest, 0.0) * 0.2, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "drainage_area", area, domain="POINT", type="FLOAT")
    for route_step in repeat_range(routing_passes):
        j = index()
        xx = (j // xs) % n
        yy = (j // ys) % n
        weight_l = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "p_r"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (-1) * xs + (0) * ys, clamp=True)
        area_l = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "drainage_area"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (-1) * xs + (0) * ys, clamp=True)
        contribution_l = area_l * weight_l if (xx > 0) else 0.0
        weight_r = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "p_l"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (1) * xs + (0) * ys, clamp=True)
        area_r = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "drainage_area"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (1) * xs + (0) * ys, clamp=True)
        contribution_r = area_r * weight_r if (xx < n - 1) else 0.0
        weight_d = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "p_u"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (0) * xs + (-1) * ys, clamp=True)
        area_d = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "drainage_area"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (0) * xs + (-1) * ys, clamp=True)
        contribution_d = area_d * weight_d if (yy > 0) else 0.0
        weight_u = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "p_d"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (0) * xs + (1) * ys, clamp=True)
        area_u = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "drainage_area"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (0) * xs + (1) * ys, clamp=True)
        contribution_u = area_u * weight_u if (yy < n - 1) else 0.0
        weight_sw = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "p_ne"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (-1) * xs + (-1) * ys, clamp=True)
        area_sw = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "drainage_area"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (-1) * xs + (-1) * ys, clamp=True)
        contribution_sw = area_sw * weight_sw if (xx > 0) and (yy > 0) else 0.0
        weight_se = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "p_nw"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (1) * xs + (-1) * ys, clamp=True)
        area_se = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "drainage_area"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (1) * xs + (-1) * ys, clamp=True)
        contribution_se = area_se * weight_se if (xx < n - 1) and (yy > 0) else 0.0
        weight_nw = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "p_se"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (-1) * xs + (1) * ys, clamp=True)
        area_nw = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "drainage_area"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (-1) * xs + (1) * ys, clamp=True)
        contribution_nw = area_nw * weight_nw if (xx > 0) and (yy < n - 1) else 0.0
        weight_ne = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "p_sw"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (1) * xs + (1) * ys, clamp=True)
        area_ne = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "drainage_area"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), j + (1) * xs + (1) * ys, clamp=True)
        contribution_ne = area_ne * weight_ne if (xx < n - 1) and (yy < n - 1) else 0.0
        accumulated = area + contribution_l + contribution_r + contribution_d + contribution_u + contribution_sw + contribution_se + contribution_nw + contribution_ne
        geo = store_named_attribute(geo, "drainage_area", accumulated, domain="POINT", type="FLOAT")
    a = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "drainage_area"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    slope_now = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "slope"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    drop = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "drop_limit"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    incision = math.min(k * math.sqrt(a) * slope_now * dt, drop) if not boundary else 0.0
    geo = store_named_attribute(geo, "height", h - incision, domain="POINT", type="FLOAT")

hfinal = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index())
initial = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "initial_height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index())
geo = store_named_attribute(geo, "height_change", hfinal - initial, domain="POINT", type="FLOAT")
pfinal = position()
geo = set_position(geo, vector(pfinal.x, pfinal.y, hfinal))
geo = node("GeometryNodeSetShadeSmooth", inputs={"Mesh": geo, "Shade Smooth": True}, props={"domain": "FACE"}, output="Mesh", typ=Geometry)
geo = set_material(geo, "Erosion Terrain")
output("Geometry", geo)

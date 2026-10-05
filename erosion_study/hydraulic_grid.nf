# Common initial terrain, identical between both approaches.
from packages import math
requested_n = input_int("Resolution", default=96)
requested_steps = input_int("Steps", default=500)
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

parameter_0 = input_float("Time Step", default=0.02)
dt = math.clamp(parameter_0, 0.0001, 0.1)
parameter_1 = input_float("Rain", default=0.03)
rain = math.max(parameter_1, 0.0)
parameter_2 = input_float("Evaporation", default=0.03)
evap = math.max(parameter_2, 0.0)
parameter_3 = input_float("Capacity", default=2.0)
capacity_k = math.max(parameter_3, 0.0)
parameter_4 = input_float("Erosion Rate", default=0.35)
erosion_k = math.max(parameter_4, 0.0)
parameter_5 = input_float("Deposition Rate", default=1.0)
deposition_k = math.max(parameter_5, 0.0)
open_edges = input_bool("Open Edges", default=True)

geo = store_named_attribute(geo, "water", 0.0, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "sediment", 0.0, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "f_l", 0.0, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "f_r", 0.0, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "f_d", 0.0, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "f_u", 0.0, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "water_export", 0.0, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "sediment_export", 0.0, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "evap_total", 0.0, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "erosion_total", 0.0, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "deposition_total", 0.0, domain="POINT", type="FLOAT")
for tick in repeat_range(steps):
    i = index()
    x = (i // xs) % n
    y = (i // ys) % n
    h = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    w_old = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "water"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    s_old = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "sediment"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    w = w_old + rain * dt
    head = h + w
    hn_l = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (-1) * xs + (0) * ys, clamp=True)
    wn_l = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "water"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (-1) * xs + (0) * ys, clamp=True)
    oldf_l = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "f_l"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    valid_l = (x > 0)
    head_l = hn_l + wn_l + rain * dt if valid_l else (0.0 if open_edges else head)
    trial_l = math.max(oldf_l + dt * 9.81 * area / dx * (head - head_l), 0.0)
    hn_r = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (1) * xs + (0) * ys, clamp=True)
    wn_r = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "water"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (1) * xs + (0) * ys, clamp=True)
    oldf_r = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "f_r"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    valid_r = (x < n - 1)
    head_r = hn_r + wn_r + rain * dt if valid_r else (0.0 if open_edges else head)
    trial_r = math.max(oldf_r + dt * 9.81 * area / dx * (head - head_r), 0.0)
    hn_d = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (0) * xs + (-1) * ys, clamp=True)
    wn_d = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "water"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (0) * xs + (-1) * ys, clamp=True)
    oldf_d = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "f_d"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    valid_d = (y > 0)
    head_d = hn_d + wn_d + rain * dt if valid_d else (0.0 if open_edges else head)
    trial_d = math.max(oldf_d + dt * 9.81 * area / dx * (head - head_d), 0.0)
    hn_u = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (0) * xs + (1) * ys, clamp=True)
    wn_u = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "water"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (0) * xs + (1) * ys, clamp=True)
    oldf_u = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "f_u"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    valid_u = (y < n - 1)
    head_u = hn_u + wn_u + rain * dt if valid_u else (0.0 if open_edges else head)
    trial_u = math.max(oldf_u + dt * 9.81 * area / dx * (head - head_u), 0.0)
    total_trial = trial_l + trial_r + trial_d + trial_u
    limiter = math.min(1.0, w * area / math.max(dt * total_trial, 0.000000001))
    geo = store_named_attribute(geo, "f_l", trial_l * limiter, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "f_r", trial_r * limiter, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "f_d", trial_d * limiter, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "f_u", trial_u * limiter, domain="POINT", type="FLOAT")
    flux_geo = geo
    out_l = sample_index(flux_geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "f_l"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    incoming_l = sample_index(flux_geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "f_r"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (-1) * xs + (0) * ys, clamp=True)
    in_l = incoming_l if valid_l else 0.0
    out_r = sample_index(flux_geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "f_r"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    incoming_r = sample_index(flux_geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "f_l"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (1) * xs + (0) * ys, clamp=True)
    in_r = incoming_r if valid_r else 0.0
    out_d = sample_index(flux_geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "f_d"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    incoming_d = sample_index(flux_geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "f_u"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (0) * xs + (-1) * ys, clamp=True)
    in_d = incoming_d if valid_d else 0.0
    out_u = sample_index(flux_geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "f_u"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    incoming_u = sample_index(flux_geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "f_d"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (0) * xs + (1) * ys, clamp=True)
    in_u = incoming_u if valid_u else 0.0
    w_flow = math.max(w + dt / area * (in_l + in_r + in_d + in_u - out_l - out_r - out_d - out_u), 0.0)
    vx = (in_l - out_l + out_r - in_r) / math.max(dx * (w + w_flow), 0.000001)
    vy = (in_d - out_d + out_u - in_u) / math.max(dx * (w + w_flow), 0.000001)
    speed = math.sqrt(vx * vx + vy * vy)
    slope_x = ((hn_r if valid_r else h) - (hn_l if valid_l else h)) / (2.0 * dx)
    slope_y = ((hn_u if valid_u else h) - (hn_d if valid_d else h)) / (2.0 * dx)
    slope = math.sqrt(slope_x * slope_x + slope_y * slope_y)
    capacity = capacity_k * speed * slope * w_flow
    lowest = math.min(math.min(hn_l if valid_l else h, hn_r if valid_r else h), math.min(hn_d if valid_d else h, hn_u if valid_u else h))
    erosion_limit = math.max(h - lowest, 0.0) * 0.25
    eroded = math.min(math.max(capacity - s_old, 0.0) * math.min(erosion_k * dt, 1.0), math.min(erosion_limit, math.max(h, 0.0)))
    deposited = math.max(s_old - capacity, 0.0) * math.min(deposition_k * dt, 1.0)
    h_new = h - eroded + deposited
    s_exchange = s_old + eroded - deposited
    concentration = s_exchange / math.max(w, 0.000001)
    geo = store_named_attribute(geo, "concentration", concentration, domain="POINT", type="FLOAT")
    transport_geo = geo
    cn_l = sample_index(transport_geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "concentration"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (-1) * xs + (0) * ys, clamp=True)
    sin_l = in_l * cn_l if valid_l else 0.0
    cn_r = sample_index(transport_geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "concentration"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (1) * xs + (0) * ys, clamp=True)
    sin_r = in_r * cn_r if valid_r else 0.0
    cn_d = sample_index(transport_geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "concentration"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (0) * xs + (-1) * ys, clamp=True)
    sin_d = in_d * cn_d if valid_d else 0.0
    cn_u = sample_index(transport_geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "concentration"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), i + (0) * xs + (1) * ys, clamp=True)
    sin_u = in_u * cn_u if valid_u else 0.0
    s_new = math.max(s_exchange + dt / area * (sin_l + sin_r + sin_d + sin_u - concentration * (out_l + out_r + out_d + out_u)), 0.0)
    lost_water = dt / area * ((out_l if not valid_l else 0.0) + (out_r if not valid_r else 0.0) + (out_d if not valid_d else 0.0) + (out_u if not valid_u else 0.0))
    lost_sediment = lost_water * concentration
    evaporated = w_flow * math.min(evap * dt, 1.0)
    prev_water_export = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "water_export"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    prev_sediment_export = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "sediment_export"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    prev_evap_total = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "evap_total"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    prev_erosion_total = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "erosion_total"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    prev_deposition_total = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "deposition_total"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index(), clamp=True)
    geo = store_named_attribute(geo, "height", h_new, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "water", w_flow - evaporated, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "sediment", s_new, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "water_export", prev_water_export + lost_water, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "sediment_export", prev_sediment_export + lost_sediment, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "evap_total", prev_evap_total + evaporated, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "erosion_total", prev_erosion_total + eroded, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "deposition_total", prev_deposition_total + deposited, domain="POINT", type="FLOAT")
    geo = store_named_attribute(geo, "speed", speed, domain="POINT", type="FLOAT")

hfinal = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index())
initial = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "initial_height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index())
geo = store_named_attribute(geo, "height_change", hfinal - initial, domain="POINT", type="FLOAT")
pfinal = position()
geo = set_position(geo, vector(pfinal.x, pfinal.y, hfinal))
geo = node("GeometryNodeSetShadeSmooth", inputs={"Mesh": geo, "Shade Smooth": True}, props={"domain": "FACE"}, output="Mesh", typ=Geometry)
geo = set_material(geo, "Erosion Terrain")
output("Geometry", geo)

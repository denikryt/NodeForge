# Noise terrain built from a mesh grid.
# The noise domain is offset and domain-warped to avoid a visible convergence artifact at object origin.

resolution = input_int("Resolution", default=64)
height_scale = input_float("Height Scale", default=1.0)
noise_scale = input_float("Noise Scale", default=4.5)
detail = input_float("Detail", default=8.0)
roughness = input_float("Roughness", default=0.55)
warp_strength = input_float("Warp Strength", default=0.35)
offset_x = input_float("Offset X", default=37.13)
offset_y = input_float("Offset Y", default=-19.71)

geo = grid(resolution, resolution)
uv = grid_uv()

x = map_range(uv.x, 0, 1, -3.0, 3.0)
y = map_range(uv.y, 0, 1, -3.0, 3.0)

# Do not sample the procedural noise directly around (0, 0, 0).  Some noise implementations
# produce an obvious focal point there when the plane origin is in the center of the domain.
base = vector(x + offset_x, y + offset_y, 13.7)
warp_x = noise(base + vector(11.2, 0, 0), scale=noise_scale * 0.65, detail=3.0, roughness=0.5)
warp_y = noise(base + vector(0, 17.8, 0), scale=noise_scale * 0.65, detail=3.0, roughness=0.5)
coord = base + vector(warp_x - 0.5, warp_y - 0.5, 0) * warp_strength

low = noise(coord, scale=noise_scale, detail=detail, roughness=roughness)
high = noise(coord * 2.7 + vector(5.1, -8.4, 2.0), scale=noise_scale * 1.9, detail=4.0, roughness=0.45)
height = ((low - 0.5) * 0.85 + (high - 0.5) * 0.25) * height_scale

geo = set_position(geo, vector(x, y, height))
geo = store_named_attribute(geo, "terrain_height", height, domain="POINT", type="FLOAT")
geo = set_material(geo, "NodeForge Terrain")

output("Geometry", geo)

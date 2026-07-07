# Parametric DNA double helix.
# The primary helix follows relaxed B-DNA: about 10.5 base pairs per turn.
# Twist Scale unwinds or overwinds the Watson-Crick twist:
#   1.0 = relaxed B-DNA twist
#   0.0 = straight ladder
#  -1.0 = same pitch, opposite handedness

base_pairs = input_int("Base Pairs", default=84)
helix_radius = input_float("Helix Radius", default=0.75)
rise_per_pair = input_float("Rise Per Pair", default=0.105)
twist_scale = input_float("Twist Scale", default=0.2)
phase = input_float("Phase", default=0.0)
backbone_thickness = input_float("Backbone Thickness", default=0.055)
base_pair_thickness = input_float("Base Pair Thickness", default=0.032)

safe_backbone = max(backbone_thickness, 0.01)
safe_base_pair = max(base_pair_thickness, 0.008)

# B-DNA primary twist: one full turn per ~10.5 base pairs.
primary_turn_per_pair = tau / 10.5
strand_b_offset = pi

backbone_bead = cube(size=vector(safe_backbone * 1.75, safe_backbone * 1.75, safe_backbone * 1.75))
base_pair_bar = cube(size=vector(helix_radius * 2.0, safe_base_pair, safe_base_pair))
end_base_bead = cube(size=vector(safe_base_pair * 2.15, safe_base_pair * 2.15, safe_base_pair * 2.15))

builder = geometry_builder()

for i in repeat_range(base_pairs):
    z = i * rise_per_pair
    z_next = (i + 1) * rise_per_pair

    angle = phase + i * primary_turn_per_pair * twist_scale
    next_angle = phase + (i + 1) * primary_turn_per_pair * twist_scale

    center = vector(0.0, 0.0, z)
    next_center = vector(0.0, 0.0, z_next)

    strand_a = vector(cos(angle) * helix_radius, sin(angle) * helix_radius, z)
    strand_b = vector(cos(angle + strand_b_offset) * helix_radius, sin(angle + strand_b_offset) * helix_radius, z)
    next_a = vector(cos(next_angle) * helix_radius, sin(next_angle) * helix_radius, z_next)
    next_b = vector(cos(next_angle + strand_b_offset) * helix_radius, sin(next_angle + strand_b_offset) * helix_radius, z_next)

    # Sugar-phosphate backbones.
    builder.add(line(strand_a, next_a))
    builder.add(line(strand_b, next_b))
    builder.add(transform(backbone_bead, translation=strand_a))
    builder.add(transform(backbone_bead, translation=strand_b))

    # Base-pair rung between the two backbone strands.
    builder.add(transform(base_pair_bar, translation=center, rotation=vector(0.0, 0.0, angle)))
    builder.add(transform(end_base_bead, translation=strand_a))
    builder.add(transform(end_base_bead, translation=strand_b))

geo = builder.geometry
geo = store_named_attribute(geo, "twist_scale", twist_scale, domain="POINT", type="FLOAT")
geo = set_material(geo, "NodeForge DNA Double Helix")
output("Geometry", geo)

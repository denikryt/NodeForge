# Parametric through-arch truss bridge.
# The road deck is explicit, with two side trusses, vertical hangers, X bracing,
# and a smooth arched top chord built from tangent-aligned short beam segments.

from functions import grid_points

bays = input_int("Bays", default=32)
visual_bays = max(bays, 2)
bay_width = input_float("Bay Width", default=0.26)
arch_height = input_float("Arch Height", default=1.42)
deck_width = input_float("Deck Width", default=1.05)
road_thickness = 0.10
member = input_float("Structural Member Thickness", default=0.04)
beam = member
bracing = member
camber = input_float("Arch Camber", default=0.18)

# Road details are intentionally independent from Structural Member Thickness.
# That parameter should only affect the load-bearing truss members.
road_mark_width = deck_width * 0.018
curb_width = deck_width * 0.055
curb_height = road_thickness * 0.72

span = visual_bays * bay_width
half_span = span * 0.5
side_y = deck_width * 0.5
road_z = 0.0
bottom_z = beam * 1.1

# A visible roadway: slab, darker asphalt strip, curbs, and a center marking.
road = cube(size=vector(span + bay_width * 1.8, deck_width * 0.82, road_thickness))
road = transform(road, translation=vector(0.0, 0.0, -road_thickness * 0.5))

asphalt = cube(size=vector(span + bay_width * 1.35, deck_width * 0.58, road_thickness * 0.16))
asphalt = transform(asphalt, translation=vector(0.0, 0.0, road_thickness * 0.08))

lane = cube(size=vector(span + bay_width * 1.05, road_mark_width, road_thickness * 0.18))
lane = transform(lane, translation=vector(0.0, 0.0, road_thickness * 0.22))

left_curb = cube(size=vector(span + bay_width * 1.55, curb_width, curb_height))
right_curb = cube(size=vector(span + bay_width * 1.55, curb_width, curb_height))
left_curb = transform(left_curb, translation=vector(0.0, side_y * 0.72, road_thickness * 0.2))
right_curb = transform(right_curb, translation=vector(0.0, -side_y * 0.72, road_thickness * 0.2))


# Short side deck brackets fill the gap between the roadway edge and the side
# trusses. They stop at the roadway edge instead of crossing over the road, so
# the driving surface stays clean while the side frames no longer float apart.
road_edge_y = deck_width * 0.41
bracket_gap = max(side_y - road_edge_y, beam)
bracket_y = road_edge_y + bracket_gap * 0.5
bracket_pts = grid_points(
    count=vector(visual_bays + 1, 2, 1),
    spacing=vector(bay_width, bracket_y * 2.0, 0.0),
    centered=True,
)
bp = position()
bracket_pts = set_position(bracket_pts, vector(bp.x, bp.y, road_thickness * 0.33))
bracket_beam = cube(size=vector(beam * 0.85, bracket_gap * 0.96, beam * 0.7))
side_brackets = instance_on_points(bracket_beam, bracket_pts, realize=True)

# Two side planes: y = +/- deck_width / 2. These are the real trusses.
post_pts = grid_points(
    count=vector(visual_bays + 1, 2, 1),
    spacing=vector(bay_width, deck_width, 0.0),
    centered=True,
)
p = position()
x = p.x
y = p.y
n = clamp(abs(x) / half_span, 0.0, 1.0)
arch_z = arch_height * (1.0 - n * n) + camber
post_height = max(arch_z - bottom_z, beam)
post_mid = vector(x, y, bottom_z + post_height * 0.5)
post_pts = set_position(post_pts, post_mid)

vertical_post = cube(size=vector(beam, beam, 1.0))
posts = instance_on_points(
    vertical_post,
    post_pts,
    scale=vector(1.0, 1.0, post_height),
    realize=True,
)

# Bottom chords run along the deck edge on both sides.
side_chord_pts = grid_points(
    count=vector(visual_bays, 2, 1),
    spacing=vector(bay_width, deck_width, 0.0),
    centered=True,
)
p2 = position()
x2 = p2.x
y2 = p2.y
side_chord_pts = set_position(side_chord_pts, vector(x2, y2, bottom_z))
bottom_chord = cube(size=vector(bay_width * 1.08, beam, beam))
bottoms = instance_on_points(bottom_chord, side_chord_pts, realize=True)

# Smooth top chord: each bay is a real segment between the two post tops.
# This avoids floating roof beams when Bays is small (1-3), while still reading
# as a smooth arch when the bay count is high.
xl = x2 - bay_width * 0.5
xr = x2 + bay_width * 0.5
nl = clamp(abs(xl) / half_span, 0.0, 1.0)
nr = clamp(abs(xr) / half_span, 0.0, 1.0)
arch_left_z = arch_height * (1.0 - nl * nl) + camber
arch_right_z = arch_height * (1.0 - nr * nr) + camber
arch_center_z = (arch_left_z + arch_right_z) * 0.5
arch_delta_z = arch_right_z - arch_left_z
arch_angle = atan2(arch_delta_z, bay_width)
arch_segment_length = sqrt(bay_width * bay_width + arch_delta_z * arch_delta_z) * 1.01
arch_pts = set_position(side_chord_pts, vector(x2, y2, arch_center_z))
arch_beam = cube(size=vector(1.0, beam * 1.22, beam * 1.22))
arches = instance_on_points(
    arch_beam,
    arch_pts,
    rotation=vector(0.0, -arch_angle, 0.0),
    scale=vector(arch_segment_length, 1.0, 1.0),
    realize=True,
)

# Warren/X bracing on both side planes, between real bay endpoints.
# Each diagonal uses the arch height at the endpoint it actually touches,
# so the braces do not poke through the top chord.
brace_up_height = max(arch_right_z - bottom_z, beam)
brace_down_height = max(arch_left_z - bottom_z, beam)
brace_up_length = sqrt(bay_width * bay_width + brace_up_height * brace_up_height)
brace_down_length = sqrt(bay_width * bay_width + brace_down_height * brace_down_height)
brace_up_angle = atan2(brace_up_height, bay_width)
brace_down_angle = atan2(brace_down_height, bay_width)
brace_up_mid = set_position(side_chord_pts, vector(x2, y2, bottom_z + brace_up_height * 0.5))
brace_down_mid = set_position(side_chord_pts, vector(x2, y2, bottom_z + brace_down_height * 0.5))
brace_bar = cube(size=vector(1.0, bracing, bracing))
brace_a = instance_on_points(
    brace_bar,
    brace_up_mid,
    rotation=vector(0.0, -brace_up_angle, 0.0),
    scale=vector(brace_up_length, 1.0, 1.0),
    realize=True,
)
brace_b = instance_on_points(
    brace_bar,
    brace_down_mid,
    rotation=vector(0.0, brace_down_angle, 0.0),
    scale=vector(brace_down_length, 1.0, 1.0),
    realize=True,
)

# Overhead cross-ties connect the two side arches into one bridge portal,
# instead of leaving two unrelated side frames.
top_tie_pts = grid_points(
    count=vector(visual_bays + 1, 1, 1),
    spacing=vector(bay_width, 0.0, 0.0),
    centered=True,
)
xt = position().x
nt = clamp(abs(xt) / half_span, 0.0, 1.0)
top_tie_z = arch_height * (1.0 - nt * nt) + camber
top_tie_pts = set_position(top_tie_pts, vector(xt, 0.0, top_tie_z))
top_tie_beam = cube(size=vector(beam * 0.9, deck_width, beam * 0.9))
top_ties = instance_on_points(top_tie_beam, top_tie_pts, realize=True)

geo = join([
    road,
    asphalt,
    lane,
    left_curb,
    right_curb,
    side_brackets,
    posts,
    bottoms,
    arches,
    brace_a,
    brace_b,
    top_ties,
])
geo = store_named_attribute(geo, "bridge_arch_height", arch_z, domain="POINT", type="FLOAT")
geo = set_material(geo, "NodeForge Through Arch Bridge")
output("Geometry", geo)

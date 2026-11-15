# Math Examples

The `nodeforge.math` package includes complete example scripts in the **Examples** catalog. Open **Library → Examples** to add an example as a Geometry Nodes group.

Examples are complete scripts intended for inspection and adaptation. Add one from the catalog, then follow the source-loading workflow in [Use the Library panel](GET_STARTED.md#use-the-library-panel) when you want to inspect or modify it.

## `dna_double_helix`

Builds a parametric DNA-style double helix from two backbone strands and repeated base-pair rungs. The controls set the base-pair count, helix radius, rise, twist, phase, and element thicknesses.


## `fibonacci_spiral`

Distributes instances of the input Geometry along a sunflower-style Fibonacci spiral. The point count, radial scale, instance scale, and angular turn are runtime controls.


## `helix_ladder`

Builds a helical ladder or spiral-stair structure from radial rungs, outer posts, and a central post. The step count and the main shape parameters remain runtime inputs.


## `kinetic_louver_wall`

Creates a rectangular field of thin facade blades. A pair of trigonometric fields controls the tilt of each blade from its position, producing a wave-like kinetic-louver pattern.



## `noise_terrain`

Generates a mesh grid and displaces it vertically with layered, domain-warped 3D noise. Runtime controls expose grid resolution, height, noise detail, roughness, warp strength, and domain offsets.


## `parametric_truss_bridge`

Builds a through-arch truss bridge with an explicit road deck, curbs, side trusses, vertical posts, arched top chords, diagonal bracing, and overhead cross-ties. The number and width of bays define the span, while the remaining controls shape the arch and structural members.


## `rippled_tile_field`

Creates a centered grid of tile instances and drives their height and vertical scale with a radial sine wave. A smooth radial falloff reduces the wave toward the outside of the field.


## `sierpinski_carpet`

Applies the eight-cell Sierpinski-carpet offset pattern repeatedly to the input Geometry. **Steps** is a runtime input, and **Scale** controls the per-iteration copy scale.


## `topographic_contour_stack`

Builds stacked closed contour rings from runtime point grids. Each ring uses several periodic waves to vary its radius, and short tangent-aligned segments overlap to form continuous-looking contour lines.

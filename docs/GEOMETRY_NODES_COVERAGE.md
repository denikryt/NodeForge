# NodeForge Geometry Nodes Coverage

This document shows the Blender Geometry Nodes areas and domains currently covered by NodeForge.

| Geometry Nodes area | NodeForge | Note |
| --- | --- | --- |
| Geometry Nodes modifier workflow | Yes | Script composes into an open base tree |
| Node group inputs / outputs | Yes | Typed `def` parameters and `return` |
| Group interface panels / metadata | Partial | Function input panels through `panel(...)` |
| Inspection | No | No inspection/debug workflow |
| Baking | No | No baking workflow |
| Node-based tools | No | No tool metadata API |
| Gizmos | No | No gizmo API |
| Runtime socket: Geometry | Yes | `Geometry` |
| Runtime socket: Float | Yes | `Float` |
| Runtime socket: Int | Yes | `Int` |
| Runtime socket: Bool | Yes | `Bool` |
| Runtime socket: Vector | Yes | `Vector` |
| Runtime socket: String | Yes | `String` function parameters |
| Runtime socket: Color | Yes | `Color` in `.nf`/raw `node()`, group defaults, Switch/Repeat/Bundle, Capture/attributes, Extension API v2 |
| Runtime socket: Material | Yes | `Material` function parameters |
| Runtime socket: Object | Yes | `Object` function parameters |
| Runtime socket: Bundle | Yes | `Bundle` function parameters |
| Runtime socket: Collection | No | Missing type |
| Runtime socket: Image | No | Missing type |
| Runtime socket: Rotation | Yes | Public `.nf` type, `NodeSocketRotation` inputs/outputs, raw `node()` and Extension API v2; XYZ Euler defaults. Operation-specific type support remains separate. |
| Runtime socket: Matrix | No | Missing type |
| Constant input nodes | Partial | Supported types only |
| Scene input nodes | Partial | Object Info through `Object` properties |
| Import input nodes | No | No import-node wrappers |
| Gizmo input nodes | No | No gizmo sockets |
| Output nodes: Viewer | No | No wrapper |
| Output nodes: Warning | No | No wrapper |
| Fields: position / normal / index / id | Partial | `position()`, `normal()`, `index()`, `id()` |
| Fields: radius / selection / active element | Partial | Selection via bool fields only |
| Fields: evaluate / capture / domain conversion | Partial | `sample_index(...)` and `capture_attribute(...)` for supported field types and domains |
| Attributes: store | Partial | `store_named_attribute(...)` |
| Attributes: read | No | No wrapper |
| Attributes: statistics / domain size | No | No wrapper |
| Attributes: blur / capture / remove | Partial | Capture only |
| Selection fields | Partial | Boolean expressions, `selection=` args |
| Math: scalar | Yes | Common math functions/operators |
| Math: integer / bitwise | Partial | Basic ints, no bitwise layer |
| Math: boolean | Yes | `and`, `or`, `not`, comparisons |
| Math: map / mix / clamp / switch | Yes | `map_range`, `mix`, `clamp`, `select` |
| Random values | Partial | `random_value(...)` |
| Noise texture | Partial | `noise(...)` only |
| Texture nodes | Partial | Noise only |
| Image texture | No | No `Image` socket |
| Color utilities | Partial | `Color` sockets through `node(...)`; no dedicated color-function library |
| Runtime String socket | Yes | Typed function, raw-node, and library sockets; runtime attribute names |
| Compile-time strings | Yes | Names, identifiers, and raw-node declarations |
| Vector construction | Yes | `vector(x, y, z)` |
| Vector math | Partial | Common vector operations |
| Rotation utilities | Partial | Helper functions only |
| Matrix utilities | No | No `Matrix` socket |
| Menu Switch / enum sockets | No | No menu/enum model |
| Bundle operations | Partial | `bundle(...)`, `bundle_get(...)`, `bundle_set(...)` |
| Geometry primitives | Partial | `empty_geometry`, `points`, `point`, `line`, `polyline`, `grid`, `cube` |
| Geometry join | Yes | `join(...)` |
| Geometry transform | Yes | `transform(...)` |
| Geometry set position | Yes | `set_position(...)` |
| Geometry read nodes | Partial | Position/normal/index/id only |
| Geometry sample nodes | Partial | `sample_index(...)`; no sample-nearest, surface, proximity, or raycast wrappers |
| Geometry write nodes | Partial | Set position and store attribute only |
| Geometry material nodes | Partial | `set_material(...)` only |
| Geometry operations | Partial | Join/transform only |
| Geometry delete / separate / duplicate / merge | No | No wrappers |
| Geometry bounding box / convex hull | No | No wrappers |
| Geometry sort / split to instances | No | No wrappers |
| Geometry to instance | No | No wrapper |
| Mesh primitives | Partial | Cube, grid, line-like output |
| Mesh read nodes | No | No vertex/edge/face topology API |
| Mesh sample nodes | No | No sample-nearest/surface wrappers |
| Mesh write nodes | No | No face-set/shade-smooth/normal wrappers |
| Mesh operations | No | No extrude, subdivide, boolean, triangulate wrappers |
| Mesh topology nodes | No | No topology construction/read API |
| Mesh normals | No | Only `normal()` field |
| Mesh conversion | No | No mesh-to-curve/points/volume wrappers |
| UV maps / UV sampling | Partial | `grid_uv()` only |
| Curve primitives | Partial | `line`, `polyline`, L-system curves |
| Curve read nodes | No | No length/tangent/handle wrappers |
| Curve sample nodes | No | No sample/resample wrappers |
| Curve write nodes | No | No radius/tilt/handle/spline wrappers |
| Curve operations | No | No trim, fill, fillet, curve-to-mesh wrappers |
| Curve topology nodes | No | No spline/control-point topology API |
| Curve conversion | No | No curve-to-mesh/points/grease-pencil wrappers |
| Grease Pencil read nodes | No | No Grease Pencil API |
| Grease Pencil write nodes | No | No Grease Pencil API |
| Grease Pencil operations | No | No Grease Pencil API |
| Instances on points | Yes | `instance_on_points(...)` |
| Realize instances | Yes | `realize_instances(...)` |
| Instance transform operations | Partial | Via `instance_on_points` args only |
| Instance read/info nodes | No | No wrappers |
| Instance selection / pick instance | Partial | `selection=` in `instance_on_points(...)`; no Pick Instance control |
| Object instancing | Partial | Object Info geometry through `Object` parameters and `.geometry` |
| Collection instancing | No | No `Collection` socket/API |
| Points / point cloud generation | Partial | `points`, `point`, layout helpers |
| Points distribution | No | No distribute-points wrappers |
| Points write nodes | No | No set-point-radius wrapper |
| Points conversion | No | No points-to-volume/curves/vertices wrappers |
| Volume read nodes | No | No volume grid API |
| Volume sample nodes | No | No volume grid API |
| Volume operations | No | No volume operation wrappers |
| Volume primitives | No | No volume primitive wrappers |
| Volume conversion | No | No mesh/points-to-volume wrappers |
| Materials: assign | Partial | `set_material(geo, material)` with a name or runtime `Material` |
| Materials: runtime material data | Yes | Typed function, raw-node, and library sockets |
| Materials: index / replace / selection | No | No wrappers |
| Simulation Zone | No | No simulation-zone syntax |
| Repeat Zone | Partial | Runtime `range(count)` and nested `repeat_range(...)` state loops |
| For Each Geometry Element Zone | No | No foreach-zone syntax |
| Bake node | No | No wrapper |
| Viewer node | No | No wrapper |
| Warning node | No | No wrapper |
| Local functions | Yes | Typed parameters and single or flat multi-value returns |
| Reusable library imports | Yes | `from packages import ...`, `from examples import ...`, `from local import ...` |
| Compile-time `for` loops | Yes | Unrolled graph generation |
| Runtime repeat loops | Partial | Nested repeat-style loops; Color carried state supported |
| Runtime `if` / switch logic | Partial | Compatible Float, Int, Vector, Color, Bool, Geometry, String, and Bundle branch values |
| Arrays / compile-time lists | Partial | Compile-time structure only |
| L-systems | Package | `ls.system(...)` from [NodeForge L-System](LSYSTEMS.md) |
| Raw Blender node creation | Raw only | `node(...)`; NodeForge runtime socket types only |

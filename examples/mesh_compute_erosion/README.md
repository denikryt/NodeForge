# Hydraulic / thermal erosion — Local NumPy example

The `erosion.py` callback translates the hydraulic and thermal **Repeat Zone**
calculation from the supplied `hydraulic_grid_shape_cpu_fast.nf` (lines 86–207)
into a deterministic NumPy array kernel. It keeps water flux histories, rain,
evaporation, four-way sediment transport, erosion/deposition, open-edge and
legacy-outlet modes, and the eight-way thermal pass. The original terrain
construction (noise, raycast, height datum), smooth shading and material remain
in the companion NodeForge script `erosion_numpy.nf`.

## Use in Blender 5.2

1. Enable NodeForge and install NumPy in Blender's Python if needed. For the full terrain generator, also install the **NodeForge Math** package (import name `math`).
2. Select a Mesh object with no existing modifiers.
3. In **NodeForge → Library → Examples**, refresh and select `mesh_compute_erosion/erosion_numpy.nf`.
4. Enable **Executable Python: Allowed** in the Examples panel. This permission applies only to bundled Examples and is disabled by default. The `erosion.py` callback executes with normal Python privileges.
5. Click **Add Node Group**. Set Resolution, Steps and the other parameters on the generated modifier. The material `Erosion Terrain` can be assigned as in the original example. Ordinary scripts selected from the same list still create reusable node groups.
6. Edit `erosion.py` and use **Reload Compute** to capture the changed Python callback. Saving/reopening a `.blend`, Undo and Redo invalidate executable bindings; use Reload Compute to restore them. Revoking the Examples Python permission disables evaluation and invalidates the result. For interactive rendering, enable **Render Properties → Lock Interface**.

The companion script imports the callback directly:

```python
from examples.mesh_compute_erosion import erosion
```

The callback's `reads` are `height` and `initial_height` (POINT FLOAT); `writes`
are `height`, `water`, `sediment`. The optional `positions` output is omitted:
the GN suffix sets the Z coordinate from the returned `height`. No topology
changes are made in Python. `initial_height` is the unshifted source field;
`height` passed to Python includes the datum shift needed by the original
physics. The callback removes that datum from the final height, keeping all
cross-stage state in declared buffers instead of forwarding an unbounded GN
field from the prefix into the suffix.

## Behavior and limits

- Input must be a square Mesh Grid with exactly `n*n` vertices in a regular
  x-fast or y-fast vertex order. The callback checks `n>=3`, vertex count and
  nonnegative iteration count. Geometry Nodes still owns grid connectivity,
  UVs, materials and all non-output native layers.
- NumPy uses float32 state, but GN arithmetic and Python/NumPy may evaluate
  temporary expressions in a different order. Small floating-point differences
  are normal; do not expect bitwise identity with native GN.
- Only the erosion *simulation* was moved to NumPy. The noise/raycast source,
  GN group compilation and final GN suffix are still Blender work.
- An independent scalar reference validates each hydraulic and thermal subpass
  at small sizes. A real Blender GN→Local callback→GN test validates the
  self-contained example. Full result-by-result parity with the original
  source GN Repeat Zone still requires the separate `nodeforge.math` package.
- Output `height` uses the same original surface datum, `height_change` is
  recomputed by the GN suffix, and all simulation inputs are explicit uniform
  parameters. The kernel has no external RNG/global/time dependencies.

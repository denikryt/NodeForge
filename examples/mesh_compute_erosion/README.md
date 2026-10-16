# Hydraulic / thermal erosion — Local NumPy example

The `erosion.py` callback translates the hydraulic and thermal **Repeat Zone**
calculation from the supplied `hydraulic_grid_shape_cpu_fast.nf` (lines 86–207)
into a deterministic NumPy array kernel. It keeps water flux histories, rain,
evaporation, four-way sediment transport, erosion/deposition, open-edge and
legacy-outlet modes, and the eight-way thermal pass. The original terrain
construction (noise, raycast, height datum), smooth shading and material remain
in the companion NodeForge script `erosion_numpy.nf`.

## Use in Blender 5.2

1. Install/enable NodeForge with Local Mesh Compute support and install NumPy in
   Blender's Python if not already available.
2. Copy `erosion.py` to the **registered Local directory** as `erosion.py`. Grant
   **Allow executable Python** to that Local source. The callback executes with
   normal Python privileges.
3. To test without extra libraries, select `erosion_numpy_smoke.nf` in the
   **NodeForge → Library → Local** browser, select a Mesh object without
   existing modifiers, and click **Compile to Modifier** beneath the Local list.
   It generates a square terrain in GN and attaches the executable Python
   stage to the modifier. Alternatively, open the `.nf` in Blender's Text
   Editor and use the main **Compile to Modifier** action. **Add Node Group**
   is for ordinary library groups and does not run Mesh Compute scripts.
4. To use the **full supplied terrain generator**, install the independent
   **NodeForge Math** package with import name `math` first. Then select
   `erosion_numpy.nf` and use **Compile to Modifier**. Set Resolution, Steps and the
   existing terrain/erosion parameters through the Geometry Nodes modifier.
   The material `Erosion Terrain` can be assigned in Blender as in the source.
5. Edit `erosion.py` and use **Reload Compute** to load new Python code. Saving
   and reopening a .blend invalidates the executable binding; Reload Compute
   is required to restore it. **Undo / Redo** also invalidates live Python
   bindings because Blender restores the node groups and modifiers as new
   datablocks. Use Reload Compute after Undo / Redo to recalculate the result.
   For interactive rendering, enable **Render Properties → Lock Interface**;
   headless Blender renders are supported without the interactive render window.

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

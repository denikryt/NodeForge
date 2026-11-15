# Mesh Compute (Experimental)

Use Mesh Compute to calculate vertex positions or POINT FLOAT attributes in Python. A typed `.nf` entry function passes Geometry to a Python callback and returns the resulting Geometry. Changing modifier inputs recalculates the result. This feature is experimental in Blender 5.2.

## Write a Mesh Compute script

Place `shift_height.py` and `move_up.nf` in a registered **Local** folder. Write the Python calculation in `shift_height.py`, then import and call it from `move_up.nf`.

`shift_height.py`:

```python
from array import array
from NodeForge import mesh_compute

@mesh_compute(reads=('height',), writes=('height',))
def shift_height(mesh, amount: float = 2.0):
    positions = array('f', mesh.positions)
    heights = array('f', mesh.point_attributes['height'])

    for i in range(mesh.vertex_count):
        positions[3 * i + 2] += amount
        heights[i] += amount

    return {
        'positions': positions,
        'point_attributes': {'height': heights},
    }
```

`move_up.nf`:

```python
from local import shift_height

def move_up(geometry: Geometry, amount: Float["Amount"] = 2.0) -> Geometry:
    geometry = store_named_attribute(
        geometry, 'height', position().z, domain='POINT', type='FLOAT'
    )
    return shift_height(geometry, amount=amount)
```

Create every attribute listed in `reads` before the call. In this example, `move_up.nf` creates `height`, passes Geometry and **Amount** to `shift_height`, then returns the resulting Geometry.

To run it:

1. Select a Mesh object with no modifiers, or with one empty Geometry Nodes modifier (no node group or Blender's unedited default pass-through group).
2. Open **Library → Local** and select `move_up.nf`.
3. Click **Add Node Group**. NodeForge creates a Mesh Compute modifier or uses the empty modifier already on the object. Change **Amount** on the modifier to recalculate the Mesh.

You do not need to create a Geometry Nodes tree before using the Library action. You can also open `move_up.nf` in a Blender Text datablock, select it under **Text Script**, and click **Install Mesh Compute**. **Compile Script** composes ordinary scripts into an open base tree; it does not install a Mesh Compute modifier.

## Write the Python function

Name the function after its `.py` file: write `def shift_height(...)` in `shift_height.py`. Decorate one top-level `def` with `@mesh_compute(...)`. Its first argument receives the mesh data; leave it required and unannotated. You may choose any name for that argument (`mesh` in the example). Add typed `float`, `int`, or `bool` parameters after it, such as `amount: float = 2.0`.

| Declaration | Meaning |
| --- | --- |
| `reads=('height',)` | Request existing POINT FLOAT attributes from the evaluated input Mesh. Omit or use `()` when none are needed. |
| `writes=('height',)` | Declare the POINT FLOAT attributes returned by the callback. Omit or use `()` when returning positions only. |
| `amount: float = 2.0` | A scalar parameter supplied by the `.nf` call. Supported annotations are built-in `float`, `int`, and `bool`; a default is optional. |

Write `reads` and `writes` as literal lists or tuples of nonempty attribute names, with no duplicates within either list. Use literal scalar defaults of the annotated type. Define ordinary parameters; `*args`, `**kwargs`, and keyword-only parameters are unsupported.

In `.nf`, pass a Geometry value first, followed by scalar arguments by position or keyword. Expose scalar controls as typed `def` parameters or calculate them earlier in the script. Each runtime scalar must have the same value at every vertex; varying fields cannot be callback parameters. Return the resulting Geometry from the entry function.

### Input available in Python

| `mesh` member | Value |
| --- | --- |
| `mesh.vertex_count` | Number of vertices in the evaluated, nonempty Mesh. |
| `mesh.positions` | Flat float32 `array('f')` in `x, y, z` order, with `3 * vertex_count` values. |
| `mesh.point_attributes[name]` | Float32 `array('f')` with one value per vertex, available for each name in `reads`. |

Provide every `reads` attribute on the input Mesh as a POINT FLOAT attribute. In Python, work with the supplied arrays; `mesh` is not a Blender Mesh object. Use Python's `array` as shown above, or NumPy if it is installed in Blender's Python.

### Return value

Return a dictionary containing `positions`, `point_attributes`, or both:

| Key | Expected value |
| --- | --- |
| `positions` | A flat sequence of `3 * vertex_count` finite numbers in XYZ order, or a NumPy array shaped `(vertex_count, 3)`. |
| `point_attributes` | A dictionary mapping every name in `writes` to a sequence of `vertex_count` finite numbers. The names must match `writes` exactly. |

For example, `{'positions': positions}` changes only positions. `{'point_attributes': {'height': heights}}` changes only the declared attribute. When `writes` is empty, return positions. NodeForge converts output values to float32, keeps the same vertex count and topology, and preserves other Mesh data. A written name can replace an existing POINT FLOAT attribute or create one; an existing attribute with another domain or type is a conflict.

## Sources and limits

Import the callback explicitly in the entry `.nf` module: `from local import shift_height`, `from local.terrain import shift_height`, or—for a bundled or installed Example—`from examples.mesh_compute_erosion import erosion`. Directory aliases and `*` imports do not select Python callbacks. The Mesh Compute call belongs in the selected typed entry function, not a nested helper.

Use callbacks from registered Local sources, bundled Examples, or installed package Examples. Python callbacks execute with normal Python privileges, so use sources you trust. There is no separate executable-Python permission setting.

Call Mesh Compute once in the selected entry function. Its first parameter must be a required `Geometry` input, and it must return one `Geometry` value. The `.nf` module must have exactly one entry function and no executable top-level statements. Pass a nonempty Mesh; the callback can read positions and declared POINT FLOAT inputs and accept uniform `float`/`int`/`bool` parameters. Keep the same vertices and faces in its result. A modified Geometry Nodes group or additional modifier cannot be replaced by Mesh Compute.

For a larger example, select `mesh_compute_erosion/erosion_numpy.nf` under **Library → Examples** and click **Add Node Group**. This example also requires NumPy in Blender's Python and the NodeForge Math package.

After editing the `.nf` or `.py` source, reopening a `.blend` file, or using Undo or Redo, select the current root `.nf` source in a Blender Text datablock under **Text Script** and click **Reload Compute** on the selected object. If the Python file is removed, its Local source is unregistered, or its Examples package is uninstalled, the calculation becomes unavailable. For interactive rendering, enable **Render Properties → Lock Interface**.

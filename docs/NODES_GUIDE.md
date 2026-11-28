# Nodes Library

NodeForge installs Nodes automatically when you enable the add-on. Import it in a `.nf` script with `from packages import nodes`.

The library is generated from Blender's Python API (`bpy`). Each generated function wraps one Blender node and exposes its supported inputs and configurations. Use the [Nodes coverage reference](NODES.md) to find the function for a Blender node and check which configurations and socket types are available.

## Use node functions

### Single output

Use a single output directly. This example creates a Curve Line between two points. Omitted socket arguments keep their Blender defaults.

```python
from packages import nodes

curve = nodes.curve_line(start=vector(0, 0, 0), end=vector(0, 0, 2))
```

### Fixed outputs

Cylinder exposes several named outputs. Use `.mesh` for the geometry and `.top` for the top-face selection.

```python
from packages import nodes

cylinder = nodes.cylinder(vertices=16, radius=0.5, depth=2.0)
mesh = cylinder.mesh
top = cylinder.top
```

### Menu inputs

Supply a menu choice as a compile-time string. For Resample Curve, `"Count"` uses the `count` input and `"Length"` uses the `length` input. Both modes return a curve directly.

```python
from packages import nodes

curve = nodes.curve_line(end=vector(0, 0, 2))
by_count = nodes.resample_curve(curve=curve, mode="Count", count=8)
by_length = nodes.resample_curve(curve=curve, mode="Length", length=0.25)
```

### Dynamic items

For Capture Attribute, pass a dictionary in `elements`. Each key names a capture item and its output. The value supplies the field and determines the socket type. Use a string key to read an output name containing spaces.

```python
from packages import nodes

base = nodes.cylinder().mesh
result = nodes.capture_attribute(
    geometry=base, domain="POINT",
    elements={
        "Rest Position": position(),
        "point_index": index(),
    },
)
geometry = result.geometry
rest_position = result["Rest Position"]
point_index = result.point_index
```

Menu Switch takes an ordered list of compile-time labels in `elements`. Positional values follow that order. The result exposes `output` and a Boolean output for each label.

```python
from packages import nodes

options = ["Low", "High, x"]
result = nodes.menu_switch(
    0.25, 0.75,
    data_type="FLOAT", menu="High, x", elements=options,
)
value = result.output
high = result["High, x"]
```

Update scripts that use the earlier string form for `elements` and compile them into a fresh tree.

NodeForge's Text Editor completion suggests Nodes functions and shows available parameter and output information. See [Text Editor completion](GET_STARTED.md#text-editor-completion-prototype).

## Coverage

Some nodes and configurations are unavailable because their sockets use types such as Matrix or Collection. The library exposes some Blender Menu inputs as compile-time strings. Runtime Menu links are unsupported. See the [coverage reference](NODES.md) for node-specific limits and available configurations.

## Regenerate the library

The development tool inspects Blender with `bpy`, records node properties and socket layouts, and generates the bundled source package from that specification. Run its commands from the repository root.

| Command | Purpose |
| --- | --- |
| `python3 tools/nodegen/cli.py inspect --blender /path/to/blender` | Inspect Blender and save the merged specification. |
| `python3 tools/nodegen/cli.py generate` | Regenerate the package from the latest saved inspection or the pinned specification. |
| `python3 tools/nodegen/cli.py test --blender /path/to/blender` | Validate the generated package in Blender. |
| `python3 tools/nodegen/cli.py build --blender /path/to/blender` | Inspect Blender, regenerate the library, and validate it. |
| `python3 tools/nodegen/cli.py coverage` | Regenerate the coverage reference from the pinned specification. |

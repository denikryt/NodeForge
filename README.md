# NodeForge

![NodeForge screenshot](media/Screenshot.png)

> **Disclaimer:** NodeForge is a vibe-coded project.

NodeForge is a Blender add-on for describing Geometry Nodes logic in a Python-like language. The source is compiled into a native Geometry Nodes group, so the result inside Blender is an ordinary node graph with the expected sockets, links and parameters.

## Installation

1. In Blender 5.2 or newer, open **Edit → Preferences → Get Extensions**.
2. Add the NodeForge remote extension repository under **Repositories → Add Remote Repository**, using the URL published for the release. The intended GitHub Pages URL is `https://denikryt.github.io/NodeForge/index.json` **once deployed**.
3. Install and enable **NodeForge** from **Get Extensions**. Blender manages later add-on updates through the same repository.

NodeForge automatically installs the included **NodeForge Math** and **NodeForge L-System** libraries into its existing user **Packages** directory when first enabled. On a later NodeForge update, it only replaces an included package if the shipped version is newer than the installed version; it never downgrades a separately updated package. No separate package installation or Python permission step is needed. Installation works offline once the extension ZIP is downloaded.

For users of the legacy ZIP add-on, disable the old NodeForge add-on before enabling the new Extension to avoid duplicate registrations. User Packages and Local files remain in their existing Blender user-data directories.

## Quick start

1. Select a mesh object and open the **Geometry Node Editor**.
2. Click **New** to create a Geometry Nodes modifier and node tree for the object.
3. Press `N` and open the **NodeForge** tab in the sidebar.
4. Open a **Text Editor**, click **New**, and enter this script:

```python
size = input_float("Size", default=2.0)
geo = cube(size=size)
output("Geometry", geo)
```

5. Return to the NodeForge sidebar and select the new text in **Text Script**.
6. Click **Compile Script**. NodeForge adds the generated group node to the current Geometry Nodes tree.
7. Connect the generated node's **Geometry** output to the **Group Output** node to display the cube.

To change the generated group, edit the text, select the generated group node, and click **Update Selected NodeGroup**. NodeForge recompiles the script into the same node group and preserves compatible links and input values.

See the [Get Started guide](https://denikryt.github.io/NodeForgeDocs/latest/GET_STARTED/) for the complete beginner workflow.

## The language at a glance

A small script can mix group inputs, ordinary expressions, built-in Geometry Nodes wrappers, local functions, and direct access to Blender nodes:

```python
size = input_float("Size", default=1.0)
gap = input_float("Gap", default=0.0)

half_spacing = (size + gap) / 2.0

def move_x(geometry: Geometry, x_shift: Float):
    return transform(
        geometry,
        translation=vector(x_shift, 0, 0),
    )

left = move_x(cube(size=size), -half_spacing)
right = move_x(cube(size=size), half_spacing)

geometry = join([left, right])

geometry = node(
    "GeometryNodeMergeByDistance",
    inputs={
        "Geometry": geometry,
        "Distance": 0.001,
    },
    output="Geometry",
    typ=Geometry,
)

output("Geometry", geometry)
```

`input_*` declarations become node-group input sockets and return values that can be used like variables. Operators such as `+`, `/`, and unary `-` are type-checked and materialized as native Geometry Nodes operations. Built-ins such as `cube(...)`, `join(...)`, and `transform(...)` provide compact wrappers around common Geometry Nodes functionality.

A script-local `def` is compiled as a reusable function Node Group. In the example above, both calls to `move_x(...)` use that function group while the two cubes move symmetrically away from the world origin as `Gap` increases.

`node(...)` is the universal low-level form for Blender nodes that do not have a dedicated NodeForge wrapper. Here it creates **Merge by Distance** directly while the rest of the script stays in the higher-level DSL.

The language is intended to make procedural logic easier to express and maintain as the graph grows. Mathematical relationships can be written directly as expressions, while Geometry Nodes operations remain visible through functions that fit naturally into Python-like code. 

NodeForge scripts can call reusable functions and can be extended through installable third-party packages. This allows project-specific operations and larger procedural components to become part of the language used by other scripts.

## Additional libraries

**NodeForge Math** and **NodeForge L-System** are installed automatically from the selected versions included in the NodeForge Extension release. These remain ordinary, independently versioned NodeForge Packages, not Blender Extensions.

You can install or update any library separately through **NodeForge → Packages → Install ZIP**. A package installed at a newer version than the one included in a future NodeForge release is not overwritten by that older included version. Automatic independent GitHub update checks for individual packages are not currently implemented.

Package manifests (`nodeforge_package.json`) may include an optional `repository_url` with an HTTP(S) project repository link. NodeForge retains this metadata without displaying it or using it to download updates.

If a library declares `nodeforge_max_version` below the installed NodeForge version, Packages displays a compatibility warning but still permits installation and use. A library requiring a **higher minimum** NodeForge version is rejected. Actual incompatible package code may still fail at compile time or runtime.

Installed package APIs are imported through the package namespace:

```python
from packages import math

angle = input_float("Angle", default=0.0)
value = math.sin(angle)
output("Value", value)
```

Reusable `.nf` files from the Local library, including folders added from disk with **Add Folder...**, can be imported and called from other scripts:

```python
from local import erosion
```

See [Local imports](https://denikryt.github.io/NodeForgeDocs/latest/SYNTAX/#local) for the supported import forms.

## Coverage

The syntax follows Python as closely as the Geometry Nodes model allows. The built-in function library covers only part of Geometry Nodes. When a dedicated NodeForge function is not available, `node(...)` can create the Blender node directly.

See the [Geometry Nodes coverage](https://denikryt.github.io/NodeForgeDocs/latest/GEOMETRY_NODES_COVERAGE/) page for the current feature matrix and the [Core DSL Built-ins](https://denikryt.github.io/NodeForgeDocs/latest/BUILTINS/) reference for the complete `node(...)` syntax.

### How the compiler works

Internally, NodeForge parses the source, resolves its types and operations, builds a semantic intermediate representation, and lowers that representation into Blender nodes. The compiler owns the translation from source-level logic to the final `GeometryNodeTree`, keeping the language-facing part of the system separate from Blender graph construction.

```text
NodeForge source
    ↓
Python AST
    ↓
semantic analysis and NFType checking
    ↓
typed Semantic IR
    ↓
Blender lowering and materialization
    ↓
GeometryNodeTree
```

### Learn more in the documentation:
https://denikryt.github.io/NodeForgeDocs/

### Support the developer:
https://www.patreon.com/c/nachitima

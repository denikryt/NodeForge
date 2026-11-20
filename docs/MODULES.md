# `.nf` Modules

A `.nf` file is a module. It can declare reusable functions, build nodes in the open Geometry Nodes tree, or do both. Modules can also contain imports and compile-time constants.

```python
def make_cube(size: Float = 2.0) -> Geometry:
    return cube(size)

def lift(geometry: Geometry, height: Float = 1.0) -> Geometry:
    return transform(geometry, translation=vector(0, 0, height))

def raised_cube(size: Float = 2.0) -> Geometry:
    return lift(make_cube(size), height=3.0)

plain = cube(0.5)
raised = raised_cube(2.0)
```

Compiling this module places the following nodes:

```text
Base tree
├─ Cube                     plain
└─ Raised Cube (Group Node) raised
   └─ Inside: Make Cube → Lift
```

## Define functions

Declare any number of top-level functions in one file. Their typed parameters and returns define Group Node sockets.

This module contains only declarations:

```python
def small_cube() -> Geometry:
    return cube(0.5)

def large_cube() -> Geometry:
    return cube(2.0)
```

See [Writing Functions](WRITING_FUNCTIONS.md) for parameter types, defaults, socket labels, panels, and return values.

Every function declaration in the selected module must have a valid signature, defaults, panels, and return shape, including functions that are not called. An invalid unused declaration rejects compilation before nodes are added. Runtime operations in a function body are checked when that function is compiled.

Functions can be called before their declarations appear in the file. Function bodies can call other functions in the same module. Recursive call cycles are not supported. See [Reusable Functions](SYNTAX.md#reusable-functions) for group sharing and `__unique__`.

## Compose the base tree

Open an editable base Geometry Nodes tree, select a Text datablock under **Text Script**, and click **Compile Script**. Imports and compile-time constants alone do not count as node-building code. A module with only top-level statements also works:

```python
shape = cube(1.0)
moved = transform(shape, translation=vector(0, 0, 1))
```

**Compile Script** uses the module in one of two ways:

- With top-level node-building code, it replaces all ordinary nodes in the base tree, including manually created nodes and Group Nodes. Group Input and Group Output nodes remain in place. A grouped function call creates a Group Node; functions it calls appear inside that group. Unused declarations add no node.
- Without top-level node-building code, it creates or updates the module's entry function node-group datablocks and their required dependencies. An entry is a function that no other function in the module calls. The declaration-only example creates groups for `small_cube` and `large_cube`. Place instances manually through Blender's **Add → Group** menu and connect their sockets as needed.

Function names beginning with `_` follow the same entry and import rules as other function names.

The mode follows the node-building operations remaining after compile-time evaluation. A call inside a function declaration does not select root replacement. Neither do imports, constants, or calls in a branch discarded at compile time. For example, compiling `FLAG = False` leaves the base tree and previously compiled function groups in place.

Compiling without top-level node-building code preserves the base tree's nodes, layout, and connections. Recompiling a function with the same name and parameter signature from the same Text updates its existing group in place. Manually placed instances keep using that group; with an unchanged interface, their sockets and links remain connected. Interface changes can cause Blender to remove incompatible links, so inspect affected instances. Removing a declaration leaves its previously compiled group available while nodes still reference it.

Switching from node-building code to declarations or constants leaves the existing base graph in place. Switching back to node-building code replaces all ordinary base-tree nodes. Repeated node-building compiles replace nodes rather than accumulate them. See [Update a script](GET_STARTED.md#update-a-script) for interface-link preservation and target requirements.

The selected Text identifies its source functions across compiles, base-tree renames, and saving and reopening the `.blend` file. Keep using the same Text when updating those functions. Copied Texts with duplicate source identities are rejected.

Create base-tree interface sockets in Blender and connect generated nodes as needed. To read an existing interface input from source, use [`node("NodeGroupInput", ...)`](BUILTINS.md#base-tree-group-input).

## Import functions

Save the first example as `geometry.nf` in **Library → Local**, or register its folder with **Add Folder...**. Import its namespace and select functions by name:

```python
from local import geometry

shape = geometry.make_cube(2.0)
raised = geometry.lift(shape, height=1.5)
```

To import one function directly, use its file path and declared name:

```python
from local.geometry import lift as raise_geometry

raised = raise_geometry(cube(1.0), height=1.5)
```

Nested `Local` files use imports such as `from local.terrain.geometry import lift`;
selected `Examples` files work similarly. Package modules are addressed at the
call site after importing the package:

```python
from packages import shapes

geo = shapes.mesh.primitives.make_cube(2.0)  # functions/mesh/primitives.nf
```

For short-name resolution and naming conflicts, see
[Importing package callables](PACKAGES.md#importing-package-callables).

Importing `geometry.nf` makes its functions available; its top-level `plain` and `raised` statements do not run in the importing script. The module namespace itself is not callable. An imported function is materialized when called. See [Imports](SYNTAX.md#imports) for the supported import forms.

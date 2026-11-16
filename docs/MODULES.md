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

- With top-level node-building code, it adds those nodes to the base tree. A function called there appears as a Group Node; functions it calls appear inside that group. Unused declarations add no node.
- Without top-level node-building code, it places the module's entry functions as Group Nodes in the base tree, even without calls. An entry is a function that no other function in the module calls. The declaration-only example places a Group Node for `small_cube` and one for `large_cube`. Required inputs on these nodes remain unlinked.

Function names beginning with `_` follow the same entry-placement and import rules as other function names.

Existing base-tree nodes remain in place. Connect the new nodes to **Group Input** and **Group Output** as needed; **Compile Script** does not create or connect the base tree's interface sockets.

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

Nested files use paths such as `from local.terrain.geometry import lift`. Selected Examples files work the same way through `examples`. Package `.nf` files are exported through their physical module paths. For
`functions/mesh/primitives.nf` declaring `make_cube`, import the package and
call `shapes.mesh.primitives.make_cube(...)`. When the declared name is unique,
`shapes.make_cube(...)` also works. See [Package Guide](PACKAGES.md#importing-package-callables)
for full-path resolution, short-name precedence and ambiguous-name diagnostics.

Importing `geometry.nf` makes its functions available; its top-level `plain` and `raised` statements do not run in the importing script. The module namespace itself is not callable. An imported function is materialized when called. See [Imports](SYNTAX.md#imports) for the supported import forms.

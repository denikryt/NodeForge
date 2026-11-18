# Writing Functions

Declare reusable functions with top-level `def` in a `.nf` module. Each parameter has a type annotation, and each function ends with a `return`. A file can declare several functions; [`.nf` Modules](MODULES.md#compose-the-base-tree) explains when their Group Nodes are placed in the base tree.

## Parameters and sockets

```python
def move(
    geometry: Geometry,
    offset: Vector["Translation"] = vector(0, 0, 1)
) -> Geometry["Moved Geometry"]:
    return transform(geometry, translation=offset)
```

Positional arguments follow parameter order. Inputs appear in that order until `panel(...)` groups or reorders them. `geometry` is required in an explicit call; `offset` has a default. The parameter identifier `offset` is also its keyword name. `Vector["Translation"]` changes only the Blender socket label. Keywords match parameter identifiers exactly, including case. Every runtime parameter needs an explicit type annotation, including parameters with defaults. The final compiler-only `__inline__` parameter may infer `Bool` from its default; see [Inline Compilation](SYNTAX.md#inline-compilation).

Function parameters support `Geometry`, `Float`, `Int`, `Bool`, `Vector`, `Color`, `Rotation`, `Material`, `Object`, `String`, and `Bundle`. You can declare defaults for `Float`, `Int`, `Bool`, `Vector`, `Color`, `Rotation`, and `String`. Parameters of `Geometry`, `Material`, `Object`, and `Bundle` are required.

A `Color` default is `(R, G, B, A)`, with four finite nonnegative numeric components. Blender stores each component as a 32-bit float. Alpha is retained independently; finite HDR values above `1.0` are valid. For example:

```python
def tint(value: Color = (0.2, 0.3, 0.4, 0.75)) -> Color:
    return value
```

`Color` values travel through group sockets and functions. Use `node()` to obtain or connect physical Color sockets; see [Raw Blender node construction](BUILTINS.md#raw-blender-node-construction).

A `Rotation` default supplies three XYZ Euler angles in radians. Each component must be a finite number representable as a 32-bit float; negative angles and values greater than a full revolution are valid. `.nf` integer literals and module constants retain the language's signed Int32 limits. The parameter and return value use Blender `NodeSocketRotation` sockets:

```python
def keep_rotation(value: Rotation = (0.0, -0.5, 7.0)) -> Rotation:
    return value
```

Runtime `Rotation` and `Vector` are distinct. To convert a runtime Euler `Vector` to a `Rotation`, use Blender's Euler to Rotation node explicitly:

```python
def as_rotation(euler: Vector) -> Rotation:
    return node("FunctionNodeEulerToRotation",
                inputs={ID("Euler"): euler}, output=ID("Rotation"), typ=Rotation)
```


```python
result = move(cube(1.0), offset=vector(0, 0, 2))
```

A `Float` parameter accepts `Float` or `Int` arguments. Other parameter types require matching argument types. A parameter without a declared default must be supplied in a source call, even if Blender gives its unlinked socket a native value.

Defaults must be known at compile time. They use the final compile-time values in the defining module, including assignments after the function declaration:

```python
DEFAULT_SIZE = 1.0

def make_cube(size: Float = DEFAULT_SIZE) -> Geometry:
    return cube(size)

DEFAULT_SIZE = 2.0
shape = make_cube()
```

For a grouped call, omitting an optional argument leaves its socket unlinked, using the declared default. An inline call materializes the same default as a typed runtime value in the caller tree. Here, the unlinked `size` input defaults to `2.0`. Constants in the calling module do not replace defaults in an imported function. Defaults cannot refer to function parameters.

Use `panel([geometry, offset], name="Transform", collapsed=True)` at the start of a function body to group its inputs in Blender's interface. Panels change socket layout while positional arguments continue to follow the function declaration. See [Interface Panels](BUILTINS.md#interface-panels).

Parameters use ordinary positional-or-keyword syntax. Positional-only parameters, keyword-only parameters, `*args`, and `**kwargs` are unsupported. Parameter names cannot replace built-ins, type names, constants such as `pi`, or compiler options `__unique__` and `__inline__`.

## Return values

End the function with exactly one `return` at the function body's indentation level. Return one value or a flat, nonempty tuple of values. Use branches to assign a result, then return it after the branches; early and branch-local returns are unsupported.

A single return annotation declares the output type; its `Type["Label"]` form also sets the output socket label. For multiple outputs, omit the return annotation and return a flat tuple. The compiler determines each output's type from its returned value.

```python
def measure(geometry: Geometry, scale: Float = 2.0):
    resized = transform(geometry, scale=vector(scale, scale, scale))
    doubled = scale * 2.0
    return resized, doubled

result, factor = measure(cube(1.0))
```

The output sockets above are **Resized** and **Doubled**, in return order. A returned variable such as `final_geometry` supplies the label **Final Geometry**. A single returned expression uses **Value**; expressions in a multiple return use **Value 1**, **Value 2**, and so on. Repeated labels receive numeric suffixes. An explicit output label must fit in 63 UTF-8 bytes.

The caller can unpack multiple outputs or select one with a compile-time integer index. A function can call other functions in its module. Recursive call cycles are not supported.

Save the module in Local to reuse its functions in other scripts. See [Import functions](MODULES.md#import-functions).

## Inline wrappers

Set the final `__inline__` default to `True` for functions whose operations should appear in the caller's tree:

```python
def increment(value: Float, __inline__=True) -> Float:
    return value + 1.0

a = increment(index())
b = increment(index(), __inline__=False)
```

The first call builds a Math node in the current tree; the second creates a Group Node. A wrapper around a single `node(...)` operation can emit that native node directly. For call-site precedence, `__unique__` interaction, and nested-call behavior, see [Inline Compilation](SYNTAX.md#inline-compilation).

## Refresh a compiled function

Edit the function in its original source, select its Group Node, and click **Reload Function from Source**. Reload reads:

- the original Blender Text datablock for a function compiled from **Text Script**;
- the current file at its registered path for a Local or Examples function;
- the current installed package source for a package function.

The original Text must still exist and have a unique source binding. Selecting another Text under **Text Script** does not change the source used by this action. **Load Script From Selected NodeGroup** copies the stored module source into the selected Text, replacing its contents; this alone does not change the function's source binding.

Reload rebuilds the selected function group in place. All Group Nodes using that shared group receive the change; a `__unique__` group retains its separate instance. Compatible links and user-edited input values are preserved.

Reload also updates inline bodies embedded in the selected grouped function. Inline calls in a base tree are refreshed by **Compile Script** or **Update Script** on their calling script. Explicit standalone creation and Library **Add Node Group** produce editable groups from definitions with `__inline__=True`.

The function name, parameter names, order, and types must match the compiled definition. Removing or renaming the definition, changing those parameters, or losing its source prevents Reload. To compile a changed signature, update the calling script and use **Compile Script** or **Update Script**. For replacement of the base graph, see [Update a script](GET_STARTED.md#update-a-script).

Blender's **Make Single User** copies the group's source binding along with its nodes. Duplicate bindings prevent NodeForge from choosing an owner when compiling or reloading that function. To keep a manually created copy independent, select that copy in a Geometry Nodes editor. In a Python Console in the same window, run the operator with that editor as its context:

```python
editor = next(area for area in bpy.context.screen.areas
              if area.type == 'NODE_EDITOR'
              and area.spaces.active.tree_type == 'GeometryNodeTree')
with bpy.context.temp_override(area=editor):
    bpy.ops.nodeforge.detach_selected_source_owner()
```

This retains the copy's graph and removes its source ownership. The original group can then be reloaded; the detached copy is edited manually and is no longer a source Reload target. For separate groups that retain source Reload, use [`__unique__=True`](SYNTAX.md#shared-and-unique-groups) when writing the calls.

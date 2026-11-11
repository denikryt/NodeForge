# NodeForge `.nf` source reference

## Compile Script

Open a Geometry Nodes editor with an existing base `GeometryNodeTree`, select a Text datablock, and use **Compile Script**. The target is `space.node_tree`, including a pinned editable base tree. Return to the base level before compiling; editing a nested group blocks both script actions. The operation appends nodes to the existing tree; it does not create a root group, change root interface sockets or connect Group Input/Output automatically. Linked/read-only roots, authoritative NodeForge function groups, transaction-private groups and Mesh Compute-managed private roots are not eligible.

Executable statements materialize ordinary Geometry Nodes at level 0. Calls to declared functions materialize `GeometryNodeGroup` instances at level 0; their implementations live in nested `GeometryNodeTree` datablocks. All declarations are collected before runtime lowering, so calls may precede their definitions.

```python
shape = move(cube(1.0), vector(0, 0, 2))

def move(geometry: Geometry, offset: Vector["Offset"] = vector(0, 0, 1)) -> Geometry:
    return transform(geometry, translation=offset)
```

The `move` declaration does **not** generate a second standalone level-0 instance when executable statements are present.

## Update Script

Open the eligible **base** Geometry Nodes tree in a Geometry Nodes editor, select the replacement Text datablock in **Text Script**, and choose **Update Script** beside **Compile Script**. The selected Text is read at each invocation. Any nonblank `.nf` source supported by direct composition can replace an existing root graph; Text selection is not persisted on the tree.

The operation retains the **physical** root `GeometryNodeTree`, its interface items, socket identifiers, defaults, unrelated custom properties, modifiers and external Group Node references. Existing physical `NodeGroupInput`/`NodeGroupOutput` nodes, their locations and direct links between structural I/O nodes also remain. All other root nodes are replaced, including manually created Group Nodes, their ordinary-to-ordinary links and their Frame hierarchy. A valid source with only compile-time statements and no ordinary output clears the old ordinary graph; a definition-only source inserts the same entry Group Nodes as Compile. Subsequent Updates replace instead of accumulating; **Compile Script** continues to append.

Update reconnects compatible **unambiguous** edges between structural I/O sockets and matching replacement nodes. When the complete ordered ordinary node shape is compatible, matching nodes inherit previous locations. With changed shapes, only individually unique node-type/function counterparts inherit locations. Other boundary edges and ambiguous layouts are skipped and left at the freshly compiled node layout. Update reports the number of skipped I/O edges; it does not migrate arbitrary manual ordinary links or automatically wire root outputs.

New ordinary nodes are staged once in the existing root alongside the old graph, then old ordinary nodes are removed. Invalid syntax, failed typing/preflight and an empty Text leave root nodes unchanged. After staging begins, nested function-group materialization may have changed shared datablocks; the existing nested-group transaction attempts rollback but does not guarantee restoration of every external link after a function signature change. Late root-cutover errors attempt local recovery using a temporary native root copy; recovery is best effort and may require Blender **Undo**. Root ordinary-node animation/drivers are not migrated: Update rejects a root whose active or NLA-strip Action FCurves or drivers address ordinary `nodes[...]` paths; animation of retained root-interface state is permitted.

Manual nested Group datablocks with no remaining users may be purged by Blender when saving and reopening a `.blend`. Update does not retain orphan groups.

## Definition-only modules

If compile-time preprocessing removes every executable top-level statement, Compile Script places a Group Node for each definition with no incoming calls from another definition in that module.

```python
def make_grid(size: Float = 2.0) -> Geometry:
    return cube(size)

def make_offset_grid(size: Float = 2.0) -> Geometry:
    return elevate(make_grid(size))

def elevate(geometry: Geometry) -> Geometry:
    return transform(geometry, translation=vector(0, 0, 1))
```

The `make_offset_grid` entry appears at level 0. Its `make_grid` and `elevate` callees appear nested inside its implementation. Because `make_grid` also has an incoming call, it is not a separate level-0 entry. If a function is independent of all other definitions, it is an additional entry. Names beginning `_` behave identically. Required entry inputs remain unlinked; automatic placement is not a DSL invocation.

Cycles between declarations, including self-recursion, are errors.

## Function signatures and outputs

Parameter order is the logical positional order. Keywords use the **exact source parameter identifiers**. Supported types use `Float`, `Int`, `Bool`, `Vector`, `Geometry`, `Rotation`, `String`, `Object`, `Material`, `Bundle`, and other forms enumerated by the current `NFType` contract. The form `Type["Socket Label"]` changes only the socket's display label. Every parameter requires an explicit type annotation. A parameter without `=` is required in an explicit DSL call; Blender's native unlinked socket value does not make it optional. A parameter with `=` has a compile-time-validated default evaluated from constants in the defining module. Parameter types are never inferred from default values, the function body or call arguments. A default cannot refer to runtime parameters, even when they share a name with a module constant. Omitting an optional parameter leaves its input socket unlinked.

```python
def translate(geo: Geometry, by: Vector["Offset"] = vector(0, 0, 1)) -> Geometry:
    return transform(geo, translation=by)

moved = translate(cube(1), by=vector(1, 0, 0))
```

Use a final `return` in each `def`. It may return one value or a nonempty flat tuple. For a single returned identifier, the output name is derived from that identifier (`return final_geometry` creates `Final Geometry`); a single expression without a returned identifier uses `Value`. Flat tuples use the same identifier naming and indexed `Value N` fallback, with duplicate-name disambiguation. An explicit scalar return annotation can override the output label with `Type["Socket Label"]`; this takes precedence over the returned variable name, and the return value must still match the annotated type. Blender node socket labels must fit in 63 UTF-8 bytes: an overlong explicit label is rejected, while an overlong inferred single-variable label falls back to `Value` for compatibility. Nested tuples and missing/multiple nonterminal returns are rejected.

```python
def move(geometry: Geometry) -> Geometry["Moved Geometry"]:
    result = transform(geometry, translation=vector(1, 0, 0))
    return result  # Blender output: Moved Geometry (explicit label wins)
```

`panel([parameter_name, ...], name="Settings", collapsed=True)` is permitted in the initial declarative prefix of a function body. It changes physical layout, not parameter identity, logical positional order or keyword spelling.

The older `input_*` and `output()` declarations do not define a function interface. Use parameters and `return`. They are also not supported as root-interface declarations in direct composition; no implicit Geometry I/O is added to top-level statements.

## Qualified modules and packages

Each `.nf` file is a module containing independently callable definitions. Importing a module binds its declarations but does not execute top-level statements or create Blender groups. Only invoked definitions are physically materialized; unused imports remain lazy.

```python
from local import geometry
shape = geometry.move(cube(1))

from local.geometry import move as relocate
other = relocate(shape, vector(0, 0, 2))

from local.terrain.geometry import deform
```

Nested directory paths select a particular file. `from local import geometry` binds the module, not a callable file. `geometry(...)`, old bare directory calls, and unqualified member searches are unsupported. Module name collisions, unknown members, invalid names and import conflicts produce diagnostics. Names beginning `_` are exported by the same rules.

Package `.nf` functions are exported independently under the package namespace. The package resolution captures source revisions before discovering exports and freezes those revisions for the entire compilation attempt.

```python
from packages import shapes

def arrange(geometry: Geometry) -> Geometry:
    return shapes.move(geometry, offset=vector(0, 0, 1))
```

### Examples file modules

A selected `.nf` file in Examples is also a definition namespace. Use its
qualified file path to import one function, or import the file namespace and
call its member. Names from different files remain distinct, even if their
filenames or function names coincide.

```python
from examples.terrain.erosion import smooth as smooth_terrain
from examples.water import erosion

def process(geometry: Geometry) -> Geometry:
    return smooth_terrain(geometry)
```

## Runtime loop counts

A runtime range count is an explicitly typed `Int` parameter. For example:

```python
def accumulate(count: Int) -> Float:
    amount = 0.0
    for i in range(count):
        amount = amount + 1.0
    return amount
```

Runtime `range(count)` compiles through a Repeat Zone; compile-time integer
ranges retain their compile-time behavior. Neither loop syntax nor a call
argument determines the type of an unannotated parameter.

A function has its own lexical bindings. Parameters and ordinary assignment
targets (including assignments under `if` or `for`) belong to that function
from entry, even when a same-named function, imported namespace or compile-time
constant exists in the module. A local read or call **before assignment** is
an error; a local Float value is not callable and never falls back to a
same-named outer function. Both runtime and compile-time analysis enforce this
rule. Source-definition aliases (`helper = function_name`) retain their
canonical callable identity once assigned, but cannot be called before the
alias declaration. Other functions still see their own module declarations.

Loop indices are narrower exceptions: each `for` target exists only inside
that loop. A runtime loop index is an Int value inside its loop, and the
enclosing binding (including a module constant) is restored afterward. A compile-time `for` instead binds each known
iteration value while its body is compiled. Compile-time assignments made by
earlier static iterations are visible to later iterations and subsequent
statements, while the loop index itself retains its lexical scope. This is
also used when resolving call-graph dependencies and definition-only entries.

### Compile-time state and source validation

Function bodies can read **unshadowed** compile-time constants in their
defining module. Each analyzed function receives its own private view of
supported mutable constant containers: a change to a list in one function
cannot silently change a sibling function or the module snapshot. Aliases
**within** one execution path still refer to the same container. For unknown
runtime `if` branches, the compiler analyzes each possible branch without
exposing its writes to the other branch, then conservatively merges facts.
Unknown runtime loops account for zero or multiple iterations; a possible
call is retained rather than erased merely because its condition is not
provably true on the first iteration. Consequently a speculative call graph
can conservatively report a dependency that proves unreachable with more
precise runtime information. Source-level recursion checks follow that
conservative graph; physical compilation remains demand-driven.

Parameter defaults use the **final** compile-time state of their own captured
module source revision, not the caller's local constants or the values at a
call's textual position:

```python
STEP = 1.0
before = offset()

def offset(value: Float = STEP) -> Float:
    return value

STEP = 9.0  # The unlinked 'before' call socket defaults to 9.0.
```

Before any direct composition or selected source Add/Reload mutates Blender
groups, the frontend validates the declaration contract of **every**
function in the selected module, including unused and `_`-prefixed ones.
This pure preflight covers explicit types, supported defaults, `panel()`,
terminal return shape, annotations and removed `input_*`/`output()` syntax.
An invalid unused declaration rejects the operation without changing an
existing Geometry Nodes tree. A valid unused definition is **not** physically
materialized merely because its declaration was checked; runtime expression
types, extension calls and other backend-specific operations are evaluated
only when that definition is demanded.

## Identity, sharing and updates

A function's source module, name and declared input-type signature determine its canonical definition identity. Ordinary repeated calls share a backing `GeometryNodeTree` when the existing ownership policy allows it. `__unique__=True` chooses a distinct function-instance owner. Updating a canonical Function Node Group recompiles it **in place**, retaining the datablock pointer and compatible links/default overrides of its Group Node users.

**Make Single User** in Blender creates an independent datablock but copies NodeForge's authoritative source-owner metadata. If the copy and original have identical metadata, both automatic canonical lookup and explicitly selected source Reload reject the ambiguity before changing either. Select the user-created copy and call the registered recovery operator `bpy.ops.nodeforge.detach_selected_source_owner()` from Blender Python. The panel does not display a Detach button. This removes only the authoritative lookup attributes; the copy's graph and generated-resource provenance are retained. The original can then be updated canonically. A detached copy is no longer an eligible canonical source Reload target. Distinct `__unique__` instance keys are not conflicting owners.

**Reload Function from Source** updates an eligible selected source-owned Function Node Group in place, preserving its complete canonical definition, declared input-type signature, instance key, owner scope and datablock pointer. Local/Examples owners use the current file at their canonical origin; package functions use the package's resolved export snapshot. A function created from a Text datablock can be reloaded automatically only while its original uniquely identified Text still exists. An arbitrary string source requires an explicit replacement source and unchanged module context. A changed or missing definition, ambiguous copy or incompatible specialization fails without modifying the selected group. This operation is distinct from Compile Script and does not selectively recompile previously placed level-0 node occurrences.

## Add-on lifecycle

Repeated enabling or disabling NodeForge does not duplicate its editor
operators, completion callback, headers or Add-menu registration. Failed
partial registration is rolled back for the resources the add-on successfully
registered; disabling does **not** delete user Geometry Nodes datablocks or
generated resources that remain referenced by the user's scene.

## Initial-attempt safety and capability limits

An initial direct composition is guarded by an ephemeral journal of the nodes created during that attempt plus the existing nested-group/resource transaction. On a pre-commit error the new level-0 nodes are removed **first**, followed by rollback of nested groups and generated resources; user-existing nodes, links and root interfaces are preserved. After successful commit the new nodes become ordinary user-managed nodes. Compile does not track earlier level-0 instances. Update Script provides separate whole-ordinary-root replacement, with an invocation-local backup and a deferred nested materialization transaction.

Top-level executable statements have semantic `ROOT` capability; ordinary
function bodies use `NESTED` regardless of placement or call depth. The **one
explicit exception** is the typed entry function passed to
`create_mesh_compute_modifier()` or selected through Local/Examples Add Node
Group for a Mesh Compute modifier: this function is compiled with `ROOT`
capability into a newly allocated, private modifier-owned GeometryNodeTree.
Functions it calls remain `NESTED`, including any attempted Mesh Compute
callback. Nested use of a root-only Mesh Compute callback is rejected before
Blender publication. The user-owned base-tree Compose and Update operations
continue to reject physical Extension API v2 implementations; it cannot
provide the private modifier ownership or rollback required by Mesh Compute.

The removed generated-root `input_*()` / `output()` source contract is **not**
restored.

## Mesh Compute compatibility

Mesh Compute is installed through an explicitly selected **typed function**.
Its first parameter must be a required `Geometry` input; the function must
return exactly one `Geometry` value. Other inputs use ordinary typed function
parameters and defaults, which become Geometry Nodes modifier sockets.
The source must have exactly one entry function and cannot contain executable
root statements. Unused Python runtime imports do not install a modifier.

```python
from local import shift

def modifier(geometry: Geometry["Geometry"], amount: Float["Amount"] = 2.0) -> Geometry:
    return shift(geometry, amount=amount)
```

`shift` here is a separately registered Local Python callback decorated with
`@mesh_compute`. Use `create_mesh_compute_modifier(source, mesh_object)` or
**Install Mesh Compute** for a selected Text block, or select the `.nf` entry
under Local/Examples and choose **Add Node Group** with a Mesh Object active.
These operations create or reuse only an empty modifier and install a private
runtime root. Subsequent changes use **Reload Compute**, retaining the existing
transactional modifier-input and callback cutover. A saved `.blend` does not
persist trusted Python execution: reload the source after reopening.

**Compile Script** still targets an existing user-owned GeometryNodeTree
and never installs Mesh Compute implicitly; ordinary nested function groups
cannot contain root-only callbacks.

# NodeForge `.nf` source reference

## Compile Script

Open a Geometry Nodes editor with an existing base `GeometryNodeTree`, select a Text datablock, and use **Compile Script**. The target is `space.node_tree`, including when the editor is viewing a nested group. The operation inserts nodes into the existing tree; it does not create a root node group, change root interface sockets or connect Group Input/Output automatically.

Executable statements materialize ordinary Geometry Nodes at level 0. Calls to declared functions materialize `GeometryNodeGroup` instances at level 0; their implementations live in nested `GeometryNodeTree` datablocks. All declarations are collected before runtime lowering, so calls may precede their definitions.

```python
shape = move(cube(1.0), vector(0, 0, 2))

def move(geometry: Geometry, offset: Vector["Offset"] = vector(0, 0, 1)) -> Geometry:
    return transform(geometry, translation=offset)
```

The `move` declaration does **not** generate a second standalone level-0 instance when executable statements are present.

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

Use a final `return` in each `def`. It may return one value or a nonempty flat tuple. For a single value, the output is named `Value`; for tuple values, output names derive from returned identifiers or indexed `Value N` labels, with duplicate-name disambiguation. A scalar return annotation is checked against its resulting type. Nested tuples and missing/multiple nonterminal returns are rejected.

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

**Make Single User** in Blender creates an independent datablock but copies NodeForge's authoritative source-owner metadata. If the copy and original have identical metadata, both automatic canonical lookup and explicitly selected source Reload reject the ambiguity before changing either. Select the user-created copy and click **Detach Selected Copy**. This removes only the authoritative lookup attributes; the copy's graph and generated-resource provenance are retained. The original can then be updated canonically. A detached copy is no longer an eligible canonical source Reload target. Distinct `__unique__` instance keys are not conflicting owners.

**Reload Function from Source** updates an eligible selected source-owned Function Node Group in place, preserving its complete canonical definition, declared input-type signature, instance key, owner scope and datablock pointer. Local/Examples owners use the current file at their canonical origin; package functions use the package's resolved export snapshot. A function created from a Text datablock can be reloaded automatically only while its original uniquely identified Text still exists. An arbitrary string source requires an explicit replacement source and unchanged module context. A changed or missing definition, ambiguous copy or incompatible specialization fails without modifying the selected group. This operation is distinct from Compile Script and does not selectively recompile previously placed level-0 node occurrences.

## Add-on lifecycle

Repeated enabling or disabling NodeForge does not duplicate its editor
operators, completion callback, headers or Add-menu registration. Failed
partial registration is rolled back for the resources the add-on successfully
registered; disabling does **not** delete user Geometry Nodes datablocks or
generated resources that remain referenced by the user's scene.

## Initial-attempt safety and capability limits

An initial direct composition is guarded by an ephemeral journal of the nodes created during that attempt plus the existing nested-group/resource transaction. On a pre-commit error the new level-0 nodes are removed **first**, followed by rollback of nested groups and generated resources; user-existing nodes, links and root interfaces are preserved. After successful commit the new nodes become ordinary user-managed nodes. NodeForge does not track, match or selectively update earlier inserted level-0 instances, and no repeated direct-compilation policy is defined.

Top-level executable statements have semantic `ROOT` capability; **every** function body is `NESTED` regardless of placement or call depth. Used root-only Mesh Compute callbacks cannot run inside a nested function. The compiler rejects physical Extension API v2 implementations called directly at level 0 **before invoking their backend**, because extension code may mutate unrelated user-owned nodes. Supported physical extensions can execute within transaction-owned nested Function Node Groups with appropriate resource transactions. Unused imported extensions do not require physical installation.

Unsupported combinations are errors; the old generated-root modifier/compiler route is not restored.

## Mesh Compute compatibility

Root-only Mesh Compute callbacks require a modifier installation contract that
the initial base-tree composition operation does not provide. Their implementation
and generated-resource transaction infrastructure remain in NodeForge, but the
existing root-only workflow and the bundled erosion example must be migrated
separately before they can be used with the unified `.nf` language.

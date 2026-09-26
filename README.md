# NodeForge

NodeForge is a Blender add-on for describing Geometry Nodes logic in a Python-like language. The source is compiled into a native Geometry Nodes group, so the result inside Blender is an ordinary node graph with the expected sockets, links and parameters.

The language is intended to make procedural logic easier to express and maintain as the graph grows. Mathematical relationships can be written directly as expressions, while Geometry Nodes operations are available through functions that fit naturally into Python-like code. The source therefore stays close to the logic of the setup and can remain readable even when the generated node graph becomes large.

NodeForge also works well with AI-generated code. An AI model can describe the Geometry Nodes logic in the same high-level language, while NodeForge handles the Blender-specific work needed to build the final node graph. This keeps generated code simpler and gives the AI fewer Blender API details to get wrong.

NodeForge scripts can call reusable functions and can be extended through installable third-party packages. This allows project-specific operations and larger procedural components to become part of the language used by other scripts.

The syntax follows Python as closely as the Geometry Nodes model allows. NodeForge adds a set of functions and language rules for concepts that are specific to Geometry Nodes, while ordinary expressions and control flow retain familiar Python syntax.

The built-in function library currently covers only part of Geometry Nodes. When a dedicated NodeForge function is not available yet, `node(...)` can create the Blender node directly. It accepts the Blender node type together with its inputs, properties and output declaration, so the same language can still reach nodes that do not yet have a dedicated wrapper.

For example, the Transform Geometry node can be written through the dedicated `transform(...)` function:

```python
size = input_float("Size", default=2.0)
height = input_float("Height", default=1.0)

geo = transform(cube(size), translation=vector(0, 0, height))
output("Geometry", geo)
```

The same Blender node can be created through `node(...)`:

```python
size = input_float("Size", default=2.0)
height = input_float("Height", default=1.0)

geo = cube(size)

geo = node(
    "GeometryNodeTransform",
    inputs={
        "Geometry": geo,
        "Translation": vector(0, 0, height),
    },
    output="Geometry",
    typ=Geometry,
)

output("Geometry", geo)
```

The dedicated function is the more convenient form when NodeForge provides one. `node(...)` keeps the rest of Geometry Nodes available while the built-in library continues to grow.

### Python extension packages

Installable packages can add typed callables through extension API v2. A package declares its public callable signatures and frozen semantic record types in declaration-only `interface.py`; because that module has canonical cross-phase identity, it does not import other owner-local package modules. Backend-only callables map directly to lazy owner-local physical implementations; semantic-capable callables may additionally use `semantic.py` to construct package-defined frontend values or normalize private state before backend realization. All forms use the permanent semantic analysis and typed Call IR pipeline rather than compiler internals.

System extensions may expose several callables. Native library extensions expose one callable under their catalog name and become visible through the existing library import syntax. Physical implementations receive a small Blender-facing context and use the existing transaction-owned generated Mesh, Curve, and Object boundary. Package-defined semantic records can be composed directly between extension calls or stored in source variables. Runtime values retained inside stored semantic state are snapshotted through compiler-owned hidden bindings, while package semantic lists remain distinct from NodeForge structural arrays.

Package authors can use [`dev/EXTENSION_API_V2.md`](dev/EXTENSION_API_V2.md) for declaration syntax, evaluation modes, semantic records, `RuntimeRef`, semantic/backend execution forms, owner-session lifecycle, backend context, and generated-resource contracts.

### Numeric values

NodeForge keeps integer and floating-point values distinct. An integer literal such as `1` is `Int`; a decimal literal such as `1.0` is `Float`; `True` and `False` are `Bool`, a separate semantic type from numeric `Int` and `Float`. `Int` uses the signed 32-bit domain. Statically known values outside that domain are rejected before they are materialized.

Arithmetic is type-directed. Integer-preserving `+`, `-`, `*`, `//` and `%` operations on two `Int` values return `Int`. `/` and `**` return `Float`, and arithmetic with either operand already `Float` returns `Float`. `//` uses floor division and `%` is its floored-remainder partner, including for negative operands.

```python
i = index()
left = i - 1          # Int
cell = i // 4         # Int
column = i % 4        # Int
ratio = i / 4         # Float
scaled = ratio + 0.5  # Float
```

Known NodeForge `Float` values use Blender-compatible binary32 representation. Compile-time values such as `0.1`, `pi`, `tau`, `e`, Float defaults and supported compile-time arithmetic therefore use the same canonical Float representation expected by Geometry Nodes. Compile-time evaluation and runtime graph removal remain separate: knowing a numeric value does not by itself remove its Geometry Nodes operation.

Variables carried by `repeat_range()` keep one semantic type for the full Repeat lifetime. Initialize a carried value with `1.0` when the loop state is intended to be `Float`; assigning a `Float` result to an `Int` carried state, or an `Int` result to a `Float` carried state, is a compile error. An `Int` carried state remains `Int` after the Repeat and continues to participate in integer-preserving arithmetic as `Int`.

Named mathematical callables such as `sin()`, `cos()` and `sqrt()` are supplied by the separate `nodeforge.math` package. Core compile-time numeric semantics cover language operators and structural helpers; package-call compile-time evaluation belongs to the package protocol when that capability is added.

### How the compiler works

Internally, NodeForge parses the source, resolves its types and operations, builds a semantic intermediate representation, and lowers that representation into Blender nodes. The compiler owns the translation from source-level logic to the final `GeometryNodeTree`, keeping the language-facing part of the system separate from Blender graph construction.

Numeric result typing and canonical Int/Float value semantics are owned by the Blender-independent `numeric_semantics` layer. Compile-time numeric evaluation consumes those same language rules, while runtime graph removal remains a separate fail-closed optimization decision. Blender lowering receives already-typed Semantic IR and selects the corresponding Integer Math, Math, Vector Math, or Compare realization without redefining source-level numeric types.

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

Ordinary statement `if` is Geometry Nodes runtime control flow. Its condition must be a runtime `Bool`, both branches are semantically valid runtime branches and both contribute to the generated graph and interface dependencies. A compile-time-known or literal condition such as `True` or `False` does not remove either branch. Top-level ordinary `if` currently requires an explicit `else` and follows the normal runtime merge rules.

### Learn more in the documentation: 
https://denikryt.github.io/NodeForgeDocs/

### Support the developer:
https://www.patreon.com/c/nachitima

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

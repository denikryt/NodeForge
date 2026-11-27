# Core Concepts

## Compilation

NodeForge compiles a small, Python-like language for describing Blender Geometry Nodes graphs. It reads `.nf` code with Python's parser and uses the resulting syntax tree (AST) to understand calls, operators, and statements such as `def`, `for`, and `return`. NodeForge builds the graph. Blender runs it.

## Nodes and links

Think of a node as a function. Its arguments are input sockets. Its results come from output sockets. Pass a result from one call to another and NodeForge connects the two sockets. Arithmetic works the same way. A multiplication that depends on a graph value becomes a Math node.

## Compile-time and runtime values

- **Compile-time values** are known while NodeForge builds the graph. In a parameter default, for example, NodeForge can calculate `2 + 2` and use `4` directly. A compile-time loop count can tell NodeForge how many times to build the same set of nodes.
- **Runtime values** come from the graph, for example from a group input or a node output. A variable can name one of these values. Blender uses the current values when it runs the graph.

## Group inputs and outputs

A function defined with `def` describes a node group. The typed parameters become its inputs, the body describes the nodes and links inside, and `return` defines the outputs.

A call normally creates a Group Node. With `__inline__=True`, the nodes go directly into the caller's tree. That can be the main tree or another group. See [Writing Functions](WRITING_FUNCTIONS.md).

## Modules and functions

A `.nf` file is a module. It can define functions and build nodes at the top level. Importing functions from another module skips that module's top-level node-building code.

**Compile Script** has two modes.

- If the top-level code builds nodes, NodeForge replaces the ordinary nodes in the main tree, including nodes you added by hand. It keeps the tree's Group Input and Group Output nodes and its interface.
- If the script builds no nodes at the top level, existing nodes stay in place.

In both modes, a function that neither the top-level code nor another function in the module calls gets its own Group Node in the main tree.

When NodeForge updates a group, it keeps links to its sockets where it can match compatible sockets. See [Modules](MODULES.md#compose-the-base-tree) and [Updating a script](GET_STARTED.md#update-a-script).

## Libraries

Packages add functions to the language. They can provide `.nf` functions or Python functions that run during compilation. The [Extension API](EXTENSION_API.md) lets Python functions declare typed inputs and results and gives them a context for building nodes and links.

NodeForge comes with Nodes, a library that makes Blender's Geometry Nodes available as functions in `.nf`. Each function represents one Blender node. The goal is to cover most nodes and let you use as many of their configurations as possible. The library is generated automatically from Blender's Python API. See the [Nodes Library guide](NODES_GUIDE.md).

## Editor support

In Blender's Text Editor, IntelliSense suggests functions, keyword arguments, and fields of named results.

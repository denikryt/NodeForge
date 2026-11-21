# Core Concepts

## Compilation

NodeForge compiles `.nf` source into Blender Geometry Nodes: function calls and expressions create nodes, and the values passed between them create links. Blender evaluates the generated graph to produce geometry or other output values. Changing a group input makes Blender reevaluate the graph. To change its nodes or connections through source code, edit the source and compile it again.

## Compile-time and runtime values

**Compile-time values** are known while NodeForge builds the graph. They can choose node options, function defaults, and how many copies of a graph fragment to create. They do not become adjustable sockets by themselves.

**Runtime values** belong to the generated graph. They flow through sockets and fields, so Blender can evaluate the same graph with different values without recompiling the source.

```python
COPIES = 3

def cube_row(spacing: Float = 1.5) -> Geometry:
    parts = []
    for i in range(COPIES):
        part = transform(
            cube(1.0),
            translation=vector(i * spacing, 0, 0),
        )
        parts.append(part)
    return join(parts)
```

`COPIES` and the loop index `i` are compile-time values: compilation creates three Cube and Transform paths. `spacing` is a runtime `Float` input connected to those paths. Changing **spacing** on the Group Node makes Blender reevaluate the existing graph.

A source variable such as `spacing` names a typed value in the graph; it is not the current numeric value on a Group Node. A `for` loop with a runtime `Int` count creates a Repeat Zone instead of unrolling. An `if` with a runtime condition creates a runtime branch. Inside a function, a compile-time Boolean condition selects the branch during compilation. See [For Statements](SYNTAX.md#for-statements) and [If Statements](SYNTAX.md#if-statements).

## Group inputs and outputs

A typed function's parameters define its Group Node input sockets. Their types determine which values can be connected; defaults provide initial values. Its `return` defines the output socket or sockets. In `cube_row`, `spacing: Float = 1.5` creates a **spacing** input, and `return join(parts)` supplies a Geometry output.

Each Group Node instance can have different input values or incoming links. Changing an input reevaluates the graph; editing the function source and recompiling changes the graph itself. See [Writing Functions](WRITING_FUNCTIONS.md) for parameters, labels, and multiple outputs.

## Modules and functions

A `.nf` file is a module. It can declare several functions, create nodes directly with top-level statements, or combine both. Grouped function calls create Group Nodes; functions can call other functions. A module without top-level node-building code creates or updates entry function groups for manual placement. See [`.nf` Modules](MODULES.md#compose-the-base-tree) for compilation modes and examples.

## Imports

Import functions from Local files, Examples, or installed packages to use them in another module. Importing a module makes its functions available without running its top-level node-building statements. See [Imports](SYNTAX.md#imports) for the supported forms.

## Native Blender data

Compiled nodes, groups, sockets, and links are Blender data. They can be inspected and saved in a `.blend` file; the generated graph continues evaluating when the NodeForge add-on is disabled. For compiling and updating a script in Blender, see [Get Started](GET_STARTED.md).

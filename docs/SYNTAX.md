# DSL Syntax and Semantics

NodeForge uses a Python-like language for building Blender Geometry Nodes. A `.nf` file is a [module](MODULES.md). Top-level statements build nodes in the open base tree; typed functions define reusable node groups. Expressions and function calls describe the operations and connections in those trees.

Read [Core Concepts](CORE_CONCEPTS.md) for the relationship between source code, group sockets, compile-time structure, and runtime Geometry Nodes values.

## Names and Assignments

An assignment binds a name to a value.

```python
size = 2.0
```

The assigned name can be used in later statements.

```python
width = 2.0
height = width
```

Assigning another value to the same name replaces the previous value.

```python
size = 1.0
size = 2.0
```

Use a single name for an ordinary assignment. Flat tuple or list targets unpack multiple-output results; see [Multiple Function Outputs](#multiple-function-outputs).

### Function Scope

Function parameters and assignment targets are local to that function. A local name must be assigned before it is read, including when a module constant or function has the same name. Functions can read module constants that their local names do not shadow:

```python
FACTOR = 2.0

def scale(value: Float) -> Float:
    result = value * FACTOR
    return result
```

## Literals

NodeForge supports integer, floating-point, Boolean, and string literals.

```python
count = 4
scale = 0.5
enabled = True
name = 'Height'
```

Strings can use single or double quotes. Standalone `None` values are unsupported.

## Value Types

NodeForge supports runtime socket values and compile-time values.

### Runtime Types

| Type | Description |
| --- | --- |
| `Geometry` | Geometry socket value. |
| `Float` | Floating-point scalar or field. |
| `Int` | Signed 32-bit integer scalar or field. Integer-preserving arithmetic remains `Int`; mixed `Int`/`Float` arithmetic becomes `Float`. |
| `Bool` | Boolean scalar or field. |
| `Vector` | Three-component vector or field. |
| `Material` | Blender material socket value. |
| `Object` | Blender object socket value with Object Info properties. |
| `String` | Blender string socket value. |
| `Bundle` | Blender bundle socket containing named runtime values. |

### Compile-Time Types

| Type | Description |
| --- | --- |
| Integer | Whole number. |
| Float | Floating-point number. |
| Boolean | `True` or `False`. |
| String | Text value. |
| Vector | Three-component value. `vector(...)` is compile-time only when all arguments are compile-time; runtime arguments produce a runtime `Vector`. |
| List | Ordered mutable collection. |
| Tuple | Ordered collection written with parentheses. |

## Function Calls

A function call uses a function name followed by parentheses.

```python
direction = vector(1, 0, 0)
```

Arguments can be positional or named. Assign the returned value to use it in later statements:

```python
first = cube(1.0)
second = cube(size=2.0)
```

Calls can be nested.

```python
geometry = transform(cube(size=2.0), translation=vector(0, 0, 1))
```

Assigning intermediate values is usually easier to read.

```python
base = cube(size=2.0)
offset = vector(0, 0, 1)
geometry = transform(base, translation=offset)
```

Calls can target a simple callable name or an imported package member, such
as `math.sin(...)` or `shapes.mesh.primitives.make_cube(...)`. Intermediate
package directories and modules are qualifiers, not runtime values. Supported
method calls are documented in the sections that introduce them.

## Operators and Expressions

Expressions combine values and produce a result.

| Syntax | Meaning |
| --- | --- |
| `a + b` | Addition. |
| `a - b` | Subtraction. |
| `a * b` | Multiplication. |
| `a / b` | Division. |
| `a // b` | Floor division. |
| `a ** b` | Power. |
| `a % b` | Modulo. |
| `-a`, `+a` | Unary sign. |
| `not a` | Boolean NOT. |
| `a and b`, `a or b` | Boolean AND and OR. |
| `a < b`, `a <= b`, `a > b`, `a >= b`, `a == b`, `a != b` | Comparisons. |
| `x if condition else y` | Conditional expression. |

```python
width = 2.0
height = 3.0
area = width * height
is_large = area > 5.0
result = area if is_large else 0.0
```

Operations are type-checked. The operands must support the selected operation.

Numeric semantics follow NodeForge's runtime types. `Int + Int`, `Int - Int`, `Int * Int`, `Int // Int`, and `Int % Int` preserve signed 32-bit `Int`. `/` and `**` produce `Float`, and an arithmetic expression mixing `Int` with `Float` produces `Float`. Floating-point constants and operations use Blender-compatible binary32 behavior. Vector arithmetic accepts numeric scalar operands where documented by the operation.

## Compile-Time and Runtime Values

Compile-time values are known while NodeForge builds the graph. They can set function defaults, node options, and the number of iterations in an unrolled loop. Function parameters are runtime values: Blender supplies their values through group sockets when evaluating the graph.

```python
DEFAULT_HEIGHT = 1.0

def scale_height(height: Float = DEFAULT_HEIGHT) -> Float:
    return height * 2.0
```

`DEFAULT_HEIGHT` supplies the initial value of the **Height** input. The parameter `height` remains a runtime value, and its multiplication becomes a graph operation.

The operation receiving a value determines whether it must be known at compile time. For example, `range(3)` unrolls a loop, while `range(count)` with a runtime `Int` parameter creates a Repeat Zone. See [For Statements](#for-statements).

See [Core Concepts](CORE_CONCEPTS.md#compile-time-and-runtime-values) for the evaluation model and [Compile-Time Helper Calls](BUILTINS.md#compile-time-helper-calls) for `len(...)` and `sum(...)`.

## Strings and F-Strings

String literals can supply compile-time names and options:

```python
attribute_name = 'height'
geometry = store_named_attribute(cube(), attribute_name, position().z)
```

Use an f-string to combine literal text and compile-time strings:

```python
prefix = 'Generated'
attribute_name = f'{prefix} Height'
geometry = store_named_attribute(cube(), attribute_name, position().z)
```

String concatenation with `+` is unsupported.

Runtime interpolation, non-string interpolation, conversion flags, and format specifications are unsupported.

A typed `String` parameter creates a runtime String socket. Pass it to operations that accept runtime strings, such as named attribute operations and Bundle paths:

```python
def store_height(geometry: Geometry, name: String = 'height') -> Geometry:
    return store_named_attribute(geometry, name, position().z)
```

A runtime string cannot supply an option that requires a compile-time string, such as an attribute domain.

## Lists and Tuples

Lists and tuples are ordered collections that NodeForge uses while compiling the source. Blender has no corresponding List or Tuple socket type. The compiler keeps track of the elements and uses them to build individual nodes, links, and socket values.

Write a list with square brackets and a tuple with parentheses:

```python
sizes = [0.5, 1.0, 2.0, 4.0]
limits = (0.0, 1.0)
```

These collections contain compile-time numbers. They can supply values for individual operations or iterations of a compile-time loop.

A list or tuple of three compile-time numbers is also accepted as a Vector in graph expressions. In that context, its component indices are `0`, `1`, and `2`. Use `vector(x, y, z)` to write a vector explicitly.

### Collections of Graph Values

Elements can also refer to runtime values, including geometry and fields. The compiler knows which values belong to the collection, while Blender evaluates those values through their individual sockets.

```python
first = cube(size=1.0)
second = cube(size=2.0)
parts = [first, second]
geometry = join(parts)
```

The two `cube(...)` calls create two Cube nodes. `parts` groups references to their Geometry outputs. Creating the list adds no node. `join(parts)` creates a Join Geometry node and connects both Cube outputs to its Geometry input. Its result is one Geometry value.

An operation determines how it uses a collection. `join(...)` accepts a collection of Geometry values; each element must be Geometry. A collection itself cannot be connected to a group socket or used as a typed function parameter. Select an element, combine the elements with an operation such as `join(...)`, or expose them as separate function outputs.

### Selecting Elements

Use a compile-time integer index to select an element. Indices start at `0`; negative indices count from the end:

```python
sizes = [0.5, 1.0, 2.0, 4.0]
smallest = sizes[0]
largest = sizes[-1]
geometry = cube(size=largest)
```

Selecting a collection element happens during compilation. For a collection of graph values, it chooses which existing value to use; it adds no runtime selection node. A runtime `Int` parameter cannot index a list or tuple.

### Building a List

Start with `[]` and use `append(value)` to add one element. A compile-time loop can build a collection of graph values:

```python
parts = []
for size in [0.5, 1.0, 2.0]:
    parts.append(cube(size=size))
geometry = join(parts)
```

Compilation unrolls the three iterations, creates three Cube nodes, and connects their outputs to a Join Geometry node. `append(...)` changes the collection used to build the graph. It adds no Blender node and does not append geometry while Blender evaluates the graph.

Assigning a list to another name gives both names access to the same collection:

```python
parts = [cube(1.0)]
alias = parts
alias.append(cube(2.0))
geometry = join(parts)
```

Here, `parts` contains both Geometry values after the append. Assigning a new list to either name replaces that name's binding.

A list of compile-time constants cannot be extended with runtime values. Start with `[]` or a list of graph values when collecting runtime results. Module-level constant lists are copied for each function's compilation; changes inside one function do not change the list seen by another function.

Appending to a collection inside runtime `if` branches or Repeat Zones is unsupported. To accumulate geometry in a runtime loop, use [Geometry Builder](#geometry-builder) or a Geometry repeat-state value; see [Runtime Loops](#runtime-loops).

### Multiple Function Outputs

A function returning several values creates a separate output socket for each value. The caller receives a fixed tuple-like result identifying those outputs:

```python
def make_part(size: Float = 1.0):
    geometry = cube(size)
    doubled = size * 2.0
    return geometry, doubled

part, doubled_size = make_part(2.0)
```

The generated Group Node has a Geometry output and a Float output. Unpacking assigns a source name to each output; the number of names must match the number of outputs. The tuple-like result is not passed through a Blender socket as a single value.

Assignment unpacking supports multiple-output call results. Unpacking an ordinary list or tuple literal in an assignment is unsupported. Compile-time loops support unpacking their items; see [Compile-Time Loops](#compile-time-loops).

The caller can also retain the result and select one output with a compile-time index:

```python
captured = capture_attribute(cube(), position().z)
geometry = captured[0]
captured_height = captured[1]
```

See [Return Values](WRITING_FUNCTIONS.md#return-values) for function return rules.

## Attribute Access and Indexing

Vector components are available through `.x`, `.y`, and `.z`.

```python
direction = vector(1, 2, 3)
x = direction.x
```

Vectors also support compile-time integer indices `0`, `1`, and `2`.

```python
direction = vector(1, 2, 3)
y = direction[1]
```

For list and tuple indexing, see [Selecting Elements](#selecting-elements).

`Object` values provide `.geometry`, `.location`, `.rotation`, and `.scale`. Configure their Object Info node with `.info(...)` before the first property access. See [Object Values](BUILTINS.md#object-values).

## Comments

A comment starts with `#` and continues to the end of the line.

```python
# Store the initial size.
size = 2.0
```

## Group Inputs and Outputs

A function's typed parameters define its group input sockets. Its final `return` defines its output socket or sockets.

```python
def move(
    geometry: Geometry,
    offset: Vector["Translation"] = vector(0, 0, 1)
) -> Geometry["Moved Geometry"]:
    return transform(geometry, translation=offset)
```

`geometry` is required in an explicit call, and `offset` has a compile-time default. The `Type["Label"]` form sets the displayed socket label. Keyword arguments use the source parameter name, such as `offset=...`.

Each parameter requires an explicit type annotation. A single return annotation is optional; when supplied, it specifies the output type and can set its label. See [Writing Functions](WRITING_FUNCTIONS.md) for supported defaults and output naming.

Top-level statements create nodes in the base tree. Connect them to the base tree's **Group Input** and **Group Output** in Blender. Top-level `input_*()` and `output()` declarations and implicit inputs are unsupported.

### Interface Panels

Use `panel(...)` at the start of the function body to group its input sockets:

```python
def shape(radius: Float = 1.0, segments: Int = 32) -> Geometry:
    panel([radius, segments], name='Shape', collapsed=True)
    return cube(size=radius)
```

Panel members are parameter names. Each input can belong to one panel. See [Interface Panels](BUILTINS.md#interface-panels) for the complete reference.

## Reusable Functions

Declare functions with top-level `def`. A function can call other functions from its module, including declarations that appear later in the file.

```python
def double(value: Float = 1.0) -> Float:
    return value * 2.0

def quadruple(value: Float = 1.0) -> Float:
    return double(double(value))

result = quadruple(3.0)
```

Each function ends with one `return`, which returns a value or a flat, nonempty tuple. Nested function declarations and recursive call cycles are unsupported.

```python
def inspect(value: Float, pos: Vector):
    return value * 2.0, pos.z, value > 0.0

doubled, height, positive = inspect(2.0, vector(0, 0, 3))
```

Multiple outputs can be unpacked in return order or selected with a compile-time integer index:

```python
def measure(value: Float):
    return value * 2.0, value * 3.0

results = measure(2.0)
tripled = results[1]
```

See [Writing Functions](WRITING_FUNCTIONS.md) for function interfaces and [Modules](MODULES.md#compose-the-base-tree) for automatic placement of functions when compiling a module containing only declarations.

### Shared and Unique Groups

Repeated calls share a backing function group by default. Set `__unique__=True` when a call needs its own editable group:

```python
def remap(value: Float) -> Float:
    return value * 2.0

first = remap(1.0, __unique__=True)
second = remap(2.0, __unique__=True)
```

Each marked call uses a separate group. `__unique__` accepts a compile-time `Bool`; omitting it or passing `False` uses the shared group. The modifier is supported by functions declared in the current module, imported `.nf` functions, and source-backed package functions. Core built-ins, Python extension callables, and `node(...)` do not support it.

The separate group is shallow: functions called inside it remain shared unless their own calls also use `__unique__=True`.

## Imports

Write imports at the top level. NodeForge provides the `local`, `examples`, and `packages` namespaces. Importing a `.nf` module makes its functions available; its top-level node-building statements do not run in the importing script.

### Local

Local imports select `.nf` files saved in **Library → Local** or registered with **Add Folder...**. For example, save this module as `geometry.nf`:

```python
def lift(geometry: Geometry, height: Float = 1.0) -> Geometry:
    return transform(geometry, translation=vector(0, 0, height))
```

Import the file namespace and call its declared function:

```python
from local import geometry

raised = geometry.lift(cube(1.0), height=2.0)
```

Import one function by extending the path with the file name. Use `as` to choose a local name:

```python
from local.geometry import lift as raise_geometry

raised = raise_geometry(cube(1.0), height=2.0)
```

For a file at `terrain/geometry.nf`, include its directory path:

```python
from local.terrain import geometry as terrain_geometry
from local.terrain.geometry import lift

first = terrain_geometry.lift(cube(1.0))
second = lift(cube(2.0))
```

The file namespace itself is not callable: `geometry(...)` is unsupported. Use `geometry.lift(...)` or import `lift` directly. See [Import Functions](MODULES.md#import-functions) for saving modules to Local.

### Examples

Examples `.nf` files use the same file and function import forms. For an example stored as `terrain/geometry.nf` with a `lift` function:

```python
from examples.terrain import geometry as terrain_geometry
from examples.terrain.geometry import lift

first = terrain_geometry.lift(cube(1.0))
second = lift(cube(2.0))
```

Use the path and declared function names of the selected example file.

### Packages

Import an installed package namespace, then call its members:

```python
from packages import math

def sine(angle: Float = 0.0) -> Float:
    return math.sin(angle)
```

Package namespaces can be aliased:

```python
from packages import math as m

def sine(angle: Float = 0.0) -> Float:
    return m.sin(angle)
```

Qualified calls identify the package that supplies a callable. A member can also be called without its namespace when exactly one imported package provides that name and no source binding or Core built-in takes precedence:

```python
from packages import math

def sine(angle: Float = 0.0) -> Float:
    return sin(angle)
```

If several imported packages provide the name, the unqualified call is ambiguous. Use a qualified call such as `math.sin(angle)` to select its owner.

Package `.nf` functions can also be called by their complete physical module
paths. For `functions/mesh/primitives.nf` with `def make_cube`, use:

```python
from packages import shapes

geo = shapes.mesh.primitives.make_cube(2.0)
```

An unambiguous short alias `shapes.make_cube(...)` resolves to the same
function. With duplicate short names, NodeForge reports the available full
paths. Direct imports such as `from packages.shapes.mesh import primitives`
are unsupported; first import the package with `from packages import shapes`.
See [Package Guide](PACKAGES.md#importing-package-callables) for category
paths and collision rules and [Python Extension API v2](EXTENSION_API.md) for
Python-backed package callables.

Star imports, plain Python `import`, relative imports, imports inside functions, and imports from arbitrary Python modules are unsupported.

Experimental Python Mesh Compute callbacks use explicit Local or Examples imports in a typed entry module. See [Mesh Compute](MESH_COMPUTE.md) for the callback and modifier workflow.

## If Statements

An `if` with a runtime `Bool` condition selects between graph values through Switch nodes. Assign the result in both branches:

```python
def choose(use_large: Bool = False) -> Geometry:
    if use_large:
        size = 2.0
    else:
        size = 1.0
    return cube(size=size)
```

Both runtime branches must be valid. Outside a runtime loop, `else` is required, and both branches must assign at least one common runtime result. Merged values must have the same type: `Float`, `Int`, `Vector`, `Bool`, `Geometry`, `String`, or `Bundle`.

Inside a runtime loop, an `if` can update existing loop state without `else`. The unchanged state passes through when the condition is false:

```python
def count_even(steps: Int = 5) -> Int:
    count = 0
    for i in range(steps):
        if i % 2 == 0:
            count = count + 1
    return count
```

### Compile-Time Conditions in Functions

Inside a function, an `if` whose condition resolves to a compile-time `Bool` compiles only the selected branch. This can choose which operations the function contains:

```python
USE_LARGE = True

def fixed_shape() -> Geometry:
    if USE_LARGE:
        geometry = cube(2.0)
    else:
        geometry = cube(1.0)
    return geometry
```

At module level, an ordinary `if` uses runtime branch semantics even with a literal Boolean condition. Both branches must be valid and satisfy the runtime merge rules.

## For Statements

### Compile-Time Loops

A `for` over a compile-time list, tuple, or `range(...)` unrolls while the module is compiled. Each iteration creates its own graph operations:

```python
parts = []
for size in [0.5, 1.0, 1.5]:
    parts.append(cube(size=size))
geometry = join(parts)
```

`range(stop)`, `range(start, stop)`, and `range(start, stop, step)` accept compile-time integers. The stop value is exclusive:

```python
parts = []
for i in range(0, 6, 2):
    part = transform(cube(1.0), translation=vector(i, 0, 0))
    parts.append(part)
geometry = join(parts)
```

A compile-time loop target can be a single name or a flat list or tuple pattern matching each item:

```python
positions = [(0, 0), (1, 0), (0, 1)]
parts = []
for x, y in positions:
    point = points(1)
    point = set_position(point, vector(x, y, 0))
    parts.append(point)
geometry = join(parts)
```

### Runtime Loops

Inside a typed function, `range(count)` with a runtime `Int` count creates a Blender Repeat Zone:

```python
def accumulate(count: Int = 5) -> Float:
    value = 0.0
    for i in range(count):
        value = value + 1.0
    return value
```

`repeat_range(count)` explicitly selects a runtime loop and also accepts a compile-time count:

```python
def accumulate() -> Float:
    value = 0.0
    for i in repeat_range(5):
        value = value + 1.0
    return value
```

Values defined before the loop and changed inside it become repeat state. State values can be `Geometry`, `Vector`, `Float`, `Int`, `Bool`, or `Bundle`. Each state must retain its exact type throughout the loop, including assignments in nested branches. For example, an `Int` state cannot become `Float`.

A runtime loop must update at least one existing value or geometry builder. Names first defined inside the loop are iteration-local temporaries. The runtime loop target is a single name holding the iteration's `Int` index.

Use `repeat_range(...)` for nested runtime loops. Each creates its own Repeat Zone and index:

```python
def accumulate_grid(rows: Int = 3, columns: Int = 4) -> Float:
    value = 0.0
    for row in repeat_range(rows):
        for column in repeat_range(columns):
            value = value + row + column
    return value
```

Loop indices are scoped to their loop. A same-named outer binding becomes available again after the loop.

## Geometry Builder

`geometry_builder()` accumulates geometry. The current result is available through `.geometry`.

```python
def build() -> Geometry:
    builder = geometry_builder()
    builder.add(cube(size=1.0))
    builder.extend([cube(size=0.5), cube(size=0.25)])
    return builder.geometry
```

Builder methods can be used inside compile-time and runtime loops. A builder cannot be assigned to another name, inserted into a collection, passed to a function, or exposed as an output. Return `builder.geometry` to expose the accumulated geometry. See [`geometry_builder()`](BUILTINS.md#geometry_builder) for the method reference.

## Unsupported Python Syntax

NodeForge does not support regular `import` statements, `lambda`, `while`, `try`, `class`, decorators, comprehensions, general dictionary or set expressions, `with`, `yield`, generators, assignment expressions, multiple assignment targets, or top-level `return`, `del`, `global`, and `nonlocal` statements. `break`, `continue`, `raise`, `assert`, and async statements are also unsupported. Dictionary syntax is accepted only as the compile-time `props`, `inputs`, or `outputs` declaration passed directly to `node(...)`.

# DSL Syntax and Semantics

NodeForge scripts are written in a small Python-like DSL. DSL means **domain-specific language**: a language designed for one specific domain instead of general-purpose programming. In NodeForge, the domain is building Blender Geometry Nodes node groups from readable source text.

The syntax is parsed with Python's `ast` parser, so the surface syntax looks like Python: assignments, function calls, arithmetic expressions, `for` blocks, `if` blocks, imports, comments, lists, tuples, and local `def` functions use familiar Python forms.

NodeForge is not full Python. A script is compiled into Geometry Nodes. Only constructs that the compiler explicitly supports are valid. Unsupported Python features such as `lambda`, `while`, `try`, classes, comprehensions, decorators, dictionaries, sets, `with`, `yield`, and arbitrary Python imports are rejected or fail during compilation.

Use this document for the source language itself. Use [Core DSL Built-ins Reference](BUILTINS.md) for built-in functions such as `input_float(...)`, `vector(...)`, `cube(...)`, and `output(...)`. Use [Function Library Reference](FUNCTIONS.md) for reusable imports from `functions/`.

## Compile-time and runtime values

NodeForge uses two kinds of values while compiling a script.

**Compile-time values** are known before Blender nodes are created. They are used for socket names, attribute names, material names, default values, raw-node declarations, import-time constants, and loop unrolling. Strings are always compile-time values. Lists and tuples can also be compile-time values when all their elements can be evaluated before node generation.

```python
name = 'Height'
height = input_float(name, default=1.0)
output(name, height)
```

Here `name` is compile-time: it chooses the input and output socket name. `height` is runtime: it is a Geometry Nodes value that can be linked, computed, and exposed as an output.

**Runtime values** are values represented by Geometry Nodes sockets or fields. Runtime values include `Float`, `Int`, `Bool`, `Vector`, and `Geometry`. They can come from explicit inputs, field built-ins such as `position()`, numeric literals lowered into value nodes, geometry built-ins, math nodes, vector nodes, and imported function calls.

```python
size = input_float('Size', default=2.0)
geo = cube(size=size)
output('Geometry', geo)
```

A runtime value can change when the generated node group is used in Blender. A compile-time value cannot; changing it requires recompiling the script.

## Script shape

A script describes one node group. It usually declares inputs, computes values or geometry, and exposes one or more outputs.

```python
size = input_float('Size', default=2.0)
height = input_float('Height', default=1.0)
base = cube(size=size)
scale = vector(1, 1, height)
geo = transform(base, scale=scale)
output('Geometry', geo)
```

At top level, NodeForge accepts only these statement families:

| Construct | Supported form |
| --- | --- |
| Import | `from functions import name`, `from examples import name`, `from local import name` |
| Assignment | `name = expression` |
| Augmented assignment | `name += expression`, `name -= expression`, `name *= expression`, `name /= expression` where the operation is type-valid |
| Expression statement | Only as the final output shorthand, or supported top-level side-effect calls |
| Function definition | `def name(a, b): ... return value` |
| `for` block | Compile-time unrolled loops and `repeat_range(...)` loops |
| `if` block | Compile-time branch selection or runtime switch-style branch merging |

Top-level `return`, `while`, `try`, `class`, `with`, `del`, `global`, `nonlocal`, and regular `import ...` statements are not part of the DSL.

## Comments

Python-style comments are supported because comments are ignored before AST compilation.

```python
# Create an adjustable cube.
size = input_float('Size', default=2.0)
geo = cube(size=size)
output('Geometry', geo)
```

## Names and assignments

Assignments bind a simple name to a compiled runtime value or a compile-time value.

```python
size = input_float('Size', default=2.0)
geo = cube(size=size)
output('Geometry', geo)
```

Only simple assignment targets are supported at top level:

```python
value = 1.0
```

Tuple/list destructuring is supported only in unrolled array `for` loop targets, not as a general assignment target.

```python
coords = [(0, 0), (1, 2), (2, 4)]
items = []
for x, y in coords:
    pos = vector(x, y, 0)
    pts = points(1)
    moved = set_position(pts, pos)
    items.append(moved)
geo = join(items)
output('Geometry', geo)
```

Names used before assignment become implicit group inputs unless they are built-ins, constants, imported function names, local function names, system constructors, type tokens, or compile-time names.

```python
geo = cube(size=width)
output('Geometry', geo)
```

In this example, `width` is not assigned anywhere in the script. NodeForge creates a group input socket named `width` and exposes it as a runtime value. The default implicit input type is `Float`.

A name used as the iteration count of `repeat_range(...)` is inferred as an `Int` input. `range(...)` requires compile-time integer arguments.

```python
geo = cube(size=1.0)
for i in repeat_range(steps):
    offset = vector(0.25, 0, 0)
    geo = transform(geo, translation=offset)
output('Geometry', geo)
```

Here `steps` becomes an implicit `Int` input.

Implicit inputs are part of the public node-group interface. If this script is saved as an importable module, implicit inputs become arguments of the imported function exactly like explicit `input_*` sockets. Explicit input built-ins are clearer because they set the name, type, and default directly.

```python
width = input_float('Width', default=2.0)
geo = cube(size=width)
output('Geometry', geo)
```

Use explicit inputs for reusable functions unless the implicit input behavior is intentional.

## Type tokens and value types

NodeForge has these runtime socket value types:

| Type | Meaning |
| --- | --- |
| `Geometry` | Geometry socket value. |
| `Float` | Numeric scalar value or field. |
| `Int` | Integer value or field. |
| `Bool` | Boolean value or field. |
| `Vector` | 3-component vector value or field. |

`Float`, `Int`, `Bool`, `Vector`, and `Geometry` are also reserved type tokens for raw `node(...)` declarations. They cannot be reused as local variable names, loop variables, local function names, or local function parameters.

## Function calls

Function calls use normal Python call syntax.

```python
angle = radians(45)
s = sin(angle)
output('Sine', s)
```

Positional and keyword arguments are supported for registered built-ins, system constructors, imported library functions, local functions, and backend helpers. The available parameter names are defined by the called function.

```python
geo = cube(size=2.0)
offset = vector(0, 0, 1)
geo = transform(geo, translation=offset)
output('Geometry', geo)
```

Nested calls are valid syntax:

```python
output('Value', sin(radians(45)))
```

For documentation and reusable scripts, assigning intermediate values is usually easier to read:

```python
angle = radians(45)
value = sin(angle)
output('Value', value)
```

Only simple function names are supported as calls. Attribute calls such as `module.fn(...)` are not supported, except for `array.append(value)` as a statement.

## Arithmetic and boolean expressions

NodeForge supports the common Python expression operators that can be mapped to nodes or compile-time values.

| Syntax | Meaning |
| --- | --- |
| `a + b` | Numeric add, vector add, or compile-time string/list/number add where valid. |
| `a - b` | Numeric subtract or vector subtract. |
| `a * b` | Numeric multiply, vector scale, or vector multiply where valid. |
| `a / b` | Numeric divide or vector divided by scalar. |
| `a ** b` | Numeric power. |
| `a % b` | Numeric modulo. |
| `-a`, `+a` | Unary sign. |
| `not a` | Boolean NOT. |
| `a and b`, `a or b` | Boolean AND/OR. |
| `a < b`, `a <= b`, `a > b`, `a >= b`, `a == b`, `a != b` | Runtime comparisons. Chained comparisons are compiled as combined comparisons. |
| `x if cond else y` | Runtime switch expression. |

```python
x = input_float('X', default=0.25)
inside = x >= 0.0 and x <= 1.0
value = x * 2.0 - 1.0
result = value if inside else 0.0
output('Result', result)
```

Runtime operations are type-checked. For example, adding two vectors is valid, multiplying a vector by a float is valid, but adding a geometry value to a float is not valid.

## Strings and f-strings

Strings are compile-time values. They are used for socket names, attribute names, material names, raw node socket names, and other identifiers that must be known before Blender nodes are created.

```python
name = 'Height'
height = input_float(name, default=1.0)
output(name, height)
```

String concatenation is supported when both sides are compile-time strings.

```python
prefix = 'Generated'
input_name = prefix + ' Value'
value = input_float(input_name, default=1.0)
output(prefix + ' Output', value)
```

Compile-time f-strings are supported with plain string interpolation only. Every interpolated expression must evaluate to a compile-time string.

```python
prefix = 'Generated'
input_name = f'{prefix} Value'
output_name = f'{prefix} Output'
value = input_float(input_name, default=1.0)
output(output_name, value)
```

These forms are rejected:

```python
value = input_float('Value', default=1.0)
output(f'Runtime {value}', value)
```

```python
prefix = 'Generated'
output(f'{prefix!r}', input_float('Value'))
```

```python
prefix = 'Generated'
output(f'{prefix:>12}', input_float('Value'))
```

Runtime values, numeric interpolation, boolean interpolation, conversion flags such as `!r`, and format specs such as `:>12` are not supported in f-strings.

## Compile-time values

Some expressions are evaluated by NodeForge before node generation. Compile-time values are used for names, defaults, loop unrolling, and raw-node declarations.

Supported compile-time values include:

| Value form | Example |
| --- | --- |
| Numbers | `1`, `2.5` |
| Booleans | `True`, `False` |
| Strings | `'Name'` |
| Lists | `[1, 2, 3]` |
| Tuples | `(1, 2, 3)` |
| Vector constants | `vector(1, 2, 3)` or `(1, 2, 3)` where a vector default is expected |
| Constants | `pi`, `tau`, `e` |
| Compile-time calls | `range(4)`, `len(items)`, `sum(items)`, `sin(angle)`, `radians(45)` |

```python
steps = 6
angle = tau / steps
items = []
for i in range(steps):
    theta = angle * i
    x = cos(theta)
    y = sin(theta)
    pos = vector(x, y, 0)
    pts = points(1)
    moved = set_position(pts, pos)
    items.append(moved)
geo = join(items)
output('Geometry', geo)
```

`range(...)`, `len(...)`, and `sum(...)` are compile-time helper calls. They operate only on compile-time values. Runtime lengths or runtime reductions are not supported through these helpers.

```python
sizes = [0.5, 1.0, 1.5]
count = len(sizes)
total = sum(sizes)
average = total / count
output('Average', average)
```

Supported compile-time math calls include `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `sqrt`, `floor`, `ceil`, `round`, `abs`, `radians`, `degrees`, `exp`, and `ln` when their arguments are compile-time numbers.

`pi`, `tau`, and `e` are lowercase. Uppercase `PI` is not a built-in constant.


## Geometry builder accumulation

`geometry_builder()` provides builder-style syntax for accumulating Geometry without manually maintaining an `empty_geometry()` plus repeated `join(...)` state variable. The builder itself is script-local compiler state. Use `.geometry` to get a normal Geometry value.

```python
builder = geometry_builder()
builder.add(cube(size=1.0))
builder.extend([cube(size=0.5), cube(size=0.25)])
output('Geometry', builder.geometry)
```

Compile-time loops over fixed lists are unrolled as builder method statements.

```python
builder = geometry_builder()
for size in [0.5, 1.0, 1.5]:
    builder.add(cube(size))
output('Geometry', builder.geometry)
```

Inside `repeat_range(...)`, each `add(...)` or element of `extend(...)` updates a Geometry Repeat Zone state item owned by the builder binding. Reading `builder.geometry` inside the loop returns the current state at that source position.

```python
builder = geometry_builder()
steps = input_int('Steps', default=3)
for i in repeat_range(steps):
    builder.add(cube(0.1))
    snapshot = builder.geometry
    builder.add(snapshot)
output('Geometry', builder.geometry)
```

Builder objects cannot be aliased, inserted into arrays, passed to built-ins, captured by local functions, or output directly. Use `builder.geometry` at value boundaries.

## Lists, tuples, and arrays

Lists and tuples are script-level collections, not Geometry Nodes socket values. They are useful for collecting several runtime values before passing them to a built-in that accepts an array, such as `join(array)`.

```python
items = []
items.append(cube(size=1.0))
items.append(cube(size=2.0))
geo = join(items)
output('Geometry', geo)
```

A non-empty list or tuple expression compiles each element and returns a script-level array.

```python
a = cube(size=1.0)
b = cube(size=2.0)
items = [a, b]
geo = join(items)
output('Geometry', geo)
```

Indexing arrays requires a compile-time integer index.

```python
items = [cube(size=1.0), cube(size=2.0)]
geo = items[1]
output('Geometry', geo)
```

Arrays cannot be output directly.

```python
items = [cube(size=1.0), cube(size=2.0)]
geo = join(items)
output('Geometry', geo)
```

Dictionaries, sets, list comprehensions, generator expressions, and dictionary comprehensions are not supported DSL collection constructs.

## Vector access and indexing

Vector values support `.x`, `.y`, `.z` field access.

```python
pos = position()
height = pos.z
output('Height', height)
```

Vector values also support compile-time integer indexing with `0`, `1`, or `2`.

```python
pos = position()
height = pos[2]
output('Height', height)
```

Raw `node(...)` results support output lookup by compile-time string key. This is documented with `node(...)` in [Core DSL Built-ins Reference](BUILTINS.md).

## Imports

Imports are top-level only. NodeForge supports imports from three catalogs:

| Catalog | Purpose |
| --- | --- |
| `functions` | Reusable bundled function library. |
| `examples` | Bundled example scripts. |
| `local` | User-owned saved scripts. |

```python
from functions import smoothstep
from functions import circle_points as circle
from functions import *
from examples import mandelbrot
from local import my_shape
```

Only `from ... import ...` is supported. Plain `import functions`, relative imports, nested imports inside blocks, and imports from arbitrary Python modules are not supported.

Import names are source-local. They do not mutate the global built-in namespace and do not automatically apply to other scripts.

A star import reserves all public names from the selected catalog in the current source file. If a local variable, loop variable, or local function uses the same name, compilation fails with an import-name conflict. Use explicit imports or aliases when a name would otherwise collide.

```python
from functions import circle_points as make_circle_points
pts = make_circle_points(32, radius=2.0)
output('Geometry', pts)
```

## Reusable modules

A reusable module is a `.nf` or `.nodeforge` source file in an import catalog. Bundled helpers live in `functions/`, bundled examples live in `examples/`, and user-saved reusable scripts live in the `local` catalog. The file or package name becomes the import name, so it must be a valid public function name such as `my_shape`, not `_my_shape` or `my-shape`.

A module is compiled into a Geometry Nodes group. Its group inputs become the arguments of the imported function call. Its single group output becomes the return value of that call.

```python
# local/my_lifted_cube.nf
size = input_float('Size', default=1.0)
height = input_float('Height', default=2.0)
base = cube(size=size)
offset = vector(0, 0, height)
geo = transform(base, translation=offset)
output('Geometry', geo)
```

Another script can import and call that module:

```python
from local import my_lifted_cube

geo = my_lifted_cube(size=1.5, height=3.0)
output('Geometry', geo)
```

For imported function calls, positional arguments connect to the module's input sockets in group socket order. Keyword arguments match the module's input socket names. Names are matched case-insensitively after spaces, underscores, and other punctuation are removed, so an input socket named `Start Angle` can be called with `start_angle=...`.

```python
from local import my_lifted_cube

geo = my_lifted_cube(1.5, height=3.0)
output('Geometry', geo)
```

A module used as an expression call must expose exactly one output. A module with no outputs or several outputs cannot be used as a normal imported function call.

Implicit inputs also become module arguments, because they are real group input sockets after compilation. Automatic final outputs can be used in modules too; see [Automatic final outputs](#automatic-final-outputs) for the full rules.

```python
# local/implicit_width_cube.nf
geo = cube(size=width)
geo
```

```python
from local import implicit_width_cube

geo = implicit_width_cube(width=2.0)
output('Geometry', geo)
```

In this example, `width` is an implicit input of `implicit_width_cube`, so it becomes a keyword argument when the module is imported. The final `geo` expression becomes the module's single return value. This form works, but explicit `input_*` calls and explicit `output(...)` calls are preferred for reusable modules when the public interface should be stable and self-documenting.

## Local functions

NodeForge supports script-local functions with `def`. A local function is not executed as Python. It is lowered into a generated node group and called from the parent group.

```python
def double_add(a, b):
    c = a * 2.0
    result = c + b
    return result

x = input_float('X', default=1.0)
y = double_add(x, 3.0)
output('Y', y)
```

Local function restrictions:

| Rule | Description |
| --- | --- |
| Parameters | Plain positional parameters only. No defaults, `*args`, `**kwargs`, or keyword-only parameters. |
| Return | A `return value` statement is required. |
| Nested functions | Nested `def` statements are not supported. |
| Argument values | Runtime `Float`, `Int`, `Bool`, `Vector`, and `Geometry` values are supported. Compile-time numeric, boolean, and vector constants are supported. Arrays are not supported as local function arguments. |
| Captures | Free names from the enclosing script scope are captured as hidden inputs when they are runtime values or supported compile-time literals. |
| Calls | Positional and keyword calls are supported using the local parameter names. |

A registered DSL name has one meaning in a source file. Built-ins, imported functions, system constructors, type tokens, local function names, and backend helper names cannot be rebound as variables, local-function parameters, assignment targets, or captured values. Use registered names only in their supported callable or type-token positions.

```python
def lift(geo, amount):
    offset = vector(0, 0, amount)
    moved = transform(geo, translation=offset)
    return moved

base = cube(size=1.0)
geo = lift(base, amount=2.0)
output('Geometry', geo)
```

## If statements

`if` statements have two modes.

If the condition can be evaluated at compile time, NodeForge keeps only the selected branch.

```python
use_large = True
if use_large:
    size = 2.0
else:
    size = 1.0
geo = cube(size=size)
output('Geometry', geo)
```

If the condition is a runtime `Bool`, NodeForge compiles both branches and merges changed variables with Switch nodes. A runtime top-level `if` currently requires an `else` branch. Both branches must assign compatible value types for variables that are merged.

```python
use_large = input_bool('Large', default=False)
if use_large:
    size = 2.0
else:
    size = 1.0
geo = cube(size=size)
output('Geometry', geo)
```

Inside `repeat_range(...)`, an `if` block may omit `else`; unchanged state values are preserved when the condition is false.

```python
x = 0.0
steps = input_int('Steps', default=5)
for i in repeat_range(steps):
    if x < 3.0:
        x = x + 1.0
output('X', x)
```

## For loops

NodeForge supports three kinds of `for` loops.

### Compile-time unrolled loops

A `for` loop over a compile-time list, tuple, or `range(...)` is unrolled before node generation.

```python
items = []
for i in range(4):
    offset = vector(i * 1.25, 0, 0)
    geo = cube(size=1.0)
    moved = transform(geo, translation=offset)
    items.append(moved)
result = join(items)
output('Geometry', result)
```

Tuple/list loop targets are supported for unrolled array loops.

```python
coords = [(0, 0), (1, 0), (0, 1)]
items = []
for x, y in coords:
    pos = vector(x, y, 0)
    pts = points(1)
    moved = set_position(pts, pos)
    items.append(moved)
geo = join(items)
output('Geometry', geo)
```

### Runtime state repeat loops

`repeat_range(steps)` creates one Blender Repeat Zone for existing Geometry, Vector, Float, Int, Bool state variables, and `geometry_builder` accumulators mutated in the body. Every pre-existing variable assigned inside the body becomes a Repeat Zone state item. New variables assigned only inside the body are iteration-local temporaries.

```python
x = 0.0
steps = input_int('Steps', default=5)
for i in repeat_range(steps):
    x = x + 1.0
output('X', x)
```

Geometry state can be updated in the same loop as scalar or vector state.

```python
geo = cube(size=1.0)
pos = vector(0, 0, 0)
steps = input_int('Steps', default=3)
for i in repeat_range(steps):
    pos = pos + vector(0.2, 0, 0)
    geo = transform(geo, translation=pos)
output('Geometry', geo)
```

The loop body supports assignments, `geometry_builder` method statements, and nested `if` blocks. It must update at least one existing variable or builder. State item order follows first assignment or builder mutation in the loop body, including nested branches. An `if` branch that omits a state assignment or builder mutation preserves that branch's incoming state value; changed state is merged with Switch nodes, including Geometry state.

The loop index name cannot also be a state variable, and state names cannot collide with Repeat Zone system socket names such as `Iterations` or `Iteration`. These naming conflicts raise `CompileError` to avoid ambiguous socket wiring.

Use `range(...)` for compile-time unrolled loops only. A `range(...)` loop with non-constant bounds raises `CompileError`; use `repeat_range(...)` when the repeat count is a runtime `Int`.

## Top-level side-effect calls

Some calls are valid only as top-level statements because they modify group outputs, the active Geometry stream, or a script-level array.

| Call | Meaning |
| --- | --- |
| `output(...)` | Adds an explicit group output. |
| `store(...)` | Stores a named attribute on the active Geometry stream. |
| `set_position(...)` | Changes positions on the active Geometry stream. |
| `items.append(value)` | Appends a value to a script-level array. |

`output(...)` and `store(...)` are not general expression functions. For example, `x = output('X', value)` and `x = store('height', value)` are not valid DSL usage.

`store(...)` and statement-form `set_position(...)` operate on an implicit active Geometry input/output stream. Expression-form `store_named_attribute(geometry, ...)` and `set_position(geometry, ...)` operate on an explicit Geometry value. See [Core DSL Built-ins Reference](BUILTINS.md#active-geometry-stream-statements).

## Automatic final outputs

A script can produce an output without an explicit `output(...)` call.

When the last top-level statement is an assignment to a runtime value, NodeForge exposes that value using the assigned variable name.

```python
angle = radians(45)
result = sin(angle)
```

This produces a group output named `result`.

When the last top-level statement is a runtime expression, NodeForge exposes that value as an output named `out`.

```python
angle = radians(45)
sin(angle)
```

This produces a group output named `out`.

The final expression rule is not specific to `sin(...)`. It works for any supported expression that compiles to a single output value.

```python
v = vector(1, 2, 3)
normalize(v)
```

```python
geo = cube(size=2.0)
geo
```

Automatic final outputs support `Float`, `Int`, `Bool`, `Vector`, and `Geometry` values. Arrays and compile-time-only objects cannot be final outputs.

```python
items = [cube(size=1.0), cube(size=2.0)]
geo = join(items)
geo
```

Only the final top-level statement may use expression-output shorthand. Earlier expression statements are rejected unless they are supported side-effect calls such as `output(...)`, `store(...)`, `set_position(...)`, or `array.append(...)`.

```python
angle = radians(45)
value = sin(angle)
value
```

This is valid because `value` is the final statement. This is not valid:

```python
angle = radians(45)
sin(angle)
value = 1.0
```

Use explicit `output(...)` when a script should expose multiple values or when output names should be stable and obvious.

```python
angle = radians(45)
s = sin(angle)
c = cos(angle)
output('Sine', s)
output('Cosine', c)
```

## Unsupported Python features

Unsupported constructs include, but are not limited to:

| Python feature | Status |
| --- | --- |
| `lambda` | Not supported. |
| `while` | Not supported. |
| `try` / `except` / `finally` | Not supported. |
| `class` | Not supported. |
| Decorators | Not supported. |
| Comprehensions | Not supported. |
| Dictionaries and sets | Not supported. |
| `with` | Not supported. |
| `yield` / generators | Not supported. |
| Arbitrary Python imports | Not supported. |
| Attribute calls such as `module.fn(...)` | Not supported. |
| Multiple assignment targets such as `a = b = 1` | Not supported. |
| General destructuring assignment such as `x, y = pair` | Not supported outside unrolled loop targets. |

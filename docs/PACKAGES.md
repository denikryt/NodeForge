# Package Guide

A NodeForge package is an installable distribution unit for reusable DSL functions, examples, and optional Python-backed extensions. This page covers package structure, manifests, imports, packaging, and installation. The Python callable ABI is documented separately in the [Extension API Reference](EXTENSION_API.md).

For scripts used only on one machine or project, the [Local catalog](GET_STARTED.md#save-a-module-to-local) is usually simpler than creating a package.

## Package layout

Every package contains `nodeforge_package.json` at its root and one or more content roots declared by that manifest.

A source-only package can be as small as:

```text
vendor.shapes/
├── nodeforge_package.json
└── functions/
    └── ring.nf
```

`functions/` supports nested category folders. The file name becomes one segment
of a fully qualified callable path:

```text
vendor.shapes/
└── functions/
    ├── mesh/
    │   ├── primitives.nf       # def make_cube(...); def make_sphere(...)
    │   └── operations.nf       # def bevel(...)
    └── utilities.nf           # def identity(...)
```

`functions/mesh/primitives.nf` exports
`shapes.mesh.primitives.make_cube(...)` and
`shapes.mesh.primitives.make_sphere(...)`. These functions can also be called
as `shapes.make_cube(...)` and `shapes.make_sphere(...)` while their short
names are unambiguous.

Packages may declare these content roots:

| Root | Purpose |
| --- | --- |
| `functions` | Reusable callables imported through the package namespace. |
| `examples` | Complete scripts exposed through **Library → Examples**. |
| `systems` | Python-backed extension owners that may export multiple callables. |

A larger package might use all three:

```text
vendor.toolkit/
├── nodeforge_package.json
├── functions/
│   ├── ring.nf
│   ├── scatter/
│   │   └── source.nf
│   └── native_value/
│       ├── interface.py
│       └── backend.py
├── examples/
│   └── demo_scene.nf
└── systems/
    └── procedural/
        ├── interface.py
        ├── semantic.py
        └── backend.py
```

Only declare roots that the package actually contains.

## Manifest

`nodeforge_package.json` uses schema version `1`.

```json
{
  "schema_version": 1,
  "id": "vendor.shapes",
  "import_name": "shapes",
  "name": "Vendor Shapes",
  "version": "1.0.0",
  "author": "Vendor",
  "description": "Reusable shape functions for NodeForge.",
  "nodeforge_min_version": "0.90.2",
  "contents": {
    "functions": "functions"
  }
}
```

| Field | Requirement |
| --- | --- |
| `schema_version` | Integer `1`. |
| `id` | Stable package identity. Use a dotted lowercase identifier such as `vendor.shapes`. |
| `import_name` | Optional DSL namespace. Defaults to the last component of `id`. |
| `name` | Display name. |
| `version` | Dotted numeric package version such as `1.2.0`. |
| `author` | Optional author text. |
| `description` | Optional short description. |
| `nodeforge_min_version` | Minimum compatible NodeForge version. |
| `contents` | Non-empty object declaring any of `functions`, `examples`, and `systems`. |

`id` identifies the installed package. `import_name` is the name used in NodeForge source and must be a public Python-style identifier: it cannot begin with `_` or be a Python keyword. Two active packages cannot use the same `import_name`.

Content paths are package-relative POSIX paths. Absolute paths and empty, `.` or `..` path segments are invalid.

Version fields are compared numerically by component, so `0.65` and `0.65.0` compare as the same version.

## Importing package callables

Import an installed package through the `packages` namespace:

```python
from packages import shapes

geo = shapes.ring(2.0, 0.25)
```

Aliases are supported:

```python
from packages import shapes as s

geo = s.ring(2.0, 0.25)
```

A full call path includes each category directory, the `.nf` module stem,
and the declared function name:

```python
from packages import shapes as s

geo = s.mesh.primitives.make_cube(2.0)
```

A short `s.make_cube(...)` call selects that same function while its declared
name is unique in the package. If another module declares `make_cube`, the
short call is ambiguous and NodeForge reports each valid full call path.
An exact root `systems` or native Functions owner retains its short spelling;
otherwise a single direct-root `.nf` definition takes precedence over
nested functions with the same leaf name. Explicit full paths always select
the specified owner.

Use full paths to distinguish functions in different categories or packages.

For the supported import forms and aliases, see [Imports](SYNTAX.md#imports).

`from packages import *` is not supported. Package namespaces are qualifiers rather than DSL runtime values, so assigning a namespace is also invalid:

```python
from packages import shapes
value = shapes  # invalid
```

`examples` and `local` are separate catalogs; see [Imports](SYNTAX.md#imports).

## Source-backed functions

The `functions` root can contain `.nf` source files at the root or in nested directories:

```text
functions/
└── ring.nf
```

or a directory containing `source.nf`:

```text
functions/
└── ring/
    └── source.nf
```

Every top-level `def` in a source file becomes a package member under its
full module path and declared function name. A file can export several
functions, including names beginning with `_`. Functions from different files
may share a declared name; select those functions with their full paths.
The same complete path can identify only one callable. A file and directory
with the same stem can coexist when the exported callable paths differ
(for example, `utilities.nf` and `utilities/field.nf`).

Function parameters and `return` define the Group Node sockets. Top-level
node-building statements in package source files are skipped when their
functions are imported or added from the Library.

```python
# functions/scale_cube.nf
def scale_cube(size: Float = 1.0, factor: Float = 2.0) -> Geometry:
    return cube(size=size * factor)
```

```python
from packages import shapes

geo = shapes.scale_cube(1.5, factor=3.0)
```

Keywords use the function's exact parameter identifiers; display labels may be set with `Type["Label"]`. See [Writing Functions](WRITING_FUNCTIONS.md#parameters-and-sockets).

Under **Library → Functions**, select a source-backed package function by
its full path and click **Add Node Group**. Reload retains the exact source
module and declared definition, including when other modules declare the same
function name. Python-backed native Functions appear in the list and remain
callable from scripts, while their **Add Node Group** action is disabled.

For general reusable-function behavior, including `__unique__=True` where supported, see [Reusable Functions](SYNTAX.md#reusable-functions).

## Examples

The `examples` root contains complete scripts shown in **Library → Examples**. Store a source example directly as `.nf`/`.nodeforge`, or place `source.nf` inside a named example directory.

```text
examples/
└── demo_scene.nf
```

Examples stay in the `examples` catalog rather than becoming package members. They can be added from the Library UI or imported explicitly:

```python
from examples import demo_scene
```

Python-backed examples are also supported; their declaration and implementation rules are part of the [Extension API Reference](EXTENSION_API.md).

## Python-backed extensions

Use Python only when the required behavior cannot be expressed cleanly as source-backed NodeForge code. Typical cases are custom Blender-node realization or package-defined semantic values.

Python-backed content uses Extension API v2. A native function under
`functions` is represented by an owner directory, including nested category
paths, such as:

```text
functions/
└── mesh/
    └── native_value/
        ├── interface.py
        └── backend.py
```

A `systems` owner can export several related callables:

```text
systems/
└── procedural/
    ├── interface.py
    ├── semantic.py
    └── backend.py
```

The owner above exports `shapes.mesh.native_value(...)` when `EXTENSIONS`
contains exactly one callable named `native_value`. Native owners under
`systems/` continue to export names directly under the package import alias.

Python-backed package code runs with normal Python privileges. Install packages only from sources you trust. For `interface.py`, evaluation modes, backend contexts, semantic values, overloads, generated resources, and owner validation rules, continue with the [Extension API Reference](EXTENSION_API.md).

## Public names

The canonical callable name is built from the relative `functions/` path
followed by the declared function name. Native owner directories contribute
their own directory names; root `systems/` callables retain their names.
Example files appear in the Examples catalog under their file or owner names.

Public directory and module names must be ASCII identifiers and cannot be
Python keywords. Paths beginning with `_` or `.` remain private and are not
exported. Declared function names follow [`.nf` Modules](MODULES.md#define-functions)
(including names beginning with `_`). Directory symlinks and source paths
escaping the package root are rejected.

Different canonical paths may declare the same short name. Short calls work
when there is one eligible owner; ambiguous short calls report their full paths.
A complete canonical path belongs to one export. Package IDs and import names
identify owners across different packages.

## Build the installation ZIP

The Blender UI installs package ZIP archives. The archive must contain exactly one `nodeforge_package.json`, either at the archive root or inside one top-level directory.

```text
shapes.zip
└── vendor.shapes/
    ├── nodeforge_package.json
    └── functions/
        └── ring.nf
```

Archives with ambiguous roots, path traversal, symbolic links, duplicate paths, Python cache artifacts, or an invalid manifest are rejected.

## Install, update, or remove a package

To install a package:

1. Open **Library → Packages**.
2. Click **Import** and select the ZIP.
3. Confirm the import.

To update an installed package with the same `id`, import the new ZIP with **Replace existing package** enabled. NodeForge validates the replacement before making it active; a failed replacement leaves the previous installation in place.

NodeForge copies an installed package into managed storage. It is not a live link to the ZIP or authoring directory, so source changes require a new import.

To remove a package, select it in **Library → Packages** and click **Uninstall**. Existing generated Geometry Nodes groups remain Blender data, but future compilations can no longer resolve members from the removed package.

## Before distributing

Check that the package:

- declares only content roots that actually exist;
- sets `nodeforge_min_version` to the newest NodeForge feature it requires;
- uses a stable, conflict-free `import_name`;
- compiles its source-backed functions from a clean Blender file;
- exposes usable examples through **Library → Examples**;
- uses full `package.category.module.function(...)` paths where the category matters;
- verifies that short callable names resolve unambiguously.

For Python-backed content, use the validation rules in the [Extension API Reference](EXTENSION_API.md) rather than duplicating them here.

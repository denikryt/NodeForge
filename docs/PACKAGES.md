# NodeForge Packages

NodeForge packages install library content into the existing DSL namespaces without adding package-qualified import syntax.

A package is a directory containing `nodeforge_package.json` and one or more declared content roots:

```text
nodeforge_package.json
functions/
examples/
systems/
```

The manifest describes package metadata and the package-owned roots:

```json
{
  "schema_version": 1,
  "id": "vendor.demo",
  "name": "Vendor Demo",
  "version": "1.0.0",
  "author": "Vendor",
  "description": "Demo NodeForge package.",
  "nodeforge_min_version": "0.49.47",
  "nodeforge_max_version": null,
  "contents": {
    "functions": "functions",
    "examples": "examples",
    "systems": "systems"
  },
  "permissions": {
    "python": false
  }
}
```

`functions`, `examples`, and `systems` are roots inside the package directory. They are not copied into global catalog folders. The active package inventory records which installed package directory contributes those roots.

## Runtime behavior

A package state entry means the package is installed and active. Removing the state entry uninstalls the package from future NodeForge resolution. Package inventory does not provide a separate enable or disable state.

Installed packages extend the existing public DSL surface:

```python
from functions import smoothstep
from examples import dragon_curve

geo = ls_system(...)
```

Package inventory does not add package-scoped imports, qualified imports, dot access, import aliases, dependency resolution, manifest `exports`, manifest `imports`, manifest `dependencies`, manifest symbol lists, or file-level ownership maps.

Duplicate public names are errors. A package function, example, or system constructor must not collide with an already active public name.

## Python permission

Packages that contain Python under declared roots, or declare systems, require `permissions.python: true`. User-selected package archives also require explicit install-time consent before executable Python is accepted.

NodeForge rescans declared roots when loading active state. If Python appears under a package that was installed without Python consent, the package is treated as inactive and appears as an invalid package diagnostic. Package diagnostics expose short package-item fields: `id`, `name`, `version`, `origin`, `status`, `path`, `python_required`, `python_allowed`, and `invalid_reason`.

Package roots are not added to `sys.path`. Native helpers and system modules load through private synthetic package names so relative imports work without creating public package import syntax.

## Systems

A system is an immediate child directory under the declared `systems` root:

```text
systems/<system_id>/system.py
```

`system.py` declares public constructor names and provides a lazy handler loader:

```python
CONSTRUCTORS = ["example_constructor"]


def load_handlers():
    from .runtime import HANDLERS
    return HANDLERS


__all__ = ["CONSTRUCTORS", "load_handlers"]
```

Constructor discovery imports only `system.py` and reads `CONSTRUCTORS`. Runtime handlers are loaded later when a constructor is dispatched. `load_handlers()` must return a dict whose keys exactly match `CONSTRUCTORS` and whose values are callable handlers.

## NodeForge-shipped packages

NodeForge ships package source directories for:

- `nodeforge.standard` — standard callable constructors, reusable non-L-system functions, and non-L-system examples.
- `nodeforge.lsystem` — L-system constructors and L-system examples.

On first package-inventory initialization, these sources are installed into the same user inventory used by all other packages. After seeding, they are ordinary package records. They can be uninstalled and reinstalled from the shipped source through package management UI.

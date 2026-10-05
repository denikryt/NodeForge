# Architecture Layers

NodeForge separates package discovery, source semantics, reusable function identity,
and Blender materialization. Compilation resolves one environment snapshot and
passes that snapshot through preparation and group creation or reload.

Packages publish reusable DSL functions as `functions/name.nf`, or native Python
callables through their manifest. Scripts import an installed package with
`from packages import pkg` and call `pkg.name(...)`. Bare callable names remain
available only when resolution is unambiguous. Package discovery does not mutate `builtins/registry.py`; the resolved environment owns package exports and the
extension registry for that compilation.

Source analysis computes free names, simple callee names, and local bindings.
Conflict validation and transitive local-call discovery apply their own filters
to these facts. Conflicts raise controlled `CompileError`s before preparation
cache reuse. Local value bindings participate in conflict checks.

The semantic IR describes typed values and operations independently of Blender.
Extension lowering can use package-local backend helper names within its own
implementation. These helpers do not become unqualified global source names.
`node(...)` remains the raw global escape hatch for explicit Blender node access.

Reusable Functions entries have package ownership. Their materialization and
reload use that exact owner; deletion or ambiguity cannot silently select a
same-named entry from another package. Examples and Local scripts are catalog
entries with their own source and reload contracts. UI operators delegate reload
to the compiler, which resolves the environment once and passes it to the backend.

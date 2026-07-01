"""Runtime loop built-ins for NodeForge DSL.

`range(...)` is parsed at statement level. It compiles to a Repeat Zone when
its body updates existing runtime state, and to compile-time unroll when its
iterable is a compile-time sequence and no runtime state is updated.
"""

NAMES = {"range"}

"""Runtime loop built-ins for NodeForge DSL.

`range(...)` and `runtime_range(...)` are parsed at statement level and compiled
by runtime.py. This module exists so built-ins have a single catalog location.
"""

NAMES = {"range", "runtime_range"}

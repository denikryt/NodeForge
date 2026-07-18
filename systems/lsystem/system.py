"""Package entrypoint for NodeForge L-system constructors."""

CONSTRUCTORS = [
    "ls_system",
    "ls_axiom",
    "ls_rule",
    "ls_iterations",
    "ls_angle",
    "ls_step",
    "ls_param",
    "ls_marker",
    "ls_points",
]


def load_handlers():
    """Load L-system runtime handlers lazily after constructor name discovery."""
    from .constructors import HANDLERS

    return HANDLERS


__all__ = ["CONSTRUCTORS", "load_handlers"]

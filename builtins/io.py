"""Input/output related built-ins for NodeForge DSL."""

from ..builtin_call_semantics import analyze_input_declaration_call

NAMES = {
    "input_geometry",
    "input_float",
    "input_int",
    "input_bool",
    "input_vector",
    "input_material",
    "input_object",
    "input_string",
    "input_bundle",
}


def compile_call(comp, expr, depth=0):
    """Compile one normalized explicit input declaration into a fresh socket."""
    semantics = analyze_input_declaration_call(expr, comp.compile_time.values)
    return comp._create_input_socket_value(semantics.display_name, semantics.typ, semantics.default)

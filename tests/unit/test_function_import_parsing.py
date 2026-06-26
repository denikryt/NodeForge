"""Unit coverage for function import parsing without Blender runtime."""

from NodeForge.parsing import FunctionImport, _extract_function_imports, _parse_source


def test_function_star_import_is_preserved_for_compiler_expansion():
    """Parsing accepts star imports but does not perform library discovery."""
    stmts = _parse_source('from functions import *\nx = 1\noutput("x", x)')
    body, imports = _extract_function_imports(stmts)

    assert len(body) == 2
    assert imports == [FunctionImport(None, None, is_star=True)]


def test_function_imports_preserve_source_order_with_star_requests():
    """Compiler validation receives explicit and star imports in source order."""
    stmts = _parse_source('from functions import fibonacci as fib\nfrom functions import *\nx = 1\noutput("x", x)')
    _, imports = _extract_function_imports(stmts)

    assert imports == [
        FunctionImport('fibonacci', 'fib'),
        FunctionImport(None, None, is_star=True),
    ]

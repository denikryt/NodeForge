"""Unit coverage for catalog import parsing without Blender runtime."""

import pytest

from NodeForge.errors import CompileError
from NodeForge.parsing import FunctionImport, _extract_function_imports, _parse_source


def test_catalog_star_import_is_preserved_for_compiler_expansion():
    """Parsing accepts star imports from all public catalogs."""
    stmts = _parse_source('from functions import *\nfrom examples import *\nfrom local import *\nx = 1\noutput("x", x)')
    body, imports = _extract_function_imports(stmts)

    assert len(body) == 2
    assert imports == [
        FunctionImport("functions", None, None, is_star=True),
        FunctionImport("examples", None, None, is_star=True),
        FunctionImport("local", None, None, is_star=True),
    ]


def test_catalog_imports_preserve_source_order_with_star_requests():
    """Compiler validation receives explicit and star imports in source order."""
    stmts = _parse_source('from examples import dragon_curve as dragon\nfrom functions import *\nfrom local import my_script\nx = 1\noutput("x", x)')
    _, imports = _extract_function_imports(stmts)

    assert imports == [
        FunctionImport("examples", "dragon_curve", "dragon"),
        FunctionImport("functions", None, None, is_star=True),
        FunctionImport("local", "my_script", "my_script"),
    ]


@pytest.mark.parametrize(
    "source",
    [
        "import functions\nx = 1\noutput(\"x\", x)",
        "from systems import ls_system\nx = 1\noutput(\"x\", x)",
        "from .functions import layout_circle\nx = 1\noutput(\"x\", x)",
        "from local.math import noise\nx = 1\noutput(\"x\", x)",
        "import local.math\nx = 1\noutput(\"x\", x)",
        "from examples import * as ex\nx = 1\noutput(\"x\", x)",
    ],
)
def test_unsupported_import_forms_are_rejected(source):
    """Only fixed top-level catalog import-from statements are accepted."""
    with pytest.raises(CompileError):
        _parse_source(source)

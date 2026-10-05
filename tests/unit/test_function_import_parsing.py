"""Unit coverage for source import parsing without Blender runtime."""

import pytest

from NodeForge.errors import CompileError
from NodeForge.parsing import FunctionImport, PackageImport, _extract_function_imports, _parse_source


def test_package_and_catalog_imports_are_split_without_losing_source_order():
    """Package namespaces are distinct from explicit examples/local callable imports."""
    stmts = _parse_source(
        'from packages import math, lsystem as ls\n'
        'from examples import mandelbrot as fractal\n'
        'from local import my_script\n'
        'x = 1\noutput("x", x)'
    )
    body, imports, package_imports = _extract_function_imports(stmts)

    assert len(body) == 2
    assert imports == [
        FunctionImport("examples", "mandelbrot", "fractal"),
        FunctionImport("local", "my_script", "my_script"),
    ]
    assert package_imports == [
        PackageImport("math", "math"),
        PackageImport("lsystem", "ls"),
    ]


def test_examples_and_local_star_imports_remain_supported():
    """Only the package namespace forbids star expansion; existing catalogs retain it."""
    stmts = _parse_source('from examples import *\nfrom local import *\nx = 1\noutput("x", x)')
    _body, imports, package_imports = _extract_function_imports(stmts)
    assert imports == [
        FunctionImport("examples", None, None, is_star=True),
        FunctionImport("local", None, None, is_star=True),
    ]
    assert package_imports == []


def test_from_functions_is_removed_with_migration_diagnostic():
    """Installed package functions no longer occupy a global Functions source namespace."""
    with pytest.raises(CompileError, match="'functions' source namespace was removed"):
        _parse_source('from functions import foo\nfoo()')
    with pytest.raises(CompileError, match="'functions' source namespace was removed"):
        _parse_source('from functions import *\nfoo()')


def test_package_star_import_is_rejected():
    """Packages bind namespaces; Stage 40 does not flatten them with star imports."""
    with pytest.raises(CompileError, match=r"from packages import \* is not supported"):
        _parse_source('from packages import *\nx = 1')


@pytest.mark.parametrize(
    "source",
    [
        "import packages\nx = 1\noutput(\"x\", x)",
        "import functions\nx = 1\noutput(\"x\", x)",
        "from systems import demo_system\nx = 1\noutput(\"x\", x)",
        "from .packages import math\nx = 1\noutput(\"x\", x)",
        "from local.math import noise\nx = 1\noutput(\"x\", x)",
        "import local.math\nx = 1\noutput(\"x\", x)",
        "from examples import * as ex\nx = 1\noutput(\"x\", x)",
    ],
)
def test_unsupported_import_forms_are_rejected(source):
    """Only fixed top-level import-from forms accepted by the DSL are legal."""
    with pytest.raises(CompileError):
        _parse_source(source)

import ast

import pytest

from NodeForge.consteval import _const_eval
from NodeForge.errors import CompileError

pytestmark = pytest.mark.unit


def test_unimported_sign_is_not_allowed_in_compile_time_calls():
    expr = ast.parse("sign(-1)", mode="eval").body
    with pytest.raises(CompileError, match="Unsupported compile-time expression"):
        _const_eval(expr, {})


def test_unimported_sign_is_not_allowed_in_compile_time_list_context():
    expr = ast.parse("[sign(-1)]", mode="eval").body
    with pytest.raises(CompileError, match="Unsupported compile-time expression"):
        _const_eval(expr, {})

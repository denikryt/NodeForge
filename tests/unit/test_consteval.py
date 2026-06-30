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


def _eval_expr(source, env=None):
    return _const_eval(ast.parse(source, mode="eval").body, env or {})


def test_compile_time_f_string_accepts_only_string_fragments():
    assert _eval_expr('f"A{part}B"', {"part": "X"}) == "AXB"
    assert _eval_expr('f"{{{part}}}"', {"part": "X"}) == "{X}"


@pytest.mark.parametrize(
    "source, env",
    [
        ('f"{missing}"', {}),
        ('f"{count}"', {"count": 1}),
        ('f"{flag}"', {"flag": True}),
        ('f"{items}"', {"items": ["A"]}),
        ('f"{part!r}"', {"part": "A"}),
        ('f"{part:>4}"', {"part": "A"}),
    ],
)
def test_compile_time_f_string_rejects_non_string_or_formatted_interpolation(source, env):
    with pytest.raises(CompileError):
        _eval_expr(source, env)


def test_literal_string_and_input_discovery_use_compile_time_fstrings():
    from NodeForge.parsing import _collect_inputs, _literal_string, _parse_source
    from NodeForge.statements import _kw_dict, _optional_string_kw

    expr = ast.parse('f"{prefix} Name"', mode="eval").body
    assert _literal_string(expr, "name", {"prefix": "Socket"}) == "Socket Name"

    call = ast.parse('store("c", x, domain=f"{domain_name}", type=f"{kind}")', mode="exec").body[0].value
    kws = _kw_dict(call)
    assert _optional_string_kw(kws, "domain", "POINT", {"domain_name": "FACE", "kind": "COLOR"}) == "FACE"
    assert _optional_string_kw(kws, "type", None, {"domain_name": "FACE", "kind": "COLOR"}) == "COLOR"

    stmts = _parse_source('prefix = "Result"\nvalue = input_float(f"{prefix} Value")\noutput(f"{prefix} Output", value)')
    from NodeForge.consteval import _preprocess_compile_time

    retained, consts = _preprocess_compile_time(stmts)
    assert _collect_inputs(retained, consts=consts) == []

    retained = _parse_source('output(f"{runtime_name}", 1)')
    assert _collect_inputs(retained, consts={}) == ["runtime_name"]

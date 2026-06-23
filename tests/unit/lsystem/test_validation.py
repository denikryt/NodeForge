import pytest

from NodeForge.errors import CompileError
from NodeForge.systems.lsystem.validation import is_allowed_symbol, validate_rule_symbol, validate_stream

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("ch", list("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_Ff+-[]"))
def test_allowed_symbol_contract(ch):
    assert is_allowed_symbol(ch)


@pytest.mark.parametrize("value", ["F F", "F.G", "F→G", "F/G", "F,G", "F:G"])
def test_validate_stream_rejects_unsupported_symbols(value):
    with pytest.raises(CompileError):
        validate_stream(value, "fixture")


@pytest.mark.parametrize("value", ["F", "X", "+", "["])
def test_validate_rule_symbol_accepts_single_allowed_symbol(value):
    assert validate_rule_symbol(value) == value


@pytest.mark.parametrize("value", ["", "FF", "→", 3])
def test_validate_rule_symbol_rejects_invalid_predecessors(value):
    with pytest.raises(CompileError):
        validate_rule_symbol(value)

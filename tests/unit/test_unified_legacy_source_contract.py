"""Single `.nf` frontend rejects obsolete generated-root interface declarations."""

import pytest

from NodeForge.compiler_identities import ModuleId
from NodeForge.errors import CompileError
from NodeForge.semantic.source_modules import parse_source_module


@pytest.mark.parametrize('source, obsolete', [
    ('x = input_float("X")', 'input_float'),
    ('output("Result", 1.0)', 'output'),
    ('if True:\n    x = input_geometry("Geometry")', 'input_geometry'),
    ('for i in range(1):\n    output("Value", i)', 'output'),
])
def test_obsolete_root_interface_is_rejected_during_source_capture(source, obsolete):
    with pytest.raises(CompileError, match=rf'Unsupported legacy .nf interface {obsolete}'):
        parse_source_module(source, ModuleId('ROOT_TEXT', 'test-obsolete'))


def test_typed_function_return_and_direct_root_statements_are_accepted():
    module = parse_source_module('''v = lift(2)
def lift(value: Float) -> Float:
    return value + 1
''', ModuleId('ROOT_TEXT', 'test-unified'))
    assert tuple(module.definitions) == ('lift',)
    assert len(module.statements) == 1

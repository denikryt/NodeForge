import pytest

from helpers import CompileError, compiler, expect_compile_error

ERROR_SOURCES = {
    "runtime_iterations": '''
n = input_int("N", default=2)
geo = ls_system(ls_axiom("F"), ls_iterations(n), ls_angle(60), ls_step(1))
output("Geometry", geo)
''',
    "invalid_rule_symbol": '''
geo = ls_system(ls_axiom("F"), ls_rule("FF", "F"), ls_iterations(1), ls_angle(60), ls_step(1))
output("Geometry", geo)
''',
    "invalid_stream_symbol": '''
geo = ls_system(ls_axiom("F F"), ls_iterations(1), ls_angle(60), ls_step(1))
output("Geometry", geo)
''',
    "part_as_output": '''
part = ls_axiom("F")
output("Part", part)
''',
}


@pytest.mark.parametrize("case_name, source", ERROR_SOURCES.items())
def test_invalid_lsystem_sources_raise_compile_error(case_name, source):
    expect_compile_error(source, "NFTest_lsystem_error_" + case_name)

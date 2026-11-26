"""Coverage reference reports real generated contracts and observed limitations."""
from collections import Counter
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'tools' / 'nodegen'
CLI = TOOL / 'cli.py'
PACKAGE = ROOT / 'packages' / 'nodes'

pytestmark = pytest.mark.unit


@pytest.fixture(scope='module')
def coverage_data():
    """Generate the real report from the pinned Inspector spec in check mode."""
    sys.path.insert(0, str(TOOL))
    try:
        from build import build, read_spec, DEFAULT_SPEC
        from coverage_report import coverage_rows
        spec = read_spec(DEFAULT_SPEC)
        report, _ = build(spec, PACKAGE, check=True, archive=False)
        rows = {row['node']: row for row in coverage_rows(spec, report)}
        yield spec, report, rows
    finally:
        sys.path.remove(str(TOOL))


def test_coverage_accounts_for_every_inspected_class(coverage_data):
    """Counts and function names originate in the actual generator report."""
    spec, report, rows = coverage_data
    assert set(rows) == set(spec['nodes'])
    assert len(rows) == 360
    assert Counter(row['status'] for row in rows.values()) == {'Full': 242, 'Partial': 55, 'None': 63}
    wrappers = {row['node']: row for row in report['wrappers']}
    assert {key for key, row in rows.items() if row['function']} == set(wrappers)
    assert sum(row['supported'] for row in rows.values()) == 2671
    assert sum(row['inspected'] for row in rows.values()) == 4472
    for key, wrapper in wrappers.items():
        assert rows[key]['function'] == wrapper['name']
        assert rows[key]['supported'] == len(wrapper['configurations'])
        assert rows[key]['inspected'] == wrapper['total_states']
    assert all(not row['exposed'] and row['supported'] == 0
               for row in rows.values() if row['status'] == 'None')
    assert all(not row['unavailable'] and row['supported'] == row['inspected']
               for row in rows.values() if row['status'] == 'Full')
    assert rows['GeometryNodeRepeatInput']['notes'] == ['Excluded zone or bake node']
    assert rows['GeometryNodeListLength']['notes'] == ['Option keys vary across otherwise representable states']


def test_coverage_distinguishes_base_states_and_dynamic_items(coverage_data):
    """Full base coverage does not imply that runtime Menu or all collections work."""
    _, _, rows = coverage_data
    capture = rows['GeometryNodeCaptureAttribute']
    assert capture['status'] == 'Partial'
    assert capture['supported'] == capture['inspected'] == 7
    assert set(capture['exposed']) == {'NodeSocketBool', 'NodeSocketColor', 'NodeSocketFloat',
                                       'NodeSocketGeometry', 'NodeSocketInt', 'NodeSocketRotation', 'NodeSocketVector'}
    assert capture['unavailable'] == ['NodeSocketMatrix']
    fmt = rows['FunctionNodeFormatString']
    assert fmt['status'] == 'Partial' and fmt['supported'] == fmt['inspected'] == 1
    assert fmt['exposed'] == ['NodeSocketString']
    assert 'Dynamic items not exposed: `format_items`' in fmt['notes']
    resample = rows['GeometryNodeResampleCurve']
    assert resample['status'] == 'Partial' and resample['supported'] == resample['inspected'] == 6
    assert resample['unavailable'] == ['NodeSocketMenu']
    assert 'Menu options: `mode`' in resample['notes']
    transform = rows['GeometryNodeTransform']
    assert transform['supported'] == 1 and transform['inspected'] == 2
    assert transform['unavailable'] == ['NodeSocketMatrix', 'NodeSocketMenu']
    join = rows['GeometryNodeJoinGeometry']
    assert join['status'] == 'Partial' and join['supported'] == join['inspected'] == 1
    assert not join['unavailable']
    assert 'One value per multi-input socket: `geometry`' in join['notes']
    assert rows['GeometryNodeInputPosition']['status'] == 'Full'
    assert rows['GeometryNodeCollectionInfo']['status'] == 'None'
    assert 'NodeSocketCollection' in rows['GeometryNodeCollectionInfo']['unavailable']


def test_coverage_cli_is_deterministic_and_read_only(tmp_path):
    """Check mode rejects stale docs; neither mode changes the Nodes package."""
    output = tmp_path / 'NODES.md'
    before = {p.relative_to(PACKAGE): p.read_bytes() for p in PACKAGE.rglob('*')
              if p.is_file() and p.suffix in {'.py', '.json'}}
    command = [sys.executable, str(CLI), 'coverage', '--output', str(output)]
    subprocess.run(command, check=True, capture_output=True)
    assert output.read_bytes() == (ROOT / 'docs' / 'NODES.md').read_bytes()
    subprocess.run([*command, '--check'], check=True, capture_output=True)
    output.write_text('stale', encoding='utf-8')
    check = subprocess.run([*command, '--check'], capture_output=True, text=True)
    assert check.returncode != 0 and 'Out-of-date Nodes coverage reference' in check.stderr
    assert output.read_text(encoding='utf-8') == 'stale'
    assert all((PACKAGE / path).read_bytes() == content for path, content in before.items())


def test_coverage_rejects_stale_package_before_publishing(tmp_path):
    """A mismatched generated owner cannot produce authoritative documentation."""
    package = tmp_path / 'nodes'
    shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns('__pycache__'))
    owner = package / 'systems' / 'blender' / 'interface.py'
    owner.write_bytes(owner.read_bytes() + b'\n# stale owner\n')
    output = tmp_path / 'NODES.md'
    output.write_text('original', encoding='utf-8')
    command = [sys.executable, str(CLI), 'coverage', '--package', str(package), '--output', str(output)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0 and 'Out-of-date generated file' in result.stderr
    assert output.read_text(encoding='utf-8') == 'original'
    assert owner.read_bytes().endswith(b'# stale owner\n')
    invalid = subprocess.run([sys.executable, str(CLI), 'coverage', '--package', str(package),
                              '--output', str(package / 'reference.md')], capture_output=True, text=True)
    assert invalid.returncode != 0 and 'outside the package directory' in invalid.stderr
    assert not (package / 'reference.md').exists()

"""The Inspector's final stage must actually write the merged JSON consumed by nodegen."""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_inspector_merges_all_seven_stage_documents(monkeypatch, tmp_path):
    """A completed Inspector run provides exactly one versioned specification."""
    tool = Path(__file__).resolve().parents[2] / 'tools/nodegen'
    monkeypatch.syspath_prepend(str(tool / 'inspector'))
    merger = _load_module(tool / 'inspector/merge_artifacts.py', 'nf_inspector_merge_test')
    runner = _load_module(tool / 'inspector/run_inspector.py', 'nf_inspector_run_test')
    with gzip.open(tool / 'data/blender-5.2.spec.json.gz', 'rt', encoding='utf-8') as stream:
        baseline = json.load(stream)
    stages = {stage: merger.restore_stage(baseline, stage) for stage in merger.STAGES}
    blender = tmp_path / 'blender'
    blender.write_bytes(b'blender test fixture')
    output = tmp_path / 'run'

    def emulate_blender(cmd, stdout, stderr, check):
        script = Path(cmd[cmd.index('--python') + 1]).name
        stage = next(name for name, filename, _ in runner.PIPELINE if filename == script)
        (output / f'{stage}.json').write_text(json.dumps(stages[stage]), encoding='utf-8')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(runner.subprocess, 'run', emulate_blender)
    monkeypatch.setattr(sys, 'argv', ['run_inspector.py', '--blender', str(blender), '--out', str(output)])
    runner.main()
    outputs = list(output.glob('*.spec.json'))
    assert len(outputs) == 1
    merged = json.loads(outputs[0].read_text(encoding='utf-8'))
    assert merged['blender_version'] == '5.2.0 LTS'
    assert merged['node_count'] == baseline['node_count']
    assert set(merged['source_files']) == set(merger.STAGES)
    for stage in merger.STAGES:
        assert merger.restore_stage(merged, stage) == stages[stage]

"""Node generator command routing without requiring Blender or an installed package."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture
def cli():
    path = Path(__file__).resolve().parents[2] / 'tools' / 'nodegen' / 'cli.py'
    spec = importlib.util.spec_from_file_location('nodegen_cli_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_build_runs_inspector_then_generation_then_tests(cli, monkeypatch, tmp_path):
    events = []
    spec = tmp_path / 'blender-5.2.spec.json'
    blender = tmp_path / 'blender'
    blender.write_text('fake executable')
    def inspect(binary, directory):
        events.append('inspect')
        return spec
    def generate(source, output, result_dir, *, check=False):
        assert source == spec and not check
        events.append('generate')
    def validate(source, output, binary, result_dir, *, all_states=True):
        assert source == spec and binary == blender and all_states
        events.append('test')
    monkeypatch.setattr(cli, 'inspector', inspect)
    monkeypatch.setattr(cli, 'generation', generate)
    monkeypatch.setattr(cli, 'validation', validate)
    monkeypatch.setattr(sys, 'argv', ['cli.py', 'build', '--blender', str(blender),
                                      '--output', str(tmp_path / 'nodes')])
    cli.main()
    assert events == ['inspect', 'generate', 'test']


def test_generate_and_test_use_directory_without_zip(cli, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli.subprocess, 'run', lambda cmd, check: calls.append((cmd, check)))
    monkeypatch.setattr(cli, 'TOOLS', tmp_path)
    (tmp_path / 'build.py').write_text('# stub')
    spec = tmp_path / 'spec.json'
    blender = tmp_path / 'blender'
    blender.write_text('fake executable')
    cli.generation(spec, tmp_path / 'package', tmp_path / 'results', check=True)
    cli.validation(spec, tmp_path / 'package', blender, tmp_path / 'results')
    assert len(calls) == 2
    assert all('--no-zip' in command and check for command, check in calls)
    assert '--check' in calls[0][0]
    assert '--verify' in calls[1][0]
    assert '--all' in calls[1][0]


def test_inspector_returns_merged_spec(cli, monkeypatch, tmp_path):
    tools = tmp_path / 'tools'
    runner = tools / 'inspector' / 'run_inspector.py'
    runner.parent.mkdir(parents=True)
    runner.write_text('# mock')
    blender = tmp_path / 'blender'
    blender.write_text('binary')
    dest = tmp_path / 'results'
    def fake_subprocess(command, check):
        assert '--blender' in command and '--out' in command
        assert check
        run_dir = Path(command[command.index('--out') + 1])
        run_dir.joinpath('blender-5.2.spec.json').write_text('{}')
    monkeypatch.setattr(cli, 'TOOLS', tools)
    monkeypatch.setattr(cli.subprocess, 'run', fake_subprocess)
    generated = cli.inspector(blender, dest)
    assert generated.name == 'blender-5.2.spec.json'
    assert generated.parent.parent == dest


def test_generate_prefers_latest_saved_52_spec(cli, monkeypatch, tmp_path):
    tools = tmp_path / 'nodegen'
    pin = tools / 'data' / 'blender-5.2.spec.json.gz'
    pin.parent.mkdir(parents=True)
    pin.write_text('pinned')
    run = tools / 'data' / 'inspection' / 'run_abc'
    run.mkdir(parents=True)
    saved = run / 'blender-5.2.spec.json'
    saved.write_text('inspected')
    monkeypatch.setattr(cli, 'TOOLS', tools)
    monkeypatch.setattr(cli, 'DEFAULT_SPEC', pin)
    assert cli.current_spec() == saved
    saved.unlink()
    assert cli.current_spec() == pin

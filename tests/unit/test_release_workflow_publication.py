"""Release workflow invariants: published extension artifacts are immutable."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

import pytest

pytestmark = pytest.mark.unit

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/release-extension.yml"
TAG_PUSH_ONLY = "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"


def _step(name_or_action: str) -> str:
    """Extract one workflow step without depending on a YAML parser in test runners."""
    content = WORKFLOW.read_text(encoding="utf-8")
    marker = f"      - {name_or_action}"
    assert content.count(marker) == 1
    start = content.index(marker)
    following = re.search(r"(?m)^      - (?:name:|uses:|id:)", content[start + len(marker):])
    end = start + len(marker) + following.start() if following else len(content)
    return content[start:end]


def test_manual_dispatch_runs_release_verification_but_not_publication():
    """Manual retries execute built-ZIP checks without changing releases or Pages."""
    content = WORKFLOW.read_text(encoding="utf-8")
    assert 'workflow_dispatch:' in content
    assert 'inputs.release_tag' in content
    assert _step('name: Install and test built Extension in Blender 5.2')
    for name in (
        'name: Publish GitHub release asset',
        'uses: actions/upload-pages-artifact@v3',
        'id: deployment',
    ):
        assert f'if: {TAG_PUSH_ONLY}' in _step(name)


def test_release_cannot_overwrite_existing_asset():
    """Release publishing uses create-only semantics with no clobber fallback."""
    step = _step('name: Publish GitHub release asset')
    script = step.split('        run: |\n', 1)[1]
    assert 'set -euo pipefail' in script
    assert 'gh release create "$TAG_NAME" public/*.zip --generate-notes --verify-tag' in script
    assert 'gh release upload' not in script
    assert '--clobber' not in script
    assert '||' not in script


def test_release_failure_stops_without_attempting_upload(tmp_path):
    """An existing release rejection leaves existing assets untouched."""
    section = _step('name: Publish GitHub release asset')
    raw_script = section.split('        run: |\n', 1)[1]
    script = '\n'.join(line[10:] for line in raw_script.splitlines() if line.strip())

    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    gh = bin_dir / 'gh'
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$GH_CALL_LOG"\nexit "$MOCK_GH_EXIT"\n')
    gh.chmod(0o755)
    public_dir = tmp_path / 'public'
    public_dir.mkdir()
    (public_dir / 'nodeforge-test.zip').write_bytes(b'archive')

    log_path = tmp_path / 'calls.txt'
    env = dict(os.environ, PATH=f'{bin_dir}:{os.environ.get("PATH", "")}',
               GH_CALL_LOG=str(log_path), TAG_NAME='v0.0.0', MOCK_GH_EXIT='23')
    failed = subprocess.run(['bash', '-c', script], cwd=tmp_path, env=env, capture_output=True)
    assert failed.returncode == 23
    assert log_path.read_text().splitlines() == [
        'release create v0.0.0 public/nodeforge-test.zip --generate-notes --verify-tag'
    ]

    env['MOCK_GH_EXIT'] = '0'
    successful = subprocess.run(['bash', '-c', script], cwd=tmp_path, env=env, capture_output=True)
    assert successful.returncode == 0
    assert len(log_path.read_text().splitlines()) == 2

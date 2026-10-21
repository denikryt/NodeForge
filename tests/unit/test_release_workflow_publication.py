"""Release workflow invariants: immutable artifacts and independently retryable Pages."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

import pytest

pytestmark = pytest.mark.unit

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/release-extension.yml"
TAG_PUSH_ONLY = "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"


def _job(name: str) -> str:
    """Extract one job by its top-level YAML indentation, with no YAML dependency."""
    content = WORKFLOW.read_text(encoding="utf-8").split("\njobs:\n", 1)[1]
    start_marker = f"  {name}:\n"
    assert content.count(start_marker) == 1
    start = content.index(start_marker)
    next_job = re.search(r"(?m)^  [a-z][\w-]*:\s*$", content[start + len(start_marker):])
    end = start + len(start_marker) + next_job.start() if next_job else len(content)
    return content[start:end]


def _step(job: str, name_or_action: str) -> str:
    """Extract a single workflow step within the specified job."""
    marker = f"      - {name_or_action}"
    assert job.count(marker) == 1
    start = job.index(marker)
    following = re.search(r"(?m)^      - (?:name:|uses:|id:)", job[start + len(marker):])
    end = start + len(marker) + following.start() if following else len(job)
    return job[start:end]


def test_manual_dispatch_runs_verification_without_publication():
    """Manual runs verify the built ZIP but skip both publishing jobs."""
    content = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in content
    assert "inputs.release_tag" in content
    assert _step(_job("test-extension"), "name: Install and test built Extension in Blender 5.2")
    for name in ("publish-release", "deploy-pages"):
        assert f"if: {TAG_PUSH_ONLY}" in _job(name)
    assert "gh release create" not in _job("build")
    assert "actions/deploy-pages" not in _job("build")


def test_pull_requests_build_and_test_without_publication():
    """Pull requests targeting main validate the merged Extension without publishing it."""
    content = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:\n    branches: [main]" in content
    assert _step(_job("test-extension"), "name: Install and test built Extension in Blender 5.2")
    assert "if tag_name.startswith('v'):" in _step(_job("build"), "name: Verify NodeForge version")
    for name in ("publish-release", "deploy-pages"):
        assert f"if: {TAG_PUSH_ONLY}" in _job(name)


def test_library_release_assets_are_downloaded_from_latest_releases():
    """The build uses the bundled-package registry for release assets and validation."""
    download = _step(_job("build"), "name: Download latest library releases")
    assert "from bundled_packages import BUNDLED_PACKAGES" in download
    assert "for package_id, repository in BUNDLED_PACKAGES.items():" in download
    assert "'--repo', repository" in download
    assert "f'{package_id}-v*_nf-v*.zip'" in download
    stage = _step(_job("build"), "name: Stage extension contents")
    assert "expected = set(BUNDLED_PACKAGES)" in stage
    assert "manifest['nodeforge_min_version']" in stage
    assert "minimum.split('.')" in stage
    assert "assert set(seen) == set(expected)" in stage
    build = _step(_job("build"), "name: Validate and build")
    assert "len(staged_archives) == len(BUNDLED_PACKAGES)" in build


def test_extension_staging_includes_text_editor_modules():
    """The release ZIP includes the editor code registered by the add-on UI."""
    stage = _step(_job("build"), "name: Stage extension contents")
    assert "cp -R blender semantic extensions examples editor staging/" in stage
    assert "test -d staging/editor" in stage


def test_publishing_jobs_depend_on_one_verified_build():
    """The release and Pages deployment reuse the validated build, not rebuilt ZIPs."""
    build = _job("build")
    release = _job("publish-release")
    pages = _job("deploy-pages")

    assert "needs: [build, test-extension]" in release
    assert "needs: build" in _job("test-extension")
    assert "needs: [build, publish-release]" in pages
    assert "contents: write" in release
    assert "pages: write" in pages and "id-token: write" in pages
    assert "name: github-pages" in pages

    upload = _step(build, "name: Preserve built release files")
    download = _step(release, "name: Retrieve verified release files")
    assert "uses: actions/upload-artifact@v4" in upload
    assert "uses: actions/download-artifact@v4" in download
    for step in (upload, download):
        assert "name: nodeforge-release-public" in step
        assert "path: public/" in step
    test = _job("test-extension")
    retrieve = _step(test, "name: Retrieve built release files")
    assert "uses: actions/download-artifact@v4" in retrieve
    assert "name: nodeforge-release-public" in retrieve
    assert "path: public/" in retrieve
    assert "verify_extension_release.py" in test
    assert "verify_extension_release.py" not in build
    assert "extension build" not in test
    assert "upload-artifact" not in test
    assert "upload-pages-artifact" not in test
    assert "inputs.release_tag || github.ref" in test
    assert test.index("name: Retrieve built release files") < test.index("name: Install and test built Extension")
    assert "gh release create" in _step(release, "name: Publish GitHub release asset")
    assert "blender-dist" not in release
    assert "blender-dist" not in pages


def test_pages_retry_reuses_existing_pages_artifact():
    """Deploy retries never re-upload an artifact or retry immutable release creation."""
    build = _job("build")
    pages = _job("deploy-pages")
    prepared = _step(build, "name: Preserve built Pages artifact")
    assert f"if: {TAG_PUSH_ONLY}" in prepared
    assert "uses: actions/upload-pages-artifact@v3" in prepared
    assert "path: public/" in prepared
    assert "retention-days: 30" in prepared
    assert "id: deployment" in pages and "uses: actions/deploy-pages@v4" in pages
    assert "upload-pages-artifact" not in pages
    assert "upload-artifact" not in pages
    assert "gh release" not in pages
    assert "server-generate" not in pages


def test_release_cannot_overwrite_existing_asset():
    """Release publishing uses create-only semantics with no clobber fallback."""
    step = _step(_job("publish-release"), "name: Publish GitHub release asset")
    script = step.split("        run: |\n", 1)[1]
    assert "set -euo pipefail" in script
    assert 'gh release create "$TAG_NAME" public/*.zip --generate-notes --verify-tag' in script
    assert "gh release upload" not in script
    assert "--clobber" not in script
    assert "||" not in script


def test_release_failure_stops_without_attempting_upload(tmp_path):
    """An existing release rejection leaves existing assets untouched."""
    section = _step(_job("publish-release"), "name: Publish GitHub release asset")
    raw_script = section.split("        run: |\n", 1)[1]
    script = "\n".join(line[10:] for line in raw_script.splitlines() if line.strip())

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$GH_CALL_LOG"\nexit "$MOCK_GH_EXIT"\n')
    gh.chmod(0o755)
    public_dir = tmp_path / "public"
    public_dir.mkdir()
    (public_dir / "nodeforge-test.zip").write_bytes(b"archive")

    log_path = tmp_path / "calls.txt"
    env = dict(os.environ, PATH=f'{bin_dir}:{os.environ.get("PATH", "")}',
               GH_CALL_LOG=str(log_path), TAG_NAME="v0.0.0", MOCK_GH_EXIT="23")
    failed = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True)
    assert failed.returncode == 23
    assert log_path.read_text().splitlines() == [
        "release create v0.0.0 public/nodeforge-test.zip --generate-notes --verify-tag"
    ]

    env["MOCK_GH_EXIT"] = "0"
    successful = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True)
    assert successful.returncode == 0
    assert len(log_path.read_text().splitlines()) == 2


def test_release_targets_repository_without_checkout(tmp_path):
    """GH_REPO identifies the publication target on a fresh runner without .git."""
    job = _job("publish-release")
    section = _step(job, "name: Publish GitHub release asset")
    assert "actions/checkout" not in job
    assert "GH_REPO: ${{ github.repository }}" in section
    script = "\n".join(
        line[10:] for line in section.split("        run: |\n", 1)[1].splitlines()
        if line.strip()
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        '#!/bin/sh\n'
        'test "${GH_REPO-}" = "denikryt/NodeForge" || exit 42\n'
        'test "$1" = "release" && test "$2" = "create"\n'
    )
    gh.chmod(0o755)
    public = tmp_path / "public"
    public.mkdir()
    (public / "nodeforge-test.zip").write_bytes(b"archive")
    env = dict(os.environ, PATH=f'{bin_dir}:{os.environ.get("PATH", "")}', TAG_NAME="v0.0.0")
    env.pop("GH_REPO", None)
    missing = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True)
    assert missing.returncode == 42

    env["GH_REPO"] = "denikryt/NodeForge"
    configured = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True)
    assert configured.returncode == 0


@pytest.mark.parametrize("name", ["build", "test-extension"])
def test_blender_cache_has_pinned_key_and_download_fallback(name):
    """Both fresh runners use the same exact Blender cache and a miss fallback."""
    job = _job(name)
    cache = _step(job, "name: Cache Blender 5.2")
    install = _step(job, "name: Install Blender 5.2")
    assert "uses: actions/cache@v4" in cache
    assert "path: blender-dist" in cache
    assert "key: blender-${{ runner.os }}-${{ runner.arch }}-5.2.0-linux-x64-v1" in cache
    assert "restore-keys:" not in cache
    assert "if: steps.blender-cache.outputs.cache-hit != 'true'" in install
    assert "set -euo pipefail" in install
    assert "Blender5.2/blender-5.2.0-linux-x64.tar.xz" in install
    assert "tar -xJf blender.tar.xz -C blender-dist --strip-components=1" in install
    assert job.index("name: Cache Blender 5.2") < job.index("name: Install Blender 5.2")

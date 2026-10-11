"""Static repository hygiene checks for permanent compiler and test terminology."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
CHRONOLOGY_PATTERN = re.compile(r"(?i)stage[-_ ]?\d+")
TEMPORARY_MARKERS = ("TODO(nodeforge-compat):", "TODO(nodeforge-migration):")


def _python_sources():
    """Yield repository Python files while ignoring generated/cache directories."""
    for path in ROOT.rglob("*.py"):
        if any(part in {".git", "__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        yield path


def _production_python_sources():
    """Yield non-test production Python files."""
    for path in _python_sources():
        relative = path.relative_to(ROOT)
        if relative.parts and relative.parts[0] == "tests":
            continue
        yield path


def test_python_paths_and_contents_use_semantic_names_instead_of_stage_chronology():
    """Executable/test Python names and text describe behavior rather than refactor chronology."""
    failures = []
    for path in _python_sources():
        relative = path.relative_to(ROOT).as_posix()
        if CHRONOLOGY_PATTERN.search(relative):
            failures.append(f"{relative}: path contains refactor chronology")
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = CHRONOLOGY_PATTERN.search(line)
            if match:
                failures.append(f"{relative}:{line_no}: {match.group(0)!r}")
    assert not failures, "\n".join(failures)


def test_production_python_has_no_temporary_compatibility_or_migration_markers():
    """Completed production boundaries use permanent invariants instead of temporary markers."""
    failures = []
    for path in _production_python_sources():
        relative = path.relative_to(ROOT).as_posix()
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for marker in TEMPORARY_MARKERS:
                if marker in line:
                    failures.append(f"{relative}:{line_no}: {marker}")
    assert not failures, "\n".join(failures)

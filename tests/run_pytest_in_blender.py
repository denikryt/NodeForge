"""Run pytest inside Blender's Python runtime."""

import sys

try:
    import pytest
except ModuleNotFoundError as exc:
    raise SystemExit(
        "pytest is required in Blender's Python environment. "
        "Install it before running tests/run_pytest_in_blender.py."
    ) from exc


def main():
    """Forward arguments after '--' to pytest, defaulting to tests/blender."""
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1:]
    else:
        args = ["tests/blender"]
    raise SystemExit(pytest.main(args))


if __name__ == "__main__":
    main()

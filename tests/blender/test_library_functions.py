from helpers import *

import json
import tempfile
from pathlib import Path

from NodeForge import packages




def _write_package_manifest(root: Path, package_id: str, *, contents=None, python=False):
    payload = {
        "schema_version": 1,
        "id": package_id,
        "name": package_id,
        "version": "1.0.0",
        "author": "Tests",
        "description": "Blender package regression fixture.",
        "nodeforge_min_version": "0.49.47",
        "nodeforge_max_version": None,
        "contents": contents or {"functions": "functions"},
        "permissions": {"python": bool(python)},
    }
    (root / "nodeforge_package.json").write_text(json.dumps(payload), encoding="utf-8")

"""Record and verify deterministic Blender graph baselines for DSL expressions."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
PACKAGE_PARENT = ROOT.parent
for _path in (ROOT, PACKAGE_PARENT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import bpy

from NodeForge import compiler, packages
from NodeForge.errors import CompileError
from NodeForge.systems import registry as systems_registry

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
SCRIPTS_DIR = CASES_DIR / "scripts"
EXPECTED_DIR = CASES_DIR / "expected"
MANIFEST_PATH = CASES_DIR / "manifest.json"
CASE_ID_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
NODE_PROPERTIES = ("operation", "data_type", "input_type", "mode", "transform_space")


def _json_value(value):
    """Normalize one Blender/RNA value to JSON-native deterministic data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, dict)):
        try:
            return [_json_value(item) for item in value]
        except (TypeError, ValueError):
            pass
    raise TypeError(f"Unsupported snapshot value type: {type(value).__name__}")


def _assert_json_native(value, path="snapshot"):
    """Reject values that cannot survive a JSON encode/decode round trip unchanged."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_native(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} contains non-string key {key!r}")
            _assert_json_native(item, f"{path}.{key}")
        return
    raise TypeError(f"{path} contains non-JSON-native {type(value).__name__}")


def _socket_ref(sockets, socket):
    """Return one stable socket reference, disambiguating duplicate display names."""
    identifier = str(getattr(socket, "identifier", "") or "")
    if identifier:
        same_identifier = [
            candidate for candidate in sockets
            if str(getattr(candidate, "identifier", "") or "") == identifier
        ]
        if len(same_identifier) <= 1:
            return identifier
        occurrence = next(
            index for index, candidate in enumerate(same_identifier)
            if candidate.as_pointer() == socket.as_pointer()
        )
        return f"{identifier}[{occurrence}]"
    name = str(getattr(socket, "name", ""))
    same_name = [candidate for candidate in sockets if getattr(candidate, "name", "") == name]
    if len(same_name) <= 1:
        return name
    occurrence = next(
        index for index, candidate in enumerate(same_name)
        if candidate.as_pointer() == socket.as_pointer()
    )
    return f"{name}[{occurrence}]"


def _interface_snapshot(group):
    """Return ordered interface-socket state relevant to compilation behavior."""
    result = []
    for item in group.interface.items_tree:
        if getattr(item, "item_type", None) != "SOCKET":
            continue
        row = {
            "name": str(item.name),
            "in_out": str(item.in_out),
            "socket_type": str(item.socket_type),
        }
        if str(item.in_out) == "INPUT" and hasattr(item, "default_value"):
            try:
                row["default"] = _json_value(item.default_value)
            except (TypeError, ValueError):
                pass
        result.append(row)
    return result


def _node_payload(node):
    """Return semantic payload stored outside ordinary node input sockets."""
    if node.bl_idname == "ShaderNodeValue":
        return {"value": _json_value(node.outputs[0].default_value)}
    if node.bl_idname == "FunctionNodeInputString":
        return {"value": str(node.string)}
    if node.bl_idname == "FunctionNodeInputInt":
        return {"value": int(node.integer)}
    return {}


def _semantic_unlinked_defaults(node):
    """Return only unlinked input defaults that are part of NodeForge semantics.

    Most Blender nodes expose inactive or operation-specific defaults that NodeForge
    never reads. Recording them makes baselines depend on Blender UI defaults rather
    than DSL behavior. Keep only values that NodeForge deliberately writes or whose
    configured value changes the generated graph semantics.
    """
    refs = []
    if node.bl_idname == "ShaderNodeCombineXYZ":
        refs = [socket for socket in node.inputs[:3] if not socket.is_linked]
    elif node.bl_idname == "GeometryNodeObjectInfo":
        socket = node.inputs.get("As Instance")
        if socket is not None and not socket.is_linked:
            refs = [socket]
    result = {}
    for socket in refs:
        if not hasattr(socket, "default_value"):
            continue
        result[_socket_ref(node.inputs, socket)] = _json_value(socket.default_value)
    return result


def _node_snapshot(node, node_id):
    """Return deterministic observable state for one Blender node."""
    row = {
        "id": node_id,
        "bl_idname": str(node.bl_idname),
        "properties": {},
    }
    for name in NODE_PROPERTIES:
        if not hasattr(node, name):
            continue
        value = getattr(node, name)
        if value is None or isinstance(value, (bool, int, float, str)):
            row["properties"][name] = value
    payload = _node_payload(node)
    if payload:
        row["payload"] = payload
    defaults = _semantic_unlinked_defaults(node)
    if defaults:
        row["defaults"] = defaults
    return row

def group_snapshot(group):
    """Return deterministic JSON-native observable state for one node group."""
    nodes = list(group.nodes)
    node_ids = {node.as_pointer(): f"n{index}" for index, node in enumerate(nodes)}
    snapshot = {
        "interface": _interface_snapshot(group),
        "nodes": [
            _node_snapshot(node, node_ids[node.as_pointer()])
            for node in nodes
        ],
        "links": [],
    }
    for link in group.links:
        snapshot["links"].append({
            "from": [
                node_ids[link.from_node.as_pointer()],
                _socket_ref(link.from_node.outputs, link.from_socket),
            ],
            "to": [
                node_ids[link.to_node.as_pointer()],
                _socket_ref(link.to_node.inputs, link.to_socket),
            ],
        })
    snapshot["links"].sort(
        key=lambda row: (row["from"][0], row["from"][1], row["to"][0], row["to"][1])
    )
    _assert_json_native(snapshot)
    round_trip = json.loads(json.dumps(snapshot))
    if snapshot != round_trip:
        raise AssertionError("group snapshot is not JSON round-trip stable")
    return snapshot




def _validate_baseline_document(baseline, case_id):
    """Validate the minimal committed baseline document shape for one case."""
    _assert_json_native(baseline, f"expected/{case_id}.json")
    if not isinstance(baseline, dict):
        raise ValueError(f"baseline for case {case_id} must be a JSON object")
    kind = baseline.get("kind")
    if kind == "graph":
        if set(baseline) != {"kind", "graph"}:
            raise ValueError(
                f"graph baseline for case {case_id} must contain exactly kind and graph"
            )
        graph = baseline["graph"]
        if not isinstance(graph, dict) or set(graph) != {"interface", "nodes", "links"}:
            raise ValueError(
                f"graph baseline for case {case_id} must contain interface, nodes and links"
            )
        for field in ("interface", "nodes", "links"):
            if not isinstance(graph[field], list):
                raise ValueError(
                    f"graph baseline for case {case_id} field {field!r} must be a list"
                )
        return
    if kind == "compile_error":
        if set(baseline) != {"kind", "exception", "message"}:
            raise ValueError(
                f"compile-error baseline for case {case_id} must contain exactly "
                "kind, exception and message"
            )
        if not isinstance(baseline["exception"], str) or not isinstance(baseline["message"], str):
            raise ValueError(
                f"compile-error baseline for case {case_id} requires string exception and message"
            )
        return
    raise ValueError(f"baseline for case {case_id} has invalid kind: {kind!r}")


def case_paths(case_id):
    """Derive the source and baseline paths for one flat case identifier."""
    if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id):
        raise ValueError(f"Invalid flat characterization case id: {case_id!r}")
    return SCRIPTS_DIR / f"{case_id}.nf", EXPECTED_DIR / f"{case_id}.json"


def _read_manifest_raw():
    """Load the raw manifest JSON before semantic validation."""
    with MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("expression characterization manifest must be a JSON array")
    return data


def validate_cases(*, allow_missing_baselines_for=()):
    """Validate manifest/files, optionally allowing selected record targets to lack baselines."""
    allowed_missing = set(allow_missing_baselines_for)
    manifest = _read_manifest_raw()
    seen = set()
    normalized = []
    for index, entry in enumerate(manifest):
        if not isinstance(entry, dict) or set(entry) != {"id", "outcome", "description", "tags"}:
            raise ValueError(f"manifest entry {index} must contain exactly id, outcome, description, tags")
        case_id = entry["id"]
        outcome = entry["outcome"]
        description = entry["description"]
        tags = entry["tags"]
        if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id):
            raise ValueError(f"invalid flat case id: {case_id!r}")
        if case_id in seen:
            raise ValueError(f"duplicate characterization case id: {case_id}")
        seen.add(case_id)
        if outcome not in {"graph", "compile_error"}:
            raise ValueError(f"case {case_id} has invalid outcome: {outcome!r}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"case {case_id} requires a non-empty description")
        if not isinstance(tags, list) or not tags or any(not isinstance(tag, str) or not tag for tag in tags):
            raise ValueError(f"case {case_id} requires a non-empty string tag list")
        if len(set(tags)) != len(tags):
            raise ValueError(f"case {case_id} contains duplicate tags")
        source_path, expected_path = case_paths(case_id)
        if not source_path.is_file():
            raise ValueError(f"missing source script for case {case_id}: {source_path}")
        if expected_path.is_file():
            with expected_path.open("r", encoding="utf-8") as handle:
                baseline = json.load(handle)
            _validate_baseline_document(baseline, case_id)
            if case_id not in allowed_missing and baseline.get("kind") != outcome:
                raise ValueError(
                    f"baseline outcome mismatch for case {case_id}: "
                    f"manifest expects {outcome!r}, baseline stores {baseline.get('kind')!r}"
                )
        elif case_id not in allowed_missing:
            raise ValueError(f"missing baseline for case {case_id}: {expected_path}")
        normalized.append({"id": case_id, "outcome": outcome, "description": description, "tags": list(tags)})

    script_ids = {path.stem for path in SCRIPTS_DIR.glob("*.nf")}
    expected_ids = {path.stem for path in EXPECTED_DIR.glob("*.json")}
    orphan_scripts = sorted(script_ids - seen)
    orphan_expected = sorted(expected_ids - seen)
    if orphan_scripts:
        raise ValueError(f"orphan characterization scripts: {orphan_scripts}")
    if orphan_expected:
        raise ValueError(f"orphan characterization baselines: {orphan_expected}")
    return normalized


def load_manifest():
    """Load the strictly validated authoritative characterization manifest."""
    return validate_cases()


def select_cases(*, case_id=None, tag=None, pre_record=False):
    """Select manifest cases by exact flat ID or one tag."""
    if case_id is not None and tag is not None:
        raise ValueError("select either case_id or tag, not both")
    raw = _read_manifest_raw()
    ids = [entry.get("id") for entry in raw if isinstance(entry, dict)]
    if case_id is not None:
        if case_id not in ids:
            raise ValueError(f"unknown characterization case id: {case_id}")
        selected_ids = {case_id}
    elif tag is not None:
        selected_ids = {
            entry.get("id") for entry in raw
            if isinstance(entry, dict) and tag in entry.get("tags", [])
        }
        if not selected_ids:
            raise ValueError(f"no characterization cases have tag: {tag}")
    else:
        selected_ids = set(ids)
    manifest = validate_cases(
        allow_missing_baselines_for=selected_ids if pre_record else (),
    )
    return [entry for entry in manifest if entry["id"] in selected_ids]


def load_case_source(case_id):
    """Load one committed NodeForge DSL source file unchanged."""
    source_path, _ = case_paths(case_id)
    return source_path.read_text(encoding="utf-8")


def load_case_baseline(case_id):
    """Load and validate one committed normalized baseline JSON document."""
    _, expected_path = case_paths(case_id)
    with expected_path.open("r", encoding="utf-8") as handle:
        baseline = json.load(handle)
    _validate_baseline_document(baseline, case_id)
    return baseline


def _group_pointer_set():
    """Return current node-group datablock identities for leak checks."""
    return {group.as_pointer() for group in bpy.data.node_groups}


def _fresh_group_references(group, fresh_pointers):
    """Return fresh node-group datablocks referenced by group-call nodes in one group."""
    references = set()
    for node in group.nodes:
        if node.bl_idname != "GeometryNodeGroup":
            continue
        dependency = getattr(node, "node_tree", None)
        if dependency is not None and dependency.as_pointer() in fresh_pointers:
            references.add(dependency.as_pointer())
    return references


def _remove_fresh_groups(before):
    """Remove case-created groups consumer-first, then verify complete isolation cleanup."""
    remaining = {
        group.as_pointer(): group
        for group in bpy.data.node_groups
        if group.as_pointer() not in before
    }
    while remaining:
        pointers = set(remaining)
        referenced = set()
        for group in remaining.values():
            referenced.update(_fresh_group_references(group, pointers))
        consumers = [group for pointer, group in remaining.items() if pointer not in referenced]
        if not consumers:
            # A cycle is not expected from NodeForge helpers. do_unlink keeps cleanup deterministic
            # if a malformed/future graph nevertheless creates one.
            consumers = [next(iter(remaining.values()))]
        for group in consumers:
            pointer = group.as_pointer()
            if pointer in _group_pointer_set():
                bpy.data.node_groups.remove(group, do_unlink=True)
            remaining.pop(pointer, None)
    after = _group_pointer_set()
    if after != before:
        raise AssertionError("characterization case cleanup did not restore node-group set")


def run_case(case):
    """Compile one case and return its normalized graph or controlled failure result."""
    if isinstance(case, str):
        matches = [entry for entry in load_manifest() if entry["id"] == case]
        if not matches:
            raise ValueError(f"unknown characterization case id: {case}")
        case = matches[0]
    case_id = case["id"]
    expected_outcome = case["outcome"]
    source = load_case_source(case_id)
    before = _group_pointer_set()
    actual_outcome = None
    try:
        try:
            group = compiler.create_expression_group(source, f"NFChar_{case_id}")
        except CompileError as exc:
            actual_outcome = "compile_error"
            result = {
                "kind": "compile_error",
                "exception": type(exc).__name__,
                "message": str(exc),
            }
        else:
            actual_outcome = "graph"
            result = {
                "kind": "graph",
                "graph": group_snapshot(group),
            }
        _assert_json_native(result)
        if actual_outcome != expected_outcome:
            raise AssertionError(
                f"characterization case {case_id} expected outcome {expected_outcome!r}, "
                f"got {actual_outcome!r}"
            )
        return result
    finally:
        fresh_before_cleanup = _group_pointer_set() - before
        _remove_fresh_groups(before)
        if actual_outcome == "compile_error" and fresh_before_cleanup:
            raise AssertionError(
                f"compile failure leaked fresh node groups for case {case_id}: "
                f"{len(fresh_before_cleanup)} group(s)"
            )

def _pretty_json(value):
    """Render deterministic human-readable JSON for files and mismatch output."""
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def assert_case_result(case_id, actual, expected):
    """Compare one normalized result and raise with a readable unified JSON diff."""
    _assert_json_native(actual, f"actual/{case_id}.json")
    if actual == expected:
        return
    expected_text = _pretty_json(expected).splitlines(keepends=True)
    actual_text = _pretty_json(actual).splitlines(keepends=True)
    diff = "".join(difflib.unified_diff(
        expected_text,
        actual_text,
        fromfile=f"expected/{case_id}.json",
        tofile=f"actual/{case_id}.json",
    ))
    raise AssertionError(f"characterization baseline mismatch for {case_id}\n{diff}")


def assert_group_snapshot(group, expected):
    """Compare a compiled group snapshot with one expected JSON-native structure."""
    actual = group_snapshot(group)
    if actual != expected:
        raise AssertionError("compiled group snapshot does not match expected structure")


def record_cases(cases):
    """Compile selected cases and overwrite their deterministic committed baselines."""
    written = []
    for entry in cases:
        case_id = entry["id"]
        actual = run_case(entry)
        _, expected_path = case_paths(case_id)
        expected_path.write_text(_pretty_json(actual), encoding="utf-8")
        written.append(expected_path)
        print(f"recorded {expected_path.relative_to(ROOT)}")
    validate_cases()
    return written


def _configure_isolated_packages():
    """Isolate direct recorder runs from user-installed NodeForge package inventories."""
    temp = TemporaryDirectory(prefix="nodeforge-characterization-")
    packages.set_packages_dir_for_tests(Path(temp.name) / "packages")
    packages.invalidate_caches()
    systems_registry.invalidate_cache()
    return temp


def _reset_isolated_packages(temp):
    """Restore package inventory configuration after a direct recorder run."""
    packages.set_packages_dir_for_tests(None)
    packages.invalidate_caches()
    systems_registry.invalidate_cache()
    temp.cleanup()


def main(argv=None):
    """Record all, one, or one tag-selected set of characterization baselines."""
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="record every manifest case")
    selection.add_argument("--case", dest="case_id", help="record one flat case id")
    selection.add_argument("--tag", help="record all cases carrying one tag")
    args = parser.parse_args(argv)
    temp = _configure_isolated_packages()
    try:
        cases = select_cases(
            case_id=args.case_id,
            tag=args.tag,
            pre_record=True,
        )
        record_cases(cases)
    finally:
        _reset_isolated_packages(temp)
    return 0


if __name__ == "__main__":
    forwarded = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    raise SystemExit(main(forwarded))

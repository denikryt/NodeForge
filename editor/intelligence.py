"""Blender-independent symbol completion and signature help for incomplete DSL text.

Catalog selection comes from the same ResolvedEnvironment used by the compiler.
Only the cursor context and preliminary display of source input declarations are
editor-specific; this module neither compiles a group nor executes DSL source.
"""

from __future__ import annotations

import ast
import inspect
import io
import keyword
import re
import tokenize
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..semantic.builtin_registry import BUILTIN_NAMES
from ..semantic.callable_contracts import normalize_callable_keyword


@dataclass(frozen=True)
class Parameter:
    """One displayable parameter with an optional usable keyword spelling."""

    name: str
    typ: str = ""
    default: str | None = None
    keyword: str | None = None

    def label(self) -> str:
        """Format a compact signature parameter."""
        return f"{self.name}{': ' + self.typ if self.typ else ''}{' = ' + self.default if self.default is not None else ''}"


@dataclass(frozen=True)
class Output:
    """One named return value with a compiler-confirmed or unknown type."""

    name: str
    typ: str


@dataclass(frozen=True)
class Signature:
    """Editor projection of one public callable alternative."""

    name: str
    parameters: tuple[Parameter, ...] = ()
    outputs: tuple[Output, ...] = ()
    source_record: object | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Suggestion:
    """Selectable completion with a source replacement span in the current line."""

    label: str
    insert: str
    kind: str
    detail: str = ""


@dataclass(frozen=True)
class Assistance:
    """Cursor-scoped suggestions and signature help for one text snapshot."""

    candidates: tuple[Suggestion, ...] = ()
    signatures: tuple[Signature, ...] = ()
    active_parameter: int = 0
    replace_start: int = 0
    replace_end: int = 0
    line: int = 0


# A small explicit core subset, not a second exhaustive representation of the
# compiler's built-in call contracts. All other builtins still complete by name.
_CORE_SIGNATURES = {
    "cube": ("size: Vector | Float = 1.0",),
    "points": ("count: Int",),
    "vector": ("x: Float", "y: Float", "z: Float"),
    "transform": ("geometry: Geometry", "translation: Vector", "rotation: Vector", "scale: Vector | Float"),
    "set_position": ("geometry: Geometry", "position: Vector", "selection: Bool"),
    "output": ("name: String", "value"),
    "input_float": ("name: String", "default: Float = 0.0"),
    "input_int": ("name: String", "default: Int = 0"),
    "input_vector": ("name: String", "default: Vector"),
    "input_bool": ("name: String", "default: Bool = False"),
    "join": ("geometries",),
}


# Only stable, unambiguous core results are declared here. The authoritative
# runtime contract remains in the semantic frontend.
_CORE_OUTPUT_TYPES = {
    "cube": "Geometry", "points": "Geometry", "vector": "Vector",
    "transform": "Geometry", "set_position": "Geometry", "join": "Geometry",
    "input_float": "Float", "input_int": "Int", "input_vector": "Vector",
    "input_bool": "Bool",
}


def _core_signature(name: str) -> Signature:
    """Return a curated core signature or an intentionally unspecified one."""
    params = []
    for label in _CORE_SIGNATURES.get(name, ()):
        head, separator, default = label.partition(" = ")
        key, colon, typ = head.partition(": ")
        positional_only = (name == "points" or name == "join" or
                           (name == "set_position" and key != "selection") or
                           (name == "transform" and key == "geometry") or
                           name == "output")
        params.append(Parameter(key, typ if colon else "", default if separator else None,
                                None if positional_only else key))
    typ = _CORE_OUTPUT_TYPES.get(name)
    outputs = (Output("Value", typ),) if typ else ()
    return Signature(name, tuple(params), outputs)


def _display_keyword(label: str) -> str | None:
    """Render a readable legal keyword with the compiler's normalization semantics."""
    name = re.sub(r"\W+", "_", label.casefold()).strip("_")
    if not name.isidentifier() or keyword.iskeyword(name):
        return None
    return name if normalize_callable_keyword(name) == normalize_callable_keyword(label) else None


def _source_signature(name: str, source: str) -> Signature:
    """Inspect declarations without executing source or building Blender nodes.

    This provisional display handles literal input labels and defaults; richer
    compile-time labels remain opaque until a future semantic-only projection.
    """
    try:
        body = ast.parse(source).body
    except SyntaxError:
        return Signature(name)
    params = []
    outputs = []
    for statement in body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            call = statement.value
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                continue
            if not call.func.id.startswith("input_") or not call.args:
                continue
            label_expr = call.args[0]
            if not isinstance(label_expr, ast.Constant) or not isinstance(label_expr.value, str):
                continue
            label = label_expr.value
            default_arg = next((arg.value for arg in call.keywords if arg.arg == "default"), None)
            default = ast.unparse(default_arg) if default_arg is not None else None
            typ = call.func.id.removeprefix("input_").capitalize()
            params.append(Parameter(label, typ, default, _display_keyword(label)))
        elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if isinstance(call.func, ast.Name) and call.func.id == "output" and len(call.args) >= 2:
                label = call.args[0]
                if isinstance(label, ast.Constant) and isinstance(label.value, str):
                    outputs.append(Output(label.value, "?"))
    keys = [normalize_callable_keyword(p.name) for p in params]
    # A repeated normalized input label must be supplied positionally.
    params = [Parameter(p.name, p.typ, p.default, p.keyword if keys.count(keys[i]) == 1 else None)
              for i, p in enumerate(params)]
    return Signature(name, tuple(params), tuple(outputs))


def _def_signatures(source: str) -> dict[str, tuple[Signature, ...]]:
    """Collect top-level local function headers even when later text is incomplete."""
    result = {}
    # AST parse of a standalone header accepts annotations/defaults without
    # requiring the remainder of the currently edited file to be valid.
    for line in source.splitlines():
        if not line.startswith("def ") or not line.rstrip().endswith(":"):
            continue
        try:
            node = ast.parse(line + "\n    pass").body[0]
        except SyntaxError:
            continue
        if not isinstance(node, ast.FunctionDef):
            continue
        all_args = node.args.posonlyargs + node.args.args
        offset = len(all_args) - len(node.args.defaults)
        parameters = []
        for i, arg in enumerate(all_args):
            typ = ast.unparse(arg.annotation) if arg.annotation else ""
            default = ast.unparse(node.args.defaults[i - offset]) if i >= offset else None
            parameters.append(Parameter(arg.arg, typ, default, arg.arg))
        returns = ()
        if node.returns is not None:
            returns = (Output("Value", ast.unparse(node.returns)),)
        result[node.name] = (Signature(node.name, tuple(parameters), returns),)
    return result


def _typespec_label(spec) -> str:
    """Convert an extension's frozen TypeSpec to a concise display label."""
    if spec.kind == "NF_SET":
        return " | ".join(sorted(t.value.title() for t in spec.nf_types))
    if spec.kind == "TUPLE_FIXED":
        return "tuple[" + ", ".join(_typespec_label(item) for item in spec.items) + "]"
    if spec.kind == "RECORD":
        return spec.record_type.name
    if spec.kind == "PY_SCALAR":
        return spec.python_scalar_kind.value.title()
    return spec.kind.title()


def _extension_signatures(name: str, specs) -> tuple[Signature, ...]:
    """Project all overloads of an Extension API callable without importing backends."""
    result = []
    for spec in specs:
        parameters = []
        for param in spec.parameters:
            default = None if param.default is inspect.Parameter.empty else repr(param.default)
            kw = None if param.kind is inspect.Parameter.POSITIONAL_ONLY else param.name
            parameters.append(Parameter(param.name, _typespec_label(param.type_spec), default, kw))
        if spec.result.kind == "TUPLE_FIXED":
            outputs = tuple(Output(f"Value {i + 1}", _typespec_label(item))
                            for i, item in enumerate(spec.result.items))
        else:
            outputs = (Output("Value", _typespec_label(spec.result)),)
        result.append(Signature(name, tuple(parameters), outputs))
    return tuple(result)


def _return_preview(signature: Signature) -> str:
    """Show short verified result types beside member-completion names."""
    types = [value.typ for value in signature.outputs]
    if not types or "?" in types:
        return ""
    if len(types) == 1:
        return "→ " + types[0]
    return "→ (" + ", ".join(types) + ")"


class SymbolIndex:
    """Expose names and signatures selected by a compiler environment snapshot."""

    def __init__(self, environment):
        """Retain one immutable environment; resolve imported names on demand."""
        self.environment = environment
        self._source_cache: dict[Path, tuple[int, Signature]] = {}
        self._typed_cache: dict[Path, tuple[int, tuple[Output, ...] | None]] = {}

    def _record_signatures(self, record, name: str) -> tuple[Signature, ...]:
        """Read a selected source function's public parameters lazily."""
        path = getattr(record, "source_path", None)
        if path is None:
            return ()
        try:
            stamp = path.stat().st_mtime_ns
            cached = self._source_cache.get(path)
            if cached is None or cached[0] != stamp:
                signature = _source_signature(name, path.read_text(encoding="utf-8"))
                self._source_cache[path] = (stamp, signature)
            else:
                signature = cached[1]
        except OSError:
            return ()
        return (Signature(name, signature.parameters, signature.outputs, record),)

    def _typed_signature(self, signature: Signature) -> Signature:
        """Resolve source output types lazily using the existing semantic frontend.

        Member inventories remain cheap: this runs only for the function at the
        caret, not for every exported function in a package or Local folder.
        Failed semantic preparation keeps the untyped source labels visible.
        """
        record = signature.source_record
        if record is None:
            return signature
        path = record.source_path
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return signature
        cached = self._typed_cache.get(path)
        if cached is not None and cached[0] == stamp:
            outputs = cached[1]
        else:
            try:
                from ..compiler_identities import GroupCompilationIdentity, library_function_id_for_record
                from ..semantic.source_callable_session import SourceCallableSession

                function_id = library_function_id_for_record(record)
                key = function_id.stable_key()
                identity = GroupCompilationIdentity(None, f"EDITOR/{key}", key, key)
                prepared = SourceCallableSession(resolved_environment=self.environment).prepare_library(
                    function_id=function_id, identity=identity, record=record
                )
                outputs = tuple(Output(item.display_name, item.typ.value.title())
                                for item in prepared.contract.outputs)
            except Exception:
                # Incomplete dependencies must not break editing. Remember the
                # failure until the source changes or the user refreshes catalogs.
                outputs = None
            self._typed_cache[path] = (stamp, outputs)
        return replace(signature, outputs=outputs) if outputs is not None else signature

    def _export_signatures(self, export, name: str) -> tuple[Signature, ...]:
        """Read source-backed or Extension API signatures of a selected export."""
        if export.extension_callable_id is not None:
            specs = self.environment.extension_registry.callable_specs(export.extension_callable_id)
            return _extension_signatures(name, specs)
        if export.record is not None:
            return self._record_signatures(export.record, name)
        return ()

    def symbols(self, source: str) -> dict[str, dict[str, tuple[Signature, ...]]]:
        """Bind imported packages and Local functions with the compiler's catalog rules.

        The result maps the empty qualifier to top-level names and namespace
        aliases to their member tables. Invalid/ambiguous names are omitted.
        """
        env = self.environment
        top = {name: (_core_signature(name),) for name in BUILTIN_NAMES}
        top.update(_def_signatures(source))
        namespaces: dict[str, dict[str, tuple[Signature, ...]]] = {"": top}
        local_index = env.catalog("local").local_index
        # One-line imports are valid even if the expression at the caret isn't.
        for line in source.splitlines():
            if not line.lstrip().startswith("from "):
                continue
            try:
                statement = ast.parse(line.strip()).body[0]
            except SyntaxError:
                continue
            if not isinstance(statement, ast.ImportFrom):
                continue
            module = statement.module or ""
            if module == "packages":
                for item in statement.names:
                    namespace = env.package_by_import_name(item.name)
                    if namespace is None:
                        continue
                    alias = item.asname or item.name
                    namespaces[alias] = {
                        name: self._export_signatures(export, name)
                        for name, export in namespace.exports.items()
                    }
            elif module == "local" or module.startswith("local."):
                if local_index is None:
                    continue
                for item in statement.names:
                    alias = item.asname or item.name
                    if item.name == "*":
                        for name in local_index.flat_names():
                            try:
                                record = local_index.find_flat_function(name)
                            except Exception:
                                continue
                            if record is not None:
                                top[name] = self._record_signatures(record, name)
                        continue
                    try:
                        if module == "local":
                            selected = local_index.resolve_root_import(item.name)
                        else:
                            directory = local_index.resolve_directory(tuple(module.split(".")[1:]))
                            selected = local_index.resolve_member(directory, item.name)
                    except Exception:
                        continue
                    if selected is None:
                        continue
                    from ..local_index import LocalDirectoryCandidate
                    if isinstance(selected, LocalDirectoryCandidate):
                        members = {}
                        for member_name in local_index.path_members.get(selected.import_path, ()):
                            try:
                                member = local_index.resolve_member(selected, member_name)
                            except Exception:
                                continue
                            if hasattr(member, "source_path"):
                                members[member_name] = self._record_signatures(member, member_name)
                                if item.asname is None and member.source_path.suffix == ".nf":
                                    top.setdefault(member_name, members[member_name])
                        namespaces[alias] = members
                    else:
                        top[alias] = self._record_signatures(selected, alias)
            elif module == "examples":
                for item in statement.names:
                    try:
                        record = env.catalog("examples").find(item.name)
                    except Exception:
                        continue
                    if record is not None:
                        top[item.asname or item.name] = self._record_signatures(record, item.asname or item.name)
        return namespaces


def _tokens_before(source: str, offset: int):
    """Yield tokens from the incomplete source prefix, ignoring tokenizer EOF errors."""
    text = source[:offset]
    try:
        yield from tokenize.generate_tokens(io.StringIO(text).readline)
    except (tokenize.TokenError, IndentationError):
        return


def cursor_assistance(source: str, line: int, column: int, index: SymbolIndex) -> Assistance:
    """Return member/name/keyword completions and signature help at the caret.

    Token depth, rather than comma counting in raw text, identifies the current
    argument even with nested calls, lists and strings. Parsing incomplete
    Python is intentionally not required.
    """
    lines = source.split("\n")
    if not 0 <= line < len(lines):
        return Assistance()
    column = min(column, len(lines[line]))
    starts = [0]
    for item in lines:
        starts.append(starts[-1] + len(item) + 1)
    offset = starts[line] + column
    prefix = source[:offset]
    tokens = list(_tokens_before(source, offset))
    for token in tokens:
        if token.start[0] - 1 > line:
            continue
        if token.type == tokenize.COMMENT and token.end[0] - 1 == line:
            return Assistance(line=line)
        if token.type == tokenize.ERRORTOKEN and token.string in {"'", '"'}:
            # An unmatched quote before the caret is an incomplete string.
            return Assistance(line=line)
        if token.type == tokenize.STRING:
            first = starts[token.start[0] - 1] + token.start[1]
            last = starts[token.end[0] - 1] + token.end[1]
            if first <= offset < last:
                return Assistance(line=line)
    # Inside an unfinished call tokenize may stop at '(' before emitting the
    # unterminated string. Check only the un-tokenized tail for an open quote.
    last_token_end = 0
    if tokens:
        last = tokens[-1]
        if 0 < last.end[0] <= len(lines):
            last_token_end = starts[last.end[0] - 1] + last.end[1]
    quote = None
    escaped = False
    for character in prefix[last_token_end:]:
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in {"'", '"'}:
            quote = character
        elif character == "#":
            break
    if quote:
        return Assistance(line=line)

    # Import completion uses the same frozen Local index and package inventory
    # as compilation, without requiring the import line to be parseable yet.
    import_match = re.fullmatch(
        r"\s*from\s+(packages|local(?:\.[A-Za-z_]\w*)*|examples)\s+import\s*([A-Za-z_]\w*)?",
        prefix.split("\n")[-1],
    )
    if import_match:
        module, partial = import_match.groups()
        partial = partial or ""
        env = index.environment
        available = set()
        if module == "packages":
            available = {ns.import_name for ns in env.package_namespaces.values()}
        elif module == "examples":
            available = set(env.catalog("examples").names())
        else:
            local_index = env.catalog("local").local_index
            if local_index is not None:
                if module == "local":
                    available.update(local_index.flat_names())
                    available.update(local_index.path_members.get((), {}))
                    def selected(name):
                        """Resolve one Local root import without accepting collisions."""
                        return local_index.resolve_root_import(name)
                else:
                    directory = local_index.resolve_directory(tuple(module.split(".")[1:]))
                    available.update(local_index.path_members.get(directory.import_path, {}))
                    def selected(name):
                        """Resolve one qualified Local member without accepting collisions."""
                        return local_index.resolve_member(directory, name)
                unique = set()
                for name in available:
                    try:
                        if selected(name) is not None:
                            unique.add(name)
                    except Exception:
                        continue
                available = unique
        candidates = tuple(Suggestion(name, name, "import") for name in sorted(available) if name.startswith(partial) and name != partial)
        return Assistance(candidates, (), 0, column - len(partial), column, line)

    names = index.symbols(source)
    frames: list[dict] = []
    for tok in tokens:
        if tok.type != tokenize.OP:
            continue
        position = starts[tok.start[0] - 1] + tok.start[1]
        if tok.string in "([{":
            before = source[:position]
            match = re.search(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*$", before)
            name = match.group(1) if tok.string == "(" and match else None
            frames.append({"open": tok.string, "name": name, "position": 0,
                           "arg_start": position + 1, "used": set()})
        elif tok.string in ")]}":
            if frames:
                frames.pop()
        elif tok.string == "," and frames and frames[-1]["open"] == "(":
            frame = frames[-1]
            segment = source[frame["arg_start"]:position]
            previous = re.match(r"\s*([A-Za-z_]\w*)\s*=", segment)
            if previous:
                frame["used"].add(previous.group(1))
            frame["position"] += 1
            frame["arg_start"] = position + 1

    frame = next((item for item in reversed(frames) if item["name"]), None)
    signatures = ()
    active_parameter = 0
    if frame is not None:
        path = frame["name"].split(".")
        signatures = tuple(index._typed_signature(signature) for signature in
                           names.get(".".join(path[:-1]), {}).get(path[-1], ()))
        active_parameter = frame["position"]
        segment = prefix[frame["arg_start"]:]
        named = re.match(r"\s*([A-Za-z_]\w*)\s*=", segment)
        used = set(frame["used"])
        if named:
            used.add(named.group(1))
            for signature in signatures:
                for i, parameter in enumerate(signature.parameters):
                    if parameter.keyword == named.group(1):
                        active_parameter = i
                        break
        if signatures and not named:
            partial_match = re.fullmatch(r"\s*([A-Za-z_]\w*)?", segment)
            if partial_match:
                partial = partial_match.group(1) or ""
                seen = {}
                for signature in signatures:
                    for parameter in signature.parameters:
                        key = parameter.keyword
                        if key and key not in used and key.startswith(partial):
                            seen[key] = Suggestion(key + "=", key + "=", "parameter", parameter.label())
                candidates = tuple(seen.values())
                return Assistance(candidates, signatures, active_parameter, column - len(partial), column, line)

    suffix = prefix.split("\n")[-1]
    match = re.search(r"(?<![\w.])(?:(\w+)\.)?([A-Za-z_]\w*)?$", suffix)
    if match:
        qualifier, partial = match.groups()
        partial = partial or ""
        table = names.get(qualifier or "", {})
        if qualifier is not None or len(partial) >= 2:
            candidates = tuple(
                Suggestion(name, name, "function", _return_preview(sigs[0]) if sigs else "")
                for name, sigs in sorted(table.items()) if name.startswith(partial) and name != partial
            )
            return Assistance(candidates, signatures, active_parameter, column - len(partial), column, line)
    return Assistance((), signatures, active_parameter, column, column, line)

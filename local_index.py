"""Immutable Local selection over one supplied filesystem discovery snapshot."""

from dataclasses import dataclass
import os
from types import MappingProxyType
from typing import Mapping

from .catalog import LibraryEntryRecord, is_public_local_name
from .errors import CompileError


@dataclass(frozen=True)
class LocalDirectoryCandidate:
    """Select one physical directory at an exact frontend import path."""

    import_path: tuple[str, ...]
    physical_directory_origin: str

    def __post_init__(self):
        """Freeze exact path components and reject non-importable descriptors."""
        path = tuple(self.import_path)
        if not path or not all(is_public_local_name(x) for x in path) or not self.physical_directory_origin:
            raise ValueError("Invalid Local directory descriptor")
        object.__setattr__(self, 'import_path', path)


@dataclass(frozen=True)
class LocalMember:
    """Retain all distinct files and directories competing at one import position."""

    source_origins: tuple[str, ...] = ()
    directory_candidates: tuple[LocalDirectoryCandidate, ...] = ()

    def __post_init__(self):
        """Freeze competing candidates in deterministic canonical origin order."""
        object.__setattr__(self, 'source_origins', tuple(sorted(set(self.source_origins))))
        object.__setattr__(self, 'directory_candidates', tuple(sorted(set(self.directory_candidates), key=lambda x: x.physical_directory_origin)))


@dataclass(frozen=True)
class LocalSourceIndex:
    """Own canonical source records and immutable flat/qualified snapshot views."""

    by_origin: Mapping[str, LibraryEntryRecord]
    flat_candidates: Mapping[str, tuple[str, ...]]
    path_members: Mapping[tuple[str, ...], Mapping[str, LocalMember]]

    def __post_init__(self):
        """Defensively freeze and validate source/position referential integrity."""
        origins = dict(self.by_origin)
        flat = {name: tuple(sorted(set(values))) for name, values in self.flat_candidates.items()}
        members = {tuple(path): MappingProxyType(dict(values)) for path, values in self.path_members.items()}
        for origin, record in origins.items():
            if (
                not isinstance(record, LibraryEntryRecord)
                or record.namespace != 'local'
                or record.source_origin != origin
                or record.source_path is None
                or record.path != record.source_path
                or not record.source_path.is_absolute()
                or origin != os.path.normcase(os.path.normpath(str(record.source_path)))
                or not is_public_local_name(record.name)
            ):
                raise ValueError('Invalid canonical Local source record')
        expected = {}
        for origin, record in origins.items():
            expected.setdefault(record.name, []).append(origin)
        if flat != {name: tuple(sorted(values)) for name, values in expected.items()}:
            raise ValueError('Local flat candidates do not match canonical records')
        for path, values in members.items():
            if not all(is_public_local_name(x) for x in path):
                raise ValueError('Invalid Local namespace path')
            for name, member in values.items():
                if not is_public_local_name(name) or not isinstance(member, LocalMember):
                    raise ValueError('Invalid Local member')
                for origin in member.source_origins:
                    if origin not in origins or origins[origin].name != name:
                        raise ValueError('Dangling Local source position')
                for directory in member.directory_candidates:
                    if directory.import_path != path + (name,) or not directory.physical_directory_origin:
                        raise ValueError('Invalid Local directory position')
        object.__setattr__(self, 'by_origin', MappingProxyType(origins))
        object.__setattr__(self, 'flat_candidates', MappingProxyType(flat))
        object.__setattr__(self, 'path_members', MappingProxyType(members))

    @classmethod
    def from_discovery(cls, records, directories=(), source_positions=()):
        """Build views without performing filesystem reads or resolving collisions."""
        origins = {record.source_origin: record for record in records}
        flat = {}
        positions = {}
        for origin, record in origins.items():
            flat.setdefault(record.name, []).append(origin)
        for path, origin in directories:
            path = tuple(path)
            if not all(is_public_local_name(x) for x in path):
                continue
            slot = positions.setdefault(path[:-1], {}).setdefault(path[-1], [set(), set()])
            slot[1].add(LocalDirectoryCandidate(path, origin))
            positions.setdefault(path, {})
        for path, name, origin in source_positions:
            positions.setdefault(tuple(path), {}).setdefault(name, [set(), set()])[0].add(origin)
        members = {path: {name: LocalMember(tuple(files), tuple(dirs)) for name, (files, dirs) in values.items()} for path, values in positions.items()}
        return cls(origins, flat, members)

    def flat_names(self):
        """Return all flat names, including ambiguous names."""
        return frozenset(self.flat_candidates)

    def all_records(self):
        """Return every canonical source, including unqualified-only candidates."""
        return tuple(sorted(self.by_origin.values(), key=lambda r: (r.name.lower(), r.source_origin)))

    def find_origin(self, origin):
        """Select an exact captured origin without name fallback."""
        return self.by_origin.get(origin)

    def _select(self, name, member):
        """Reject collisions at selection and return exactly one member kind."""
        files, dirs = member.source_origins, member.directory_candidates
        if len(files) > 1 and not dirs:
            from .semantic.call_resolution import ambiguous_callable_error
            labels, suggestions = [], []
            for origin in files:
                qualified = self.qualified_imports(origin)
                if qualified:
                    labels.append(qualified[0][0])
                    suggestions.append(qualified[0][1])
                else:
                    labels.append(f'{origin} (rename the file/directory or resolve the physical collision)')
            raise ambiguous_callable_error(name, labels, suggestions)
        if len(files) + len(dirs) > 1:
            kind = 'file-versus-directory' if files and dirs else 'directory'
            paths = sorted((*files, *(d.physical_directory_origin for d in dirs)))
            raise CompileError(f"Ambiguous Local {kind} import {name!r}: " + ', '.join(paths))
        return self.by_origin[files[0]] if files else (dirs[0] if dirs else None)

    def find_flat_function(self, name):
        """Select an unambiguous flat callable independent of directory names."""
        return self._select(name, LocalMember(self.flat_candidates.get(name, ())))

    def resolve_root_import(self, name):
        """Select a globally flat file or explicitly imported root directory."""
        dirs = self.path_members.get((), {}).get(name, LocalMember()).directory_candidates
        return self._select(name, LocalMember(self.flat_candidates.get(name, ()), dirs))

    def resolve_directory(self, path):
        """Resolve every directory prefix before inspecting any requested member."""
        parent = ()
        selected = None
        for name in path:
            if not is_public_local_name(name):
                raise CompileError(f"Invalid Local directory name {name!r}; rename the physical directory using a valid public ASCII non-keyword identifier")
            member = self.path_members.get(parent, {}).get(name, LocalMember())
            selected = self._select(name, LocalMember(directory_candidates=member.directory_candidates))
            if selected is None:
                raise CompileError(f"Unknown Local directory prefix: local.{'.'.join(parent + (name,))}")
            parent = selected.import_path
        return selected

    def resolve_member(self, parent, name, *, source_extensions=None):
        """Select a member, optionally filtering source records before collision checks."""
        if self.resolve_directory(parent.import_path) != parent:
            raise CompileError('Local directory does not belong to this snapshot')
        member = self.path_members.get(parent.import_path, {}).get(name, LocalMember())
        if source_extensions is not None:
            # Bare directory exports are .nf-only; qualified lookup keeps both suffixes.
            member = LocalMember(
                tuple(origin for origin in member.source_origins
                      if self.by_origin[origin].source_path.suffix in source_extensions),
                member.directory_candidates,
            )
        return self._select(name, member)

    def qualified_imports(self, origin):
        """Offer only directory-qualified imports proven to select this exact file."""
        record = self.by_origin[origin]
        results = []
        for path, values in self.path_members.items():
            member = values.get(record.name)
            if not path or member is None or member.source_origins != (origin,) or member.directory_candidates:
                continue
            try:
                self.resolve_directory(path)
            except CompileError:
                continue
            module = 'local.' + '.'.join(path)
            results.append((f'{module}.{record.name}', f'from {module} import {record.name}'))
        return tuple(sorted(results))

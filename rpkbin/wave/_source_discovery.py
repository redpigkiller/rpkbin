"""Resolve log inputs and discover new files for a live workspace."""

from collections import Counter
import os
from pathlib import Path

from rpkbin.flow import Flow

from .sources import FileSource, TailSource


_ARCHIVE_SUFFIXES = (".sqlite3", ".sqlite3-wal", ".sqlite3-shm")
_TEMPORARY_DIRECTORY_ERRORS = (FileNotFoundError, NotADirectoryError, PermissionError)


def _path_key(path):
    return os.path.normcase(str(path))


def _source_name(path, directories, recursive):
    if recursive:
        for root in directories:
            if path.is_relative_to(root):
                return path.relative_to(root).as_posix()
    return path.name


def _directory_error(exc):
    if not isinstance(exc, _TEMPORARY_DIRECTORY_ERRORS):
        raise exc


def _scan(workspace, directories, globs, recursive):
    archive_key = _path_key(workspace.archive.path.resolve()) if workspace.archive else None
    for root in directories:
        try:
            entries = (Path(directory) / name
                       for directory, _, files in os.walk(root, onerror=_directory_error)
                       for name in files) if recursive else root.iterdir()
            for entry in entries:
                if entry.name.lower().endswith(_ARCHIVE_SUFFIXES):
                    continue
                relative = entry.relative_to(root)
                if not any(relative.match(pattern) for pattern in globs):
                    continue
                try:
                    if not entry.is_file():
                        continue
                    path = entry.resolve()
                except OSError:
                    # An entry may vanish or be locked between listing and stat.
                    continue
                if (_path_key(path) != archive_key
                        and not path.name.lower().endswith(_ARCHIVE_SUFFIXES)):
                    yield path, _source_name(entry, directories, recursive)
        except _TEMPORARY_DIRECTORY_ERRORS:
            # Retry if the directory is temporarily unavailable or replaced.
            continue


def _register(workspace, candidates, source_type, flow, options):
    pending = {}
    for path, name in candidates:
        key = _path_key(path)
        if key not in workspace._discovered_paths:
            pending.setdefault(key, (path, name))

    used = {_path_key(job.name) for job in workspace.jobs}
    counts = Counter(_path_key(name) for _, name in pending.values())
    jobs = []
    for key, (path, name) in sorted(pending.items()):
        if counts[_path_key(name)] > 1 or _path_key(name) in used:
            name = f"{path.parent.name}/{path.name}"
            if _path_key(name) in used:
                name = path.as_posix()
        job = workspace.add_source(source_type(path, **options), flow=flow, name=name)
        jobs.append(job)
        used.add(_path_key(job.name))
        workspace._discovered_paths.add(key)
    return jobs


def add_paths(workspace, paths, *, once, globs, recursive, flow, source_options):
    workspace._require_no_job_event()
    if flow is not None and not isinstance(flow, Flow):
        raise TypeError("flow must be a Flow")
    globs = (globs,) if isinstance(globs, str) else tuple(globs)
    if not globs or any(not isinstance(pattern, str) or not pattern.strip() for pattern in globs):
        raise ValueError("globs must contain nonempty patterns")
    unique = {}
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        unique.setdefault(_path_key(path), path)

    directories, files = [], []
    for path in unique.values():
        if path.is_dir():
            directories.append(path)
        else:
            if once and not path.is_file():
                raise FileNotFoundError(f"Source is not a file: {path}")
            files.append(path)

    source_type = FileSource if once else TailSource
    initial = [(path, _source_name(path, directories, recursive)) for path in files]
    initial.extend(_scan(workspace, directories, globs, recursive))
    follow = not once and directories
    if follow:
        # Validate live options even when the starting directory is empty.
        interval = TailSource(directories[0], **source_options).poll_interval
        new_options = dict(source_options, start="beginning")

        def poll(stop):
            while not stop.is_set():
                candidates = list(_scan(workspace, directories, globs, recursive))
                with workspace.manager._start_lock:
                    if stop.is_set():
                        return
                    _register(workspace, candidates, TailSource, flow, new_options)
                stop.wait(interval)

    with workspace.manager._start_lock:
        workspace._check_open()
        jobs = _register(workspace, initial, source_type, flow, source_options)
        if follow:
            workspace._watch_directory(poll)
        return jobs

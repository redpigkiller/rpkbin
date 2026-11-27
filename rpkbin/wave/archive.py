"""Bounded SQLite history and the archive writer lifecycle."""

from __future__ import annotations

import base64
import getpass
import heapq
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import uuid
import warnings
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from rpkbin import __version__

from ._archive_flow import _ArchiveFlowQueries
from ._archive_format import BATCH_BYTES, BATCH_RECORDS, SCHEMA_VERSION, _json
from ._chart_projection import CHART_SCHEMA, _project_charts, _save_chart_config
from ._archive_charts import _ArchiveChartQueries
from ._archive_reader import _ArchiveReaderQueries

ARCHIVE_MODES = ("persist", "keep_on_fail", "ephemeral")
_RUN_ID_RE = re.compile(r"[0-9a-f]{32}")
_ARCHIVE_STEM_RE = re.compile(
    r"(?:[0-9a-f]{32}|\d{8}_\d{6}_[A-Za-z0-9_-]+_[0-9a-f]{8})"
)


def normalize_archive_mode(mode: str | None) -> str:
    """Validate and normalize an archive persistence mode."""
    if mode is None:
        return "persist"
    if not isinstance(mode, str):
        raise ValueError("Archive mode must be a string")
    normalized = mode.strip().lower().replace("-", "_")
    if normalized not in ARCHIVE_MODES:
        raise ValueError(
            f"Unsupported archive mode {mode!r}; expected one of {ARCHIVE_MODES}"
        )
    return normalized


def resolve_directory_option(value, *, default_fn) -> Path:
    """Resolve None or an explicit directory path."""
    if value is None:
        return Path(default_fn()).expanduser().resolve()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("Directory path must not be empty")
        return Path(stripped).expanduser().resolve()
    if isinstance(value, os.PathLike):
        return Path(value).expanduser().resolve()
    raise TypeError(f"Expected directory path or None; got {type(value).__name__}")


def _user_temp_tag() -> str:
    if os.name == "posix" and hasattr(os, "getuid"):
        return f"uid{os.getuid()}"
    try:
        raw = getpass.getuser()
    except Exception:
        raw = "user"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_")
    return safe or "user"


def default_temp_dir() -> Path:
    """Per-user local scratch directory for ephemeral / staged Wave runs."""
    override = os.environ.get("RPK_WAVE_TEMP_DIR")
    if override and override.strip():
        return resolve_directory_option(override, default_fn=lambda: Path(tempfile.gettempdir()))
    return Path(tempfile.gettempdir()).resolve() / f"rpk-wave-{_user_temp_tag()}"


def _is_safe_private_dir(path: Path) -> bool:
    if path.is_symlink():
        return False
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False
    if os.name == "posix" and hasattr(os, "getuid") and st.st_uid != os.getuid():
        return False
    return True


def _ensure_private_dir(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        return Path(tempfile.mkdtemp(prefix=f"{path.name}-", dir=path.parent)).resolve()
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        if not _is_safe_private_dir(path):
            return Path(tempfile.mkdtemp(prefix=f"{path.name}-", dir=path.parent)).resolve()
    if not _is_safe_private_dir(path):
        return Path(tempfile.mkdtemp(prefix=f"{path.name}-", dir=path.parent)).resolve()
    if os.name == "posix":
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path.resolve()


def _validate_retention_days(value, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a non-negative finite number of days")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{name} must be a non-negative finite number of days")
    return numeric


def _env_retention_days(env_name: str) -> float | None:
    raw = os.environ.get(env_name)
    if raw is None or not raw.strip():
        return None
    try:
        return _validate_retention_days(float(raw.strip()), env_name)
    except ValueError as exc:
        raise ValueError(f"Invalid {env_name}={raw!r}: {exc}") from exc


def _env_archive_mode() -> str | None:
    raw = os.environ.get("RPK_WAVE_ARCHIVE_MODE")
    if raw is None or not raw.strip():
        return None
    return normalize_archive_mode(raw)


def _clean_path_arg(value, name: str) -> str | Path | None:
    if value is None:
        return None
    if isinstance(value, os.PathLike):
        return Path(value)
    if isinstance(value, str):
        if not value.strip():
            raise ValueError(f"{name} must not be empty")
        return value.strip()
    raise TypeError(f"{name} must be a path string, PathLike, or None")


@dataclass(frozen=True)
class ArchivePolicy:
    """Declarative Wave archive persistence, staging, and retention settings."""

    mode: str = "persist"
    archive_dir: str | Path | None = None
    temp_dir: str | Path | None = None
    retention_days: float | None = None
    pass_retention_days: float | None = None
    fail_retention_days: float | None = None
    stage_on_network_fs: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", normalize_archive_mode(self.mode))
        object.__setattr__(self, "archive_dir", _clean_path_arg(self.archive_dir, "archive_dir"))
        object.__setattr__(self, "temp_dir", _clean_path_arg(self.temp_dir, "temp_dir"))
        object.__setattr__(
            self, "retention_days", _validate_retention_days(self.retention_days, "retention_days")
        )
        object.__setattr__(
            self,
            "pass_retention_days",
            _validate_retention_days(self.pass_retention_days, "pass_retention_days"),
        )
        object.__setattr__(
            self,
            "fail_retention_days",
            _validate_retention_days(self.fail_retention_days, "fail_retention_days"),
        )
        if type(self.stage_on_network_fs) is not bool:
            raise TypeError("stage_on_network_fs must be a bool")

    def resolved_archive_dir(self) -> Path:
        return resolve_directory_option(self.archive_dir, default_fn=default_archive_dir)

    def resolved_temp_dir(self) -> Path:
        if self.temp_dir is None:
            return default_temp_dir()
        return resolve_directory_option(self.temp_dir, default_fn=default_temp_dir)

    def effective_pass_retention_days(self) -> float | None:
        return self.pass_retention_days if self.pass_retention_days is not None else self.retention_days

    def effective_fail_retention_days(self) -> float | None:
        return self.fail_retention_days if self.fail_retention_days is not None else self.retention_days

    def with_overrides(self, **kwargs) -> ArchivePolicy:
        updates = {}
        for key in (
            "mode",
            "archive_dir",
            "temp_dir",
            "retention_days",
            "pass_retention_days",
            "fail_retention_days",
            "stage_on_network_fs",
        ):
            if key in kwargs:
                updates[key] = kwargs[key]
        if not updates:
            return self
        data = {
            "mode": self.mode,
            "archive_dir": self.archive_dir,
            "temp_dir": self.temp_dir,
            "retention_days": self.retention_days,
            "pass_retention_days": self.pass_retention_days,
            "fail_retention_days": self.fail_retention_days,
            "stage_on_network_fs": self.stage_on_network_fs,
        }
        data.update(updates)
        return ArchivePolicy(**data)


def archive_filename(run_id: str, *, source_path=None, created: float | None = None) -> str:
    """Return a sortable ``YYYYMMDD_HHMMSS_<label>_<short_id>.sqlite3`` filename."""
    if not _RUN_ID_RE.fullmatch(run_id or ""):
        raise ValueError(f"Invalid run identity: {run_id!r}")
    stamp = time.strftime(
        "%Y%m%d_%H%M%S",
        time.localtime(time.time() if created is None else float(created)),
    )
    raw_label = "run"
    if source_path:
        text = str(source_path).strip()
        if text and not text.startswith("<"):
            stem = Path(text).stem
            if stem:
                raw_label = stem
    label = re.sub(r"[^A-Za-z0-9_-]+", "_", raw_label).strip("_-")[:40] or "run"
    return f"{stamp}_{label}_{run_id[:8]}.sqlite3"


def find_archive_by_run_id(directory: str | Path, run_id: str) -> Path | None:
    """Find a Wave archive in ``directory`` by ``run_id`` (readable or legacy 32-hex name)."""
    if not _RUN_ID_RE.fullmatch(run_id or ""):
        raise ValueError("Invalid run identity")
    root = Path(directory).expanduser()
    legacy = root / f"{run_id}.sqlite3"
    if legacy.is_file():
        return legacy
    short_suffix = f"_{run_id[:8]}.sqlite3"
    for candidate in sorted(root.glob(f"*{short_suffix}")):
        if not _ARCHIVE_STEM_RE.fullmatch(candidate.stem):
            continue
        try:
            if RunArchive(candidate).run_id == run_id:
                return candidate
        except (OSError, ValueError, sqlite3.Error, KeyError):
            continue
    return None


def remove_archive_files(path: str | Path) -> None:
    """Remove an archive SQLite file and any leftover WAL/SHM sidecars."""
    base = Path(path).expanduser()
    for candidate in (base, Path(f"{base}-wal"), Path(f"{base}-shm")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def move_archive_file(src: str | Path, dest_dir: str | Path) -> Path:
    """Atomically publish a closed single-file archive from staging to dest_dir."""
    source = Path(src).expanduser().resolve()
    target_dir = Path(dest_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    if source.parent == target_dir:
        return source
    wal_path = Path(f"{source}-wal")
    if wal_path.exists():
        try:
            wal_size = wal_path.stat().st_size
        except OSError:
            wal_size = 1
        if wal_size > 0:
            raise OSError(f"Cannot move archive with uncheckpointed WAL sidecar: {wal_path}")
    dest = target_dir / source.name
    tmp = target_dir / f".{source.name}.tmp-{uuid.uuid4().hex[:8]}"
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, dest)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    remove_archive_files(source)
    return dest


def default_archive_dir():
    """Keep SQLite on the local host unless explicitly directed elsewhere."""
    root = os.environ.get("RPK_WAVE_ARCHIVE_DIR")
    if root and root.strip():
        return resolve_directory_option(root, default_fn=Path.cwd)
    env_var = "LOCALAPPDATA" if os.name == "nt" else "XDG_STATE_HOME"
    raw_base = os.environ.get(env_var, "").strip()
    candidate = Path(raw_base) if raw_base else None
    if candidate is not None and candidate.is_absolute():
        base = candidate
    else:
        base = Path.home() / ("AppData/Local" if os.name == "nt" else ".local/state")
    return (base / "rpk-wave" / "archives").resolve()


_NETWORK_FILESYSTEMS = frozenset((
    "nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs", "afs", "ceph", "glusterfs",
))
_REQUIRED_ARCHIVE_TABLES = frozenset((
    "metadata", "attempts", "records", "flow_records", "flow_heads", "flow_fields",
    "automation_heads", "chart_config", "chart_series", "chart_samples", "chart_overflow",
))
_CATALOG_CACHE_MAX = 16384
_CATALOG_CACHE_LOCK = threading.Lock()
_CATALOG_CACHE: dict[str, tuple[tuple[int, int, int, int], str, dict | None]] = {}
_json1_available: bool | None = None
_network_warned = False


def _validate_archive_connection(conn) -> dict:
    """Read and validate archive metadata and required tables from an open SQLite connection."""
    try:
        metadata = {
            row[0]: json.loads(row[1])
            for row in conn.execute("SELECT key,value FROM metadata")
        }
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise ValueError("Not a readable Wave archive") from exc
    if type(metadata.get("schema_version")) is not int or metadata["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported Wave archive schema version")
    if (not {"run_id", "producer_version", "source_path", "created", "closed_cleanly"}.issubset(metadata)
            or not isinstance(metadata["run_id"], str) or not metadata["run_id"]
            or not isinstance(metadata["producer_version"], str)
            or (metadata["source_path"] is not None and not isinstance(metadata["source_path"], str))
            or type(metadata["created"]) not in (float, int)
            or not math.isfinite(metadata["created"])
            or type(metadata["closed_cleanly"]) is not bool):
        raise ValueError("Malformed Wave archive metadata")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not _REQUIRED_ARCHIVE_TABLES.issubset(tables):
        raise ValueError("Incomplete Wave archive schema")
    return metadata


def _open_catalog_connection(path: Path, *, immutable: bool):
    suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    conn = sqlite3.connect(path.as_uri() + suffix, uri=True, timeout=0.25)
    return closing(conn)


def _catalog_cache_get(key: str, sig: tuple[int, int, int, int]):
    with _CATALOG_CACHE_LOCK:
        entry = _CATALOG_CACHE.get(key)
        if entry is None or entry[0] != sig:
            return None
        _CATALOG_CACHE.pop(key)
        _CATALOG_CACHE[key] = entry
        return entry[1], (dict(entry[2]) if entry[2] is not None else None)


def _catalog_cache_set(key: str, sig: tuple[int, int, int, int], kind: str, meta: dict | None) -> None:
    with _CATALOG_CACHE_LOCK:
        if key in _CATALOG_CACHE:
            del _CATALOG_CACHE[key]
        elif len(_CATALOG_CACHE) >= _CATALOG_CACHE_MAX:
            _CATALOG_CACHE.pop(next(iter(_CATALOG_CACHE)))
        _CATALOG_CACHE[key] = (sig, kind, dict(meta) if meta is not None else None)


def _require_json1():
    """Fail early and clearly when this Python's SQLite lacks the JSON1 functions."""
    global _json1_available
    if _json1_available is None:
        try:
            conn = sqlite3.connect(":memory:")
            try:
                _json1_available = conn.execute(
                    "SELECT json_extract('{\"a\":1}','$.a')").fetchone()[0] == 1
            finally:
                conn.close()
        except sqlite3.Error:
            _json1_available = False
    if not _json1_available:
        raise RuntimeError(
            "Wave history needs SQLite with the JSON1 extension (json_extract), but this "
            f"Python's SQLite {sqlite3.sqlite_version} lacks it. Use a Python build linked "
            "against SQLite 3.9+ with JSON1 enabled."
        )


def _network_filesystem(directory, mounts_text=None):
    """Return the network filesystem type holding ``directory`` (POSIX only), else None."""
    if os.name != "posix":
        return None
    try:
        target = os.path.realpath(directory)
        if mounts_text is None:
            with open("/proc/mounts", encoding="utf-8", errors="replace") as handle:
                mounts_text = handle.read()
        best, kind = "", None
        for line in mounts_text.splitlines():
            fields = line.split()
            if len(fields) < 3:
                continue
            mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[1])
            inside = target == mount or target.startswith(mount.rstrip("/") + "/")
            if inside and len(mount) >= len(best):
                best, kind = mount, fields[2]
        return kind if kind in _NETWORK_FILESYSTEMS else None
    except (OSError, ValueError):
        return None


def _warn_network_filesystem(directory):
    """Warn once per process: SQLite WAL is unsafe/slow on NFS-like mounts."""
    global _network_warned
    if _network_warned:
        return
    kind = _network_filesystem(directory)
    if kind is None:
        return
    _network_warned = True
    warnings.warn(
        f"Wave archive directory {directory} is on a network filesystem ({kind}); SQLite "
        "WAL history can be slow or corrupt there. Set RPK_WAVE_ARCHIVE_DIR=/tmp/... "
        "(a local disk) to keep history local.",
        RuntimeWarning, stacklevel=3,
    )


class RunArchive(_ArchiveReaderQueries, _ArchiveFlowQueries, _ArchiveChartQueries):
    """A run archive; construct without ``create`` to inspect an existing file.

    Snapshot metadata is bounded by the existing attempt state, not log history.
    Query results preview oversized values/text; ``read_text`` retrieves every
    character in bounded chunks. JSON text retains arbitrary Python integers.
    """

    def __init__(self, path, *, create=False, run_id=None, source_path=None):
        self.path = Path(path).expanduser().absolute()
        self._lock = threading.RLock()
        self._writer = None
        self._pending = self._pending_bytes = 0
        self._last_commit = time.monotonic()
        self.error = None
        self._closed = False
        self._record_depth = 0
        self._flush_requested = False
        try:
            if create:
                _require_json1()
                self.path.parent.mkdir(parents=True, exist_ok=True)
                _warn_network_filesystem(self.path.parent)
                # Never overwrite an earlier run, including an incomplete one.
                with self.path.open("xb"):
                    pass
                self._writer = sqlite3.connect(
                    self.path, timeout=2, check_same_thread=False
                )
                self._writer.executescript("""
                    PRAGMA journal_mode=WAL;
                    PRAGMA synchronous=NORMAL;
                    PRAGMA foreign_keys=ON;
                    PRAGMA cache_size=-4096;
                    CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE attempts(
                        id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                        number INTEGER NOT NULL, snapshot TEXT NOT NULL);
                    CREATE INDEX attempts_job ON attempts(job_id,number);
                    CREATE TABLE records(
                        attempt_id TEXT NOT NULL REFERENCES attempts(id),
                        seq INTEGER NOT NULL, time REAL NOT NULL,
                        elapsed REAL NOT NULL, source TEXT NOT NULL, text BLOB NOT NULL,
                        PRIMARY KEY(attempt_id,seq));
                    CREATE TABLE flow_records(
                        attempt_id TEXT NOT NULL REFERENCES attempts(id),
                        seq INTEGER NOT NULL, stage TEXT NOT NULL,
                        producer TEXT NOT NULL, kind TEXT NOT NULL,
                        data_json TEXT NOT NULL, evidence_json TEXT,
                        PRIMARY KEY(attempt_id,seq));
                    CREATE INDEX flow_producer ON flow_records(attempt_id,stage,producer,kind,seq);
                    CREATE INDEX flow_report_name ON flow_records(
                        attempt_id,json_extract(data_json,'$.name'),seq)
                        WHERE stage='analyzer' AND kind='report';
                    CREATE TABLE flow_heads(
                        attempt_id TEXT NOT NULL, stage TEXT NOT NULL,
                        producer TEXT NOT NULL, report_name TEXT NOT NULL,
                        seq INTEGER NOT NULL, metadata_json TEXT NOT NULL,
                        PRIMARY KEY(attempt_id,stage,producer,report_name));
                    CREATE INDEX flow_heads_seq ON flow_heads(attempt_id,stage,seq);
                    CREATE INDEX flow_heads_names ON flow_heads(attempt_id,stage,report_name,producer);
                    CREATE TABLE flow_fields(
                        attempt_id TEXT NOT NULL, stage TEXT NOT NULL,
                        producer TEXT NOT NULL, report_name TEXT NOT NULL,
                        name TEXT NOT NULL, key_json TEXT NOT NULL,
                        value_json TEXT NOT NULL, seq INTEGER NOT NULL,
                        PRIMARY KEY(attempt_id,stage,producer,report_name,name));
                    CREATE TABLE automation_heads(
                        attempt_id TEXT NOT NULL, request_id INTEGER NOT NULL,
                        producer TEXT NOT NULL, seq INTEGER NOT NULL,
                        PRIMARY KEY(attempt_id,request_id));
                    CREATE INDEX automation_heads_producer ON automation_heads(attempt_id,producer,request_id);
                """ + CHART_SCHEMA)
                self._writer.executemany(
                    "INSERT INTO metadata VALUES(?,?)",
                    [(k, _json(v)) for k, v in dict(
                        schema_version=SCHEMA_VERSION, producer_version=__version__,
                        run_id=run_id or uuid.uuid4().hex, source_path=source_path,
                        created=time.time(), closed_cleanly=False,
                    ).items()],
                )
                self._writer.commit()
            with self._reader() as conn:
                self.metadata = _validate_archive_connection(conn)
            self.run_id = self.metadata["run_id"]
        except BaseException:
            if self._writer is not None:
                try:
                    self._writer.close()
                finally:
                    self._writer = None
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close(clean=exc_type is None)

    def __repr__(self) -> str:
        mode = "rw" if self._writer is not None else "ro"
        state = "closed" if self._closed else ("failed" if self.error else "open")
        run_id = getattr(self, "run_id", "")[:8]
        return f"RunArchive({self.path.name!r}, run_id={run_id!r}, mode={mode!r}, state={state!r})"

    @staticmethod
    def catalog(directory, *, source_path=None, cursor=None, limit=50, include_all=False, max_seconds=None):
        """List matching runs with bounded memory; a directory is not a second DB."""
        RunArchive._limit(limit)
        boundary = None
        if cursor:
            try:
                if len(cursor) > 256:
                    raise ValueError()
                boundary = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if (len(boundary) != 2 or type(boundary[0]) not in (float, int)
                        or not math.isfinite(boundary[0]) or not _RUN_ID_RE.fullmatch(boundary[1])):
                    raise ValueError()
                boundary = tuple(boundary)
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValueError("Invalid run catalog cursor") from exc
        dir_path = Path(directory).expanduser()
        if not dir_path.is_dir():
            return dict(rows=[], cursor=None, has_more=False, unreadable_files=0,
                        total_files=0, scanned_files=0, partial=False)

        unreadable = 0
        wal_stats: dict[str, tuple[int, int]] = {}
        candidates: list[tuple[int, str, str, int]] = []
        try:
            with os.scandir(dir_path) as scan:
                for entry in scan:
                    name = entry.name
                    try:
                        if name.endswith("-wal"):
                            st = entry.stat()
                            wal_stats[name] = (st.st_mtime_ns, st.st_size)
                        elif name.endswith(".sqlite3") and _ARCHIVE_STEM_RE.fullmatch(name[:-8]) and entry.is_file():
                            st = entry.stat()
                            candidates.append((st.st_mtime_ns, name, entry.path, st.st_size))
                    except OSError:
                        if name.endswith(".sqlite3") and _ARCHIVE_STEM_RE.fullmatch(name[:-8]):
                            unreadable += 1
        except OSError:
            return dict(rows=[], cursor=None, has_more=False, unreadable_files=0,
                        total_files=0, scanned_files=0, partial=False)

        ranked = []
        for st_mtime_ns, name, path_str, st_size in candidates:
            wal_info = wal_stats.get(name + "-wal")
            wal_mtime_ns, wal_size = wal_info if wal_info is not None else (0, 0)
            eff_mtime_ns = max(st_mtime_ns, wal_mtime_ns)
            ranked.append((eff_mtime_ns, st_mtime_ns, wal_mtime_ns, wal_size, name, path_str, st_size))
        ranked.sort(key=lambda item: (item[0], item[4]), reverse=True)
        deadline = (time.monotonic() + max_seconds) if max_seconds is not None else None
        now = time.time()
        scanned = 0
        partial = False
        top: list[tuple] = []

        for eff_mtime_ns, st_mtime_ns, wal_mtime_ns, wal_size, _name, path_str, st_size in ranked:
            mtime_sec = eff_mtime_ns / 1_000_000_000
            if deadline is not None:
                if len(top) >= limit + 1 and scanned >= max(limit * 3, 64) and mtime_sec < top[0][0] - 60.0:
                    break
                if scanned > 0 and time.monotonic() >= deadline:
                    partial = True
                    break
            sig = (st_mtime_ns, st_size, wal_mtime_ns, wal_size)
            abs_str = os.path.abspath(path_str)
            cache_eligible = (now - mtime_sec) > 2.0
            cached = _catalog_cache_get(abs_str, sig) if cache_eligible else None
            scanned += 1

            if cached is not None:
                kind, raw_meta = cached
                if kind != "ok" or raw_meta is None:
                    unreadable += 1
                    continue
            else:
                immutable = wal_size == 0 and (now - mtime_sec) > 5.0
                try:
                    with _open_catalog_connection(Path(abs_str), immutable=immutable) as conn:
                        raw_meta = _validate_archive_connection(conn)
                    if cache_eligible:
                        _catalog_cache_set(abs_str, sig, "ok", raw_meta)
                except (OSError, ValueError, sqlite3.Error, KeyError):
                    unreadable += 1
                    if cache_eligible:
                        _catalog_cache_set(abs_str, sig, "bad", None)
                    continue

            if not include_all and raw_meta["source_path"] != source_path:
                continue
            key = (raw_meta["created"], raw_meta["run_id"])
            if boundary is not None and key >= boundary:
                continue
            item = (key[0], key[1], path_str, dict(raw_meta), st_size, wal_size, mtime_sec, abs_str, sig, cache_eligible)
            if len(top) < limit + 1:
                heapq.heappush(top, item)
            elif item > top[0]:
                heapq.heapreplace(top, item)

        top.sort(key=lambda item: (item[0], item[1]), reverse=True)
        has_over = len(top) > limit
        more = has_over
        selected = top[:limit]
        rows = []
        for _created, _run_id, path_str, meta, st_size, wal_size, mtime_sec, abs_str, sig, cache_eligible in selected:
            meta["filename"] = os.path.basename(path_str)
            meta["path"] = path_str
            meta["size"] = int(st_size)
            if not meta.get("run_outcome"):
                if not meta.get("closed_cleanly"):
                    meta["run_outcome"] = "UNCLOSED"
                else:
                    immutable = wal_size == 0 and (now - mtime_sec) > 5.0
                    try:
                        with _open_catalog_connection(Path(abs_str), immutable=immutable) as conn:
                            inferred = _infer_outcome_from_conn(conn)
                    except (OSError, ValueError, sqlite3.Error):
                        inferred = "UNCLOSED"
                    meta["run_outcome"] = inferred
                    if cache_eligible:
                        with _CATALOG_CACHE_LOCK:
                            entry = _CATALOG_CACHE.get(abs_str)
                            if entry is not None and entry[0] == sig and entry[1] == "ok" and entry[2] is not None:
                                _CATALOG_CACHE[abs_str] = (entry[0], entry[1], {**entry[2], "run_outcome": inferred})
            rows.append(meta)

        next_cursor = base64.urlsafe_b64encode(_json(
            [rows[-1]["created"], rows[-1]["run_id"]]
        ).encode()).decode().rstrip("=") if (has_over and rows) else None
        return dict(
            rows=rows,
            cursor=next_cursor,
            has_more=more,
            unreadable_files=unreadable,
            total_files=len(candidates),
            scanned_files=scanned,
            partial=partial,
        )

    def bind_source(self, source_path):
        """Attach a work-file identity before the manager dispatches."""
        with self._lock:
            if not self._writer or self._closed or self.error:
                return
            try:
                self._writer.execute("UPDATE metadata SET value=? WHERE key='source_path'", (_json(source_path),))
                self.metadata["source_path"] = source_path
                self.flush()
            except sqlite3.Error as exc:
                self._fail(exc)

    def _fail(self, exc):
        # Sticky and independent of monitor verdict, including diagnostic eviction.
        if self.error is None:
            self.error = dict(message=f"{type(exc).__name__}: {exc}", path=str(self.path))
        if self._writer:
            try:
                self._writer.rollback()
            except sqlite3.Error:
                pass
        self._pending = self._pending_bytes = 0

    def save_attempt(self, snapshot, *, flush=False):
        with self._lock:
            if self.error or self._closed:
                return
            try:
                self._writer.execute(
                    "INSERT INTO attempts VALUES(?,?,?,?) ON CONFLICT(id) "
                    "DO UPDATE SET snapshot=excluded.snapshot",
                    (snapshot["attempt_id"], snapshot["id"],
                     snapshot["attempt_number"], _json(snapshot)),
                )
                _save_chart_config(self._writer, snapshot)
                if flush:
                    self.flush()
            except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
                self._fail(exc)

    def delete_unstarted_attempt(self, attempt_id: str) -> bool:
        """Delete an unstarted Preview attempt and its empty projection metadata."""
        with self._lock:
            if self.error or self._closed:
                return False
            try:
                has_records = self._writer.execute(
                    "SELECT 1 FROM records WHERE attempt_id=? LIMIT 1", (attempt_id,)
                ).fetchone()
                has_flow = self._writer.execute(
                    "SELECT 1 FROM flow_records WHERE attempt_id=? LIMIT 1", (attempt_id,)
                ).fetchone()
                if has_records is not None or has_flow is not None:
                    return False
                for table in (
                    "chart_samples",
                    "chart_series",
                    "chart_overflow",
                    "chart_config",
                    "automation_heads",
                    "flow_fields",
                    "flow_heads",
                ):
                    self._writer.execute(
                        f"DELETE FROM {table} WHERE attempt_id=?", (attempt_id,)
                    )
                cur = self._writer.execute(
                    "DELETE FROM attempts WHERE id=?", (attempt_id,)
                )
                self.flush()
                return bool(cur.rowcount)
            except sqlite3.Error as exc:
                self._fail(exc)
                return False

    def append(self, attempt_id, record, elapsed):
        """Store raw Log text independently of Parser and Analyzer outputs."""
        with self._lock:
            if self.error or self._closed:
                return
            try:
                encoded = record["text"].encode("utf-8", errors="surrogatepass")
                self._writer.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?,?)",
                    (attempt_id, record["line_no"], record["time"], elapsed, record["source"], encoded),
                )
                self._pending += 1
                self._pending_bytes += len(encoded)
                if (self._pending >= BATCH_RECORDS or self._pending_bytes >= BATCH_BYTES
                        or time.monotonic() - self._last_commit >= .25):
                    self.flush()
            except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
                self._fail(exc)

    def append_flow(self, attempt_id, publication):
        """Stage a detached publication in the existing bounded transaction."""
        return self._append_source_batch(attempt_id, (), (publication,))

    def _write_file_batch(self, attempt_id, records, publications, interrupted):
        # Defer commits until raw and Flow have both been staged. A Ctrl+C
        # inside SQLite can then roll back just this batch and retry it.
        with self.record_batch(interrupted=interrupted):
            if self.error or self._closed:
                return False
            while True:
                pending = self._pending, self._pending_bytes, self._flush_requested
                if not self._writer.in_transaction:
                    self._writer.execute("BEGIN")
                self._writer.execute("SAVEPOINT file_input")
                try:
                    saved = self._append_source_batch(attempt_id, records, publications)
                except KeyboardInterrupt:
                    self._writer.execute("ROLLBACK TO file_input")
                    self._writer.execute("RELEASE file_input")
                    self._pending, self._pending_bytes, self._flush_requested = pending
                    interrupted()
                    continue
                if saved:
                    self._writer.execute("RELEASE file_input")
                return saved

    def _append_source_batch(self, attempt_id, records, publications):
        """Stage prepared raw rows followed by their ordered publications."""
        with self._lock:
            if self.error or self._closed:
                return False
            if self._writer is None:
                raise ValueError("Cannot append to a read-only archive")
            try:
                publications = tuple(publications)
                raw = [
                    (attempt_id, row["line_no"], row["time"], elapsed, row["source"],
                     row["text"].encode("utf-8", errors="surrogatepass"))
                    for row, elapsed in records
                ]
                flow, heads, fields, actions = [], [], [], []
                for publication in publications:
                    stage, producer, kind = (publication[key] for key in ("stage", "producer", "kind"))
                    seq, value = publication["seq"], publication["data"]
                    flow.append((attempt_id, seq, stage, producer, kind, _json(value),
                                 _json(publication.get("evidence"))))
                    if (stage == "parser" and kind == "record" and isinstance(value, dict)
                            or stage == "analyzer" and kind == "report" and isinstance(value, dict)
                            and isinstance(value.get("name"), str) and isinstance(value.get("values"), dict)):
                        report_name = value["name"] if stage == "analyzer" else ""
                        group = (attempt_id, stage, producer, report_name)
                        metadata = {k: v for k, v in value.items() if k != "values"} if stage == "analyzer" else {}
                        heads.append((*group, seq, _json(metadata)))
                        values = value["values"] if stage == "analyzer" else value
                        fields.extend((*group, name, _json(name), _json(item), seq) for name, item in values.items())
                    if (stage == "automation" and kind == "action" and isinstance(value, dict)
                            and type(value.get("request_id")) is int and value["request_id"] > 0):
                        actions.append((attempt_id, value["request_id"], producer, seq))
                if raw:
                    self._writer.executemany("INSERT INTO records VALUES(?,?,?,?,?,?)", raw)
                if flow:
                    self._writer.executemany("INSERT INTO flow_records VALUES(?,?,?,?,?,?,?)", flow)
                    self._pending_bytes += _project_charts(self._writer, attempt_id, publications)
                if heads:
                    self._writer.executemany(
                        "INSERT INTO flow_heads VALUES(?,?,?,?,?,?) ON CONFLICT(attempt_id,stage,producer,report_name) "
                        "DO UPDATE SET seq=excluded.seq,metadata_json=excluded.metadata_json WHERE excluded.seq>flow_heads.seq",
                        heads,
                    )
                if fields:
                    self._writer.executemany(
                        "INSERT INTO flow_fields VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(attempt_id,stage,producer,report_name,name) DO UPDATE SET "
                        "value_json=excluded.value_json,seq=excluded.seq WHERE excluded.seq>flow_fields.seq",
                        fields,
                    )
                if actions:
                    self._writer.executemany(
                        "INSERT INTO automation_heads VALUES(?,?,?,?) ON CONFLICT(attempt_id,request_id) "
                        "DO UPDATE SET producer=excluded.producer,seq=excluded.seq WHERE excluded.seq>automation_heads.seq",
                        actions,
                    )
                self._pending += len(raw) + len(flow)
                self._pending_bytes += sum(len(row[-1]) for row in raw)
                self._pending_bytes += sum(len(row[-2]) + len(row[-1]) for row in flow)
                if (self._pending >= BATCH_RECORDS or self._pending_bytes >= BATCH_BYTES
                        or time.monotonic() - self._last_commit >= .25):
                    self.flush()
                return self.error is None
            except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
                self._fail(exc)
                return False

    @contextmanager
    def record_batch(self, *, interrupted=None):
        """Write a prepared input group without exposing a partial batch.

        Callers prepare data and run callbacks before acquiring this lock.
        """
        with self._lock:
            self._record_depth += 1
            try:
                yield
            except BaseException as exc:
                self._fail(exc)
                raise
            finally:
                self._record_depth -= 1
                if not self._record_depth and self._flush_requested:
                    self._flush_requested = False
                    while True:
                        try:
                            self.flush()
                            break
                        except KeyboardInterrupt:
                            if interrupted is None:
                                raise
                            interrupted()

    def flush(self):
        with self._lock:
            if self.error or self._closed or not self._writer:
                return
            if self._record_depth:
                self._flush_requested = True
                return
            try:
                self._writer.commit()
                self._pending = self._pending_bytes = 0
                self._last_commit = time.monotonic()
            except (sqlite3.Error, OSError) as exc:
                self._fail(exc)

    def set_outcome(self, outcome: str) -> None:
        """Record terminal run outcome metadata before closing."""
        with self._lock:
            if self.error or self._closed or not self._writer:
                return
            try:
                self._writer.execute(
                    "INSERT OR REPLACE INTO metadata VALUES('run_outcome',?)",
                    (_json(str(outcome)),),
                )
                self.metadata["run_outcome"] = str(outcome)
            except sqlite3.Error as exc:
                self._fail(exc)

    def close(self, *, clean=True):
        with self._lock:
            if self._closed:
                return
            if self._writer:
                clean = clean and not self._record_depth
                try:
                    if not self.error and clean:
                        closed_at = time.time()
                        self._writer.execute(
                            "UPDATE metadata SET value='true' WHERE key='closed_cleanly'"
                        )
                        self._writer.execute(
                            "INSERT OR REPLACE INTO metadata VALUES('closed_at',?)",
                            (_json(closed_at),),
                        )
                        self.flush()
                        if self.error is None:
                            self.metadata["closed_cleanly"] = True
                            self.metadata["closed_at"] = closed_at
                    elif not self.error:
                        self.flush()
                except sqlite3.Error as exc:
                    self._fail(exc)
                finally:
                    try:
                        if not self.error:
                            busy = 0
                            for attempt in range(10):
                                row = self._writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                                busy = int(row[0]) if row else 0
                                if busy == 0:
                                    break
                                if attempt < 9:
                                    time.sleep(0.02)
                            if busy != 0:
                                raise sqlite3.OperationalError(
                                    "SQLite WAL checkpoint remained busy; uncheckpointed WAL frames remain"
                                )
                            if clean:
                                self._writer.execute("PRAGMA busy_timeout=100")
                                mode_row = self._writer.execute("PRAGMA journal_mode=DELETE").fetchone()
                                mode = str(mode_row[0]).lower() if mode_row else ""
                                if mode != "delete":
                                    raise sqlite3.OperationalError(
                                        f"Failed to switch SQLite journal_mode to DELETE (got {mode!r})"
                                    )
                    except sqlite3.Error as exc:
                        if clean:
                            self.metadata["closed_cleanly"] = False
                            self._fail(exc)
                    finally:
                        self._writer.close()
            self._closed = True

    @property
    def health(self):
        clean = self.metadata["closed_cleanly"]
        if self._writer is None and not clean:
            with self._reader() as conn:
                clean = self._closed_cleanly(conn)
        return dict(enabled=True, run_id=self.run_id, path=str(self.path),
                    error=self.error, closed_cleanly=clean,
                    status="failed" if self.error else ("closed" if clean else
                           "recording" if self._writer and not self._closed else "unclosed"),
                    complete=self.error is None and clean)


def _infer_outcome_from_conn(conn) -> str:
    """Infer PASS/FAIL/UNSTARTED from stored attempts on an open connection."""
    seen: set[str] = set()
    for job_id, _number, status, result, error_count in conn.execute(
        "SELECT job_id, number, "
        "json_extract(snapshot, '$.status'), "
        "json_extract(snapshot, '$.result'), "
        "json_extract(snapshot, '$.error_count') "
        "FROM attempts ORDER BY job_id, number DESC"
    ):
        if job_id not in seen:
            seen.add(job_id)
            if status != "DONE" or result not in ("PASS", "N/A") or bool(error_count):
                return "FAIL"
    return "PASS" if seen else "UNSTARTED"


def _infer_archive_outcome(archive: RunArchive) -> str:
    """Return recorded run_outcome or infer PASS/FAIL/UNSTARTED from stored attempts."""
    outcome = archive.metadata.get("run_outcome")
    if isinstance(outcome, str) and outcome:
        return outcome
    with archive._reader() as conn:
        return _infer_outcome_from_conn(conn)


def prune_archives(
    directory: str | Path,
    *,
    pass_retention_days: float | None = None,
    fail_retention_days: float | None = None,
    retention_days: float | None = None,
    exclude_run_id: str | None = None,
    now: float | None = None,
) -> dict[str, object]:
    """Delete closed Wave archives older than the configured retention windows.

    Age and outcome are read from SQLite ``metadata`` (never filesystem mtime);
    active or unclosed archives and non-Wave SQLite files are never deleted.
    """
    pass_days = _validate_retention_days(
        pass_retention_days if pass_retention_days is not None else retention_days,
        "pass_retention_days",
    )
    fail_days = _validate_retention_days(
        fail_retention_days if fail_retention_days is not None else retention_days,
        "fail_retention_days",
    )
    target_dir = Path(directory).expanduser()
    if (pass_days is None and fail_days is None) or not target_dir.is_dir():
        return {"deleted": [], "skipped": 0, "errors": 0}
    current_time = time.time() if now is None else float(now)
    deleted: list[str] = []
    skipped = 0
    errors = 0
    for path in sorted(target_dir.glob("*.sqlite3")):
        if not _ARCHIVE_STEM_RE.fullmatch(path.stem):
            continue
        if exclude_run_id and path.stem == exclude_run_id:
            skipped += 1
            continue
        try:
            store = RunArchive(path)
            if exclude_run_id and store.run_id == exclude_run_id:
                skipped += 1
                continue
            if not store.metadata.get("closed_cleanly"):
                skipped += 1
                continue
            ts = store.metadata.get("closed_at")
            if type(ts) not in (int, float) or not math.isfinite(ts):
                ts = store.metadata["created"]
            outcome = _infer_archive_outcome(store)
            limit_days = pass_days if outcome == "PASS" else fail_days
            if limit_days is not None and (current_time - float(ts)) >= limit_days * 86400.0:
                remove_archive_files(path)
                deleted.append(str(path))
            else:
                skipped += 1
        except (OSError, ValueError, sqlite3.Error, KeyError):
            errors += 1
    return {"deleted": deleted, "skipped": skipped, "errors": errors}

"""Read-only attempt and Log archive queries."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import sqlite3
import time
from contextlib import closing, contextmanager

from . import _archive_format as limits
from ._archive_format import (
    CHUNK_CHARS,
    PAGE_LIMIT,
    PREVIEW_CHARS,
    READER_TEXT_BYTES,
    _json,
    _text_prefix,
)
from ._run import source_capabilities, source_origin


class _ArchiveReaderQueries:
    def _reader(self):
        conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA cache_size=-4096")
        return closing(conn)

    @staticmethod
    def _closed_cleanly(conn):
        return conn.execute("SELECT value FROM metadata WHERE key='closed_cleanly'").fetchone()[0] == "true"

    def _attempt_projection(self, snapshot, verified):
        kind = snapshot.setdefault("source_kind", "process")
        origin, label = source_origin(kind)
        snapshot.setdefault("origin", origin)
        snapshot.setdefault("origin_label", label)
        snapshot.setdefault("completion_reason", (snapshot.get("source_descriptor") or {}).get("completion_reason"))
        snapshot.setdefault("capabilities", source_capabilities(kind).snapshot(
            can_rerun=kind == "process" and bool(snapshot.get("can_rerun"))))
        snapshot["verified"] = verified
        if not verified and (self._writer is None or self._closed or self.error):
            # Keep the original verdict and all evidence on disk. An unverified
            # read must not present a complete Flow as a confirmed success.
            if (flow := snapshot.get("flow")) and flow.get("complete"):
                flow["complete"] = False
        return snapshot

    @staticmethod
    def _replay_snapshot(conn, attempt_id):
        row = conn.execute("SELECT snapshot FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise ValueError("Attempt (attempt_id) not found in this archive")
        return json.loads(row[0])

    def _replay_attempt_name(self, attempt_id):
        self.flush()
        with self._reader() as conn:
            return self._replay_snapshot(conn, attempt_id)["name"]

    @contextmanager
    def replay(self, attempt_id):
        """Yield attempt metadata and exact ordered Log text in one read transaction."""
        self.flush()
        with self._reader() as conn:
            conn.execute("BEGIN")
            snapshot = self._attempt_projection(self._replay_snapshot(conn, attempt_id),
                                                self._closed_cleanly(conn))
            records = conn.execute("SELECT text FROM records WHERE attempt_id=? ORDER BY seq", (attempt_id,))
            yield snapshot, (row[0].decode("utf-8", errors="surrogatepass") for row in records)

    def _require_attempt(self, job_id, attempt_id):
        self.flush()
        with self._reader() as conn:
            found = conn.execute("SELECT 1 FROM attempts WHERE id=? AND job_id=?", (attempt_id, job_id)).fetchone()
        if found is None:
            raise ValueError("Job or attempt not found in this archive")

    def attempt(self, job_id, attempt_id, *, preview=False):
        self.flush()
        with self._reader() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT snapshot FROM attempts WHERE id=? AND job_id=?",
                (attempt_id, job_id),
            ).fetchone()
            if row is None:
                raise ValueError("Job or attempt not found in this archive")
            snapshot = self._attempt_projection(json.loads(row[0]), self._closed_cleanly(conn))
        if not preview:
            return snapshot
        # Flow values have their own exact, bounded chunk APIs. Keep HTTP
        # attempt metadata readable without replaying analysis.
        def text_previews(value):
            if isinstance(value, str) and len(value) > PREVIEW_CHARS:
                return value[:PREVIEW_CHARS] + "… [preview]"
            if isinstance(value, dict):
                return {key: text_previews(item) for key, item in value.items()}
            if isinstance(value, list):
                return [text_previews(item) for item in value]
            return value

        return text_previews(snapshot)

    def attempts(self, job_id=None, *, before=None, limit=PAGE_LIMIT):
        self.flush()
        limit = self._limit(limit)
        where, params = [], []
        if job_id:
            where.append("job_id=?")
            params.append(job_id)
        if before is not None:
            if isinstance(before, bool) or not isinstance(before, int) or before < 1:
                raise ValueError("before must be a positive attempt row number")
            where.append("rowid<?")
            params.append(before)
        rows, output_bytes, more = [], 0, False
        summary_keys = ("id", "name", "attempt_id", "submission_id", "attempt_number",
                        "status", "result", "elapsed_str", "pid", "returncode", "warning_count",
                        "error_count", "total_lines", "finalized", "source_kind",
                        "origin", "origin_label", "completion_reason", "capabilities", "verified")
        with self._reader() as conn:
            conn.execute("BEGIN")
            verified = self._closed_cleanly(conn)
            cursor = conn.execute(
                "SELECT rowid,snapshot FROM attempts" +
                (" WHERE " + " AND ".join(where) if where else "") +
                " ORDER BY rowid DESC LIMIT ?", [*params, limit + 1],
            )
            # The picker needs identity/result summaries, not 100 copies of
            # potentially large latest values and evidence. Decode one at a time.
            for row in cursor:
                snapshot = self._attempt_projection(json.loads(row[1]), verified)
                summary = {key: snapshot[key] for key in summary_keys if key in snapshot}
                size = len(_json(summary))
                if len(rows) == limit or (rows and output_bytes + size > limits.PAGE_BYTES):
                    more = True
                    break
                rows.append((row[0], summary))
                output_bytes += size
        return dict(rows=[row[1] for row in rows],
                    before=rows[-1][0] if more else None, has_more=more)

    @staticmethod
    def _limit(value):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= PAGE_LIMIT:
            raise ValueError(f"limit must be between 1 and {PAGE_LIMIT}")
        return value

    @staticmethod
    def _time_filters(start, end):
        where, args = [], []
        for bound, operator in ((start, ">="), (end, "<=")):
            if bound is not None:
                if isinstance(bound, bool) or not isinstance(bound, (float, int)) or not math.isfinite(bound) or bound < 0:
                    raise ValueError("time bounds must be finite nonnegative seconds")
                where.append(f"r.elapsed{operator}?")
                args.append(bound)
        if start is not None and end is not None and start > end:
            raise ValueError("start must not exceed end")
        return where, args

    @staticmethod
    def _cursor(scope, seq):
        return base64.urlsafe_b64encode(_json([scope, seq]).encode()).decode().rstrip("=")

    @staticmethod
    def _position(cursor, scope):
        if cursor is None:
            return None
        try:
            if not isinstance(cursor, str) or not 0 < len(cursor) <= 1024:
                raise ValueError()
            identity, seq = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if identity != scope or type(seq) is not int or seq < 1:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError("Cursor does not match this run, attempt or filters") from exc
        return seq

    def page(self, job_id, attempt_id, *, limit=PAGE_LIMIT, cursor=None,
             direction="forward", start=None, end=None, around=None):
        """Read a bounded raw-Log page with an opaque keyset cursor."""
        self._require_attempt(job_id, attempt_id)
        self._limit(limit)
        if direction not in ("forward", "backward"):
            raise ValueError("Invalid page direction")
        if around is not None and (type(around) is not int or around < 1 or cursor):
            raise ValueError("around requires a positive Log sequence without a cursor")
        filters, params = self._time_filters(start, end)
        scope = hashlib.sha256(_json(["logs-page", self.run_id, job_id, attempt_id, start, end, around]).encode()).hexdigest()
        where, params = ["r.attempt_id=?", *filters], [attempt_id, *params]
        if around is not None:
            where.append("r.seq BETWEEN ? AND ?")
            params.extend((max(1, around - 10), around + 10))
        position = self._position(cursor, scope)
        if position is not None:
            where.append("r.seq>?" if direction == "forward" else "r.seq<?")
            params.append(position)
        order = "ASC" if direction == "forward" else "DESC"
        with self._reader() as conn:
            rows = conn.execute(
                f"SELECT r.seq,r.time,r.elapsed,r.source,substr(r.text,1,{PREVIEW_CHARS}),length(r.text) "
                "FROM records r WHERE " + " AND ".join(where) + f" ORDER BY r.seq {order} LIMIT ?",
                [*params, limit + 1]).fetchall()
        more, output, size = len(rows) > limit, [], 0
        for row in rows[:limit]:
            text, consumed = _text_prefix(row[4])
            item = dict(seq=row[0], time=row[1], elapsed=row[2], source=row[3],
                        text=text, text_bytes=row[5], truncated=consumed < row[5])
            item_size = len(_json(item))
            if item_size > limits.PAGE_BYTES:
                raise ValueError("Log row identity exceeds the page payload limit")
            if output and size + item_size > limits.PAGE_BYTES:
                more = True
                break
            output.append(item)
            size += item_size
        if direction == "backward":
            output.reverse()
        return dict(run_id=self.run_id, job_id=job_id, attempt_id=attempt_id, kind="logs", rows=output,
                    has_more=more, direction=direction,
                    first=self._cursor(scope, output[0]["seq"]) if output else None,
                    last=self._cursor(scope, output[-1]["seq"]) if output else None, health=self.health)

    def reader(self, job_id, attempt_id, *, limit=PAGE_LIMIT, cursor=None,
               direction="forward", anchor=None, query="", regex=False,
               case_sensitive=False, whole_word=False, start=None, end=None):
        """Read raw Log windows; sparse searches advance past scanned inputs."""
        self._require_attempt(job_id, attempt_id)
        self._limit(limit)
        if direction not in ("forward", "backward"):
            raise ValueError("Invalid reader direction")
        if (not isinstance(query, str) or len(query) > 2048
                or type(regex) is not bool or type(case_sensitive) is not bool
                or type(whole_word) is not bool):
            raise ValueError("query must be at most 2048 characters and regex, case_sensitive and whole_word must be bool")
        if anchor is not None and (type(anchor) is not int or anchor < 1 or cursor):
            raise ValueError("anchor requires a positive sequence without a cursor")
        self._time_filters(start, end)
        pattern = None
        if query and (regex or whole_word):
            import regex as timed_regex
            flags = timed_regex.VERSION0 | (0 if case_sensitive else timed_regex.IGNORECASE)
            try:
                if regex:
                    pattern = timed_regex.compile(query, flags)
                    if whole_word:
                        pattern = timed_regex.compile(rf"\b(?:{query})\b", flags)
                else:
                    pattern = timed_regex.compile(rf"\b{timed_regex.escape(query)}\b", flags)
            except (timed_regex.error, RecursionError) as exc:
                raise ValueError(f"Invalid regular expression: {exc}") from exc
        needle = query.casefold()
        scope = hashlib.sha256(_json([
            "logs-reader", self.run_id, job_id, attempt_id,
            query, regex, case_sensitive, whole_word, start, end,
        ]).encode()).hexdigest()
        position = self._position(cursor, scope)
        with self._reader() as conn:
            last = conn.execute("SELECT seq FROM records WHERE attempt_id=? ORDER BY seq DESC LIMIT 1", (attempt_id,)).fetchone()
            end_seq = last[0] if last else 0
            order, op = ("ASC", ">") if direction == "forward" else ("DESC", "<")
            where, params = ["r.attempt_id=?"], [attempt_id]
            if position is not None:
                where.append(f"r.seq{op}?")
                params.append(position)
            elif anchor is not None:
                where.append(f"r.seq{op}=?")
                params.append(anchor)
            # Fetch bounded full text with the candidate row for searches, avoiding one
            # SELECT per scanned row. Oversized records come back NULL and are rejected below.
            full_text = ("CASE WHEN length(r.text)<=? THEN r.text ELSE NULL END"
                         if query else "NULL")
            candidates = conn.execute(
                f"SELECT r.seq,r.time,r.elapsed,r.source,substr(r.text,1,{PREVIEW_CHARS}),length(r.text),{full_text} "
                "FROM records r WHERE " + " AND ".join(where) + f" ORDER BY r.seq {order} LIMIT ?",
                [*([READER_TEXT_BYTES] if query else []), *params, limits.READER_SCAN_LIMIT + 1])
            output, tokens, size, scanned, last_scanned, exhausted = [], [], 0, 0, None, True
            deadline = time.monotonic() + limits.READER_SECONDS
            for row in candidates:
                if scanned >= limits.READER_SCAN_LIMIT or len(output) >= limit or (scanned and time.monotonic() >= deadline):
                    exhausted = False
                    break
                token = self._cursor(scope, row[0])
                matches = (start is None or row[2] >= start) and (end is None or row[2] <= end)
                if matches and query:
                    if row[5] > READER_TEXT_BYTES or row[6] is None:
                        raise ValueError(f"Log #{row[0]} exceeds the 8 MiB search record limit; shorten the source record")
                    raw = row[6]
                    text = raw.decode("utf-8", errors="surrogatepass") if isinstance(raw, (bytes, bytearray, memoryview)) else str(raw)
                    try:
                        if pattern is not None:
                            matches = bool(pattern.search(text, timeout=.02))
                        elif case_sensitive:
                            matches = query in text
                        else:
                            matches = needle in text.casefold()
                    except TimeoutError as exc:
                        raise ValueError("Regular expression timed out; simplify the search expression") from exc
                if matches:
                    text, consumed = _text_prefix(row[4])
                    item = dict(seq=row[0], time=row[1], elapsed=row[2], source=row[3],
                                text=text, text_bytes=row[5], truncated=consumed < row[5])
                    item_size = len(_json(item))
                    if item_size > limits.PAGE_BYTES:
                        raise ValueError("Reader row identity exceeds the page payload limit")
                    if output and size + item_size > limits.PAGE_BYTES:
                        exhausted = False
                        break
                    output.append(item)
                    tokens.append(token)
                    size += item_size
                scanned += 1
                last_scanned = (row[0], token)
            if direction == "backward":
                output.reverse()
                tokens.reverse()
            return dict(run_id=self.run_id, job_id=job_id, attempt_id=attempt_id, kind="logs", rows=output,
                        direction=direction, health=self.health,
                        first=tokens[0] if tokens else None, last=tokens[-1] if tokens else None,
                        next=last_scanned[1] if last_scanned else None, has_more=not exhausted, scanned=scanned,
                        scanned_seq=last_scanned[0] if last_scanned else None, end_seq=end_seq)

    def read_text(self, job_id, attempt_id, seq, *, offset=0, limit=CHUNK_CHARS):
        """Read complete raw Log text in bounded UTF-8 byte chunks."""
        self._require_attempt(job_id, attempt_id)
        if type(seq) is not int or seq < 1 or type(offset) is not int or offset < 0:
            raise ValueError("seq and offset must be valid nonnegative integers")
        if type(limit) is not int or not 1 <= limit <= CHUNK_CHARS:
            raise ValueError(f"chunk limit must be between 1 and {CHUNK_CHARS}")
        with self._reader() as conn:
            row = conn.execute("SELECT substr(text,?,?),length(text) FROM records WHERE attempt_id=? AND seq=?",
                               (offset + 1, limit, attempt_id, seq)).fetchone()
        if row is None:
            raise ValueError("Log record not found")
        if offset > row[1]:
            raise ValueError("Offset is beyond the Log record")
        text, consumed = _text_prefix(row[0])
        if not consumed and offset < row[1]:
            raise ValueError("Chunk is too small for the Unicode code point; use limit >= 4")
        return dict(text=text, offset=offset, next_offset=offset + consumed,
                    total=row[1], offset_unit="bytes", has_more=offset + consumed < row[1])

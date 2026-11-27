"""Read-only Flow and Automation archive queries."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from . import _archive_format as limits
from ._archive_format import (
    CHUNK_CHARS,
    PAGE_LIMIT,
    PREVIEW_CHARS,
    READER_TEXT_BYTES,
    _json,
)


class _ArchiveFlowQueries:
    @staticmethod
    def _flow_conditions(conditions):
        if conditions is None:
            return []
        if not isinstance(conditions, list) or len(conditions) > 8:
            raise ValueError("conditions must contain at most eight field conditions")
        result = []
        for item in conditions:
            if (not isinstance(item, dict) or set(item) != {"field", "op", "value"}
                    or not isinstance(item["field"], str) or not 0 < len(item["field"]) <= 256
                    or item["op"] not in ("eq", "gte", "contains")
                    or type(item["value"]) not in (str, int, float, bool, type(None))):
                raise ValueError("Invalid Flow field condition")
            _json(item["value"])
            if item["op"] == "gte" and type(item["value"]) not in (int, float):
                raise ValueError("gte requires a numeric value")
            if item["op"] == "contains" and not isinstance(item["value"], str):
                raise ValueError("contains requires a text value")
            result.append(dict(item))
        return result

    def flow_page(self, job_id, attempt_id, *, stage=None, producer=None, kind=None, report_name=None,
                  after=None, before=None, conditions=None, limit=PAGE_LIMIT):
        """Keyset pages of spec outputs; sparse filtering has a scan cursor.

        JSON comparisons happen in Python to preserve integers outside SQLite's
        signed-64-bit/float ranges. Neither a sparse query nor a huge row can
        create an unbounded scan or HTTP payload.
        """
        self._require_attempt(job_id, attempt_id)
        self._limit(limit)
        if after is not None and before is not None:
            raise ValueError("Use after or before, not both")
        for value in (after, before):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Flow cursors must be nonnegative integers")
        if stage is not None and stage not in ("parser", "analyzer", "automation", "trigger"):
            raise ValueError("Unknown Flow stage")
        for label, value in (("producer", producer), ("kind", kind), ("report_name", report_name)):
            if value is not None and (not isinstance(value, str) or not 0 < len(value) <= 256):
                raise ValueError(f"Invalid Flow {label}")
        conditions = self._flow_conditions(conditions)
        backwards = before is not None
        where, params = ["attempt_id=?"], [attempt_id]
        for column, value in (("stage", stage), ("producer", producer), ("kind", kind)):
            if value is not None:
                where.append(column + "=?")
                params.append(value)
        if report_name is not None:
            where.extend(("stage='analyzer'", "kind='report'", "json_extract(data_json,'$.name')=?"))
            params.append(report_name)
        if before is not None:
            where.append("seq<?")
            params.append(before)
        elif after is not None:
            where.append("seq>?")
            params.append(after)
        budget = READER_TEXT_BYTES if conditions else PREVIEW_CHARS
        output, size, scanned, last = [], 0, 0, None
        started, exhausted = time.monotonic(), True
        with self._reader() as conn:
            end = conn.execute("SELECT coalesce(max(seq),0) FROM flow_records WHERE attempt_id=?", (attempt_id,)).fetchone()[0]
            cursor = conn.execute(
                f"SELECT seq,stage,producer,kind,substr(data_json,1,{budget + 1}),length(data_json),"
                "json_extract(evidence_json,'$.line_no'),json_extract(evidence_json,'$.line_start'),"
                "json_extract(evidence_json,'$.line_end'),substr(json_extract(evidence_json,'$.line'),1,128) "
                "FROM flow_records WHERE "
                + " AND ".join(where) + " ORDER BY seq " + ("DESC" if backwards else "ASC")
                + " LIMIT ?", [*params, limits.READER_SCAN_LIMIT + 1],
            )
            for row in cursor:
                if len(output) >= limit or scanned >= limits.READER_SCAN_LIMIT or (scanned and time.monotonic() - started > limits.READER_SECONDS):
                    exhausted = False
                    break
                if conditions and row[5] > READER_TEXT_BYTES:
                    raise ValueError("Flow record exceeds condition-search size limit; inspect its full output")
                raw = row[4]
                data = json.loads(raw) if row[5] <= budget else None
                matches = True
                for condition in conditions:
                    field, op, expected = condition["field"], condition["op"], condition["value"]
                    if not isinstance(data, dict) or field not in data:
                        matches = False
                        break
                    actual = data[field]
                    if op == "eq":
                        matches = actual == expected and (type(actual) is bool) == (type(expected) is bool)
                    elif op == "gte":
                        matches = type(actual) in (int, float) and actual >= expected
                    else:
                        matches = isinstance(actual, str) and expected in actual
                    if not matches:
                        break
                if matches:
                    truncated = row[5] > PREVIEW_CHARS
                    evidence = {key: value for key, value in zip(
                        ("line_no", "line_start", "line_end", "line"), row[6:10]) if value is not None} or None
                    item = dict(seq=row[0], stage=row[1], producer=row[2], kind=row[3],
                                data={} if truncated else data, evidence=evidence,
                                truncated=truncated, data_chars=row[5])
                    if truncated:
                        item["data_preview"] = raw[:PREVIEW_CHARS]
                    item_size = len(_json(item))
                    if output and size + item_size > limits.PAGE_BYTES:
                        exhausted = False
                        break
                    output.append(item)
                    size += item_size
                scanned += 1
                last = row[0]
        if backwards:
            output.reverse()
        return dict(rows=output, has_more=not exhausted, next_seq=last, cursor=last,
                    end_seq=end, scanned=scanned, health=self.health)

    def flow_text(self, job_id, attempt_id, seq, *, offset=0, limit=CHUNK_CHARS):
        """Retrieve exact output JSON in bounded character chunks."""
        self._require_attempt(job_id, attempt_id)
        if (type(seq) is not int or seq < 1 or type(offset) is not int or offset < 0
                or type(limit) is not int or not 1 <= limit <= CHUNK_CHARS):
            raise ValueError("Invalid Flow text range")
        with self._reader() as conn:
            row = conn.execute("SELECT substr(data_json,?,?),length(data_json) FROM flow_records WHERE attempt_id=? AND seq=?",
                               (offset + 1, limit, attempt_id, seq)).fetchone()
        if row is None:
            raise ValueError("Flow output not found")
        if offset > row[1]:
            raise ValueError("Offset is beyond the Flow output")
        return dict(text=row[0], offset=offset, next_offset=offset + len(row[0]), total=row[1],
                    has_more=offset + len(row[0]) < row[1], offset_unit="characters", health=self.health)

    @contextmanager
    def _projection_reader(self, label="Latest"):
        # Bound each query; never present a timed-out projection as complete.
        with self._reader() as conn:
            deadline = time.monotonic() + limits.READER_SECONDS
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            conn.execute("BEGIN")
            try:
                yield conn
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc):
                    raise ValueError(f"{label} query exceeded its read budget; narrow the producer or report name, or use History") from exc
                raise

    @staticmethod
    def _latest_options(stage, producer, report_name, mode):
        if stage not in ("parser", "analyzer"):
            raise ValueError("Latest stage must be parser or analyzer")
        if mode not in ("record", "fields"):
            raise ValueError("Latest mode must be record or fields")
        for label, value in (("producer", producer), ("report_name", report_name)):
            if value is not None and (not isinstance(value, str) or not 0 < len(value) <= 256):
                raise ValueError(f"Invalid Flow {label}")
        if stage == "parser" and report_name is not None:
            raise ValueError("report_name requires the analyzer stage")

    @staticmethod
    def _latest_query(attempt_id, stage, producer, report_name):
        params, where = [attempt_id, stage], ["attempt_id=?", "stage=?"]
        if producer is not None:
            where.append("producer=?")
            params.append(producer)
        if report_name is not None:
            where.append("report_name=?")
            params.append(report_name)
        return ("SELECT rowid AS head_id,seq,stage,producer,report_name,length(metadata_json) AS metadata_chars "
                "FROM flow_heads WHERE " + " AND ".join(where)), params

    @staticmethod
    def _field_group(head, attempt_id):
        return (attempt_id, head["stage"], head["producer"], head["report_name"])

    def _projection_parts(self, conn, head, attempt_id, mode, *, sources=False):
        # A tuple is a SQL text span, not loaded text. Slicing can skip a huge
        # value without fetching its preceding characters on every chunk read.
        if mode == "record":
            if sources:
                yield "{}"
            else:
                yield ("flow_records", "data_json", head["record_id"], head["record_chars"])
            return
        if not sources and head["stage"] == "analyzer":
            yield ("flow_heads", "metadata_json", head["head_id"], head["metadata_chars"] - 1)
            yield ',"values":'
        yield "{"
        first = True
        for field in conn.execute(
                "SELECT rowid,length(key_json),length(value_json),seq FROM flow_fields "
                "WHERE attempt_id=? AND stage=? AND producer=? AND report_name=? ORDER BY name",
                self._field_group(head, attempt_id)):
            if not first:
                yield ","
            first = False
            yield ("flow_fields", "key_json", field[0], field[1])
            yield ":"
            if sources:
                yield str(field[3])
            else:
                yield ("flow_fields", "value_json", field[0], field[2])
        yield "}"
        if not sources and head["stage"] == "analyzer":
            yield "}"

    @staticmethod
    def _text_slice(conn, parts, offset, limit):
        chunks = []
        deadline = time.monotonic() + limits.READER_SECONDS
        for part in parts:
            if time.monotonic() > deadline:
                raise ValueError("Latest text exceeded its read budget; narrow the producer or report name, or use History")
            length = part[3] if isinstance(part, tuple) else len(part)
            if offset >= length:
                offset -= length
                continue
            if isinstance(part, tuple):
                table, column, row_id, _ = part
                piece = conn.execute(f"SELECT substr({column},?,?) FROM {table} WHERE rowid=?",
                                     (offset + 1, min(length - offset, limit), row_id)).fetchone()[0]
            else:
                piece = part[offset:offset + limit]
            chunks.append(piece)
            offset = 0
            limit -= len(piece)
            if not limit:
                break
        return "".join(chunks)

    def _projection_sizes(self, conn, head, attempt_id, mode):
        if mode == "record":
            return head["record_chars"], 2
        row = conn.execute(
            "SELECT count(*),coalesce(sum(length(key_json)+length(value_json)+1),0),"
            "coalesce(sum(length(key_json)+length(CAST(seq AS TEXT))+1),0) FROM flow_fields "
            "WHERE attempt_id=? AND stage=? AND producer=? AND report_name=?",
            self._field_group(head, attempt_id)).fetchone()
        separators = 2 + max(0, row[0] - 1)
        data_chars = row[1] + separators
        if head["stage"] == "analyzer":
            data_chars += head["metadata_chars"] + len(',"values":')
        return data_chars, row[2] + separators

    def _projection_known_fields(self, conn, head, attempt_id):
        names, chars, truncated = [], 2, False
        for row in conn.execute(
                "SELECT substr(name,1,257),length(name) FROM flow_fields "
                "WHERE attempt_id=? AND stage=? AND producer=? AND report_name=? ORDER BY name LIMIT 257",
                self._field_group(head, attempt_id)):
            if len(names) >= 256 or row[1] > 256:
                truncated = True
                break
            size = len(_json(row[0])) + bool(names)
            if chars + size > PREVIEW_CHARS:
                truncated = True
                break
            names.append(row[0])
            chars += size
        return names, truncated

    @staticmethod
    def _head_columns(*, direct=False):
        projection = ("length(h.metadata_json) AS metadata_chars,h.rowid AS head_id," if direct
                      else "h.metadata_chars,h.head_id,")
        return ("h.seq,h.stage,h.producer,h.report_name," + projection
                + "f.rowid AS record_id,length(f.data_json) AS record_chars,"
                "json_extract(f.evidence_json,'$.line_no') AS line_no,"
                "json_extract(f.evidence_json,'$.line_start') AS line_start,"
                "json_extract(f.evidence_json,'$.line_end') AS line_end,"
                "substr(json_extract(f.evidence_json,'$.line'),1,128) AS line")

    def flow_latest(self, job_id, attempt_id, *, stage, producer=None, report_name=None,
                    mode="record", after=None, before=None, limit=PAGE_LIMIT):
        """Latest whole publication or explicitly merged fields, per producer/name.

        Cursors filter the chosen head, never the input history. Projections
        are maintained transactionally with the saved publications.
        """
        self._require_attempt(job_id, attempt_id)
        self._limit(limit)
        self._latest_options(stage, producer, report_name, mode)
        if after is not None and before is not None:
            raise ValueError("Use after or before, not both")
        if any(value is not None and (type(value) is not int or value < 0) for value in (after, before)):
            raise ValueError("Flow cursors must be nonnegative integers")
        output, size, last = [], 0, None
        with self._projection_reader() as conn:
            source, params = self._latest_query(attempt_id, stage, producer, report_name)
            where = " AND h.seq<?" if before is not None else " AND h.seq>?" if after is not None else ""
            params.append(attempt_id)
            if where:
                params.append(before if before is not None else after)
            heads = conn.execute(
                "WITH heads AS (" + source + ") SELECT " + self._head_columns()
                + " FROM heads h JOIN flow_records f ON f.attempt_id=? AND f.seq=h.seq WHERE 1=1"
                + where + " ORDER BY h.seq " + ("DESC" if before is not None else "ASC") + " LIMIT ?",
                [*params, limit + 1],
            )
            end = conn.execute("SELECT coalesce(max(seq),0) FROM flow_records WHERE attempt_id=?", (attempt_id,)).fetchone()[0]
            more = False
            started = time.monotonic()
            for head in heads:
                if len(output) >= limit or (output and time.monotonic() - started > limits.READER_SECONDS):
                    more = True
                    break
                data_chars, sources_chars = self._projection_sizes(conn, head, attempt_id, mode)
                raw = self._text_slice(conn, self._projection_parts(conn, head, attempt_id, mode), 0, PREVIEW_CHARS)
                sources = self._text_slice(conn, self._projection_parts(conn, head, attempt_id, mode, sources=True), 0, PREVIEW_CHARS)
                truncated = data_chars > PREVIEW_CHARS or sources_chars > PREVIEW_CHARS
                known_fields, fields_truncated = self._projection_known_fields(conn, head, attempt_id)
                evidence = {key: head[key] for key in ("line_no", "line_start", "line_end", "line") if head[key] is not None} or None
                item = dict(seq=head["seq"], stage=stage, producer=head["producer"],
                            kind="record" if stage == "parser" else "report", report_name=head["report_name"] if stage == "analyzer" else None,
                            mode=mode, data={} if truncated else json.loads(raw), evidence=evidence,
                            field_sources={} if truncated else json.loads(sources), truncated=truncated,
                            data_chars=data_chars, field_sources_chars=sources_chars,
                            known_fields=known_fields, fields_truncated=fields_truncated)
                if truncated:
                    item.update(data_preview=raw, field_sources_preview=sources)
                item_size = len(_json(item))
                if output and size + item_size > limits.PAGE_BYTES:
                    more = True
                    break
                output.append(item)
                size += item_size
                last = head["seq"]
        if before is not None:
            output.reverse()
        return dict(rows=output, has_more=more, next_seq=last, cursor=last, end_seq=end,
                    health=self.health)

    def flow_latest_text(self, job_id, attempt_id, seq, *, mode="fields", offset=0, limit=CHUNK_CHARS):
        """Chunk an exact {data,field_sources} projection at the selected head.

        Merged fields are current state. If that head has advanced, reject the
        request so clients refresh instead of mixing revisions between chunks.
        Record-mode full JSON is also available through the stable flow_text API.
        """
        self._require_attempt(job_id, attempt_id)
        if (type(seq) is not int or seq < 1 or type(offset) is not int or offset < 0
                or type(limit) is not int or not 1 <= limit <= CHUNK_CHARS or mode not in ("record", "fields")):
            raise ValueError("Invalid Latest text range")
        with self._projection_reader() as conn:
            head = conn.execute("SELECT " + self._head_columns(direct=True)
                                + " FROM flow_heads h "
                                "JOIN flow_records f ON f.attempt_id=h.attempt_id AND f.seq=h.seq "
                                "WHERE h.attempt_id=? AND h.seq=?", (attempt_id, seq)).fetchone()
            if head is None:
                raise ValueError("Latest projection changed or was not found; refresh Latest and retry")
            data_chars, sources_chars = self._projection_sizes(conn, head, attempt_id, mode)
            total = len('{"data":,"field_sources":}') + data_chars + sources_chars
            if offset > total:
                raise ValueError("Offset is beyond the Latest projection")

            def parts():
                yield '{"data":'
                yield from self._projection_parts(conn, head, attempt_id, mode)
                yield ',"field_sources":'
                yield from self._projection_parts(conn, head, attempt_id, mode, sources=True)
                yield "}"

            raw = self._text_slice(conn, parts(), offset, limit)
        return dict(text=raw, offset=offset, next_offset=offset + len(raw), total=total,
                    has_more=offset + len(raw) < total, offset_unit="characters", health=self.health)

    def flow_report_names(self, job_id, attempt_id, *, producer=None, query=None, after=None, limit=PAGE_LIMIT):
        """Bounded, literal-prefix name catalog; names are never identifiers."""
        self._require_attempt(job_id, attempt_id)
        self._limit(limit)
        self._latest_options("analyzer", producer, None, "record")
        for value in (query, after):
            if value is not None and (not isinstance(value, str) or len(value) > 256):
                raise ValueError("Invalid report-name catalog filter")
        with self._projection_reader() as conn:
            source, params = self._latest_query(attempt_id, "analyzer", producer, None)
            where = []
            if query:
                where.append("substr(report_name,1,?)=?")
                params.extend((len(query), query))
            if after is not None:
                where.append("report_name>?")
                params.append(after)
            rows = conn.execute("WITH heads AS (" + source + ") SELECT DISTINCT report_name FROM heads"
                                + (" WHERE " + " AND ".join(where) if where else "")
                                + " ORDER BY report_name LIMIT ?", [*params, limit + 1]).fetchall()
            names = [row[0] for row in rows[:limit]]
        more = len(rows) > limit
        cursor = names[-1] if more else None
        return dict(rows=names, has_more=more, cursor=cursor, next_name=cursor,
                    health=self.health)

    def automation_history(self, job_id, attempt_id, *, producer=None,
                           after=None, before=None, limit=PAGE_LIMIT):
        """One saved action per request, with its latest accepted publication.

        Cursor identity is the first requested publication's request_id, not
        the latest receipt's seq. A terminal update cannot move a request to
        another page. Readers refresh a page to see pending requests complete;
        flow_text(row.seq) reads that exact publication independently of updates.
        """
        self._require_attempt(job_id, attempt_id)
        self._limit(limit)
        if producer is not None and (not isinstance(producer, str) or not 0 < len(producer) <= 256):
            raise ValueError("Invalid Automation producer")
        if after is not None and before is not None:
            raise ValueError("Use after or before, not both")
        if any(value is not None and (type(value) is not int or value < 0) for value in (after, before)):
            raise ValueError("Automation cursors must be nonnegative integers")
        output, size, last, more = [], 0, None, False
        with self._projection_reader("Automation history") as conn:
            where, params = ["h.attempt_id=?"], [attempt_id]
            if producer is not None:
                where.append("h.producer=?")
                params.append(producer)
            if before is not None:
                where.append("h.request_id<?")
                params.append(before)
            elif after is not None:
                where.append("h.request_id>?")
                params.append(after)
            end = conn.execute("SELECT coalesce(max(request_id),0) FROM automation_heads WHERE attempt_id=?", (attempt_id,)).fetchone()[0]
            cursor = conn.execute(
                "SELECT h.request_id,f.seq,f.stage,f.producer,f.kind,"
                f"substr(f.data_json,1,{PREVIEW_CHARS}),length(f.data_json),"
                "json_extract(f.evidence_json,'$.line_no'),json_extract(f.evidence_json,'$.line_start'),"
                "json_extract(f.evidence_json,'$.line_end'),substr(json_extract(f.evidence_json,'$.line'),1,128) "
                "FROM automation_heads h JOIN flow_records f ON f.attempt_id=h.attempt_id AND f.seq=h.seq WHERE "
                + " AND ".join(where) + " ORDER BY h.request_id " + ("DESC" if before is not None else "ASC")
                + " LIMIT ?", [*params, limit + 1],
            )
            started = time.monotonic()
            for row in cursor:
                if len(output) >= limit or (output and time.monotonic() - started > limits.READER_SECONDS):
                    more = True
                    break
                truncated = row[6] > PREVIEW_CHARS
                evidence = {key: value for key, value in zip(
                    ("line_no", "line_start", "line_end", "line"), row[7:11]) if value is not None} or None
                item = dict(request_id=row[0], seq=row[1], stage=row[2], producer=row[3], kind=row[4],
                            data={} if truncated else json.loads(row[5]), evidence=evidence,
                            truncated=truncated, data_chars=row[6])
                if truncated:
                    item["data_preview"] = row[5]
                item_size = len(_json(item))
                if output and size + item_size > limits.PAGE_BYTES:
                    more = True
                    break
                output.append(item)
                size += item_size
                last = row[0]
        if before is not None:
            output.reverse()
        return dict(rows=output, has_more=more, next_seq=last, cursor=last, end_seq=end,
                    health=self.health)

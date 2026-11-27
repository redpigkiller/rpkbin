"""Read committed chart windows without calling the Archive writer."""

from __future__ import annotations

import hashlib
import heapq
import hmac
import json
import math
import re
import secrets
import sqlite3
import threading
import time
from bisect import bisect_right
from contextlib import contextmanager

from rpkbin.flow._views import _number, _read_view_payload, _text

from ._archive_format import _json
from ._chart_projection import _chart_number, _evidence_seq, _path_key, _typed_value

_SLOTS = threading.BoundedSemaphore(2)
_SECONDS = .15
# Keep the 50k-row cap as the primary bound, with headroom for a 40k-row
# two-series overview when the host is briefly contended.
_OVERVIEW_SECONDS = .5
_SCAN = 50_000
_RAW = 1000
_BODY = 252 * 1024  # Leave room for the HTTP scope and health envelope.
_CURSOR_KEY = secrets.token_bytes(32)
_AXES = {"time": "x", "elapsed": "elapsed", "seq": "flow_seq"}
_COLUMNS = "flow_seq,log_seq,val_num,val_text"


def _nonnegative(value):
    if type(value) is not int or not 0 <= value < (1 << 53):
        raise ValueError("Invalid chart sequence")
    return value


def _bounded(value, limit):
    if type(value) is not int or not 1 <= value <= limit:
        raise ValueError(f"Expected chart limit between 1 and {limit}")
    return value


_INTEGER_LITERAL = re.compile(r"(-?)(?:0[xX]([0-9a-fA-F]+)|0[bB]([01]+)|([0-9]+))", re.ASCII)


def _integer_literal(text):
    """Parse a decimal, 0x hex or 0b binary integer, optionally negative."""
    match = _INTEGER_LITERAL.fullmatch(text)
    if match is None or len(text) - len(match.group(1)) > 78:
        raise ValueError("Expected a decimal, 0x hexadecimal or 0b binary integer")
    sign, hex_digits, bin_digits, dec_digits = match.groups()
    value = int(hex_digits, 16) if hex_digits else int(bin_digits, 2) if bin_digits else int(dec_digits)
    return -value if sign else value


def _point(row, series_id=None):
    x, seq, log_seq, number, text = row
    result = dict(x=x, flow_seq=seq, log_seq=log_seq, value=text if text is not None else number)
    if series_id is not None:
        result["series_id"] = series_id
    return result


def _value(row):
    return row[4] if row[4] is not None else row[3]


def _is_unknown_value(val) -> bool:
    return val is None or val == "X" or (isinstance(val, str) and val.startswith("4s:") and "X" in val[3:])


def _is_highz_value(val) -> bool:
    return val == "Z" or (
        isinstance(val, str) and val.startswith("4s:") and "Z" in val[3:] and "X" not in val[3:]
    )


def _token(scope, payload):
    body = _json([scope, payload])
    return hmac.digest(_CURSOR_KEY, body.encode("ascii"), "sha256").hex() + "." + body


def _scope(*args):
    return hashlib.sha256(_json(args).encode("ascii")).hexdigest()


def _resume(cursor, scope):
    if cursor is None:
        return None
    if not isinstance(cursor, str) or len(cursor) > 6144:
        raise ValueError("Invalid chart cursor")
    signature, separator, body = cursor.partition(".")
    if not separator or not body.isascii():
        raise ValueError("Invalid chart cursor")
    expected = hmac.digest(_CURSOR_KEY, body.encode("ascii"), "sha256").hex()
    if not signature.isascii() or not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid or expired chart cursor")
    value = json.loads(body)
    if not isinstance(value, list) or len(value) != 2 or value[0] != scope:
        raise ValueError("Chart cursor belongs to a different query")
    return value[1]


def _position(value, length):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError("Invalid chart scan position")
    return [_number(value[0]), _nonnegative(value[1]), *value[2:]]


def _select(conn, key, axis, *, lo=None, hi=None, inclusive=False, after=None, before=None,
            reverse=False, as_of=None, limit=_SCAN + 1, scatter=False):
    # Only the validated axis can contribute an SQL identifier.
    column = _AXES[axis]
    clause, args = [f"{column} IS NOT NULL"], list(key)
    for value, op, name in ((lo, ">=", column), (hi, "<=" if inclusive else "<", column),
                            (after, ">=", f"({column},flow_seq)"),
                            (before, "<", f"({column},flow_seq)"),
                            (as_of, "<=", "flow_seq")):
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            clause.append(f"{name}{op}(?,?)")
            args.extend(value)
        else:
            clause.append(f"{name}{op}?")
            args.append(value)
    order = "DESC" if reverse else "ASC"
    columns = "flow_seq,log_seq,val_num,x" if scatter else _COLUMNS
    ordering = f"{column} {order}" + (f",flow_seq {order}" if axis != "seq" else "")
    sql = (f"SELECT {column},{columns} FROM chart_samples "
           "WHERE attempt_id=? AND view_id=? AND series_id=? AND " + " AND ".join(clause)
           + f" ORDER BY {ordering} LIMIT ?")
    cursor = conn.cursor()
    cursor.row_factory = None
    return cursor.execute(sql, [*args, limit])


def _predecessor(conn, key, axis, lo, as_of):
    return next(_select(conn, key, axis, hi=lo, reverse=True, as_of=as_of, limit=1), None)


class _Buckets:
    def __init__(self, kind, lo, hi, count, thresholds, left):
        self.wave = kind == "waveform"
        self.lo, self.hi, self.count = lo, hi, count
        self.scale = count / (hi - lo) if hi - lo >= 1e-300 else None
        self.thresholds = thresholds
        self.previous = left.copy()
        self.cells = {series_id: {} for series_id in left}

    def add(self, series_id, row):
        index = min(self.count - 1, max(0, int((row[0] - self.lo) * self.scale))) if self.scale else 0
        cells = self.cells[series_id]
        cell = cells.get(index)
        if self.wave:
            previous = self.previous[series_id]
            if cell is None:
                cell = cells[index] = dict(index=index, entry=previous, first=row, last=row,
                                           first_change=None, transitions=0,
                                           unknown=previous is None or _is_unknown_value(_value(previous)))
            value = _value(row)
            if previous is not None and _value(previous) != value:
                if cell["transitions"] == 0:
                    cell["first_change"] = row
                cell["transitions"] += 1
            cell["last"] = row
            if _is_unknown_value(value):
                cell["unknown"] = True
            self.previous[series_id] = row
        else:
            num = row[3]
            if cell is None:
                cell = cells[index] = dict(index=index, first=row, last=row, min=None, max=None,
                                           has_gap=False, violation=None)
            cell["last"] = row
            if num is None:
                cell["has_gap"] = True
                return
            if cell["min"] is None or num < cell["min"][3]:
                cell["min"] = row
            if cell["max"] is None or num > cell["max"][3]:
                cell["max"] = row
            if cell["violation"] is None:
                low, high = self.thresholds.get(series_id, (-math.inf, math.inf))
                if num < low or num > high:
                    cell["violation"] = row

    def export(self):
        return {sid: [{k: _point(v) if isinstance(v, tuple) else v for k, v in cell.items()}
                      for cell in cells.values()] for sid, cells in self.cells.items()}


def _thresholds(view):
    limits = {}
    for item in view["options"].get("thresholds", ()):
        sides = limits.setdefault(item["series_id"], [-math.inf, math.inf])
        sides[item["side"] == "max"] = item["value"]
    return limits


def _scatter_sample(heap, row, lane, limit):
    # A fixed integer permutation spreads selection across the full stream.
    rank = (row[1] * 11400714819323198485 + lane * 14029467366897019727) & ((1 << 64) - 1)
    item = (-rank, lane, row)
    if len(heap) < limit:
        heapq.heappush(heap, item)
    elif item > heap[0]:
        heapq.heapreplace(heap, item)


def _stats_add(stats, value):
    n, low, high, mean, m2, missing = stats
    if value is None:
        stats[5] += 1
        return
    delta = value - mean
    next_mean = mean + delta / (n + 1)
    stats[:5] = [n + 1, value if low is None else min(low, value),
                 value if high is None else max(high, value),
                 next_mean, m2 + delta * (value - next_mean)]


def _stats_export(stats):
    n, low, high, mean, m2, missing = stats
    return dict(n=n, min=low, max=high, mean=mean if n else None,
                std=math.sqrt(max(0, m2 / n)) if n else None, missing=missing)


def _bin_edges(stats, bins):
    low, high = stats[1:3]
    if low is None:
        return []
    if low == high:
        return [low, high]
    # Adjacent floats can make multiple nominal edges round to the same float.
    return list(dict.fromkeys([low + (high - low) * (i / bins) for i in range(bins)] + [high]))


def _bin(edges, counts, tails, value):
    if value is None or not edges:
        return
    if value < edges[0]:
        tails[0] += 1
    elif value > edges[-1]:
        tails[1] += 1
    else:
        counts[min(len(counts) - 1, bisect_right(edges, value) - 1)] += 1


class _ArchiveChartQueries:
    @contextmanager
    def _chart_reader(self, job_id, attempt_id, seconds=_SECONDS):
        for name in (job_id, attempt_id):
            _text(name, 128)
        if not _SLOTS.acquire(blocking=False):
            raise ValueError("Chart readers are busy; retry this request")
        try:
            with self._reader() as conn:
                conn.execute("PRAGMA busy_timeout=35")
                deadline = time.monotonic() + seconds
                # Give a scan a chance to publish its own resume position before
                # the VM backstop interrupts an expensive SQLite operation.
                conn.set_progress_handler(lambda: int(time.monotonic() > deadline + .015), 1000)
                conn.execute("BEGIN")
                valid = conn.execute("SELECT job_id FROM attempts WHERE id=?", (attempt_id,)).fetchone()
                if valid is None or valid[0] != job_id:
                    raise ValueError("Attempt not found for this job")
                config = conn.execute("SELECT spec,parsers FROM chart_config WHERE attempt_id=?",
                                      (attempt_id,)).fetchone()
                config = (None, []) if config is None else (_read_view_payload(json.loads(config[0])),
                                                            json.loads(config[1]))
                head = conn.execute(
                    "SELECT seq FROM flow_records WHERE attempt_id=? ORDER BY seq DESC LIMIT 1",
                    (attempt_id,),
                ).fetchone()
                health = dict(error=self.error, closed_cleanly=self._closed_cleanly(conn))
                yield conn, config, head[0] if head else 0, health, deadline
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT:
                raise ValueError("Chart read budget expired; narrow the range or retry") from exc
            raise
        finally:
            _SLOTS.release()

    @staticmethod
    def _chart_fallback(conn, attempt_id, parsers, as_of):
        for parser in parsers:
            rows = conn.execute(
                "SELECT seq,substr(data_json,1,4097),substr(evidence_json,1,8193) "
                "FROM flow_records WHERE attempt_id=? AND stage='parser' AND producer=? "
                "AND kind='record' AND seq<=? ORDER BY seq DESC LIMIT 100",
                (attempt_id, parser, as_of),
            ).fetchall()
            if rows:
                break
        else:
            return None, [], []
        parsed = []
        for seq, raw, evidence in reversed(rows):
            data = json.loads(raw) if len(raw) <= 4096 else {}
            ref = _evidence_seq(json.loads(evidence)) if evidence and len(evidence) <= 8192 else None
            parsed.append((seq, data, ref))
        wanted = sorted({ref for _, _, ref in parsed if ref is not None})
        existing = set()
        for start in range(0, len(wanted), 500):  # at most 100 refs; chunked for the variable limit
            chunk = wanted[start:start + 500]
            existing.update(row[0] for row in conn.execute(
                "SELECT seq FROM records WHERE attempt_id=? AND seq IN (%s)" % ",".join("?" * len(chunk)),
                (attempt_id, *chunk)))
        items, fields = [], set()
        for seq, data, ref in parsed:
            if ref is not None and ref not in existing:
                ref = None
            values = {}
            for key, value in data.items():
                if _chart_number(value) is not None:
                    try:
                        values[_text(key)] = value
                    except ValueError:
                        continue
            fields.update(values)
            items.append((seq, ref, values))
        ids = sorted(fields)[:4]
        if not ids:
            return None, [], []
        defs = [dict(id=key, label=key, unit="", type="number", path=[key]) for key in ids]
        view = dict(id="__auto__", title="Recent parser scalars", kind="xy_line",
                    source=dict(stage="parser", producer=parser), axis=dict(field=None, fallback=["seq"]),
                    series=dict(mode="wide", defaults=ids), options=dict(thresholds=[]))
        return view, defs, items

    @staticmethod
    def _watermark(requested, head):
        if requested is None:
            return head
        if _nonnegative(requested) > head:
            raise ValueError("Chart watermark has not been committed")
        return requested

    def _chart_view(self, conn, config, attempt_id, view_id, as_of):
        payload, parsers = config
        items = payload["items"] if payload else []
        if view_id == "__auto__" and not items:
            view, defs, rows = self._chart_fallback(conn, attempt_id, parsers, as_of)
            if view is not None:
                return view, "auto", defs, rows
        for view in items:
            if view["id"] == view_id:
                return view, payload["digest"], [], []
        raise ValueError("Chart view not found")

    def chart_meta(self, job_id, attempt_id):
        with self._chart_reader(job_id, attempt_id) as (conn, config, head, health, _):
            payload, parsers = config
            views = payload["items"] if payload else []
            diagnostics = []
            if not views:
                view, defs, rows = self._chart_fallback(conn, attempt_id, parsers, head)
                if view:
                    views = [dict(view, series_count=len(defs), domains={"seq": [rows[0][0], rows[-1][0]]},
                                  axes=["seq"], default_axis="seq")]
                else:
                    diagnostics.append("No declared views or recent numeric Parser records")
            else:
                for view in views:
                    count, slo, shi, lo, hi, elo, ehi, invalid = conn.execute(
                        "SELECT count(*),min(first_seq),max(last_seq),min(x_lo),max(x_hi),"
                        "min(elapsed_lo),max(elapsed_hi),sum(invalid) "
                        "FROM chart_series WHERE attempt_id=? AND view_id=?",
                        (attempt_id, view["id"]),
                    ).fetchone()
                    dropped = conn.execute("SELECT count FROM chart_overflow WHERE attempt_id=? AND view_id=?",
                                           (attempt_id, view["id"])).fetchone()
                    domains = {axis: [start, end] for axis, start, end in
                               (("time", lo, hi), ("elapsed", elo, ehi), ("seq", slo, shi)) if start is not None}
                    if view["kind"] == "scatter":
                        domains = {key: value for key, value in domains.items() if key == "seq"}
                    preferred = ["seq"] if view["axis"]["field"] is None else ["time", *view["axis"]["fallback"]]
                    axes = list(domains)
                    default = next((mode for mode in preferred if mode in domains), axes[0] if axes else None)
                    view.update(series_count=count, domains=domains, axes=axes, default_axis=default,
                                diagnostics=dict(invalid=invalid or 0, dropped=dropped[0] if dropped else 0))
            return dict(views=views, digest=payload["digest"] if payload else None,
                        spec_version=1, as_of_seq=head, diagnostics=diagnostics, health=health)

    def chart_series(self, job_id, attempt_id, *, view_id, as_of_seq=None,
                     prefix=None, query="", limit=100, cursor=None, ids=None):
        _bounded(limit, 100)
        query = _text(query, empty=True).casefold()
        if prefix is None:
            prefix = []
        if not isinstance(prefix, list) or len(prefix) > 12:
            raise ValueError("Invalid chart hierarchy prefix")
        path = _path_key([_text(part, 512) for part in prefix])
        with self._chart_reader(job_id, attempt_id) as (conn, config, head, health, deadline):
            mark = self._watermark(as_of_seq, head)
            view, digest, defs, _ = self._chart_view(conn, config, attempt_id, view_id, mark)
            if ids is not None:
                if (not isinstance(ids, list) or len(ids) > min(limit, 64)
                        or prefix or query or cursor is not None):
                    raise ValueError("Invalid exact chart series lookup")
                ids = list(dict.fromkeys(_text(sid, 512) for sid in ids))
                if defs:
                    items = [item for item in defs if item["id"] in ids]
                else:
                    items = []
                    for sid in ids:
                        row = conn.execute(
                            "SELECT path,definition,first_seq FROM chart_series WHERE attempt_id=? "
                            "AND view_id=? AND series_id=? AND first_seq<=?",
                            (attempt_id, view_id, sid, mark),
                        ).fetchone()
                        if row is not None:
                            items.append(dict(json.loads(row[1]), path=json.loads(row[0]), first_seq=row[2]))
                return dict(series=items, complete=True, next_cursor=None, as_of_seq=mark, health=health)
            identity = _scope(self.run_id, attempt_id, digest, view_id, mark, prefix, query)
            start = _resume(cursor, identity)
            if defs:
                matches = [item for item in defs if (not prefix or item["path"][:len(prefix)] == prefix)
                           and query in item["id"].casefold()]
                offset = _nonnegative(start) if start is not None else 0
                end = offset + limit
                return dict(series=matches[offset:end], complete=end >= len(matches),
                            next_cursor=_token(identity, end) if end < len(matches) else None,
                            as_of_seq=mark, health=health)
            where, params = [], [attempt_id, view["id"]]
            if prefix:
                where.append(" AND path_key>=? AND path_key<?")
                params.extend((path + "/", path + "0"))
            if start is not None:
                if not isinstance(start, list) or len(start) != 2:
                    raise ValueError("Invalid chart directory cursor")
                where.append(" AND (path_key,series_id)>(?,?)")
                params.extend((_text(start[0], 3100, empty=True), _text(start[1], 512)))
            rows = conn.execute(
                "SELECT path_key,series_id,path,definition,first_seq FROM chart_series INDEXED BY chart_paths "
                "WHERE attempt_id=? AND view_id=?" + "".join(where)
                + " ORDER BY path_key,series_id LIMIT 1001", params,
            )
            items, size, last, done = [], 0, None, True
            for index, (key, sid, raw_path, definition, first_seq) in enumerate(rows):
                if index >= 1000 or len(items) >= limit or (last and time.monotonic() > deadline):
                    done = False
                    break
                if first_seq <= mark:
                    parts = json.loads(raw_path)
                    item = dict(json.loads(definition), path=parts, first_seq=first_seq)
                    if not query or query in "/".join([sid, item["label"], *parts]).casefold():
                        size += len(_json(item)) + 1
                        if size > _BODY - 10000:
                            done = False
                            break
                        items.append(item)
                last = [key, sid]
            return dict(series=items, as_of_seq=mark, health=health, complete=done,
                        next_cursor=None if done else _token(identity, last))

    @staticmethod
    def _definitions(conn, attempt_id, view, ids, defs, mark):
        if defs:
            by_id = {item["id"]: item for item in defs}
        else:
            by_id = {}
            for series_id in ids:
                row = conn.execute(
                    "SELECT definition FROM chart_series WHERE attempt_id=? AND view_id=? "
                    "AND series_id=? AND first_seq<=?", (attempt_id, view["id"], series_id, mark),
                ).fetchone()
                if row:
                    by_id[series_id] = json.loads(row[0])
        if any(series_id not in by_id for series_id in ids):
            raise ValueError("Chart series not found at this watermark")
        return [by_id[series_id] for series_id in ids]

    def chart_window(self, job_id, attempt_id, *, view_id, series, lo, hi, axis="time",
                     mode="raw", buckets=160, as_of_seq=None, inclusive=False, cursor=None):
        lo, hi = _number(lo), _number(hi)
        if lo > hi or (lo == hi and not inclusive):
            raise ValueError("Invalid chart window range")
        if axis not in _AXES or mode not in ("raw", "overview") or type(inclusive) is not bool:
            raise ValueError("Invalid chart axis or window mode")
        if not isinstance(series, list) or not 1 <= len(series) <= 64:
            raise ValueError("Select 1 to 64 chart series")
        ids = [_text(sid, 512) for sid in series]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate chart series")
        _bounded(buckets, 1200)
        with self._chart_reader(job_id, attempt_id,
                                seconds=_OVERVIEW_SECONDS if mode == "overview" else _SECONDS) as (
                                    conn, config, head, health, deadline
                                ):
            mark = self._watermark(as_of_seq, head)
            view, digest, defs, auto = self._chart_view(conn, config, attempt_id, view_id, mark)
            definitions = self._definitions(conn, attempt_id, view, ids, defs, mark)
            kind = view["kind"]
            if (auto or kind == "scatter") and axis != "seq":
                raise ValueError("This view uses a publication sequence window")
            if kind == "histogram" and len(ids) != 1:
                raise ValueError("Select a single Histogram series")
            identity = _scope(self.run_id, attempt_id, digest, view_id, mark, ids, lo, hi, axis, mode,
                              inclusive, buckets)
            state = _resume(cursor, identity)
            histogram = view["options"].get("bins") if kind == "histogram" else None
            hist_state = None
            if histogram is not None and state is not None:
                if not isinstance(state, dict) or "position" not in state:
                    raise ValueError("Invalid histogram cursor")
                hist_state, state = state, state["position"]
            if state is not None:
                state = _position(state, 3)
                _nonnegative(state[2])
                if state[2] >= len(ids):
                    raise ValueError("Invalid chart lane")
            left = {}
            for sid in ids:
                left[sid] = (_predecessor(conn, (attempt_id, view_id, sid), axis, lo, mark)
                             if kind == "waveform" else None)

            resume_key = tuple(state) if state is not None else None

            def events(lane, sid):
                if auto:
                    source = ((seq, seq, ref, values.get(sid), None) for seq, ref, values in auto
                              if lo <= seq and (seq < hi or inclusive and seq == hi))
                else:
                    source = _select(conn, (attempt_id, view_id, sid), axis, lo=lo, hi=hi,
                                     inclusive=inclusive, after=state[:2] if state else None,
                                     scatter=kind == "scatter")
                for row in source:
                    position = (row[0], row[1], lane)
                    if resume_key is None or position > resume_key:
                        yield position, row

            def scan():
                return heapq.merge(*(events(i, sid) for i, sid in enumerate(ids)))

            result = dict(as_of_seq=mark, mode=mode, kind=kind, complete=True, next_cursor=None,
                          scanned=0, health=health, coverage=None)
            if kind == "waveform":
                result["left"] = {sid: _point(row) if row else None for sid, row in left.items()}
            if histogram is not None:
                return self._histogram(scan, histogram, hist_state, state, result, identity, deadline, mark)

            overview = mode == "overview" and kind in ("waveform", "xy_line")
            accumulator, points, sample, valid, size = None, [], [], 0, 0
            scatter_limit = min(350, (_BODY - 18000) // (180 + max(len(_json(sid)) for sid in ids)))
            if overview:
                # Each chart and value type has a known upper bound per bucket.
                cell_bytes = sum(4600 if d["type"] == "state" else
                                 1150 if kind == "waveform" else 680 for d in definitions)
                buckets = min(buckets, max(1, (_BODY - 24000) // cell_bytes))
                accumulator = _Buckets(kind, lo, hi, buckets, _thresholds(view), left)
                if state is not None and kind == "waveform":
                    for lane, sid in enumerate(ids):
                        # Resume inside a bucket with the actual preceding value of this lane.
                        if lane <= state[2]:
                            next_key = [state[0], state[1] + 1]
                        else:
                            next_key = state[:2]
                        previous = next(_select(conn, (attempt_id, view_id, sid), axis, lo=lo,
                                                before=next_key, reverse=True, as_of=mark, limit=1), None)
                        if previous is not None:
                            accumulator.previous[sid] = previous
            last = state
            for position, row in scan():
                if result["scanned"] >= (_SCAN if overview or kind == "scatter" else _RAW) or (
                        result["scanned"] and time.monotonic() > deadline):
                    result["complete"] = False
                    break
                lane = position[2]
                if row[1] <= mark:
                    if overview:
                        accumulator.add(ids[lane], row)
                    elif kind == "scatter":
                        if row[3] is not None and row[4] is not None:
                            valid += 1
                            _scatter_sample(sample, row, lane, scatter_limit)
                    else:
                        point = _point(row, ids[lane])
                        size += len(_json(point)) + 1
                        if size > _BODY - 18000:
                            result["complete"] = False
                            break
                        points.append(point)
                result["scanned"] += 1
                last = position
            if overview:
                result.update(buckets=accumulator.export(), bucket_count=buckets)
            elif kind == "scatter":
                points = [dict(x=row[4], y=row[3], flow_seq=row[1], log_seq=row[2],
                               series_id=ids[lane], rank=str(-rank))
                          for rank, lane, row in sorted(sample, key=lambda item: (item[2][1], item[1]))]
                result.update(points=points, valid=valid, sampled=valid > len(points), sample_limit=scatter_limit)
            else:
                result["points"] = points
            result["coverage"] = list(last) if last is not None else None
            if not result["complete"]:
                result["next_cursor"] = _token(identity, last)
            return result

    @staticmethod
    def _histogram(scan, bins, saved, position, result, identity, deadline, mark):
        fixed = isinstance(bins, list)
        stats = [0, None, None, 0, 0, 0] if saved is None else saved.get("stats")
        edges = bins if fixed else ([] if saved is None else saved.get("edges"))
        phase = "bins" if fixed or edges else "stats"
        if (not isinstance(stats, list) or len(stats) != 6 or not isinstance(edges, list) or len(edges) > 129
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in stats if n is not None)
                or any(_chart_number(n) is None for n in edges)
                or any(a > b for a, b in zip(edges, edges[1:]))):
            raise ValueError("Invalid histogram cursor")
        counts, tails = [0] * max(0, len(edges) - 1), [0, 0]
        last = position
        for key, row in scan():
            if result["scanned"] >= _SCAN or (result["scanned"] and time.monotonic() > deadline):
                result["complete"] = False
                break
            if row[1] <= mark:
                value = row[3]
                if fixed or phase == "stats":
                    _stats_add(stats, value)
                if phase == "bins":
                    _bin(edges, counts, tails, value)
            last = key
            result["scanned"] += 1
        if result["complete"] and phase == "stats" and stats[0]:
            edges = _bin_edges(stats, bins)
            counts = [0] * (len(edges) - 1)
            if position is None and time.monotonic() < deadline:
                for index, (key, row) in enumerate(scan()):
                    if result["scanned"] >= _SCAN or (index % 128 == 0 and time.monotonic() > deadline):
                        result["complete"] = False
                        if index == 0:
                            last = None
                        break
                    if row[1] <= mark:
                        _bin(edges, counts, tails, row[3])
                    result["scanned"] += 1
                    last = key
            else:
                result["complete"] = False
                last = None
            phase = "bins"
        result.update(stats=_stats_export(stats), edges=edges, bins=counts,
                      underflow=tails[0], overflow=tails[1], phase=phase,
                      coverage=list(last) if last is not None else None)
        if not result["complete"]:
            result["next_cursor"] = _token(identity, dict(position=last, stats=stats, edges=edges))
        return result

    def chart_find(self, job_id, attempt_id, *, view_id, series_id, op, value=None,
                   axis="time", direction="forward", anchor=None, bit_slice=None,
                   as_of_seq=None, cursor=None):
        if (axis not in _AXES or op not in ("rising", "falling", "any", "eq")
                or direction not in ("forward", "backward")):
            raise ValueError("Invalid chart search")
        series_id = _text(series_id, 512)
        anchor = _position(anchor, 2) if anchor is not None else None
        reverse = direction == "backward"
        with self._chart_reader(job_id, attempt_id) as (conn, config, head, health, deadline):
            mark = self._watermark(as_of_seq, head)
            view, digest, defs, _ = self._chart_view(conn, config, attempt_id, view_id, mark)
            if view["kind"] != "waveform":
                raise ValueError("Chart find requires a waveform")
            definition, = self._definitions(conn, attempt_id, view, [series_id], defs, mark)
            width = definition.get("bits", 0)
            shift, mask = 0, None
            if bit_slice is not None:
                if not isinstance(bit_slice, list) or len(bit_slice) != 2:
                    raise ValueError("Expected [msb, lsb]")
                msb, shift = (_nonnegative(n) for n in bit_slice)
                if not 0 <= shift <= msb < width:
                    raise ValueError("Bit slice outside the bus")
                mask = (1 << (msb - shift + 1)) - 1
            if op in ("rising", "falling") and definition["type"] != "bit" and mask != 1:
                raise ValueError("Rising and falling require a single bit")
            expected = None
            if op == "eq":
                if width or definition["type"] == "bit":
                    if isinstance(value, str) and value.strip().upper() in ("X", "Z"):
                        expected = value.strip().upper()
                    else:
                        if isinstance(value, str):
                            value = _integer_literal(value)
                        if mask is not None:
                            if type(value) is not int or not 0 <= value <= mask:
                                raise ValueError("Value outside the bit slice")
                            expected = value
                        else:
                            num, text = _typed_value(value, definition)
                            expected = num if text is None else text
                else:
                    num, text = _typed_value(value, definition)
                    expected = num if text is None else text
            elif value is not None:
                raise ValueError("A search value requires the eq operation")
            identity = _scope(self.run_id, attempt_id, digest, view_id, series_id, axis, direction,
                              op, expected, bit_slice, anchor, mark)
            resume = _resume(cursor, identity)
            start = _position(resume["after"], 2) if resume is not None else anchor
            key = attempt_id, view_id, series_id
            start_key = tuple(start) if start is not None else None
            source = _select(conn, key, axis, reverse=reverse,
                             after=start if not reverse else None, before=start if reverse else None,
                             limit=_SCAN + 2)
            adjacent = resume["adjacent"] if resume is not None and op != "eq" else None
            if resume is None and not reverse and op != "eq" and start is not None:
                adjacent = next(_select(conn, key, axis, before=[start[0], start[1] + 1],
                                        reverse=True, as_of=mark, limit=1), None)

            def converted(row):
                item = _value(row)
                if item is None:
                    return "X"
                if item in ("X", "Z"):
                    return item
                if isinstance(item, str) and item.startswith("4s:"):
                    bits_str = item[3:]
                    if mask is not None:
                        start_idx = len(bits_str) - 1 - msb
                        stop_idx = len(bits_str) - shift
                        sub = bits_str[start_idx:stop_idx]
                        if "X" in sub:
                            return "X" if set(sub) == {"X"} else "4s:" + sub
                        if "Z" in sub:
                            return "Z" if set(sub) == {"Z"} else "4s:" + sub
                        return int(sub, 2)
                    return item
                return (int(item) >> shift) & mask if mask is not None else item

            def matches(row, before):
                val = converted(row)
                if op == "eq":
                    if expected == "X":
                        return _is_unknown_value(val)
                    if expected == "Z":
                        return _is_highz_value(val)
                    return val == expected
                if before is None:
                    return False
                old = converted(before)
                if op == "any":
                    return old != val
                return (old, val) == ((0, 1) if op == "rising" else (1, 0))

            scanned, last, hit, complete = 0, None, None, True
            for row in source:
                if not reverse and start_key is not None and row[:2] <= start_key:
                    continue
                if scanned >= _SCAN or (scanned and time.monotonic() > deadline):
                    complete = False
                    break
                scanned += 1
                last = row[:2]
                if row[1] > mark:
                    continue
                target, before = (adjacent, row) if reverse and op != "eq" else (row, adjacent)
                if target is not None and matches(target, before):
                    hit = _point(target)
                    break
                adjacent = row
            if not complete:
                # In reverse, the last eligible event still needs an older
                # neighbor. In forward order it is the previous value.
                cursor = _token(identity, dict(after=last, adjacent=None if op == "eq" else adjacent))
            return dict(hit=hit, complete=complete, scanned=scanned, as_of_seq=mark, health=health,
                        next_cursor=None if complete else cursor)

    def export_vcd(self, job_id, attempt_id, *, view_id, as_of_seq=None, series=None, axis="time"):
        """Export committed waveform samples to standard IEEE 1364 VCD text."""
        if axis not in _AXES:
            raise ValueError("Invalid chart axis")
        if series is not None:
            if not isinstance(series, list) or not 1 <= len(series) <= 64:
                raise ValueError("Select 1 to 64 chart series")
            series_req = list(dict.fromkeys(_text(sid, 512) for sid in series))
        else:
            series_req = None
        with self._chart_reader(job_id, attempt_id, seconds=_OVERVIEW_SECONDS) as (
            conn, config, head, _health, _deadline
        ):
            mark = self._watermark(as_of_seq, head)
            view, _digest, defs, auto = self._chart_view(conn, config, attempt_id, view_id, mark)
            if auto or view["kind"] not in ("waveform", "xy_line"):
                raise ValueError("VCD export requires a waveform or time-series view")
            if series_req is None:
                if defs:
                    series_req = [item["id"] for item in defs[:64]]
                else:
                    series_req = [
                        row[0] for row in conn.execute(
                            "SELECT series_id FROM chart_series WHERE attempt_id=? AND view_id=? "
                            "AND first_seq<=? ORDER BY path_key,series_id LIMIT 64",
                            (attempt_id, view["id"], mark),
                        )
                    ]
            if not series_req:
                raise ValueError("No signals available to export")
            definitions = self._definitions(conn, attempt_id, view, series_req, defs, mark)
            paths = {}
            if defs:
                paths = {d["id"]: list(d.get("path") or [d.get("label", d["id"])]) for d in definitions}
            else:
                for sid in series_req:
                    row = conn.execute(
                        "SELECT path FROM chart_series WHERE attempt_id=? AND view_id=? AND series_id=?",
                        (attempt_id, view["id"], sid),
                    ).fetchone()
                    paths[sid] = json.loads(row[0]) if row else [sid]

            def vcd_code(index):
                digits = []
                n = index
                while True:
                    digits.append(chr(33 + (n % 94)))
                    n //= 94
                    if n == 0:
                        break
                    n -= 1
                return "".join(reversed(digits))

            def vcd_ident(text):
                cleaned = re.sub(r"[^A-Za-z0-9_.\[\]:]", "_", str(text or "sig"))
                return cleaned or "sig"

            codes = {d["id"]: vcd_code(i) for i, d in enumerate(definitions)}
            unit_raw = str((view.get("axis") or {}).get("unit") or "ns").lower()
            if axis == "elapsed":
                timescale, tscale_mult = "1us", 1_000_000.0
            elif axis == "seq":
                timescale, tscale_mult = "1ns", 1.0
            elif unit_raw in ("fs", "ps", "ns", "us", "ms", "s"):
                timescale, tscale_mult = f"1{unit_raw}", 1.0
            else:
                timescale, tscale_mult = "1ns", 1.0

            lines = [
                f"$date\n   {time.strftime('%Y-%m-%d %H:%M:%S')}\n$end",
                "$version\n   rpk-wave\n$end",
                f"$timescale {timescale} $end",
            ]
            tree = {}
            for d in definitions:
                parts = [vcd_ident(p) for p in (paths.get(d["id"]) or [d.get("label", d["id"])])]
                cur = tree
                for mod in parts[:-1]:
                    cur = cur.setdefault(mod, {})
                cur.setdefault("__vars__", []).append((d, parts[-1]))

            def emit_scope(node):
                for d, leaf in node.get("__vars__", ()):
                    code = codes[d["id"]]
                    sig_type = d.get("type", "bit")
                    bits = int(d.get("bits") or 1)
                    if sig_type == "number":
                        lines.append(f"$var real 64 {code} {leaf} $end")
                    elif sig_type == "state":
                        lines.append(f"$var string 1 {code} {leaf} $end")
                    else:
                        lines.append(f"$var wire {bits} {code} {leaf} $end")
                for mod, child in node.items():
                    if mod == "__vars__":
                        continue
                    lines.append(f"$scope module {mod} $end")
                    emit_scope(child)
                    lines.append("$upscope $end")

            emit_scope(tree)
            lines.append("$enddefinitions $end")

            by_id = {d["id"]: d for d in definitions}

            def fmt_vcd_val(d, val):
                code = codes[d["id"]]
                sig_type = d.get("type", "bit")
                bits = int(d.get("bits") or 1)
                if sig_type == "number":
                    try:
                        return f"r{float(val):.16g} {code}" if val is not None else None
                    except (TypeError, ValueError):
                        return None
                if sig_type == "state":
                    txt = re.sub(r"\s+", "_", str(val if val is not None else "X")) or "X"
                    return f"s{txt} {code}"
                if bits <= 1:
                    if val is None or val == "X":
                        ch = "x"
                    elif val == "Z":
                        ch = "z"
                    elif isinstance(val, str) and val.startswith("4s:"):
                        ch = val[-1].lower() if val[-1].lower() in ("0", "1", "x", "z") else "x"
                    elif isinstance(val, (int, float)):
                        ch = (
                            "x"
                            if isinstance(val, float) and not math.isfinite(val)
                            else ("0" if val == 0 else "1")
                        )
                    else:
                        ch = "1" if str(val) not in ("0", "0.0", "", "False", "false") else "0"
                    return f"{ch}{code}"
                if val is None or val == "X":
                    return f"bx {code}"
                if val == "Z":
                    return f"bz {code}"
                if isinstance(val, str) and val.startswith("4s:"):
                    return f"b{val[3:].lower()} {code}"
                try:
                    iv = int(val)
                    return f"b{format(iv & ((1 << bits) - 1), 'b')} {code}"
                except (TypeError, ValueError):
                    return f"bx {code}"

            def stream_series(sid):
                for row in _select(conn, (attempt_id, view["id"], sid), axis, as_of=mark, limit=_SCAN * 4):
                    yield row[0], row[1], sid, _value(row)

            merged = heapq.merge(*(stream_series(sid) for sid in series_req), key=lambda item: (item[0], item[1]))
            current_tick = None
            for idx, (x_val, _fseq, sid, val) in enumerate(merged):
                if idx >= _SCAN * 4:
                    break
                tick = max(0, int(round(float(x_val) * tscale_mult)))
                encoded = fmt_vcd_val(by_id[sid], val)
                if not encoded:
                    continue
                if current_tick is None or tick > current_tick:
                    current_tick = tick
                    lines.append(f"#{current_tick}")
                lines.append(encoded)
            return "\n".join(lines) + "\n"

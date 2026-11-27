"""Project Flow publications into the Archive's transaction."""

from __future__ import annotations

import json
import re

from rpkbin.flow._views import _number, _text

from ._archive_format import _json

CHART_SCHEMA = """
CREATE TABLE chart_config(
    attempt_id TEXT PRIMARY KEY REFERENCES attempts(id),
    spec TEXT NOT NULL, parsers TEXT NOT NULL);
CREATE TABLE chart_series(
    attempt_id TEXT NOT NULL REFERENCES attempts(id), view_id TEXT NOT NULL,
    series_id TEXT NOT NULL, path_key TEXT NOT NULL, path TEXT NOT NULL,
    definition TEXT NOT NULL, first_seq INTEGER NOT NULL, last_seq INTEGER NOT NULL,
    x_lo REAL, x_hi REAL, elapsed_lo REAL, elapsed_hi REAL, invalid INTEGER NOT NULL,
    PRIMARY KEY(attempt_id,view_id,series_id)) WITHOUT ROWID;
CREATE INDEX chart_paths ON chart_series(attempt_id,view_id,path_key,series_id,first_seq);
CREATE TABLE chart_samples(
    attempt_id TEXT NOT NULL, view_id TEXT NOT NULL, series_id TEXT NOT NULL,
    flow_seq INTEGER NOT NULL, x REAL, elapsed REAL, log_seq INTEGER,
    val_num REAL, val_text TEXT,
    PRIMARY KEY(attempt_id,view_id,series_id,flow_seq),
    FOREIGN KEY(attempt_id,view_id,series_id) REFERENCES chart_series(attempt_id,view_id,series_id),
    FOREIGN KEY(attempt_id,flow_seq) REFERENCES flow_records(attempt_id,seq),
    FOREIGN KEY(attempt_id,log_seq) REFERENCES records(attempt_id,seq)) WITHOUT ROWID;
CREATE INDEX chart_time ON chart_samples(
    attempt_id,view_id,series_id,x,flow_seq,log_seq,val_num,val_text) WHERE x IS NOT NULL;
CREATE INDEX chart_elapsed ON chart_samples(
    attempt_id,view_id,series_id,elapsed,flow_seq,log_seq,val_num,val_text) WHERE elapsed IS NOT NULL;
CREATE TABLE chart_overflow(
    attempt_id TEXT NOT NULL REFERENCES attempts(id), view_id TEXT NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY(attempt_id,view_id)) WITHOUT ROWID;
"""

_MAX_SIGNALS = 4096
_SEQ_CHUNK = 500  # stays below SQLite's historical 999 bound-variable limit
_MISSING = object()


def _path_key(parts):
    return "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def _save_chart_config(conn, snapshot):
    flow = snapshot.get("flow") or {}
    if "views" in flow:
        conn.execute("INSERT OR IGNORE INTO chart_config VALUES(?,?,?)",
                     (snapshot["attempt_id"], _json(flow["views"]),
                      _json([parser["name"] for parser in flow["parsers"]])))


def _chart_number(value):
    try:
        return _number(value)
    except ValueError:
        return None


def _axis_value(axis, data):
    value = data.get(axis["field"])
    if type(value) not in (int, float):
        return None
    try:
        return _chart_number(value - axis.get("origin", 0))
    except OverflowError:
        return None


def _evidence_seq(evidence):
    evidence = evidence or {}
    seq = evidence.get("line_no", evidence.get("line_start"))
    return seq if type(seq) is int and 0 < seq <= (1 << 53) - 1 else None


_BIN4_RE = re.compile(r"^(?:(\d+)'[bB]|0[bB])([01xXzZ_]+)$")
_HEX4_RE = re.compile(r"^(?:(\d+)'[hH]|0[xX])([0-9a-fA-FxXzZ_]+)$")
_BARE4_RE = re.compile(r"^[01xXzZ_]+$")
_HEX4_DIGITS = {
    "X": "XXXX",
    "Z": "ZZZZ",
    **{f"{n:X}": f"{n:04b}" for n in range(16)},
}


def _extend_4state(bits_text: str, width: int) -> str:
    if len(bits_text) == width:
        return bits_text
    fill = bits_text[0] if bits_text[0] in ("X", "Z") else "0"
    return fill * (width - len(bits_text)) + bits_text


def _normalize_4state(value: str, bits: int | None) -> str | None:
    text = value.strip()
    if not text:
        return None
    upper = text.upper()
    if upper in ("X", "Z"):
        return upper
    if bits is None:
        if upper in ("0", "1'B0", "0B0"):
            return "0"
        if upper in ("1", "1'B1", "0B1"):
            return "1"
        if upper in ("1'BX", "0BX", "1'HX", "0XX"):
            return "X"
        if upper in ("1'BZ", "0BZ", "1'HZ", "0XZ"):
            return "Z"
        return None
    if match := _BIN4_RE.fullmatch(text):
        declared, raw_digits = match.groups()
        raw = raw_digits.replace("_", "").upper()
        if not raw or ("X" not in raw and "Z" not in raw):
            return None
        target_w = int(declared) if declared is not None else bits
        if not 1 <= target_w <= bits or len(raw) > target_w:
            raise ValueError("Value outside the declared digital type")
        return "4s:" + _extend_4state(_extend_4state(raw, target_w), bits)
    if match := _HEX4_RE.fullmatch(text):
        declared, raw_digits = match.groups()
        raw = raw_digits.replace("_", "").upper()
        if not raw or ("X" not in raw and "Z" not in raw):
            return None
        target_w = int(declared) if declared is not None else bits
        if not 1 <= target_w <= bits:
            raise ValueError("Value outside the declared digital type")
        expanded = "".join(_HEX4_DIGITS[ch] for ch in raw)
        if len(expanded) > target_w:
            fill = raw[0] if raw[0] in ("X", "Z") else "0"
            if any(ch != fill for ch in expanded[:-target_w]):
                raise ValueError("Value outside the declared digital type")
            expanded = expanded[-target_w:]
        return "4s:" + _extend_4state(_extend_4state(expanded, target_w), bits)
    if _BARE4_RE.fullmatch(text):
        raw = upper.replace("_", "")
        if not raw or ("X" not in raw and "Z" not in raw) or len(raw) > bits:
            return None
        return "4s:" + _extend_4state(raw, bits)
    return None


def _typed_value(value, definition):
    kind = definition["type"]
    if value is None:
        return None, None
    if kind == "number":
        return _number(value), None
    if kind == "state":
        return None, _text(value, empty=True)
    if kind == "bit":
        if type(value) is int and value in (0, 1):
            return value, None
        if isinstance(value, str):
            norm = _normalize_4state(value, None)
            if norm in ("0", "1"):
                return int(norm), None
            if norm in ("X", "Z"):
                return None, norm
    else:
        bits = definition["bits"]
        signed = definition["signed"]
        if type(value) is int and -(1 << (bits - 1)) * signed <= value < (1 << (bits - signed)):
            return None, str(value)
        if isinstance(value, str):
            norm = _normalize_4state(value, bits)
            if norm is not None:
                return None, norm
    raise ValueError("Value outside the declared digital type")


def _long_sample(series, overrides, data):
    series_id = _text(data.get(series["id_field"]), 512)
    value = data.get(series["value_field"], _MISSING)
    path = data.get(series.get("path_field"))
    if path is None:
        path = [series_id]
    elif not isinstance(path, list) or not 1 <= len(path) <= 12:
        raise ValueError("Invalid signal path")
    else:
        path = [_text(part) for part in path]
    definition = overrides.get(series_id)
    if definition is None:
        definition = {key: series[key] for key in ("bits", "signed", "radix", "frac_bits") if key in series}
        definition.update(type=series["value_type"], id=series_id, label=path[-1], unit="")
    return definition, path, value


def _bounds(low, high, value):
    if value is None:
        return low, high
    return (value, value) if low is None else (min(low, value), max(high, value))


def _project_charts(conn, attempt_id, publications):
    config = conn.execute("SELECT spec FROM chart_config WHERE attempt_id=?", (attempt_id,)).fetchone()
    if config is None:
        return 0
    views = json.loads(config[0])["items"]
    if not views:
        return 0
    grouped = {}
    for view in views:
        source, series = view["source"], view["series"]
        key = source["stage"], source["producer"], source.get("report_name", "")
        grouped.setdefault(key, []).append((view, {item["id"]: item for item in series.get("overrides", [])}))
    catalog, limits, overflow, samples = {}, {}, {}, []
    sample_bytes = 0

    def route(pub):
        stage, kind, data = pub["stage"], pub["kind"], pub["data"]
        if not isinstance(data, dict):
            return None
        if stage == "parser" and kind == "record":
            report = ""
        elif stage == "analyzer" and kind == "report" and isinstance(data.get("values"), dict):
            report, data = data["name"], data["values"]
        else:
            return None
        selected = grouped.get((stage, pub["producer"], report))
        return (data, selected) if selected else None

    publications = list(publications)
    # Resolve every evidence line's elapsed time in a few chunked queries instead of one per record.
    wanted = {log_seq for pub in publications if route(pub) is not None
              for log_seq in (_evidence_seq(pub.get("evidence")),) if log_seq is not None}
    elapsed_by_seq = {}
    ordered = sorted(wanted)
    for start in range(0, len(ordered), _SEQ_CHUNK):
        chunk = ordered[start:start + _SEQ_CHUNK]
        elapsed_by_seq.update(conn.execute(
            "SELECT seq,elapsed FROM records WHERE attempt_id=? AND seq IN (%s)" % ",".join("?" * len(chunk)),
            (attempt_id, *chunk)))
    for pub in publications:
        routed = route(pub)
        if routed is None:
            continue
        data, selected = routed
        seq, log_seq = pub["seq"], _evidence_seq(pub.get("evidence"))
        evidence = elapsed_by_seq.get(log_seq) if log_seq is not None else None
        elapsed = _chart_number(evidence) if evidence is not None else None
        if evidence is None:
            log_seq = None
        for view, overrides in selected:
            view_id, series = view["id"], view["series"]
            x = _axis_value(view["axis"], data)
            if series["mode"] == "long":
                try:
                    items = [_long_sample(series, overrides, data)]
                except (ValueError, UnicodeError):
                    overflow[view_id] = overflow.get(view_id, 0) + 1
                    continue
            else:
                items = ((item, [item["id"]], data.get(item["field"], _MISSING)) for item in series["items"])
            for definition, path, value in items:
                if value is _MISSING and view["kind"] == "waveform":
                    continue
                series_id = definition["id"]
                key = attempt_id, view_id, series_id
                if key not in catalog:
                    row = conn.execute(
                        "SELECT path,first_seq,last_seq,x_lo,x_hi,elapsed_lo,elapsed_hi,invalid "
                        "FROM chart_series WHERE attempt_id=? AND view_id=? AND series_id=?", key,
                    ).fetchone()
                    if row is None:
                        if view_id not in limits:
                            limits[view_id] = conn.execute(
                                "SELECT count(*) FROM chart_series WHERE attempt_id=? AND view_id=?",
                                key[:2],
                            ).fetchone()[0]
                        if limits[view_id] >= _MAX_SIGNALS:
                            overflow[view_id] = overflow.get(view_id, 0) + 1
                            continue
                        limits[view_id] += 1
                        raw_path = _json(path)
                        row = raw_path, seq, seq, None, None, None, None, 0
                        conn.execute("INSERT INTO chart_series VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                     (*key, _path_key(path), raw_path, _json(definition), *row[1:]))
                    catalog[key] = list(row)
                state = catalog[key]
                invalid = x is None and view["axis"]["field"] is not None
                if series["mode"] == "long" and json.loads(state[0]) != path:
                    invalid = True
                try:
                    num, text = _typed_value(None if value is _MISSING else value, definition)
                except (ValueError, UnicodeError):
                    num = text = None
                    invalid = True
                state[2] = seq
                state[3:5] = _bounds(*state[3:5], x)
                state[5:7] = _bounds(*state[5:7], elapsed)
                state[7] += invalid
                samples.append((*key, seq, x, elapsed, log_seq, num, text))
                sample_bytes += 128 + len(series_id) * 4 + (len(text) * 4 if text else 0)
                if len(samples) >= 256:
                    conn.executemany("INSERT INTO chart_samples VALUES(?,?,?,?,?,?,?,?,?)", samples)
                    samples.clear()
    conn.executemany("INSERT INTO chart_samples VALUES(?,?,?,?,?,?,?,?,?)", samples)
    conn.executemany(
        "UPDATE chart_series SET last_seq=?,x_lo=?,x_hi=?,elapsed_lo=?,elapsed_hi=?,invalid=? "
        "WHERE attempt_id=? AND view_id=? AND series_id=?",
        ((*state[2:], *key) for key, state in catalog.items()),
    )
    conn.executemany(
        "INSERT INTO chart_overflow VALUES(?,?,?) ON CONFLICT(attempt_id,view_id) "
        "DO UPDATE SET count=count+excluded.count",
        ((attempt_id, view_id, count) for view_id, count in overflow.items()),
    )
    return sample_bytes

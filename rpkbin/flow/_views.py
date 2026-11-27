"""Declarative chart contracts shared by Flow producers and Archive readers."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

_KINDS = ("waveform", "xy_line", "scatter", "histogram")
_RADICES = ("hex", "bin", "unsigned", "signed", "fxp")
_TYPES = ("bit", "bus", "signed_bus", "fxp", "state", "number")
_SAFE_INT = (1 << 53) - 1


def _object(value, allowed, required="", *, name):
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    keys = set(value)
    if keys - set(allowed.split()) or set(required.split()) - keys:
        raise ValueError(f"Invalid {name} keys")
    return dict(value)


def _text(value, limit=128, *, empty=False):
    if not isinstance(value, str) or not (0 if empty else 1) <= len(value) <= limit:
        raise ValueError("Invalid chart text length or type")
    if len(value.encode("utf-8")) > limit or any(ord(c) < 32 or c in "<>" for c in value):
        raise ValueError("Invalid chart text")
    return value


def _number(value, *, origin=False):
    if type(value) is int and abs(value) <= ((1 << 256) - 1 if origin else _SAFE_INT):
        return value
    if type(value) is float and math.isfinite(value) and abs(value) <= 1e150:
        return value
    raise ValueError("Invalid chart numeric value")


def _choice(value, choices):
    if value not in choices:
        raise ValueError(f"Expected one of {choices}")
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Expected integer in [{low}, {high}]")
    return value


def _array(value, limit):
    if not isinstance(value, (tuple, list)) or len(value) > limit:
        raise ValueError(f"Expected a list of at most {limit} items")
    return value


def _unique(values):
    if len(set(values)) != len(values):
        raise ValueError("Duplicate chart IDs or axis modes")
    return values


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(map(_freeze, value))
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return list(map(_plain, value))
    return value


def _typed(item, default, radix):
    kind = _choice(item.get("type", default), _TYPES)
    result = {"type": kind}
    if kind in ("bus", "signed_bus", "fxp"):
        result["bits"] = _integer(item.get("bits"), 1, 256)
        result["radix"] = _choice(item.get("radix", radix), _RADICES)
        result["frac_bits"] = _integer(item.get("frac_bits", 0), 0, 256)
        signed = item.get("signed", kind in ("signed_bus", "fxp"))
        if type(signed) is not bool or (kind != "fxp" and signed != (kind == "signed_bus")):
            raise ValueError("Invalid bus signedness")
        if kind == "fxp" and "frac_bits" not in item:
            raise ValueError("fxp requires frac_bits")
        result["signed"] = signed
    elif {"bits", "radix", "frac_bits", "signed"} & item.keys():
        raise ValueError("Bit width and radix apply to buses")
    return result


def _axis(value):
    axis = _object(value, "field label unit fallback origin", name="axis")
    field = axis.get("field")
    axis.update(field=_text(field) if field is not None else None,
                label=_text(axis.get("label", field or "Publication seq")),
                unit=_text(axis.get("unit", ""), empty=True),
                fallback=_unique([_choice(v, ("elapsed", "seq"))
                                  for v in _array(axis.get("fallback", ["elapsed", "seq"]), 2)]))
    if "origin" in axis:
        if field is None:
            raise ValueError("origin requires an axis field")
        axis["origin"] = _number(axis["origin"], origin=True)
    return axis


def _series(value, kind, radix):
    keys = "label unit type bits frac_bits signed radix"
    if not isinstance(value, Mapping):
        raise TypeError("series must be a mapping")
    mode = _choice(value.get("mode"), ("long", "wide"))
    if mode == "wide":
        spec = _object(value, "mode items defaults", "items", name="wide series")
        items = []
        for v in _array(spec["items"], 16):
            item = _object(v, f"id field {keys}", "id field", name="wide item")
            id_ = _text(item["id"], 512)
            items.append(dict(id=id_, field=_text(item["field"]), label=_text(item.get("label", id_), 512),
                              unit=_text(item.get("unit", ""), empty=True), **_typed(item, "number", radix)))
        ids = _unique([item["id"] for item in items])
        if not ids:
            raise ValueError("wide series requires at least one item")
        spec["items"] = items
    else:
        spec = _object(value, "mode id_field path_field value_field value_type bits frac_bits signed radix overrides defaults",
                       "id_field value_field", name="long series")
        default = "state" if kind == "waveform" else "number"
        typed = _typed(dict(spec, type=spec.pop("value_type", default)), default, radix)
        spec.update({k: _text(spec[k]) for k in ("id_field", "value_field")})
        if spec.get("path_field") is not None:
            spec["path_field"] = _text(spec["path_field"])
        spec.update(typed)
        spec["value_type"] = spec.pop("type")
        overrides = []
        for v in _array(spec.get("overrides", []), 128):
            item = _object(v, f"id {keys}", "id", name="series override")
            id_ = _text(item["id"], 512)
            overrides.append(dict(id=id_, label=_text(item.get("label", id_), 512),
                                  unit=_text(item.get("unit", ""), empty=True), **_typed(item, default, radix)))
        _unique([item["id"] for item in overrides])
        spec["overrides"] = overrides
        items = [dict(type=spec["value_type"]), *overrides]
        ids = []
    defaults = _unique([_text(v, 512) for v in _array(spec.get("defaults", ids[:4]), 16)])
    if mode == "wide" and set(defaults) - set(ids):
        raise ValueError("Unknown default series")
    if kind != "waveform" and any(item["type"] != "number" for item in items):
        raise ValueError("Scientific chart series must be numeric")
    spec["defaults"] = defaults
    return spec


def _options(value, kind):
    allowed = {"waveform": "radix", "xy_line": "thresholds",
               "scatter": "equal_aspect", "histogram": "bins"}
    options = _object(value, allowed[kind], name="options")
    if kind == "waveform":
        options["radix"] = _choice(options.get("radix", "hex"), _RADICES)
    elif kind == "xy_line":
        thresholds = []
        for value in _array(options.get("thresholds", []), 32):
            item = _object(value, "series_id side value label", "series_id side value", name="threshold")
            thresholds.append(dict(series_id=_text(item["series_id"], 512),
                                   side=_choice(item["side"], ("min", "max")),
                                   value=_number(item["value"]),
                                   label=_text(item.get("label", ""), empty=True)))
        _unique([(item["series_id"], item["side"]) for item in thresholds])
        options["thresholds"] = thresholds
    elif kind == "scatter":
        aspect = options.setdefault("equal_aspect", False)
        if type(aspect) is not bool:
            raise ValueError("equal_aspect must be boolean")
    else:
        bins = options.get("bins", 32)
        if isinstance(bins, (list, tuple)):
            bins = [_number(v) for v in _array(bins, 129)]
            if len(bins) < 2 or any(a >= b for a, b in zip(bins, bins[1:])):
                raise ValueError("Bin edges must strictly increase")
        else:
            bins = _integer(bins, 1, 128)
        options["bins"] = bins
    return options


@dataclass(frozen=True, slots=True, init=False)
class ViewSpec:
    """One immutable chart definition. Source is a Parser, Analyzer, or JSON mapping."""

    id: str
    kind: str
    title: str
    source: Mapping
    axis: Mapping
    series: Mapping
    options: Mapping
    _definition: object

    def __init__(self, id, kind, *, source, axis, series, title=None, options=None, report_name=None):
        from ._session import Analyzer, Parser

        definition = None
        if isinstance(source, (Analyzer, Parser)):
            definition = source
            source = dict(stage="parser" if isinstance(source, Parser) else "analyzer", producer=source.name)
            if report_name is not None:
                source["report_name"] = report_name
        elif report_name is not None:
            raise ValueError("Specify report_name inside a source mapping")
        source = _object(source, "stage producer report_name", "stage producer", name="source")
        stage = _choice(source["stage"], ("parser", "analyzer"))
        source["producer"] = _text(source["producer"])
        if stage == "analyzer":
            source["report_name"] = _text(source.get("report_name"))
        elif "report_name" in source:
            raise ValueError("report_name requires an Analyzer")
        kind = _choice(kind, _KINDS)
        id = _text(id)
        if id == "__auto__":
            raise ValueError("__auto__ is reserved")
        options = _options({} if options is None else options, kind)
        axis = _axis(axis)
        series = _series(series, kind, options.get("radix", "hex"))
        if kind == "scatter" and axis["field"] is None:
            raise ValueError("Scatter requires an x field")
        if kind == "xy_line" and series["mode"] == "wide":
            ids = {item["id"] for item in series["items"]}
            if any(item["series_id"] not in ids for item in options["thresholds"]):
                raise ValueError("Unknown threshold series")
        for key, value in dict(id=id, kind=kind, title=_text(id if title is None else title),
                               source=source, axis=axis, series=series, options=options,
                               _definition=definition).items():
            object.__setattr__(self, key, _freeze(value))

    def to_json(self):
        """Return a detached JSON-compatible chart definition."""
        return {name: _plain(getattr(self, name)) for name in
                ("id", "kind", "title", "source", "axis", "series", "options")}

    @classmethod
    def waveform(cls, id, **kwargs):
        return cls(id, "waveform", **kwargs)

    @classmethod
    def xy_line(cls, id, **kwargs):
        return cls(id, "xy_line", **kwargs)

    @classmethod
    def scatter(cls, id, **kwargs):
        return cls(id, "scatter", **kwargs)

    @classmethod
    def histogram(cls, id, **kwargs):
        return cls(id, "histogram", **kwargs)


def _view_payload(views):
    _array(views, 8)
    if any(not isinstance(view, ViewSpec) for view in views):
        raise TypeError("views must contain ViewSpec definitions")
    _unique([view.id for view in views])
    width = sum(len(view.series["items"]) if view.series["mode"] == "wide" else 1 for view in views)
    if width > 32:
        raise ValueError("Chart projection exceeds 32 samples per publication")
    payload = dict(schema_version=1, items=[view.to_json() for view in views])
    canonical = json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True,
                           separators=(",", ":")).encode("ascii")
    if len(canonical) > 32 * 1024 - 80:
        raise ValueError("views payload exceeds 32 KiB")
    return dict(payload, digest=hashlib.sha256(canonical).hexdigest())


def _read_view_payload(payload):
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Unsupported chart spec version")
    items = _array(payload.get("items"), 8)
    checked = _view_payload([ViewSpec(**item) for item in items])
    if checked != payload:
        raise ValueError("Chart spec does not match its canonical digest")
    return checked

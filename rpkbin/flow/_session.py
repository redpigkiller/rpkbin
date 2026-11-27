"""Inspectable parsing, analysis and reactions, independent of process execution.

Each input completes parsing and analysis before its live reactions are queued.
Only bounded diagnostic/event tails and latest values live here; ``publish``
receives every accepted output, including repeated values, for durable storage.
User-owned ``ctx.state`` is private working memory, not a persisted object store.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
import inspect
import json
import math
import threading
from types import MappingProxyType
from typing import Callable, Generic, TypeVar

from ._automation import AutomationRunner

class Result(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    INCOMPLETE = "INCOMPLETE"
    NA = "N/A"


TAIL_LIMIT = 100
MAX_OUTPUTS_PER_CALL = 10000
MAX_OUTPUTS_PER_CYCLE = 10000
MAX_CODE_CHARS = 16384
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_LEVELS = frozenset(("info", "warning", "error"))
_RESULTS = frozenset(item.value for item in Result)


def _plain(value):
    """Detach JSON data without losing Python integer precision or key identity."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Flow values must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Flow object keys must be strings")
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    raise TypeError(f"Flow values must be JSON-compatible, got {type(value).__name__}")


def _readonly(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _readonly(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_readonly(item) for item in value)
    return value


def _json_size(value, limit):
    size = 0
    for part in json.JSONEncoder(ensure_ascii=True).iterencode(value):
        size += len(part)
        if size > limit:
            raise ValueError(f"Flow output exceeds {limit} bytes")
    return size


def _name(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("Flow names must be nonempty text without surrounding space")
    return value


_ZERO_ARG_BUILTIN_TYPES = frozenset((
    dict, list, set, frozenset, tuple, int, float, bool, complex,
    str, bytes, bytearray, object, deque, defaultdict,
))


def _callback(value, name, *, optional=False, arity=2):
    if value is None and optional:
        return
    if not callable(value):
        raise TypeError(f"{name} must be callable")
    call_target = getattr(value, "__call__", None) if not isinstance(value, type) else None
    if (
        inspect.iscoroutinefunction(value)
        or inspect.isasyncgenfunction(value)
        or (
            call_target is not None
            and (inspect.iscoroutinefunction(call_target) or inspect.isasyncgenfunction(call_target))
        )
    ):
        raise TypeError(f"{name} must be synchronous")
    try:
        signature = inspect.signature(value)
    except (ValueError, TypeError):
        if arity == 0 and value in _ZERO_ARG_BUILTIN_TYPES:
            return
        raise TypeError(f"{name} must have an inspectable signature") from None
    try:
        signature.bind(*([None] * arity))
    except TypeError as exc:
        raise TypeError(f"{name} must accept {arity} positional arguments: {exc}") from None


def _accepts_keyword(callback, name):
    try:
        sig = inspect.signature(callback)
    except (ValueError, TypeError):
        return False
    param = sig.parameters.get(name)
    if param is not None and param.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    ):
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def _producer_name(value):
    _name(value)
    if "." in value:
        raise ValueError("producer names cannot contain '.' (reserved for field namespaces)")


@dataclass(frozen=True)
class Binding:
    """An explicit source definition and its ordinary two-argument handler."""

    source: object
    handler: Callable

    def __post_init__(self):
        if not isinstance(self.source, (Parser, Analyzer)):
            raise TypeError("Binding source must be a Parser or Analyzer definition")
        _callback(self.handler, "Binding handler")


def _bindings(values, expected, name):
    if not isinstance(values, (tuple, list)) or any(not isinstance(item, Binding) for item in values):
        raise TypeError(f"{name} must be a list or tuple of Binding objects")
    sources = set()
    for item in values:
        if not isinstance(item.source, expected):
            raise TypeError(f"{name} requires {expected.__name__} sources")
        if id(item.source) in sources:
            raise ValueError(f"duplicate {name} source: {item.source.name}")
        sources.add(id(item.source))
    return tuple(values)


def _description(value):
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise TypeError("outputs must be a mapping of field descriptions")
    return _readonly(_plain(value))


def _code(callback):
    try:
        return inspect.getsource(callback)[:MAX_CODE_CHARS]
    except (OSError, TypeError):
        return ""


@dataclass(frozen=True)
class AnalysisReport:
    """One explicitly named analysis output, independent of the session verdict.

    ``result`` is display text, never an instruction or an inferred outcome.
    Values retain their JSON types; arbitrary Python objects stay in ctx.state.
    """

    name: str
    result: str | None = field(default=None, kw_only=True)
    reason: str = field(default="", kw_only=True)
    values: Mapping | None = field(default=None, kw_only=True)
    evidence: Mapping | None = field(default=None, kw_only=True)

    def __post_init__(self):
        _name(self.name)
        if len(self.name) > 256:
            raise ValueError("report name must contain at most 256 characters")
        if self.result is not None and not isinstance(self.result, str):
            raise TypeError("report result must be text or None")
        if not isinstance(self.reason, str):
            raise TypeError("report reason must be text")
        if self.values is not None and not isinstance(self.values, Mapping):
            raise TypeError("report values must be a mapping")
        if self.evidence is not None and not isinstance(self.evidence, Mapping):
            raise TypeError("report evidence must be a mapping")
        object.__setattr__(self, "values", _readonly(_plain({} if self.values is None else self.values)))
        if self.evidence is not None:
            object.__setattr__(self, "evidence", _readonly(_plain(self.evidence)))


@dataclass(frozen=True, eq=False)
class Parser:
    name: str
    parse: Callable
    version: str = field(default="1", kw_only=True)
    outputs: Mapping | None = field(default=None, kw_only=True)
    on_finish: Callable | None = field(default=None, kw_only=True)
    state_factory: Callable = field(default=dict, kw_only=True)

    def __post_init__(self):
        _producer_name(self.name)
        _name(self.version)
        _callback(self.parse, "parse")
        _callback(self.on_finish, "on_finish", optional=True)
        _callback(self.state_factory, "state_factory", arity=0)
        object.__setattr__(self, "outputs", _description(self.outputs))


@dataclass(frozen=True, eq=False)
class Analyzer:
    name: str
    records: tuple = field(default=(), kw_only=True)
    reports: tuple = field(default=(), kw_only=True)
    version: str = field(default="1", kw_only=True)
    outputs: Mapping | None = field(default=None, kw_only=True)
    on_finish: Callable | None = field(default=None, kw_only=True)
    on_tick: Callable | None = field(default=None, kw_only=True)
    every: float | None = field(default=None, kw_only=True)
    state_factory: Callable = field(default=dict, kw_only=True)

    def __post_init__(self):
        _producer_name(self.name)
        _name(self.version)
        _callback(self.on_finish, "on_finish", optional=True)
        _callback(self.on_tick, "on_tick", optional=True)
        _callback(self.state_factory, "state_factory", arity=0)
        object.__setattr__(self, "records", _bindings(self.records, Parser, "records"))
        object.__setattr__(self, "reports", _bindings(self.reports, Analyzer, "reports"))
        object.__setattr__(self, "outputs", _description(self.outputs))
        if self.every is not None and (
            isinstance(self.every, bool)
            or not isinstance(self.every, (int, float))
            or not math.isfinite(self.every)
            or self.every <= 0
        ):
            raise ValueError("every must be a finite positive number of seconds")
        if (self.every is None) != (self.on_tick is None):
            raise ValueError("every and on_tick must be specified together")
        if not (self.records or self.reports or self.on_finish or self.on_tick):
            raise ValueError("Analyzer requires a binding, on_tick or on_finish")


@dataclass(frozen=True, eq=False)
class Automation:
    name: str
    reports: tuple = field(default=(), kw_only=True)
    on_event: Callable | None = field(default=None, kw_only=True)
    enabled: bool = field(default=True, kw_only=True)
    state_factory: Callable = field(default=dict, kw_only=True)

    def __post_init__(self):
        _producer_name(self.name)
        object.__setattr__(self, "reports", _bindings(self.reports, Analyzer, "reports"))
        _callback(self.on_event, "on_event", optional=True)
        _callback(self.state_factory, "state_factory", arity=0)
        if not self.reports and self.on_event is None:
            raise ValueError("Automation requires reports or on_event")
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be bool")


@dataclass(frozen=True)
class FlowEvent:
    name: str
    data: Mapping

    def __post_init__(self):
        if self.name not in ("started", "finished", "status_changed", "error"):
            raise ValueError("FlowEvent name must be started, finished, status_changed or error")
        object.__setattr__(self, "data", _readonly(_plain(self.data)))


@dataclass(frozen=True)
class ActionOutcome:
    status: str
    reason: str

    def __post_init__(self):
        if self.status not in ("accepted", "rejected", "failed"):
            raise ValueError("Invalid action outcome status")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("Action outcome reason must be nonempty text")

    @property
    def accepted(self):
        return self.status == "accepted"

    def __bool__(self):
        return self.accepted


@dataclass(frozen=True)
class EndInfo:
    """Source completion, not a process success verdict."""

    complete: bool
    execution: Mapping

    def __post_init__(self):
        object.__setattr__(self, "execution", _readonly(_plain(self.execution)))


@dataclass(frozen=True)
class Tick:
    """Explicit live execution clock input; never synthesized from Log data."""

    elapsed: float


@dataclass(frozen=True)
class Flow:
    name: str
    parsers: tuple = field(default=(), kw_only=True)
    analyzers: tuple = field(default=(), kw_only=True)
    automations: tuple = field(default=(), kw_only=True)
    version: str = field(default="1", kw_only=True)
    result: Analyzer | Callable | None = field(default=None, kw_only=True)
    views: tuple = field(default=(), kw_only=True)
    _views_json: Mapping = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        _name(self.name)
        _name(self.version)
        for key, expected in (("parsers", Parser), ("analyzers", Analyzer), ("automations", Automation)):
            values = getattr(self, key)
            if not isinstance(values, (tuple, list)) or any(not isinstance(item, expected) for item in values):
                raise TypeError(f"{key} must contain {expected.__name__} definitions")
            object.__setattr__(self, key, tuple(values))
        names = [item.name for item in (*self.parsers, *self.analyzers, *self.automations)]
        if len(names) != len(set(names)):
            raise ValueError("Flow producer names must be unique")
        definitions = {id(item) for item in (*self.parsers, *self.analyzers)}
        from ._views import _freeze, _view_payload

        payload = _view_payload(self.views)
        for view in self.views:
            steps = self.parsers if view.source["stage"] == "parser" else self.analyzers
            if not any(step.name == view.source["producer"]
                       and (view._definition is None or view._definition is step) for step in steps):
                raise ValueError(f"{view.id}: chart source is not in this Flow")
        object.__setattr__(self, "views", tuple(self.views))
        object.__setattr__(self, "_views_json", _freeze(payload))
        for spec in (*self.analyzers, *self.automations):
            for binding in (*getattr(spec, "records", ()), *spec.reports):
                if id(binding.source) not in definitions:
                    raise ValueError(f"{spec.name}: Binding source {binding.source.name} is not in this Flow")
        # Definitions are reusable values; only the compiled dependency order is
        # normalized. No state lives on a definition or on a Binding.
        available = {id(item) for item in self.parsers}
        pending, ordered = list(self.analyzers), []
        while pending:
            ready = [item for item in pending if all(id(link.source) in available for link in (*item.records, *item.reports))]
            if not ready:
                raise ValueError("Flow contains a cyclic Analyzer dependency")
            for item in ready:
                ordered.append(item)
                available.add(id(item))
                pending.remove(item)
        object.__setattr__(self, "analyzers", tuple(ordered))
        if isinstance(self.result, Analyzer):
            if self.result not in self.analyzers:
                raise ValueError("result Analyzer must be in this Flow")
        elif self.result is not None and not callable(self.result):
            raise TypeError("result must be an Analyzer definition or aggregation callable")
        elif callable(self.result):
            _callback(self.result, "result aggregation", arity=1)


@dataclass
class _State:
    spec: object
    stage: str
    state: object = field(default_factory=dict)
    latest: dict = field(default_factory=dict)
    count: int = 0
    publication_count: int = 0
    report_count: int = 0
    processed: int = 0
    evidence: dict | None = None
    error: str | None = None
    result: str | None = None
    reason: str = ""
    final: bool = False
    issues: dict = field(default_factory=dict)
    event_count: int = 0
    events: deque = field(default_factory=lambda: deque(maxlen=TAIL_LIMIT))
    last: dict | None = None
    action_count: int = 0
    last_tick: float = 0.0
    record_evidence: dict | None = None


class _Actions:
    _METHODS = frozenset(("stop", "send", "interrupt", "send_ctrl_c", "send_signal", "kill"))

    def __init__(self, context):
        self._context = context

    def __getattr__(self, action):
        if action not in self._METHODS:
            raise AttributeError(action)

        def call(*args, **kwargs):
            ctx, session = self._context, self._context._session
            ctx._active()
            if any(key in kwargs for key in ("expected_attempt", "expected_submission")):
                raise TypeError("Automation controls are bound to their original Run")
            controls = session._control
            actual = action
            if isinstance(controls, Mapping):
                callback = controls.get(actual)
                if callback is None and action == "interrupt":
                    callback = controls.get("send_ctrl_c")
            else:
                callback = getattr(controls, actual, None)
                if callback is None and action == "interrupt":
                    callback = getattr(controls, "send_ctrl_c", None)
                if (
                    action in ("stop", "kill", "interrupt", "send_ctrl_c", "send_signal")
                    and callable(callback)
                    and _accepts_keyword(callback, "source")
                ):
                    kwargs.setdefault("source", f"automation:{ctx._step.spec.name}")
            secret = action == "send" and kwargs.get("secret") is True
            audit_args, audit_kwargs = list(args), dict(kwargs)
            if secret:
                if audit_args:
                    audit_args[0] = "***"
                if "text" in audit_kwargs:
                    audit_kwargs["text"] = "***"
            request = dict(version=1, action=action, args=_plain(audit_args), kwargs=_plain(audit_kwargs), source=_plain(ctx._source))
            # Operations are immediate, unlike buffered analysis publications.
            # Reject oversized audit arguments before persisting or executing.
            _json_size((request, _plain(ctx._evidence)), MAX_OUTPUT_BYTES)
            requested = session._emit(ctx._step, "action", {**request, "phase": "requested", "status": "requested"}, ctx._evidence)
            request["request_id"] = requested["seq"]
            if session.publication_error:
                with session._publication_lock:
                    session._outstanding_actions.pop(request["request_id"], None)
                raise RuntimeError("Automation operation rejected because audit persistence failed")
            try:
                if not callable(callback):
                    raise RuntimeError(f"control {action} is unavailable")
                receipt = callback(*args, **kwargs)
                if not isinstance(receipt, ActionOutcome):
                    raise TypeError("Action handlers must return ActionOutcome")
                if secret:
                    text = args[0] if args else kwargs.get("text")
                    if isinstance(text, str) and text:
                        receipt = ActionOutcome(receipt.status, receipt.reason.replace(text, "***"))
                _json_size(dict(status=receipt.status, reason=receipt.reason), MAX_OUTPUT_BYTES)
            except Exception as exc:
                reason = "Secret input write failed" if secret else f"{type(exc).__name__}: {exc}"[:2048]
                receipt = ActionOutcome("failed", reason)
            session._emit(ctx._step, "action", {**request, "phase": "failed" if receipt.status == "failed" else "returned", "status": receipt.status, "reason": receipt.reason, "receipt": dict(status=receipt.status, reason=receipt.reason)}, ctx._evidence)
            return receipt

        return call


_WorkingState = TypeVar("_WorkingState")


class _Context(Generic[_WorkingState]):
    def __init__(self, session, step, kind, evidence, input_publication=None, *, execution_provider=None):
        self._session, self._step = session, step
        self._kind, self._evidence = kind, _readonly(_plain(evidence))
        metadata = {key: input_publication[key] for key in ("seq", "stage", "producer", "kind", "name") if key in input_publication} if input_publication else dict(
            stage="log" if kind == "line" else "flow", producer="log" if kind == "line" else step.spec.name, kind=kind)
        provider = session._execution_facts if execution_provider is None else execution_provider
        self._source_info = _readonly(_plain({**metadata, "evidence": evidence, "execution": provider()}))
        self._open = True
        self._pending = []
        self._pending_bytes = 0

    @property
    def state(self) -> _WorkingState:
        self._active()
        return self._step.state

    @property
    def source(self):
        return self._source_info

    def _active(self):
        if not self._open:
            raise RuntimeError("Flow context is valid only during its callback")
        if self._step.stage == "automation" and (self._session._automation.closed or self._step.error):
            raise RuntimeError("Automation is disabled for this Run")
        if self._session._sealed:
            raise RuntimeError("Flow input was sealed")

    def _stage(self, kind, data, evidence):
        self._active()
        if len(self._pending) >= MAX_OUTPUTS_PER_CALL:
            raise ValueError(f"callback exceeds {MAX_OUTPUTS_PER_CALL} outputs")
        data, evidence = _plain(data), _plain(evidence)
        # Reject an oversized batch without publishing a prefix or building one
        # giant JSON string. The buffer owns detached, JSON-compatible snapshots.
        self._pending_bytes += _json_size((data, evidence), MAX_OUTPUT_BYTES - self._pending_bytes)
        self._pending.append((kind, data, evidence))

    def debug(self, message, data=None):
        self._active()
        if not isinstance(message, str):
            raise TypeError("debug message must be text")
        payload = dict(message=message, data=_plain(data))
        if isinstance(self, AutomationContext):
            _json_size((payload, _plain(self._evidence)), MAX_OUTPUT_BYTES)
            self._session._emit(self._step, "diagnostic", payload, self._evidence)
        else:
            self._stage("diagnostic", payload, self._evidence)

    def __repr__(self):
        return f"{type(self).__name__}(producer={self._step.spec.name!r}, kind={self._kind!r}, open={self._open})"


class ParserContext(_Context[_WorkingState]):
    """Private working state and immutable Log provenance; no actions."""


class AnalyzerContext(_Context[_WorkingState]):
    """Private working state and an explicit analysis summary output namespace."""

    @property
    def summary(self):
        self._active()
        return _Summary(self)


class AutomationContext(_Context[_WorkingState]):
    """Live, callback-scoped controls bound to this session."""

    def __init__(self, session, step, kind, evidence, input_publication=None, *, execution_provider=None):
        super().__init__(session, step, kind, evidence, input_publication, execution_provider=execution_provider)
        self._source = _readonly(_plain(input_publication)) if input_publication else None

    @property
    def actions(self):
        self._active()
        if not self._session.automation_enabled:
            raise RuntimeError("actions are available only to live Automation callbacks")
        return _Actions(self)


class _Summary:
    """Analysis summary publications, distinct from arbitrary AnalysisReport data."""

    def __init__(self, context):
        self._ctx = context

    def issue(self, name, level="info", message="", active=True, evidence=None):
        _name(name)
        if level not in _LEVELS:
            raise ValueError("level must be info, warning or error")
        if type(active) is not bool or not isinstance(message, str):
            raise TypeError("issue requires bool active and text message")
        self._ctx._stage("issue", dict(name=name, level=level, message=message, active=active), self._ctx._evidence if evidence is None else evidence)

    def event(self, name, level="info", message="", data=None, evidence=None):
        _name(name)
        if level not in _LEVELS or not isinstance(message, str):
            raise ValueError("event requires a valid level and text message")
        self._ctx._stage("event", dict(name=name, level=level, message=message, data=_plain(data)), self._ctx._evidence if evidence is None else evidence)

    def result(self, value, reason="", final=False, evidence=None):
        value = value.value if isinstance(value, Result) else value
        if value not in _RESULTS:
            raise ValueError("use a Result value")
        if not isinstance(reason, str) or type(final) is not bool:
            raise TypeError("result requires text reason and bool final")
        if final and value in ("PASS", "N/A") and (self._ctx._kind != "finish" or not self._ctx._session.complete):
            raise ValueError("final PASS/N/A requires a complete source finish")
        self._ctx._stage("result", dict(value=value, reason=reason, final=final), self._ctx._evidence if evidence is None else evidence)


class FlowSession:
    """One isolated execution/replay of a reusable Flow definition.

    A synchronous ``publish`` callback owns history persistence. Its failure is
    exposed as ``publication_error`` independently of the analysis verdict.
    Returned snapshots never execute callbacks or controls.
    """

    def __init__(
        self, flow, *, publish=None, execution_provider=None, clock_enabled=False,
        automation_enabled=False, control=None, on_automation_idle=None,
        _write_scope=nullcontext, _automation_scope=nullcontext, _callback_scope=nullcontext,
    ):
        if not isinstance(flow, Flow):
            raise TypeError("flow must be a Flow")
        _callback(publish, "publish", optional=True, arity=1)
        _callback(on_automation_idle, "on_automation_idle", optional=True, arity=0)
        _callback(execution_provider, "execution_provider", optional=True, arity=0)
        if type(clock_enabled) is not bool or type(automation_enabled) is not bool:
            raise TypeError("clock_enabled and automation_enabled must be bool")
        self.flow = flow
        self.clock_enabled = clock_enabled
        self.automation_enabled = automation_enabled
        self._execution_provider = execution_provider
        self._last_elapsed = None
        self._explicit_tick = False
        self._final_execution = {}
        self._publish, self._control = publish, control
        self._on_automation_idle = on_automation_idle
        self._write_scope = _write_scope
        self._automation_scope = _automation_scope
        self._callback_scope = _callback_scope
        self._outstanding_actions = {}
        self._publication_lock = threading.RLock()
        self._publication_ready = threading.Condition(self._publication_lock)
        self._input_pending = False
        self._sealed = False
        self._steps = {}
        for stage, definitions in (("parser", flow.parsers), ("analyzer", flow.analyzers), ("automation", flow.automations)):
            for spec in definitions:
                self._steps[spec.name] = _State(spec, stage)
        self._codes = {}
        self._routes = {}
        for name, step in self._steps.items():
            spec = step.spec
            callbacks = [spec.parse] if step.stage == "parser" else [link.handler for link in (*getattr(spec, "records", ()), *spec.reports)]
            callbacks.extend(cb for cb in (getattr(spec, "on_finish", None), getattr(spec, "on_tick", None), getattr(spec, "on_event", None)) if cb is not None)
            if spec.state_factory is not dict:
                callbacks.append(spec.state_factory)
            self._codes[name] = "\n\n".join(_code(cb) for cb in callbacks)[:MAX_CODE_CHARS]
            if step.stage == "analyzer":
                for kind, links in (("record", spec.records), ("report", spec.reports)):
                    for link in links:
                        self._routes.setdefault((link.source.name, kind), []).append((step, link.handler))
        self._result_code = _code(flow.result) if callable(flow.result) else ""
        self._records, self._reactions = deque(), deque()
        self._diagnostics = deque(maxlen=TAIL_LIMIT)
        self._seq = self._lines = 0
        self._busy = False
        self.complete = self.finished = False
        self.error_count = 0
        self.automation_error_count = 0
        self.publication_error = None
        self.aggregation_error = None
        self._result = None
        self._cycle_records = 0
        self._automation = AutomationRunner(self)
        self._started = False
        for step in self._steps.values():
            if step.stage == "automation" and (not self.automation_enabled or not step.spec.enabled):
                continue
            try:
                step.state = step.spec.state_factory()
                if inspect.isawaitable(step.state):
                    if inspect.iscoroutine(step.state):
                        step.state.close()
                    raise TypeError("state_factory must return synchronous state")
            except Exception as exc:
                if step.stage == "automation":
                    self._automation_fail(step, exc, None)
                else:
                    self._fail(step, exc, None)

    def _execution_facts(self, *, use_explicit_tick=True):
        facts = self._execution_provider() if self._execution_provider is not None else {}
        if not isinstance(facts, Mapping):
            raise TypeError("execution_provider must return a mapping")
        facts = dict(facts)
        if use_explicit_tick and self._explicit_tick:
            facts["elapsed"] = self._last_elapsed
        elif self._last_elapsed is not None:
            facts.setdefault("elapsed", self._last_elapsed)
        facts.update(self._final_execution)
        return facts

    def _inputs(self, spec):
        return tuple(link.source.name for link in (*getattr(spec, "records", ()), *spec.reports))

    def _end_input(self):
        # Called under the publication lock after the collector commits its batch.
        self._input_pending = False
        self._publication_ready.notify_all()

    def _seal(self, *, incomplete=False):
        # The collector owns its writer scope. Resolve audited actions before
        # closing publication, even if a callback has not handed back a receipt.
        with self._publication_lock:
            if self._sealed:
                return
            reason = "Control receipt lost at attempt close; operation outcome is unknown"
            for step, request, evidence in tuple(self._outstanding_actions.values()):
                self._emit(step, "action", dict(
                    request, phase="failed", status="failed", reason=reason,
                    receipt=dict(status="failed", reason=reason),
                ), evidence)
            if incomplete:
                self.complete = False
                self.finished = True
                if self._result not in ("ERROR", "FAIL"):
                    self._result = "INCOMPLETE"
            self._sealed = True
            self._end_input()
            self._automation.close()

    def _wait_input(self):
        self._publication_ready.wait_for(lambda: not self._input_pending or self._sealed)

    def _emit(self, step, kind, data, evidence):
        requested = kind == "action" and data.get("phase") == "requested"
        while True:
            if requested:
                # Never wait for a staged input while holding the source event lock.
                with self._publication_lock:
                    self._wait_input()
            with self._write_scope(), self._publication_lock:
                if self._sealed:
                    if requested:
                        raise RuntimeError("Flow input was sealed")
                    return None
                if requested and self._input_pending:
                    continue
                publication = self._prepare_publication(step, kind, data, evidence)
                # Sequence assignment and persistence share the writer order.
                if self._publish:
                    try:
                        self._publish(deepcopy(publication))
                    except Exception as exc:
                        if self.publication_error is None:
                            self.publication_error = f"{type(exc).__name__}: {exc}"
                return publication if requested else None

    def _prepare_publication(self, step, kind, data, evidence):
        data, evidence = _plain(data), _plain(evidence)
        if kind in ("record", "report"):
            if self._cycle_records >= MAX_OUTPUTS_PER_CYCLE:
                raise ValueError(f"input cycle exceeds {MAX_OUTPUTS_PER_CYCLE} output records")
            self._cycle_records += 1
        self._seq += 1
        if kind == "action":
            if data.get("phase") == "requested":
                data["request_id"] = self._seq
                step.action_count += 1
                self._outstanding_actions[self._seq] = (step, data, evidence)
            else:
                self._outstanding_actions.pop(data["request_id"], None)
        step.publication_count += 1
        publication = dict(seq=self._seq, stage=step.stage, producer=step.spec.name, kind=kind, data=data, evidence=evidence)
        if kind == "record":
            step.count += 1
            step.latest.update(deepcopy(data))
            step.evidence = deepcopy(evidence)
            step.record_evidence = deepcopy(evidence)
            self._records.append(publication)
        elif kind == "report":
            # Histories and per-name projections belong to the disk archive.
            # Unique names must not grow an unbounded in-memory report map.
            step.report_count += 1
            self._records.append(publication)
        elif kind == "issue":
            if data["active"]:
                step.issues[data["name"]] = {**deepcopy(data), "evidence": deepcopy(evidence)}
            else:
                step.issues.pop(data["name"], None)
        elif kind == "event":
            step.event_count += 1
            step.events.append(deepcopy(publication))
        elif kind == "result":
            severity = {"ERROR": 2, "FAIL": 1}
            incoming = severity.get(data["value"], 0)
            current = severity.get(step.result, 0)
            if incoming > current or (not step.final and incoming == current):
                step.result = data["value"]
                step.reason = data["reason"]
                step.final = step.final or data["final"]
                step.evidence = deepcopy(evidence)
            elif data["final"]:
                step.final = True
            data["effective_value"] = step.result
        elif kind == "diagnostic":
            self._diagnostics.append(deepcopy(publication))
        elif kind == "action":
            step.last = deepcopy(publication)
        if step.stage == "analyzer" and kind == "report" and self.automation_enabled:
            self._reactions.append(publication)
        return publication

    def _automation_fail(self, step, exc, evidence):
        message = f"{type(exc).__name__}: {exc}"
        with self._write_scope(), self._publication_lock:
            if self._sealed or step.error:
                return
            step.error = message
            self.automation_error_count += 1
            self._emit(step, "diagnostic", dict(message=step.error, error=True), evidence)

    def _run_automation(self, step, kind, data, evidence, source, execution=None):
        with self._publication_lock:
            self._wait_input()
            if self._sealed or step.error:
                return
        with self._callback_scope():
            ctx = None
            try:
                ctx = AutomationContext(
                    self, step, kind, evidence, source,
                    execution_provider=None if execution is None else lambda: execution,
                )
                with self._write_scope(), self._publication_lock:
                    if self._sealed:
                        return
                    step.processed += 1
                with self._automation_scope():
                    if kind == "report":
                        value = AnalysisReport(data["name"], result=data["result"], reason=data["reason"], values=data["values"], evidence=evidence)
                        handler = next(link.handler for link in step.spec.reports if link.source.name == source["producer"])
                        returned = handler(value, ctx)
                    else:
                        returned = step.spec.on_event(FlowEvent(data["name"], data["data"]), ctx)
                if returned is not None and not isinstance(returned, ActionOutcome):
                    if inspect.iscoroutine(returned):
                        returned.close()
                    raise TypeError("Automation callbacks return None or ActionOutcome; use ctx.actions for operations")
            except Exception as exc:
                self._automation_fail(step, exc, evidence)
            finally:
                if ctx is not None:
                    ctx._open = False

    def start(self):
        """Signal the start of the source before its first input."""
        if self._started or self.finished:
            raise RuntimeError("Start has already been delivered or source finished")
        self._started = True
        self._deliver_event("started")

    def emit_event(self, name, data=None):
        """Deliver a source status change or error to Automation."""
        if name not in ("status_changed", "error"):
            raise ValueError("Use start() or finish() for lifecycle boundaries")
        if self.finished:
            raise RuntimeError("Flow session has finished")
        if data is not None and not isinstance(data, Mapping):
            raise TypeError("Flow event data must be a mapping")
        self._deliver_event(name, data)

    def _deliver_event(self, name, extra=None):
        if not self.automation_enabled:
            return
        facts = _plain(self._execution_facts())
        if extra:
            facts.update(_plain(extra))
        if name == "finished":
            facts["result"] = self._result
        # Keep the persisted source tags used by archive and HTTP readers.
        source = dict(stage="job", producer="job", kind="job_event", name=name, data=facts)
        data = dict(name=name, data=facts)
        for spec in self.flow.automations:
            if spec.enabled and spec.on_event is not None:
                self._automation.submit(self._steps[spec.name], "job_event", data, None, source)

    def wait_automation(self, timeout=2.0):
        """Wait for deliveries currently queued; call without holding source locks."""
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite nonnegative seconds")
        return self._automation.wait(timeout)

    @property
    def automation_pending(self):
        return self._automation.pending

    def close_automation(self):
        """Reject late operations and release queued inputs after a deadline."""
        with self._write_scope():
            self._automation.close()
            if self._on_automation_idle is not None:
                self._on_automation_idle()

    def _fail(self, step, exc, evidence):
        message = f"{type(exc).__name__}: {exc}"
        with self._write_scope(), self._publication_lock:
            if self._sealed or step.error:
                return
            step.error = message
            step.evidence = deepcopy(evidence)
            self.error_count += 1
            self._emit(step, "diagnostic", dict(message=step.error, error=True), evidence)
            if step.stage == "analyzer":
                step.result, step.reason = "ERROR", step.error
            for spec in self.flow.analyzers:
                dependent = self._steps[spec.name]
                if step.spec.name in self._inputs(spec) and not dependent.error:
                    self._fail(dependent, RuntimeError(f"input {step.spec.name} failed"), evidence)

    def _consume(self, returned, ctx):
        if returned is None:
            return
        expected = dict if isinstance(ctx, ParserContext) else AnalysisReport
        if isinstance(returned, expected):
            items = (returned,)
        elif isinstance(returned, (Mapping, str, bytes, set, frozenset)) or not isinstance(returned, Iterable):
            if inspect.iscoroutine(returned):
                returned.close()
            raise TypeError(f"{ctx._step.stage} returns {expected.__name__}, None, or an ordered iterable of {expected.__name__}")
        else:
            items = returned
        try:
            for item in items:
                if not isinstance(item, expected):
                    raise TypeError(f"{ctx._step.stage} output must be {expected.__name__}, got {type(item).__name__}")
                if expected is dict:
                    ctx._stage("record", _plain(item), ctx._evidence)
                else:
                    ctx._stage("report", dict(version=1, name=item.name, result=item.result, reason=item.reason, values=_plain(item.values)),
                               ctx._evidence if item.evidence is None else _plain(item.evidence))
        finally:
            if inspect.isgenerator(items):
                items.close()

    def _invoke(self, step, callback, args, kind, evidence, *, input_publication=None, execution_provider=None):
        # Working counters are separate from the source's committed projection.
        with self._publication_lock:
            if self._sealed or step.error:
                return
            step.processed += 1
        context = ParserContext if step.stage == "parser" else AnalyzerContext
        with self._callback_scope():
            ctx = None
            try:
                ctx = context(self, step, kind, evidence, input_publication, execution_provider=execution_provider)
                returned = callback(*args, ctx)
                if self._sealed:
                    return
                self._consume(returned, ctx)
                count = sum(kind in ("record", "report") for kind, _, _ in ctx._pending)
                if self._cycle_records + count > MAX_OUTPUTS_PER_CYCLE:
                    raise ValueError(f"input cycle exceeds {MAX_OUTPUTS_PER_CYCLE} output records")
                for output_kind, data, output_evidence in ctx._pending:
                    self._emit(step, output_kind, data, output_evidence)
            except Exception as exc:
                self._fail(step, exc, evidence)
            finally:
                if ctx is not None:
                    ctx._pending.clear()
                    ctx._open = False

    def _drain_records(self, *, execution_provider=None):
        while self._records and not self._sealed:
            publication = self._records.popleft()
            for step, callback in self._routes.get((publication["producer"], publication["kind"]), ()):
                data = publication["data"]
                value = _readonly(_plain(data)) if publication["kind"] == "record" else AnalysisReport(
                    data["name"], result=data["result"], reason=data["reason"], values=data["values"], evidence=publication["evidence"])
                self._invoke(
                    step, callback, (value,), publication["kind"], publication["evidence"],
                    input_publication=publication, execution_provider=execution_provider,
                )

    def _react(self, *, execution=None):
        while self._reactions and not self._sealed:
            publication = self._reactions.popleft()
            source = {key: publication[key] for key in ("seq", "stage", "producer", "kind")}
            source["name"] = publication["data"]["name"]
            for spec in self.flow.automations:
                step = self._steps[spec.name]
                if not spec.enabled or step.error or publication["producer"] not in self._inputs(spec):
                    continue
                self._automation.submit(step, "report", publication["data"], publication["evidence"], source, execution=execution)

    def _enter(self):
        if self.finished:
            raise RuntimeError("Flow session has finished")
        if self._busy:
            raise RuntimeError("Flow callbacks cannot recursively feed their session")
        self._busy = True
        self._cycle_records = 0

    def feed(self, line, evidence=None):
        if not isinstance(line, str):
            raise TypeError("feed requires decoded text")
        evidence = _plain(evidence)
        self._enter()
        self._lines += 1
        if evidence is None:
            evidence = dict(line_no=self._lines, line=line)
        try:
            for spec in self.flow.parsers:
                self._invoke(self._steps[spec.name], spec.parse, (line,), "line", evidence)
            self._drain_records()
            self._react()
            self._update_result()
        finally:
            self._busy = False

    def tick(self, elapsed=None):
        explicit = elapsed is not None
        facts = None if explicit else self._execution_facts(use_explicit_tick=False)
        if facts is not None:
            elapsed = facts.get("elapsed", 0)
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("elapsed must be a finite nonnegative number")
        self._enter()
        self._last_elapsed = elapsed
        self._explicit_tick = explicit
        execution = None

        def tick_execution():
            nonlocal execution
            if execution is None:
                # For explicit ticks, read lazily inside the step's error boundary.
                source = facts if facts is not None else self._execution_facts(use_explicit_tick=False)
                execution = _plain({**source, "elapsed": elapsed})
            return execution

        try:
            due = False
            for spec in self.flow.analyzers:
                step = self._steps[spec.name]
                if (
                    self.clock_enabled
                    and spec.every is not None
                    and elapsed + 1e-12 * max(elapsed, step.last_tick + spec.every) >= step.last_tick + spec.every
                ):
                    step.last_tick = elapsed
                    due = True
                    self._invoke(step, spec.on_tick, (Tick(elapsed),), "tick", None, execution_provider=tick_execution)
            if due:
                self._drain_records(execution_provider=tick_execution)
                self._react(execution=execution)
                self._update_result()
        finally:
            self._busy = False

    def finish(self, complete=True, execution=None):
        if self.finished:
            return self.snapshot()
        if type(complete) is not bool:
            raise TypeError("complete must be bool")
        if execution is not None:
            execution = _plain(execution)
            if not isinstance(execution, dict):
                raise TypeError("execution must be a mapping")
        self._enter()
        self.complete = complete
        if execution is not None:
            self._final_execution.update(execution)
        try:
            end = EndInfo(complete, self._execution_facts())
            for spec in self.flow.parsers:
                if spec.on_finish:
                    self._invoke(self._steps[spec.name], spec.on_finish, (end,), "finish", None)
            self._drain_records()
            for spec in self.flow.analyzers:
                if spec.on_finish:
                    self._invoke(self._steps[spec.name], spec.on_finish, (end,), "finish", None)
                    self._drain_records()
            if not complete:
                for spec in self.flow.analyzers:
                    step = self._steps[spec.name]
                    if step.result not in (None, "FAIL", "ERROR"):
                        self._emit(step, "result", dict(value="INCOMPLETE", reason="Source incomplete", final=True), None)
            self._react()
            self.finished = True
            self._update_result()
            self._deliver_event("finished")
            self._automation.finish()
        finally:
            self._busy = False
        return self.snapshot()

    def _analyzer_summaries(self):
        with self._publication_lock:
            out = []
            for spec in self.flow.analyzers:
                step = self._steps[spec.name]
                item = {
                    "name": spec.name,
                    "version": spec.version,
                    "inputs": tuple(self._inputs(spec)),
                    "count": step.count,
                    "publication_count": step.publication_count,
                    "report_count": step.report_count,
                    "processed": step.processed,
                    "result": step.result,
                    "reason": step.reason,
                    "final": step.final,
                    "issues": tuple(_readonly(issue) for issue in step.issues.values()),
                    "event_count": step.event_count,
                }
                if step.error:
                    item["error"] = step.error
                out.append(MappingProxyType(item))
            return tuple(out)

    def _aggregate(self, analyzers):
        if self.error_count:
            return "ERROR"
        if callable(self.flow.result):
            try:
                passed = analyzers if isinstance(analyzers, tuple) else _readonly(deepcopy(analyzers))
                result = self.flow.result(passed)
                result = result.value if isinstance(result, Result) else result
                if result is not None and result not in _RESULTS:
                    raise ValueError("aggregation must return a Result value or None")
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                with self._write_scope(), self._publication_lock:
                    if not self._sealed:
                        self.aggregation_error = message
                        self.error_count += 1
                return "ERROR"
            values = [result] if result is not None else []
        elif isinstance(self.flow.result, Analyzer):
            values = [next(item["result"] for item in analyzers if item["name"] == self.flow.result.name)]
        else:
            values = [item["result"] for item in analyzers if item["result"] is not None]
        if "ERROR" in values:
            return "ERROR"
        if "FAIL" in values:
            return "FAIL"
        if not self.finished:
            return None
        if not self.complete or None in values or "INCOMPLETE" in values:
            return "INCOMPLETE"
        return "PASS" if "PASS" in values else "N/A"

    def _update_result(self):
        # Aggregation is computation and therefore belongs to the input cycle,
        # never to a GUI read of snapshot().
        if self._sealed:
            return
        values = self._analyzer_summaries() if callable(self.flow.result) else [
            dict(name=spec.name, result=self._steps[spec.name].result)
            for spec in self.flow.analyzers
        ]
        result = self._aggregate(values)
        with self._publication_lock:
            if not self._sealed:
                self._result = result

    def close(self):
        if self._busy or self._automation._thread is threading.current_thread():
            raise RuntimeError("cannot close FlowSession inside a callback")
        if self.finished and self._automation.pending:
            self._automation.wait(2.0)
        self._seal(incomplete=not self.finished)
        thread = self._automation._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def __enter__(self):
        with self._publication_lock:
            if self._sealed or self.finished:
                raise RuntimeError("Flow session is closed or finished")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __repr__(self):
        with self._publication_lock:
            return (
                f"FlowSession(flow={self.flow.name!r}, finished={self.finished}, "
                f"sealed={self._sealed}, result={self._result!r})"
            )

    def snapshot(self):
        return deepcopy(self._projection())

    def _projection(self):
        # Nested values were detached before storing. Writers replace these
        # values, so copy the owning containers for the internal projection.
        with self._publication_lock:
            return self._snapshot()

    def _snapshot(self):
        from ._views import _plain as _plain_views

        parsers, analyzers, automations = [], [], []
        for step in self._steps.values():
            spec = step.spec
            base = dict(name=spec.name, count=step.count, publication_count=step.publication_count, processed=step.processed, code=self._codes[spec.name])
            if step.error:
                base["error"] = step.error
            if step.stage in ("parser", "analyzer"):
                base.update(version=spec.version, outputs=_plain(spec.outputs), latest=dict(step.latest),
                            integer_fields=[name for name, value in step.latest.items() if type(value) is int],
                            latest_evidence=step.record_evidence,
                            evidence=step.evidence)
            if step.stage == "parser":
                parsers.append(base)
            elif step.stage == "analyzer":
                base.update(inputs=list(self._inputs(spec)), report_count=step.report_count, result=step.result, reason=step.reason, final=step.final, issues=list(step.issues.values()), event_count=step.event_count, events=list(step.events))
                analyzers.append(base)
            else:
                # This key is shared with historical archive and HTTP snapshots.
                base.update(inputs=list(self._inputs(spec)), enabled=spec.enabled, on_report=bool(spec.reports), on_job_event=spec.on_event is not None, action_count=step.action_count, last=step.last)
                automations.append(base)
        return dict(name=self.flow.name, version=self.flow.version, parsers=parsers, analyzers=analyzers, automations=automations, views=_plain_views(self.flow._views_json), diagnostics=list(self._diagnostics), error_count=self.error_count, automation_error_count=self.automation_error_count, complete=self.complete, finished=self.finished, result=self._result, result_source=self.flow.result.name if isinstance(self.flow.result, Analyzer) else "custom" if callable(self.flow.result) else "default", result_code=self._result_code, publication_error=self.publication_error, aggregation_error=self.aggregation_error)

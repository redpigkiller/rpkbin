"""A small stdio Debug Adapter Protocol server for :mod:`rpkbin.debug`."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any, BinaryIO

from .model import (
    DebugCapabilities,
    DebugLocation,
    DebugSessionFactory,
    DebugTarget,
    InstructionBreakpoint,
    SourceBreakpoint,
    StopReason,
    StopResult,
    SupportsAttach,
    SupportsClose,
    SupportsReverseStep,
    SupportsSourceBreakpoints,
)


class DAPProtocolError(ValueError):
    """Raised for malformed DAP framing or payloads."""


class DAPTransport:
    """Read and write ``Content-Length`` framed JSON messages."""

    def __init__(
        self,
        reader: BinaryIO,
        writer: BinaryIO,
        *,
        max_message_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._max_message_bytes = max_message_bytes
        self._write_lock = threading.Lock()

    def read_message(self) -> dict[str, Any] | None:
        headers: dict[str, str] = {}
        line = self._reader.readline()
        if line == b"":
            return None
        while line not in (b"\r\n", b"\n"):
            try:
                name, value = line.decode("ascii").split(":", 1)
            except (UnicodeDecodeError, ValueError) as exc:
                raise DAPProtocolError("malformed DAP header") from exc
            headers[name.strip().lower()] = value.strip()
            line = self._reader.readline()
            if line == b"":
                raise DAPProtocolError("unexpected EOF in DAP headers")

        raw_length = headers.get("content-length")
        if raw_length is None:
            raise DAPProtocolError("missing Content-Length header")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise DAPProtocolError("invalid Content-Length header") from exc
        if length < 0 or length > self._max_message_bytes:
            raise DAPProtocolError("Content-Length is out of range")

        payload = self._read_exact(length)
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DAPProtocolError("invalid DAP JSON payload") from exc
        if not isinstance(message, dict):
            raise DAPProtocolError("DAP payload must be a JSON object")
        return message

    def write_message(self, message: dict[str, Any]) -> None:
        payload = json.dumps(
            message, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        frame = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
        with self._write_lock:
            self._writer.write(frame)
            self._writer.flush()

    def _read_exact(self, length: int) -> bytes:
        chunks: list[bytes] = []
        remaining = length
        while remaining:
            chunk = self._reader.read(remaining)
            if not chunk:
                raise DAPProtocolError("unexpected EOF in DAP payload")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


class DAPServer:
    """Dispatch DAP requests to a target-neutral debug-session adapter."""

    def __init__(
        self,
        factory: DebugSessionFactory,
        *,
        worker_join_timeout: float = 2.0,
    ) -> None:
        self._factory = factory
        self._worker_join_timeout = worker_join_timeout
        self._transport: DAPTransport | None = None
        self._target: DebugTarget | None = None
        self._initialized = False
        self._configured = False
        self._stop_on_entry = True
        self._disconnected = False
        self._sequence = 1
        self._sequence_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._frame_to_context: dict[int, int] = {}
        self._scope_references: dict[int, tuple[int, str]] = {}
        self._next_reference = 1
        self._after_response: list[Callable[[], None]] = []
        self._closed = False

    def serve(self, reader: BinaryIO, writer: BinaryIO) -> None:
        self._transport = DAPTransport(reader, writer)
        try:
            while not self._disconnected:
                request = self._transport.read_message()
                if request is None:
                    break
                self.handle_message(request)
        finally:
            self._shutdown()

    def handle_message(self, request: dict[str, Any]) -> None:
        request_seq = request.get("seq")
        command = request.get("command")
        if request.get("type") != "request":
            raise DAPProtocolError("incoming DAP message must be a request")
        if not isinstance(request_seq, int) or request_seq <= 0:
            raise DAPProtocolError("request seq must be a positive integer")
        if not isinstance(command, str) or not command:
            raise DAPProtocolError("request command must be a non-empty string")
        arguments = request.get("arguments", {})
        if not isinstance(arguments, dict):
            self._respond(request, success=False, message="arguments must be an object")
            return

        handler = getattr(self, f"_request_{command}", None)
        if handler is None:
            self._respond(
                request,
                success=False,
                message=f"unsupported request: {command}",
            )
            return
        self._after_response = []
        try:
            body = handler(arguments)
        except Exception as exc:
            self._after_response = []
            self._respond(request, success=False, message=str(exc))
            return
        callbacks, self._after_response = self._after_response, []
        self._respond(request, body=body)
        for callback in callbacks:
            callback()

    def _request_initialize(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._initialized:
            raise RuntimeError("debug session is already initialized")
        self._initialized = True
        return self._dap_capabilities(self._factory.capabilities)

    def _request_launch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._require_initialized()
        self._install_target(
            self._factory.launch(dict(arguments)),
            stop_on_entry=self._boolean_argument(arguments, "stopOnEntry", True),
        )
        self._defer(lambda: self._event("initialized"))
        return {}

    def _request_attach(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._require_initialized()
        if not self._factory.supports_attach:
            raise RuntimeError("session factory does not support attach")
        if not isinstance(self._factory, SupportsAttach):
            raise RuntimeError("factory advertises attach but does not implement it")
        self._install_target(
            self._factory.attach(dict(arguments)),
            stop_on_entry=self._boolean_argument(arguments, "stopOnEntry", True),
        )
        self._defer(lambda: self._event("initialized"))
        return {}

    def _request_configurationDone(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        target = self._require_target()
        if self._configured:
            raise RuntimeError("configurationDone was already received")
        self._configured = True
        if self._stop_on_entry:
            context = self._validated_contexts(target)[0]
            location = target.location(context.id)
            self._validate_location(target, location)
            result = StopResult(StopReason.ENTRY, context.id, location)
            self._defer(lambda: self._emit_stop(target, result))
        else:
            self._defer(lambda: self._start_continue(target, None))
        return {}

    def _request_threads(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = self._require_target()
        contexts = self._validated_contexts(target)
        return {
            "threads": [
                {"id": context.id, "name": context.name} for context in contexts
            ]
        }

    def _request_stackTrace(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = self._require_target()
        context_id = self._positive_int(arguments, "threadId")
        self._require_context(target, context_id)
        location = target.location(context_id)
        self._validate_location(target, location)
        frame_id = self._frame_id(context_id)
        frame: dict[str, Any] = {
            "id": frame_id,
            "name": location.instruction or f"context {context_id}",
            "line": location.source.line if location.source else 1,
            "column": location.source.column if location.source else 1,
            "instructionPointerReference": self._format_reference(location),
        }
        if location.source is not None:
            name = location.source.path.replace("\\", "/").rsplit("/", 1)[-1]
            frame["source"] = {"name": name, "path": location.source.path}
        return {"stackFrames": [frame], "totalFrames": 1}

    def _request_scopes(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = self._require_target()
        frame_id = self._positive_int(arguments, "frameId")
        try:
            context_id = self._frame_to_context[frame_id]
        except KeyError as exc:
            raise ValueError(f"unknown frameId: {frame_id}") from exc
        scopes = target.scopes(context_id)
        result = []
        for scope in scopes:
            reference = self._next_reference
            self._next_reference += 1
            self._scope_references[reference] = (context_id, scope.id)
            result.append(
                {
                    "name": scope.name,
                    "variablesReference": reference,
                    "expensive": scope.expensive,
                }
            )
        return {"scopes": result}

    def _request_variables(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = self._require_target()
        reference = self._positive_int(arguments, "variablesReference")
        try:
            context_id, scope_id = self._scope_references[reference]
        except KeyError as exc:
            raise ValueError(
                f"unknown variablesReference: {reference}"
            ) from exc
        variables = target.variables(context_id, scope_id)
        return {
            "variables": [
                {
                    "name": variable.name,
                    "value": variable.value,
                    "type": variable.type_name,
                    "variablesReference": 0,
                }
                for variable in variables
            ]
        }

    def _request_setInstructionBreakpoints(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        target = self._require_target()
        if not target.capabilities.supports_instruction_breakpoints:
            raise RuntimeError("target does not support instruction breakpoints")
        raw_breakpoints = arguments.get("breakpoints", [])
        if not isinstance(raw_breakpoints, list):
            raise ValueError("breakpoints must be an array")
        requested: list[InstructionBreakpoint] = []
        for item in raw_breakpoints:
            if not isinstance(item, dict):
                raise ValueError("each breakpoint must be an object")
            space, address = self._parse_reference(
                item.get("instructionReference"), target
            )
            offset = item.get("offset", 0)
            if not isinstance(offset, int):
                raise ValueError("breakpoint offset must be an integer")
            condition = item.get("condition")
            if condition is not None and not isinstance(condition, str):
                raise ValueError("breakpoint condition must be a string")
            requested.append(
                InstructionBreakpoint(space, address + offset, condition)
            )
        results = target.set_instruction_breakpoints(tuple(requested))
        if len(results) != len(requested):
            raise RuntimeError(
                "target returned a different number of breakpoint results"
            )
        spaces = {space.name for space in target.address_spaces()}
        if any(result.address_space not in spaces for result in results):
            raise RuntimeError(
                "target returned a breakpoint in an unknown address space"
            )
        return {
            "breakpoints": [
                {
                    "id": result.id,
                    "verified": result.verified,
                    "instructionReference": (
                        f"{result.address_space}:{result.address:#x}"
                    ),
                    "message": result.message,
                }
                for result in results
            ]
        }

    def _request_setBreakpoints(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        target = self._require_target()
        source = arguments.get("source")
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            raise ValueError("source.path must be a string")
        path = source["path"]
        raw_breakpoints = arguments.get("breakpoints", [])
        if not isinstance(raw_breakpoints, list):
            raise ValueError("breakpoints must be an array")
        if (
            not target.capabilities.supports_source_breakpoints
            or not isinstance(target, SupportsSourceBreakpoints)
        ):
            return {
                "breakpoints": [
                    {
                        "verified": False,
                        "line": item.get("line") if isinstance(item, dict) else None,
                        "message": "target does not support source breakpoints",
                    }
                    for item in raw_breakpoints
                ]
            }
        requested: list[SourceBreakpoint] = []
        for item in raw_breakpoints:
            if not isinstance(item, dict):
                raise ValueError("each breakpoint must be an object")
            line = item.get("line")
            condition = item.get("condition")
            if not isinstance(line, int):
                raise ValueError("source breakpoint line must be an integer")
            if condition is not None and not isinstance(condition, str):
                raise ValueError("breakpoint condition must be a string")
            requested.append(SourceBreakpoint(path, line, condition))
        results = target.set_source_breakpoints(path, tuple(requested))
        if len(results) != len(requested):
            raise RuntimeError(
                "target returned a different number of source breakpoint results"
            )
        return {
            "breakpoints": [
                {
                    "id": result.id,
                    "verified": result.verified,
                    "source": {"path": result.path},
                    "line": result.line,
                    "message": result.message,
                }
                for result in results
            ]
        }

    def _request_continue(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = self._require_ready_target()
        requested_context_id = self._positive_int(arguments, "threadId")
        self._require_context(target, requested_context_id)
        context_id = (
            requested_context_id
            if target.capabilities.supports_single_thread_execution
            else None
        )
        self._defer(lambda: self._start_continue(target, context_id))
        return {"allThreadsContinued": context_id is None}

    def _request_pause(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = self._require_target()
        if not target.capabilities.supports_pause:
            raise RuntimeError("target does not support pause")
        context_id = self._positive_int(arguments, "threadId")
        self._require_context(target, context_id)
        with self._state_lock:
            if self._worker is None or not self._worker.is_alive():
                raise RuntimeError("target is not running")
            self._cancel.set()
        return {}

    def _request_stepIn(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = self._require_ready_target()
        context_id = self._positive_int(arguments, "threadId")
        self._require_context(target, context_id)
        result = target.step_instruction(context_id)
        self._validate_stop_result(target, result)
        self._invalidate_handles()
        self._defer(lambda: self._emit_stop(target, result))
        return {}

    def _request_stepBack(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = self._require_ready_target()
        if not target.capabilities.supports_reverse_step:
            raise RuntimeError("target does not support reverse-step")
        if not isinstance(target, SupportsReverseStep):
            raise RuntimeError("target advertises reverse-step but does not implement it")
        context_id = self._positive_int(arguments, "threadId")
        self._require_context(target, context_id)
        result = target.reverse_step(context_id)
        self._validate_stop_result(target, result)
        self._invalidate_handles()
        self._defer(lambda: self._emit_stop(target, result))
        return {}

    def _request_disconnect(self, arguments: dict[str, Any]) -> dict[str, Any]:
        def disconnect() -> None:
            self._disconnected = True
            self._shutdown()

        self._defer(disconnect)
        return {}

    def _install_target(
        self, target: DebugTarget, *, stop_on_entry: bool
    ) -> None:
        if self._target is not None:
            raise RuntimeError("a debug target is already active")
        if target.capabilities != self._factory.capabilities:
            raise RuntimeError(
                "target capabilities do not match factory-advertised capabilities"
            )
        self._validated_contexts(target)
        spaces = target.address_spaces()
        if not spaces or len({space.name for space in spaces}) != len(spaces):
            raise RuntimeError("target address spaces must be non-empty and unique")
        self._target = target
        self._stop_on_entry = stop_on_entry

    def _start_continue(
        self, target: DebugTarget, context_id: int | None
    ) -> None:
        self._invalidate_handles()
        with self._state_lock:
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("target is already running")
            self._cancel = threading.Event()

            def run() -> None:
                try:
                    result = target.continue_execution(context_id, self._cancel)
                    self._emit_stop(target, result)
                except Exception as exc:
                    self._event(
                        "output",
                        {
                            "category": "stderr",
                            "output": f"debug target failed: {exc}\n",
                        },
                    )
                    selected = context_id or self._validated_contexts(target)[0].id
                    try:
                        location = target.location(selected)
                        self._validate_location(target, location)
                    except Exception:
                        location = None
                    self._emit_stop(
                        target,
                        StopResult(
                            StopReason.EXCEPTION,
                            selected,
                            location,
                            str(exc),
                        ),
                    )
                finally:
                    with self._state_lock:
                        self._worker = None

            self._worker = threading.Thread(
                target=run, name="rpkbin-dap-continue", daemon=True
            )
        self._event(
            "continued",
            {
                "threadId": context_id or self._validated_contexts(target)[0].id,
                "allThreadsContinued": context_id is None,
            },
        )
        self._worker.start()

    def _emit_stop(self, target: DebugTarget, result: StopResult) -> None:
        self._validate_stop_result(target, result)
        self._invalidate_handles()
        if result.reason is StopReason.COMPLETE:
            self._event("terminated")
            return
        body: dict[str, Any] = {
            "reason": self._dap_stop_reason(result.reason),
            "threadId": result.context_id,
            "allThreadsStopped": True,
        }
        if result.description is not None:
            body["description"] = result.description
        if result.breakpoint_id is not None:
            body["hitBreakpointIds"] = [result.breakpoint_id]
        self._event("stopped", body)

    def _validate_stop_result(
        self, target: DebugTarget, result: StopResult
    ) -> None:
        if not isinstance(result, StopResult):
            raise RuntimeError("target must return a StopResult")
        if not isinstance(result.reason, StopReason):
            raise RuntimeError("target returned an invalid stop reason")
        self._require_context(target, result.context_id)
        if result.location is not None:
            self._validate_location(target, result.location)

    @staticmethod
    def _dap_stop_reason(reason: StopReason) -> str:
        if reason is StopReason.WATCHPOINT:
            return StopReason.DATA_BREAKPOINT.value
        return reason.value

    def _validated_contexts(self, target: DebugTarget):
        contexts = target.contexts()
        if not contexts:
            raise RuntimeError("target must expose at least one execution context")
        if len({context.id for context in contexts}) != len(contexts):
            raise RuntimeError("target execution-context ids must be unique")
        return contexts

    def _require_context(self, target: DebugTarget, context_id: int) -> None:
        if context_id not in {
            context.id for context in self._validated_contexts(target)
        }:
            raise ValueError(f"unknown threadId: {context_id}")

    def _validate_location(
        self, target: DebugTarget, location: DebugLocation
    ) -> None:
        spaces = {space.name for space in target.address_spaces()}
        if location.address_space not in spaces:
            raise RuntimeError(
                f"target returned unknown address space: {location.address_space}"
            )

    def _parse_reference(
        self, value: Any, target: DebugTarget
    ) -> tuple[str, int]:
        if not isinstance(value, str) or ":" not in value:
            raise ValueError(
                "instructionReference must use '<address-space>:<address>'"
            )
        space, raw_address = value.rsplit(":", 1)
        if space not in {item.name for item in target.address_spaces()}:
            raise ValueError(f"unknown address space: {space}")
        try:
            address = int(raw_address, 0)
        except ValueError as exc:
            raise ValueError(f"invalid instruction address: {raw_address}") from exc
        if address < 0:
            raise ValueError("instruction address must be non-negative")
        return space, address

    @staticmethod
    def _format_reference(location: DebugLocation) -> str:
        return f"{location.address_space}:{location.address:#x}"

    def _frame_id(self, context_id: int) -> int:
        for frame_id, existing in self._frame_to_context.items():
            if existing == context_id:
                return frame_id
        frame_id = self._next_reference
        self._next_reference += 1
        self._frame_to_context[frame_id] = context_id
        return frame_id

    def _invalidate_handles(self) -> None:
        self._frame_to_context.clear()
        self._scope_references.clear()

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("initialize must run before launch or attach")

    def _require_target(self) -> DebugTarget:
        if self._target is None:
            raise RuntimeError("launch or attach must create a debug target first")
        return self._target

    def _require_ready_target(self) -> DebugTarget:
        target = self._require_target()
        if not self._configured:
            raise RuntimeError("configurationDone must run before execution")
        with self._state_lock:
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("target is already running")
        return target

    @staticmethod
    def _positive_int(arguments: dict[str, Any], name: str) -> int:
        value = arguments.get(name)
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _boolean_argument(
        arguments: dict[str, Any], name: str, default: bool
    ) -> bool:
        value = arguments.get(name, default)
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        return value

    @staticmethod
    def _dap_capabilities(capabilities: DebugCapabilities) -> dict[str, Any]:
        return {
            "supportsConfigurationDoneRequest": True,
            "supportsInstructionBreakpoints": (
                capabilities.supports_instruction_breakpoints
            ),
            "supportsFunctionBreakpoints": False,
            "supportsStepBack": capabilities.supports_reverse_step,
            "supportsSingleThreadExecutionRequests": (
                capabilities.supports_single_thread_execution
            ),
            "supportsReadMemoryRequest": False,
            "supportsDisassembleRequest": False,
            "supportsEvaluateForHovers": False,
        }

    def _defer(self, callback: Callable[[], None]) -> None:
        self._after_response.append(callback)

    def _respond(
        self,
        request: dict[str, Any],
        *,
        success: bool = True,
        body: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> None:
        response: dict[str, Any] = {
            "seq": self._next_seq(),
            "type": "response",
            "request_seq": request["seq"],
            "success": success,
            "command": request["command"],
        }
        if body is not None:
            response["body"] = body
        if message is not None:
            response["message"] = message
        self._write(response)

    def _event(
        self, event: str, body: dict[str, Any] | None = None
    ) -> None:
        message: dict[str, Any] = {
            "seq": self._next_seq(),
            "type": "event",
            "event": event,
        }
        if body is not None:
            message["body"] = body
        self._write(message)

    def _write(self, message: dict[str, Any]) -> None:
        if self._transport is None:
            raise RuntimeError("DAP server is not connected to a transport")
        self._transport.write_message(message)

    def _next_seq(self) -> int:
        with self._sequence_lock:
            result = self._sequence
            self._sequence += 1
            return result

    def _shutdown(self) -> None:
        self._cancel.set()
        worker = self._worker
        if (
            worker is not None
            and worker.is_alive()
            and worker is not threading.current_thread()
        ):
            worker.join(self._worker_join_timeout)
        if worker is not None and worker.is_alive():
            return
        target = self._target
        if (
            target is not None
            and isinstance(target, SupportsClose)
            and not self._closed
        ):
            self._closed = True
            target.close()


def serve_stdio(factory: DebugSessionFactory) -> None:
    """Run a DAP server on the process standard streams."""

    import sys

    DAPServer(factory).serve(sys.stdin.buffer, sys.stdout.buffer)

"""Standard-library HTTP server for the local isasim debugger."""

from __future__ import annotations

from hmac import compare_digest
import json
from secrets import token_urlsafe
import threading
from time import monotonic
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from rpkbin.isasim import IsaSimError

from .session import DebugSession

STATIC_INDEX = Path(__file__).parent / "static" / "index.html"
_MAX_BODY_SIZE = 2 * 1024 * 1024
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})
_SIMPLE_ACTIONS = {
    "step_in": "step_in",
    "step_over": "step_over",
    "step_out": "step_out",
    "step_back": "step_back",
    "continue": "continue_run",
    "clear_breakpoints": "clear_breakpoints",
    "clear_watchpoints": "clear_watchpoints",
    "reset": "reset",
    "reload": "reload",
}


def _parse_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 10)
        except ValueError as exc:
            raise ValueError(f"{field} must be an integer") from exc
    raise ValueError(f"{field} must be an integer")


def _parse_bool(value: Any, *, field: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
            return lowered in {"true", "1", "yes", "on"}
    raise ValueError(f"{field} must be a boolean")


def _optional_str(value: Any, *, field: str, default: str | None = None) -> str | None:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _exec(session: DebugSession, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    memory_space = _optional_str(payload.get("space"), field="space")
    memory_filter = _optional_str(payload.get("filter"), field="filter")
    hide_uninit = _parse_bool(payload.get("hide_uninit"), field="hide_uninit")

    method_name: str
    args: tuple[Any, ...]
    if action in _SIMPLE_ACTIONS:
        method_name, args = _SIMPLE_ACTIONS[action], ()
    elif action in {"run_to_cursor", "set_pc"}:
        file = _optional_str(payload.get("file"), field="file", default="")
        if "line" not in payload:
            raise KeyError("line")
        line = _parse_int(payload["line"], field="line")
        method_name, args = action, (file, line)
    elif action == "poke":
        method_name, args = "poke", (payload["target"], payload["value"])
    elif action == "breakpoint":
        file = _optional_str(payload.get("file"), field="file", default="")
        if "line" not in payload:
            raise KeyError("line")
        line = _parse_int(payload["line"], field="line")
        cond = _optional_str(payload.get("condition"), field="condition")
        method_name, args = "toggle_breakpoint", (file, line, cond)
    elif action == "watchpoint":
        kind = _optional_str(payload.get("kind"), field="kind", default="w")
        cond = _optional_str(payload.get("condition"), field="condition")
        method_name, args = "add_watchpoint", (payload["target"], kind, cond)
    elif action == "remove_watchpoint":
        method_name, args = "remove_watchpoint", (payload.get("id"),)
    else:
        raise ValueError(f"Unknown action: {action}")

    session.set_memory_view(
        memory_space=memory_space,
        memory_filter=memory_filter,
        hide_uninit=hide_uninit,
    )
    return getattr(session, method_name)(*args)


def _state(session: DebugSession, query: dict[str, list[str]]) -> dict[str, Any]:
    def one(name: str) -> str | None:
        values = query.get(name)
        if values is not None and len(values) != 1:
            raise ValueError(f"Duplicate {name} query parameter")
        return values[0] if values else None

    hide_uninit = _parse_bool(one("hide_uninit"), field="hide_uninit")
    return session.view_state(
        memory_space=one("space"),
        memory_filter=one("filter"),
        hide_uninit=hide_uninit,
    )


class DebuggerHTTPServer(ThreadingHTTPServer):
    """One debugger session served over a loopback-only threaded HTTP server."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], session: DebugSession) -> None:
        if address[0] != "127.0.0.1":
            raise ValueError("isasim GUI binds only to loopback (127.0.0.1)")
        self.session = session
        self.token = token_urlsafe(32)
        self.action_lock = threading.RLock()
        super().__init__(address, DebuggerRequestHandler)


class DebuggerRequestHandler(BaseHTTPRequestHandler):
    server: DebuggerHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _request_denial(self, *, mutating: bool = False) -> str | None:
        if self.client_address[0] != "127.0.0.1":
            return "Local requests only"
        hosts = self.headers.get_all("Host", [])
        host = hosts[0].lower() if len(hosts) == 1 else ""
        expected = {f"{name}:{self.server.server_port}" for name in _LOOPBACK_HOSTS}
        if self.server.server_port == 80:
            expected.update(_LOOPBACK_HOSTS)
        if host not in expected:
            return "Invalid local Host"
        origins = self.headers.get_all("Origin", [])
        authority = host.removesuffix(":80") if self.server.server_port == 80 else host
        if (origins or mutating) and origins != [f"http://{authority}"]:
            return "Same-origin requests only"
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return "Cross-origin requests are not allowed"
        if mutating:
            tokens = self.headers.get_all("X-Isasim-Token", [])
            if len(tokens) != 1 or not tokens[0].isascii() or not compare_digest(tokens[0], self.server.token):
                return "Invalid debugger token"
        return None

    def _body_length(self) -> int:
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding") or len(lengths) > 1:
            raise ValueError("Invalid request body size")
        if not lengths:
            return 0
        length = lengths[0]
        if not length.isascii() or not length.isdecimal() or int(length) > _MAX_BODY_SIZE:
            raise ValueError("Invalid request body size")
        return int(length)

    def _discard_rejected_body(self) -> None:
        # Closing with unread request bytes can abort the 403 response on Windows.
        try:
            remaining = self._body_length()
        except ValueError:
            return
        if not 0 < remaining <= 64 * 1024:
            return
        previous_timeout = self.connection.gettimeout()
        deadline = monotonic() + 0.25
        try:
            while remaining:
                timeout = deadline - monotonic()
                if timeout <= 0:
                    break
                self.connection.settimeout(timeout)
                chunk = self.rfile.read1(remaining)
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            self.connection.settimeout(previous_timeout)

    def do_GET(self) -> None:
        if denial := self._request_denial():
            self._json({"detail": denial}, 403)
            return
        parsed = urllib.parse.urlsplit(self.path)
        try:
            if parsed.path == "/":
                if not STATIC_INDEX.is_file():
                    self._json({"detail": "index.html not found"}, 404)
                    return
                html = STATIC_INDEX.read_bytes().replace(b"__ISASIM_TOKEN__", self.server.token.encode("ascii"))
                self._send(200, html, "text/html; charset=utf-8")
            elif parsed.path == "/api/state":
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                with self.server.action_lock:
                    self._json(_state(self.server.session, query))
            else:
                self._json({"detail": "Not Found"}, 404)
        except (ValueError, TypeError, KeyError, IsaSimError) as exc:
            self._json({"detail": str(exc)}, 400)

    def do_POST(self) -> None:
        if denial := self._request_denial(mutating=True):
            self._discard_rejected_body()
            self._json({"detail": denial}, 403)
            return
        try:
            length = self._body_length()
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            path = urllib.parse.urlsplit(self.path).path
            action_by_path = {
                "/api/step_in": "step_in",
                "/api/step_over": "step_over",
                "/api/step_out": "step_out",
                "/api/step_back": "step_back",
                "/api/continue": "continue",
                "/api/run_to_cursor": "run_to_cursor",
                "/api/set_pc": "set_pc",
                "/api/poke": "poke",
                "/api/breakpoint": "breakpoint",
                "/api/clear_breakpoints": "clear_breakpoints",
                "/api/watchpoint": "watchpoint",
                "/api/remove_watchpoint": "remove_watchpoint",
                "/api/clear_watchpoints": "clear_watchpoints",
                "/api/reset": "reset",
                "/api/reload": "reload",
            }
            if path == "/api/action":
                action = str(payload.get("action", ""))
            else:
                action = action_by_path.get(path)
                if action is None:
                    self._json({"detail": "Not Found"}, 404)
                    return
            with self.server.action_lock:
                self._json(_exec(self.server.session, action, payload))
        except Exception as exc:
            self._json({"detail": str(exc)}, 400)


def create_server(
    session: DebugSession, *, host: str = "127.0.0.1", port: int = 8000
) -> DebuggerHTTPServer:
    """Create a server bound only to a loopback address."""
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer from 0 to 65535")
    return DebuggerHTTPServer((host, port), session)

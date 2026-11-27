"""Local Wave HTTP server lifecycle."""

from __future__ import annotations

import secrets
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

from .archive import RunArchive

from ._web_routes import make_handler
from ._web_state import _WaveState
from .workspace import WaveWorkspace, safe_print, terminate_as_interrupt


class WaveServer(_WaveState):
    """Serve one local manager; page lifetime never owns process lifetime."""

    def __init__(
        self,
        workspace: WaveWorkspace | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        source_path: str | Path | None = None,
        archive_path: str | Path | None = None,
        archive_policy: dict | None = None,
    ) -> None:
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError("Wave GUI binds only to loopback (127.0.0.1 or localhost)")
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 0 <= port <= 65535
        ):
            raise ValueError("port must be an integer from 0 to 65535")
        self.workspace = workspace if workspace is not None else WaveWorkspace()
        self.host, self._requested_port = host, port
        self.source_path = str(Path(source_path).resolve()) if source_path else None
        self._opened_archive = RunArchive(archive_path) if archive_path else None
        self._archive_policy = dict(
            archive_policy or getattr(self.workspace, "_cli_archive_policy", None) or {}
        )
        if self._opened_archive:
            if self.workspace.jobs:
                raise ValueError("Archive viewing cannot own live jobs")
            self.source_path = self._opened_archive.metadata["source_path"]
        self.launch_cwd = Path.cwd()
        self._workspace_id = uuid.uuid4().hex
        self._action_lock = threading.RLock()
        self._workspace_lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._token: str | None = None
        if not self.workspace.started and not self._opened_archive:
            if self._archive_policy and self.workspace.archive is None and self.workspace.archive_error is None:
                self.workspace.configure_archive(**self._archive_policy, override=True)
            self.workspace.enable_archive(source_path=self.source_path)

    @property
    def port(self) -> int:
        return int(self._httpd.server_port) if self._httpd else self._requested_port

    @property
    def url(self) -> str:
        base = f"http://{self.host}:{self.port}/"
        return base + "?" + urllib.parse.urlencode({"token": self._token}) if self._token else base

    def start(self, *, open_browser: bool = False) -> str:
        """Start HTTP only. Neither serving nor reconnecting dispatches jobs."""
        if self._httpd is not None:
            return self.url
        self._token = secrets.token_urlsafe(32)
        handler = make_handler(self)
        self._httpd = ThreadingHTTPServer((self.host, self._requested_port), handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="WaveServer-HTTP", daemon=True
        )
        self._thread.start()
        if open_browser:
            webbrowser.open(self.url)
        return self.url

    def stop(self) -> None:
        """Stop the HTTP service only; the caller owns manager cleanup."""
        httpd, self._httpd = self._httpd, None
        if httpd:
            httpd.shutdown()
            httpd.server_close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None


def launch(
    workspace: WaveWorkspace | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = False,
    block: bool = True,
    source_path: str | Path | None = None,
    archive_path: str | Path | None = None,
    archive_policy: dict | None = None,
) -> WaveServer:
    """Open a preview; explicit Start dispatches. Ctrl+C closes the core."""
    if workspace is not None and workspace.jobs:
        workspace.validate()
    server = WaveServer(
        workspace,
        host=host,
        port=port,
        source_path=source_path,
        archive_path=archive_path,
        archive_policy=archive_policy,
    )
    url = server.start(open_browser=open_browser)
    hint = "" if open_browser else "pass --open to launch a browser automatically; "
    safe_print(f"[Wave] GUI: {url} ({hint}Ctrl+C shuts down the core; closing the page does not)")
    if block:
        # SIGTERM/SIGHUP act like Ctrl+C; handlers exist only on the main thread.
        with terminate_as_interrupt():
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                safe_print("\n[Wave] Stopping jobs and closing the core…")
            finally:
                try:
                    server.stop()
                finally:
                    server.workspace.close()
    return server

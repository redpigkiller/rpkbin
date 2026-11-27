"""Startup tokens authenticate API access without blocking static dependencies (S6)."""

import http.client
import json
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path

import pytest

from rpkbin.wave.web import WaveServer, launch


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("RPK_WAVE_ARCHIVE_DIR", str(tmp_path))
    server = WaveServer()
    server.start()
    yield server
    server.workspace.close()
    server.stop()


def bootstrap(server):
    url = urllib.parse.urlsplit(server.url)
    token = urllib.parse.parse_qs(url.query).get("token", [None])[0]
    assert token and len(token) >= 32
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token)
    return url, token


def raw(server, path, *, method="GET", token=None, headers=None, body=None):
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    try:
        request_headers = dict(headers or {})
        if token is not None:
            request_headers["X-Wave-Token"] = token
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("path", ["/api/state", "/api/logs", "/api/archive", "/api/files", "/api/not-found"])
def test_get_apis_require_header_token(server, path):
    _, token = bootstrap(server)
    for supplied in (None, "", "wrong"):
        assert raw(server, path, token=supplied)[0] == 403
    assert raw(server, path + "?token=" + token)[0] == 403
    assert raw(server, "/api/state", token=token)[0] == 200


@pytest.mark.parametrize("path", ["/api/settings/save", "/api/manager/action", "/api/work/load", "/api/job/action", "/api/job/priority", "/api/job/retention", "/api/not-found"])
def test_post_authentication_precedes_body_parsing_and_actions(server, path):
    _, token = bootstrap(server)
    before = server.workspace.max_workers
    for supplied in (None, "wrong"):
        assert raw(server, path, method="POST", token=supplied, body="not json")[0] == 403
    assert server.workspace.max_workers == before
    body = json.dumps({"workspace_id": server._workspace_id, "max_workers": 7})
    assert raw(server, "/api/settings/save", method="POST", token=token,
               headers={"Content-Type": "application/json"}, body=body)[0] == 200
    assert server.workspace.max_workers == 7


def test_bootstrap_html_and_static_asset_authentication(server):
    url, token = bootstrap(server)
    for path in ("/", "/index.html", "/static/index.html"):
        assert raw(server, path)[0] == 403
        assert raw(server, path + "?token=wrong")[0] == 403
        assert raw(server, path + "?token=" + token + "&token=" + token)[0] == 403
        assert raw(server, path + "?token=" + token)[0] == 200
    for path in ("/app.css", "/app.js", "/static/app.css", "/static/app.js",
                 "/history.js", "/analysis.js", "/reading.js", "/jobs.js", "/flow.js", "/icons.js", "/charts.js"):
        status, body = raw(server, path)
        assert status == 200 and body
    assert url.path == "/"


def test_token_does_not_bypass_host_origin_or_fetch_site_checks(server):
    _, token = bootstrap(server)
    for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"},
                    {"Sec-Fetch-Site": "cross-site"}):
        assert raw(server, "/api/state", token=token, headers=headers)[0] == 403
        assert raw(server, "/?token=" + token, headers=headers)[0] == 403
        assert raw(server, "/app.js", headers=headers)[0] == 403
    assert raw(server, "/api/state", token=token,
               headers={"Origin": f"http://{server.host}:{server.port}"})[0] == 200


def test_ssh_port_forwarded_loopback_hosts_and_origin_validation(server):
    _, token = bootstrap(server)
    for host in ("localhost:18080", "127.0.0.1:18080", "[::1]:18080", "localhost:1", "127.0.0.1:65535"):
        assert raw(server, "/api/state", token=token, headers={"Host": host})[0] == 200
        assert raw(server, "/api/state", token=token,
                   headers={"Host": host, "Origin": f"http://{host}"})[0] == 200
    for bad_host in (
        "evil.example:18080", "127.0.0.2:18080", "localhost", "127.0.0.1:",
        "localhost:0", "localhost:65536", "localhost:018080", "localhost:+8080",
        "user@localhost:18080", "localhost:18080/path", "localhost:18080 ",
    ):
        assert raw(server, "/api/state", token=token, headers={"Host": bad_host})[0] == 403
    assert raw(server, "/api/state", token=token,
               headers={"Host": "localhost:18080", "Origin": f"http://localhost:{server.port}"})[0] == 403
    assert raw(server, "/api/state", token=token,
               headers={"Host": "localhost:18080", "Origin": "https://localhost:18080"})[0] == 403


def test_handler_timeout_post_content_length_flow_conditions_and_get_500(server, monkeypatch):
    from rpkbin.wave import _web_routes

    _, token = bootstrap(server)
    handler_cls = _web_routes.make_handler(server)
    assert _web_routes._SOCKET_TIMEOUT_SECONDS == 10.0

    class DummyConn:
        timeout = None
        def settimeout(self, value):
            self.timeout = value
        def makefile(self, *args, **kwargs):
            import io
            return io.BytesIO()

    dummy = object.__new__(handler_cls)
    dummy.request = DummyConn()
    dummy.rfile = dummy.wfile = None
    dummy.disable_nagle_algorithm = False
    handler_cls.setup(dummy)
    assert dummy.connection.timeout == 10.0

    body = json.dumps({"workspace_id": server._workspace_id, "max_workers": 5}).encode("utf-8")
    for bad_length in ("+10", "10_0", "-1", "abc", "\xb2"):
        connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
        try:
            connection.putrequest("POST", "/api/settings/save")
            connection.putheader("X-Wave-Token", token)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", bad_length)
            connection.endheaders()
            resp = connection.getresponse()
            assert resp.status == 400
            resp.read()
        finally:
            connection.close()

    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    try:
        connection.putrequest("POST", "/api/settings/save")
        connection.putheader("X-Wave-Token", token)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(body)))
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        resp = connection.getresponse()
        assert resp.status == 400
        resp.read()
    finally:
        connection.close()

    for bad_cond in ("not-json", "[" * 2000 + "]" * 2000, json.dumps(["x" * 17000])):
        qs = urllib.parse.urlencode({
            "workspace_id": server._workspace_id,
            "view": "flow",
            "conditions": bad_cond,
        })
        status, payload = raw(server, "/api/archive?" + qs, token=token)
        assert status == 400
        assert "error" in json.loads(payload)

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected state failure")

    monkeypatch.setattr(server, "_state", boom)
    status, payload = raw(server, "/api/state", token=token)
    assert status == 500
    assert json.loads(payload) == {"error": "unexpected state failure"}



def test_duplicate_and_non_ascii_headers_are_rejected(server):
    _, token = bootstrap(server)
    assert raw(server, "/api/state", token="\u00ff")[0] == 403
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    try:
        connection.putrequest("GET", "/api/state")
        connection.putheader("X-Wave-Token", token)
        connection.putheader("X-Wave-Token", token)
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 403
        response.read()
    finally:
        connection.close()


def test_restart_rotates_token_and_repeated_start_preserves_it(server):
    _, first = bootstrap(server)
    assert server.start() == server.url
    assert bootstrap(server)[1] == first
    server.stop()
    server.start()
    _, second = bootstrap(server)
    assert second != first
    assert raw(server, "/api/state", token=first)[0] == 403
    assert raw(server, "/api/state", token=second)[0] == 200


def test_launch_prints_and_opens_authenticated_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RPK_WAVE_ARCHIVE_DIR", str(tmp_path))
    opened = []
    monkeypatch.setattr("rpkbin.wave.web.webbrowser.open", opened.append)
    server = launch(open_browser=True, block=False)
    try:
        bootstrap(server)
        assert opened == [server.url]
        assert "[Wave] GUI: " + server.url in capsys.readouterr().out
    finally:
        server.workspace.close()
        server.stop()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_gui_fetch_json_authenticates_get_and_post_without_losing_headers():
    source = (Path(__file__).parents[2] / "rpkbin/wave/static/app.js").read_text(encoding="utf-8")
    source, removed = re.subn(r"\nsetupMonitorLayout\(\);\s*\npollState\(\);\s*$", "\n", source)
    assert removed == 1
    checks = r"""
(async () => {
 const rows=[];
 fetch = async (path, options) => {
   rows.push({path, options});
   return {ok:true, json:async()=>({ok:true})};
 };
 await fetchJson('/api/state');
 await fetchJson('/api/files?path=/tmp', {headers:{'X-Example':'kept'}});
 await fetchJson('/api/archive?view=runs', {headers:new Headers({'X-Example':'kept'})});
 CONNECTED = true; STATE.workspace_id='workspace';
 await apiPost('/api/settings/save', {max_workers:7});
 assert.equal(rows.length,4);
 for (const row of rows) {
   const headers = new Headers(row.options.headers);
   assert.equal(headers.get('X-Wave-Token'), 'browser_token');
   assert.ok(!row.path.includes('token='));
 }
 assert.equal(new Headers(rows[1].options.headers).get('X-Example'),'kept');
 assert.equal(new Headers(rows[2].options.headers).get('X-Example'),'kept');
 assert.equal(new Headers(rows[3].options.headers).get('Content-Type'),'application/json');
 assert.equal(rows[3].options.method,'POST');

 // Verify AbortError normalization (no raw "signal is aborted without reason")
 const ext = new AbortController();
 fetch = async (_p, opts) => new Promise((_res, rej) => {
   opts.signal.addEventListener('abort', () => rej(new DOMException('signal is aborted without reason', 'AbortError')));
 });
 const pAbort = fetchJson('/api/archive?view=flow', {signal: ext.signal});
 ext.abort();
 await assert.rejects(pAbort, err => err.name === 'AbortError' && err.isAborted === true && !/without reason/i.test(err.message));

 // Verify 15s timeout produces a clear timeout error
 const origSetTimeout = setTimeout;
 const timeouts = [];
 setTimeout = (fn, ms) => { timeouts.push({fn, ms}); return 999; };
 try {
   const pTimeout = fetchJson('/api/archive?view=flow');
   assert.equal(timeouts.at(-1).ms, 15000);
   timeouts.at(-1).fn();
   await assert.rejects(pTimeout, err => err.message === 'Request timed out (15s)');
 } finally {
   setTimeout = origSetTimeout;
 }
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    harness = (
        "const vm=require('node:vm'),assert=require('node:assert/strict');\n"
        "vm.runInNewContext(" + repr(source + "\n" + checks) +
        ",{assert,process,console,Headers,URLSearchParams,AbortController,DOMException,setTimeout,clearTimeout,"
        "document:{addEventListener(){}},window:{location:{search:'?token=browser_token'}}});"
    )
    result = subprocess.run([shutil.which("node"), "-"], input=harness.encode(), capture_output=True, timeout=10)
    assert result.returncode == 0, (result.stdout + result.stderr).decode(errors="replace")

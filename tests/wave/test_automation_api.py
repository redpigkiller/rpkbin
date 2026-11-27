"""Automation reads show saved receipts and cannot replay live callbacks."""

from rpkbin.wave import WaveWorkspace

import json
import sys
import urllib.parse
import urllib.request

from rpkbin.flow import Automation, Flow
from rpkbin.jobcore import Job, JobManager
from rpkbin.wave.web import WaveServer

from test_wave import request


def history(server, job, **extra):
    query=dict(view="automation_history", job_id=job.id, attempt_id=job.attempt_id,
               workspace_id=server._workspace_id, **extra)
    return request(server.url.rstrip("/"), "/api/archive?"+urllib.parse.urlencode(query))


def test_automation_history_http_and_saved_event_source_are_read_only(tmp_path):
    calls = []

    def finished(event, ctx):
        if event.name == "finished":
            calls.append(event.name)
            assert ctx.actions.send("after exit").status == "rejected"

    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    server = None
    try:
        job = manager.add(Job("one", [sys.executable, "-c", "print('sample')"], pty=False), flow=Flow("native", automations=[Automation("finish", on_event=finished)]))
        manager.start()
        assert manager._drain(5)
        server = WaveServer(manager)
        server.start()
        for _ in range(3):
            status, page = history(server, job)
            assert status == 200 and len(page["rows"]) == 1
            row = page["rows"][0]
            assert row["data"]["status"] == "rejected"
            source = row["data"]["source"]
            assert source["name"] == "finished" and source["data"]["returncode"] == 0
            assert source["data"]["attempt_id"] == job.attempt_id
            assert row["evidence"] is None and "seq" not in source
        assert calls == ["finished"]
        assert history(server, job, after=page["next_seq"])[1]["rows"] == []
        assert history(server, job, before=-1)[0] == 400
        assert history(server, job, producer="different")[1]["rows"] == []
        with urllib.request.urlopen(urllib.parse.urljoin(server.url, "reading.js"), timeout=5) as response:
            assert b"const ReadingLayout" in response.read()
        path = manager.archive.path
        server.stop()
        server = None
        manager.close()
        reader_manager = WaveWorkspace(JobManager())
        archived = WaveServer(reader_manager, archive_path=path)
        archived.start()
        try:
            status, saved = history(archived, job)
            assert status == 200 and saved["rows"][0]["data"]["status"] == "rejected"
            assert calls == ["finished"]
            query=urllib.parse.urlencode(dict(view="flow_text", workspace_id=archived._workspace_id,
                                             job_id=job.id, attempt_id=job.attempt_id,seq=saved["rows"][0]["seq"]))
            _, full=request(archived.url.rstrip("/"),"/api/archive?"+query)
            assert json.loads(full["text"])["source"]["data"]["returncode"] == 0
        finally:
            archived.stop()
            reader_manager.close()
    finally:
        if server:
            server.stop()
        manager.close()

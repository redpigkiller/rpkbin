"""Automation action replies persisted in the archive."""

import json
import uuid

import pytest

from rpkbin.flow import ActionOutcome, Automation, Flow, FlowSession


@pytest.mark.parametrize("status", ["accepted", "rejected", "failed"])
def test_automation_redacts_secret_from_returned_receipt_before_persistence(tmp_path, status):
    from rpkbin.wave import RunArchive
    secret = "private-" + uuid.uuid4().hex
    output, receipts = [], []
    store = RunArchive(tmp_path / "receipt.sqlite3", create=True)
    store.save_attempt(dict(id="job", attempt_id="attempt", attempt_number=1))
    def publish(publication):
        output.append(publication)
        assert store.append_flow("attempt", publication)
    def control(text, **kwargs):
        return ActionOutcome(status, "Control result for " + text)
    def react(event, ctx):
        if event.name == "started":
            receipts.append(ctx.actions.send(secret, secret=True))
    engine = FlowSession(Flow("privacy", automations=[Automation("send", on_event=react)]),
                         clock_enabled=True, automation_enabled=True, control={"send": control}, publish=publish)
    try:
        engine.start()
        engine.finish()
        assert engine.wait_automation()
        assert len(receipts) == 1 and receipts[0].status == status
        assert secret not in repr(receipts[0])
        assert "***" in receipts[0].reason
        assert secret not in json.dumps(output)
        store.flush()
        assert secret not in json.dumps(store.automation_history("job", "attempt"))
        assert all(secret.encode() not in p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    finally:
        store.close()


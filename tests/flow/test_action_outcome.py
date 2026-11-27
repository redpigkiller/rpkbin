"""Standalone action failures and secret handling."""

import json
import uuid

from rpkbin.flow import Automation, Flow, FlowSession


def test_automation_secret_exception_does_not_enter_audit_or_receipt():
    secret = "private-" + uuid.uuid4().hex
    receipts, output = [], []
    def broken(text, **kwargs):
        raise OSError("bad write " + text)
    def react(event, ctx):
        if event.name == "started":
            receipts.append(ctx.actions.send(secret, secret=True))
    engine = FlowSession(Flow("privacy", automations=[Automation("send", on_event=react)]),
                         clock_enabled=True, automation_enabled=True, control={"send": broken}, publish=output.append)
    engine.start()
    engine.finish()
    assert engine.wait_automation()
    assert receipts[0].status == "failed"
    assert secret not in json.dumps(output)
    assert secret not in repr(receipts[0])


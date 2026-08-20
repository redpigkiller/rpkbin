from __future__ import annotations

import io
from threading import Event

from rpkbin.debug import (
    AddressSpace,
    BreakpointResult,
    DAPTransport,
    DebugCapabilities,
    DebugLocation,
    DebugScope,
    DebugVariable,
    ExecutionContext,
    InstructionBreakpoint,
    SourceBreakpointResult,
    SourceLocation,
    StopReason,
    StopResult,
)
from rpkbin.debug.dap import DAPServer


def encode_requests(*requests):
    output = io.BytesIO()
    transport = DAPTransport(io.BytesIO(), output)
    for request in requests:
        transport.write_message(request)
    return output.getvalue()


def decode_messages(data):
    transport = DAPTransport(io.BytesIO(data), io.BytesIO())
    messages = []
    while (message := transport.read_message()) is not None:
        messages.append(message)
    return messages


def request(seq, command, **arguments):
    return {
        "seq": seq,
        "type": "request",
        "command": command,
        "arguments": arguments,
    }


class ToyTarget:
    def __init__(self, capabilities=None):
        self.capabilities = capabilities or DebugCapabilities(
            supports_source_breakpoints=True,
            supports_reverse_step=True,
        )
        self.pc = {1: 0, 2: 4}
        self.breakpoints: tuple[InstructionBreakpoint, ...] = ()
        self.closed = False
        self.continue_active = False
        self.last_continue_context = object()
        self.close_saw_active = False

    def address_spaces(self):
        return (AddressSpace("program", 16), AddressSpace("data", 8))

    def contexts(self):
        return (ExecutionContext(1, "worker 1"), ExecutionContext(2, "worker 2"))

    def location(self, context_id):
        return DebugLocation(
            "program",
            self.pc[context_id],
            SourceLocation("toy.asm", self.pc[context_id] + 1),
            f"op {self.pc[context_id]}",
        )

    def scopes(self, context_id):
        return (DebugScope("registers", "Registers"),)

    def variables(self, context_id, scope_id):
        assert scope_id == "registers"
        return (DebugVariable("pc", str(self.pc[context_id]), "address"),)

    def set_instruction_breakpoints(self, breakpoints):
        self.breakpoints = breakpoints
        return tuple(
            BreakpointResult(True, item.address_space, item.address, id=index)
            for index, item in enumerate(breakpoints, 1)
        )

    def set_source_breakpoints(self, path, breakpoints):
        return tuple(
            SourceBreakpointResult(True, path, item.line, id=index)
            for index, item in enumerate(breakpoints, 10)
        )

    def step_instruction(self, context_id):
        self.pc[context_id] += 1
        return StopResult(StopReason.STEP, context_id, self.location(context_id))

    def reverse_step(self, context_id):
        self.pc[context_id] -= 1
        return StopResult(StopReason.STEP, context_id, self.location(context_id))

    def continue_execution(self, context_id, cancel: Event):
        self.last_continue_context = context_id
        self.continue_active = True
        try:
            cancel.wait(1)
            selected = context_id or 1
            return StopResult(StopReason.PAUSE, selected, self.location(selected))
        finally:
            self.continue_active = False

    def close(self):
        self.close_saw_active = self.continue_active
        self.closed = True


class ToyFactory:
    def __init__(self, target=None):
        self.target = target or ToyTarget()
        self.capabilities = self.target.capabilities
        self.supports_attach = False
        self.launch_arguments = None

    def launch(self, arguments):
        self.launch_arguments = arguments
        return self.target


class AttachFactory(ToyFactory):
    def __init__(self, target=None):
        super().__init__(target)
        self.supports_attach = True
        self.attach_arguments = None

    def attach(self, arguments):
        self.attach_arguments = arguments
        return self.target


def run(factory, *requests):
    output = io.BytesIO()
    DAPServer(factory).serve(io.BytesIO(encode_requests(*requests)), output)
    return decode_messages(output.getvalue())


def response(messages, command):
    return next(
        message
        for message in messages
        if message["type"] == "response" and message["command"] == command
    )


def test_dispatches_target_neutral_debug_session():
    factory = ToyFactory()
    messages = run(
        factory,
        request(1, "initialize"),
        request(2, "launch", image="toy.hex"),
        request(3, "configurationDone"),
        request(4, "threads"),
        request(5, "stackTrace", threadId=1),
        request(6, "scopes", frameId=1),
        request(7, "variables", variablesReference=2),
        request(
            8,
            "setInstructionBreakpoints",
            breakpoints=[
                {
                    "instructionReference": "program:0x10",
                    "offset": 2,
                    "condition": "target syntax",
                }
            ],
        ),
        request(
            9,
            "setBreakpoints",
            source={"path": "toy.asm"},
            breakpoints=[{"line": 3, "condition": "opaque"}],
        ),
        request(10, "stepIn", threadId=1, granularity="instruction"),
        request(11, "variables", variablesReference=2),
        request(12, "scopes", frameId=1),
        request(13, "stepBack", threadId=1),
        request(14, "disconnect"),
    )

    assert factory.launch_arguments == {"image": "toy.hex"}
    assert response(messages, "initialize")["body"]["supportsStepBack"] is True
    assert response(messages, "threads")["body"]["threads"][1]["id"] == 2
    frame = response(messages, "stackTrace")["body"]["stackFrames"][0]
    assert frame["instructionPointerReference"] == "program:0x0"
    assert response(messages, "variables")["body"]["variables"][0]["name"] == "pc"
    assert factory.target.breakpoints == (
        InstructionBreakpoint("program", 0x12, "target syntax"),
    )
    assert response(messages, "setBreakpoints")["body"]["breakpoints"][0][
        "verified"
    ]
    variable_responses = [
        message
        for message in messages
        if message.get("type") == "response"
        and message.get("command") == "variables"
    ]
    assert variable_responses[-1]["success"] is False
    scope_responses = [
        message
        for message in messages
        if message.get("type") == "response"
        and message.get("command") == "scopes"
    ]
    assert scope_responses[-1]["success"] is False
    assert [
        message["event"]
        for message in messages
        if message["type"] == "event"
    ].count("stopped") == 3
    launch_index = messages.index(response(messages, "launch"))
    initialized_index = next(
        index
        for index, message in enumerate(messages)
        if message.get("event") == "initialized"
    )
    assert launch_index < initialized_index
    assert factory.target.closed


def test_continue_is_nonblocking_and_pause_is_cooperative():
    factory = ToyFactory()
    messages = run(
        factory,
        request(1, "initialize"),
        request(2, "launch"),
        request(3, "configurationDone"),
        request(4, "continue", threadId=1),
        request(5, "pause", threadId=1),
        request(6, "disconnect"),
    )
    assert response(messages, "continue")["success"]
    assert response(messages, "pause")["success"]
    events = [item for item in messages if item["type"] == "event"]
    assert any(item["event"] == "continued" for item in events)
    assert any(
        item["event"] == "stopped"
        and item["body"]["reason"] == StopReason.PAUSE.value
        for item in events
    )
    assert factory.target.close_saw_active is False


def test_multi_context_continue_is_global_unless_target_advertises_single_thread():
    factory = ToyFactory()
    messages = run(
        factory,
        request(1, "initialize"),
        request(2, "launch"),
        request(3, "configurationDone"),
        request(4, "continue", threadId=2),
        request(5, "pause", threadId=1),
        request(6, "disconnect"),
    )
    assert factory.target.last_continue_context is None
    assert response(messages, "continue")["body"]["allThreadsContinued"] is True
    assert response(messages, "initialize")["body"][
        "supportsSingleThreadExecutionRequests"
    ] is False


def test_global_continue_still_validates_required_dap_thread_id():
    messages = run(
        ToyFactory(),
        request(1, "initialize"),
        request(2, "launch"),
        request(3, "configurationDone"),
        request(4, "continue"),
        request(5, "continue", threadId=99),
        request(6, "disconnect"),
    )
    continue_responses = [
        message
        for message in messages
        if message.get("type") == "response"
        and message.get("command") == "continue"
    ]
    assert len(continue_responses) == 2
    assert all(not message["success"] for message in continue_responses)
    assert "positive integer" in continue_responses[0]["message"]
    assert "unknown threadId" in continue_responses[1]["message"]


def test_single_context_continue_is_forwarded_only_when_advertised():
    capabilities = DebugCapabilities(
        supports_source_breakpoints=True,
        supports_reverse_step=True,
        supports_single_thread_execution=True,
    )
    factory = ToyFactory(ToyTarget(capabilities))
    messages = run(
        factory,
        request(1, "initialize"),
        request(2, "launch"),
        request(3, "configurationDone"),
        request(4, "continue", threadId=2),
        request(5, "pause", threadId=2),
        request(6, "disconnect"),
    )
    assert factory.target.last_continue_context == 2
    assert response(messages, "continue")["body"]["allThreadsContinued"] is False


def test_optional_attach_positive_path():
    factory = AttachFactory()
    messages = run(
        factory,
        request(1, "initialize"),
        request(2, "attach", endpoint="local"),
        request(3, "configurationDone"),
        request(4, "disconnect"),
    )
    assert response(messages, "attach")["success"]
    assert factory.attach_arguments == {"endpoint": "local"}


def test_stop_on_entry_false_starts_running_after_configuration():
    factory = ToyFactory()
    messages = run(
        factory,
        request(1, "initialize"),
        request(2, "launch", stopOnEntry=False),
        request(3, "configurationDone"),
        request(4, "pause", threadId=1),
        request(5, "disconnect"),
    )
    events = [item for item in messages if item["type"] == "event"]
    assert not any(
        item.get("body", {}).get("reason") == StopReason.ENTRY.value
        for item in events
    )
    assert any(item["event"] == "continued" for item in events)
    assert any(
        item.get("body", {}).get("reason") == StopReason.PAUSE.value
        for item in events
    )


def test_unsupported_requests_and_optional_capabilities_fail_closed():
    capabilities = DebugCapabilities(
        supports_instruction_breakpoints=False,
        supports_pause=False,
    )
    factory = ToyFactory(ToyTarget(capabilities))
    messages = run(
        factory,
        request(1, "initialize"),
        request(2, "launch"),
        request(
            3,
            "setBreakpoints",
            source={"path": "toy.asm"},
            breakpoints=[{"line": 7}],
        ),
        request(4, "next", threadId=1),
        request(5, "readMemory", memoryReference="data:0x0"),
        request(6, "attach"),
        request(7, "disconnect"),
    )
    source_result = response(messages, "setBreakpoints")["body"]["breakpoints"][0]
    assert source_result["verified"] is False
    for command in ("next", "readMemory", "attach"):
        result = response(messages, command)
        assert result["success"] is False
    assert "unsupported request: next" in response(messages, "next")["message"]


class DuplicateContextTarget(ToyTarget):
    def contexts(self):
        return (ExecutionContext(1, "one"), ExecutionContext(1, "duplicate"))


class BadStopTarget(ToyTarget):
    def step_instruction(self, context_id):
        return StopResult(
            StopReason.EXCEPTION,
            context_id,
            DebugLocation("unknown", 0),
        )


def test_invalid_target_data_is_a_failed_response_not_a_server_crash():
    duplicate_messages = run(
        ToyFactory(DuplicateContextTarget()),
        request(1, "initialize"),
        request(2, "launch"),
        request(3, "disconnect"),
    )
    assert response(duplicate_messages, "launch")["success"] is False
    assert "unique" in response(duplicate_messages, "launch")["message"]

    bad_stop_messages = run(
        ToyFactory(BadStopTarget()),
        request(1, "initialize"),
        request(2, "launch"),
        request(3, "configurationDone"),
        request(4, "stepIn", threadId=1),
        request(5, "disconnect"),
    )
    assert response(bad_stop_messages, "stepIn")["success"] is False
    assert "unknown address space" in response(bad_stop_messages, "stepIn")[
        "message"
    ]


def test_exception_stop_may_omit_location():
    class FaultTarget(ToyTarget):
        def step_instruction(self, context_id):
            return StopResult(
                StopReason.EXCEPTION,
                context_id,
                None,
                "decode failed",
            )

    messages = run(
        ToyFactory(FaultTarget()),
        request(1, "initialize"),
        request(2, "launch"),
        request(3, "configurationDone"),
        request(4, "stepIn", threadId=1),
        request(5, "disconnect"),
    )
    assert response(messages, "stepIn")["success"]
    stopped = next(
        message
        for message in messages
        if message.get("event") == "stopped"
        and message["body"]["reason"] == "exception"
    )
    assert stopped["body"]["reason"] == "exception"


def test_internal_watchpoint_uses_standard_dap_data_breakpoint_reason():
    class WatchTarget(ToyTarget):
        def step_instruction(self, context_id):
            return StopResult(
                StopReason.WATCHPOINT,
                context_id,
                self.location(context_id),
            )

    messages = run(
        ToyFactory(WatchTarget()),
        request(1, "initialize"),
        request(2, "launch"),
        request(3, "configurationDone"),
        request(4, "stepIn", threadId=1),
        request(5, "disconnect"),
    )
    stopped = [
        message
        for message in messages
        if message.get("event") == "stopped"
    ]
    assert stopped[-1]["body"]["reason"] == "data breakpoint"


def test_continue_exception_has_output_and_exception_stop():
    class RaisingTarget(ToyTarget):
        def continue_execution(self, context_id, cancel):
            raise RuntimeError("toy failure")

    messages = run(
        ToyFactory(RaisingTarget()),
        request(1, "initialize"),
        request(2, "launch", stopOnEntry=False),
        request(3, "configurationDone"),
        request(4, "disconnect"),
    )
    assert any(
        message.get("event") == "output"
        and "toy failure" in message["body"]["output"]
        for message in messages
    )
    assert any(
        message.get("event") == "stopped"
        and message["body"]["reason"] == StopReason.EXCEPTION.value
        for message in messages
    )
    assert not any(message.get("event") == "terminated" for message in messages)

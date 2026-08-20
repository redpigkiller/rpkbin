import io
import json

import pytest

from rpkbin.debug import DAPProtocolError, DAPTransport


def frame(message):
    payload = json.dumps(message).encode()
    return f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload


class PartialReader(io.BytesIO):
    def read(self, size=-1):
        if size < 0:
            size = 3
        return super().read(min(size, 3))


def test_partial_payload_reads_and_multiple_messages():
    one = {"seq": 1, "type": "request", "command": "initialize"}
    two = {"seq": 2, "type": "request", "command": "threads"}
    transport = DAPTransport(PartialReader(frame(one) + frame(two)), io.BytesIO())
    assert transport.read_message() == one
    assert transport.read_message() == two
    assert transport.read_message() is None


def test_writer_uses_utf8_byte_length():
    output = io.BytesIO()
    DAPTransport(io.BytesIO(), output).write_message({"text": "測試"})
    header, payload = output.getvalue().split(b"\r\n\r\n", 1)
    assert int(header.split(b":", 1)[1]) == len(payload)
    assert json.loads(payload) == {"text": "測試"}


@pytest.mark.parametrize(
    "data,match",
    [
        (b"X: 1\r\n\r\n", "Content-Length"),
        (b"Content-Length: nope\r\n\r\n", "invalid Content-Length"),
        (b"Content-Length: 2\r\n\r\n{}", None),
        (b"Content-Length: 3\r\n\r\n{}", "unexpected EOF"),
    ],
)
def test_malformed_frames_fail_closed(data, match):
    transport = DAPTransport(io.BytesIO(data), io.BytesIO())
    if match is None:
        assert transport.read_message() == {}
    else:
        with pytest.raises(DAPProtocolError, match=match):
            transport.read_message()

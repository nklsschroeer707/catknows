"""Self-check: send_dm's attachment lookup must not list /self/chat-channels.

Skool refuses limit > 30 on /self/chat-channels ("invalid limit: 100",
Dan's 2026-08-15 report), and an account can hold hundreds of channels —
so ANY one-shot listing either crashes or silently misses recipients.
The group_id now comes from the single-channel messages endpoint.

Run: python test_send_dm_attachment_lookup.py    (no network, no pytest)
"""

import inspect
import os

os.environ["CATKNOWS_ALLOW_WRITE"] = "1"  # send_dm only registers in WRITE mode

from catknows import mcp_server
from catknows.client import SkoolClient
from catknows.http import SkoolHTTPError

_tool = mcp_server.send_dm
send_dm = _tool if callable(_tool) else (getattr(_tool, "fn", None) or _tool.func)


def _client(channel_exists=True):
    """A SkoolClient whose transport records every api2 URL it is asked for."""
    c = SkoolClient.__new__(SkoolClient)  # no login, no real session
    calls = []

    class HTTP:
        def get_api2(self, q):
            calls.append(q)
            if not channel_exists:
                raise SkoolHTTPError("HTTP 404: no such channel", 404)
            return {"messages": [], "channel": {"id": "ch1", "group_id": "g-1"}}

        def post_api2(self, q, body):
            calls.append(q)
            return {"id": "m1", "metadata": {"attachments": "f-1"}}

    c.http = HTTP()
    c.uploaded = []
    c.upload_file = lambda gid, path: (c.uploaded.append((gid, path)) or {"id": "f-1"})
    return c, calls


def test_lookup_uses_single_channel_endpoint():
    c, calls = _client()
    mcp_server._client = c
    out = send_dm(channel_id="ch1", content="hi", attachments="x.gif", confirm=True)
    assert out["status"] == "sent", out
    assert c.uploaded == [("g-1", "x.gif")], c.uploaded  # group_id reached the upload
    assert any("/channels/ch1/messages" in q for q in calls), calls
    # The regression this file exists for: no channel listing on this path.
    assert not any("chat-channels" in q for q in calls), calls


def test_unknown_channel_raises_before_sending():
    c, calls = _client(channel_exists=False)
    mcp_server._client = c
    try:
        send_dm(channel_id="nope", content="hi", attachments="x.gif", confirm=True)
    except ValueError as e:
        assert "nope" in str(e), e
        assert not any("ct=wdm" in q for q in calls), calls  # nothing was sent
        return
    raise AssertionError("an unknown channel must raise, not send")


def test_chat_channels_default_within_skool_ceiling():
    limit = inspect.signature(SkoolClient.chat_channels).parameters["limit"].default
    assert limit <= 30, f"Skool refuses limit > 30, default is {limit}"


if __name__ == "__main__":
    test_lookup_uses_single_channel_endpoint()
    test_unknown_channel_raises_before_sending()
    test_chat_channels_default_within_skool_ceiling()
    print("ok — attachment lookup resolves the channel directly, within Skool's limits")

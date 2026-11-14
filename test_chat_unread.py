"""Self-check: unread DMs must come back with chat_messages.

catnose 2026-11-01 (two members): with unread messages Skool's opening window
`before=N` ends at our own last read position and says `has_more_after`.
chat_messages never walked forward, so exactly the unread messages were
missing and the autopilot never answered them.

Run: python test_chat_unread.py    (no network, no pytest)
"""

import catknows.client as cl
from catknows.client import SkoolClient

cl._INTER_PAGE_DELAY_S = 0


def _m(i):
    return {"id": f"m{i}", "created_at": f"2026-10-31T10:{i:02d}:00Z"}


class FakeHttp:
    def __init__(self, read_upto, total):
        self.read_upto, self.total, self.calls = read_upto, total, []

    def get_api2(self, path):
        self.calls.append(path)
        if "msg=" in path:
            i = int(path.split("msg=m")[1])
            hi = min(self.total, i + 50)
            return {"messages": [_m(k) for k in range(i, hi)], "has_more_after": hi < self.total,
                    "has_more_before": False}
        return {"messages": [_m(k) for k in range(max(0, self.read_upto - 10), self.read_upto)],
                "has_more_after": self.read_upto < self.total, "has_more_before": self.read_upto > 10}


def _client(http):
    c = SkoolClient.__new__(SkoolClient)
    c.http = http
    return c


def test_unread_tail_is_fetched():
    out = _client(FakeHttp(read_upto=8, total=11)).chat_messages("k", count=10)
    ids = [m["id"] for m in out["messages"]]
    assert ids[-1] == "m10", ids
    assert ids == sorted(ids, key=lambda s: int(s[1:])), ids


def test_all_read_needs_one_call():
    http = FakeHttp(read_upto=11, total=11)
    out = _client(http).chat_messages("k", count=10)
    assert len(http.calls) == 1 and out["messages"][-1]["id"] == "m10"


if __name__ == "__main__":
    test_unread_tail_is_fetched()
    test_all_read_needs_one_call()
    print("ok")

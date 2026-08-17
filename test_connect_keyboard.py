"""The streamed login must be typable on a touchscreen.

Sean Hegarty (2026-08-15) could not use the on-screen keyboard on
catknows.app/connect. Cause: input hung off the screencast `<img>`, and a mobile
browser opens its keyboard only for an editable element, so a focused image gets
no keyboard at all.

This drives the real page JS in a real browser with the WebSocket stubbed, and
asserts on what would go over the wire. It cannot raise an OS keyboard — no
headless browser can — so it checks the two things that decide whether one
appears and whether its output arrives: the focus target is an <input>, and
`input` events become insertText.

    .venv/Scripts/python -m pytest test_connect_keyboard.py     # or run directly
"""

from __future__ import annotations

import json
import re

from playwright.sync_api import sync_playwright

import catknows.dashboard as dashboard

# Stubs the socket and records every message the page tries to send, so the
# assertions below read exactly what the server would have received.
HARNESS = """
window.__sent = [];
window.WebSocket = function(){
  this.readyState = 1;
  window.__ws = this;
  var self = this;
  // Wait until the page has attached its handlers before delivering `ready`;
  // the real socket cannot answer sooner than the next tick either.
  var wait = setInterval(function(){
    if(!self.onmessage) return;
    clearInterval(wait);
    self.onmessage({data: JSON.stringify({type:'ready', width:1280, height:800})});
  }, 5);
};
window.WebSocket.prototype.send = function(m){ window.__sent.push(JSON.parse(m)); };
"""


def _page(pw, touch: bool):
    browser = pw.chromium.launch()
    ctx = browser.new_context(
        has_touch=touch,
        is_mobile=touch,
        viewport={"width": 390, "height": 844} if touch else {"width": 1280, "height": 800},
    )
    page = ctx.new_page()
    page.add_init_script(HARNESS)
    # A real origin, not set_content: the page builds its socket URL from
    # location.protocol/host, which on about:blank throws before the stub runs.
    html = dashboard._page_connect("sean@example.com", stored=False)
    page.route(
        "https://catknows.test/connect",
        lambda route: route.fulfill(status=200, content_type="text/html", body=html),
    )
    page.goto("https://catknows.test/connect")
    page.click("#go")
    page.wait_for_selector("#stage", state="visible")
    return browser, page


def _sent(page) -> list[dict]:
    return page.evaluate("window.__sent")


def test_focus_target_is_editable():
    """The keyboard only opens for an editable element, so tapping must focus one."""
    with sync_playwright() as pw:
        browser, page = _page(pw, touch=True)
        try:
            page.tap("#screen")
            focused = page.evaluate(
                "document.activeElement.tagName + '#' + document.activeElement.id"
            )
            assert focused == "INPUT#kbd", f"focus landed on {focused}, no keyboard there"
        finally:
            browser.close()


def test_typing_sends_text():
    """What a soft keyboard produces — input events — must reach the server."""
    with sync_playwright() as pw:
        browser, page = _page(pw, touch=True)
        try:
            page.tap("#screen")
            page.keyboard.type("hunter2")

            texts = [m["text"] for m in _sent(page) if m["type"] == "insertText"]
            assert "".join(texts) == "hunter2", f"got {texts!r}"
        finally:
            browser.close()


def test_printable_characters_are_not_sent_twice():
    """keydown and input both fire; only one of them may carry the character."""
    with sync_playwright() as pw:
        browser, page = _page(pw, touch=False)
        try:
            page.click("#screen")
            page.keyboard.type("ab")

            typed = "".join(
                m.get("text", "")
                for m in _sent(page)
                if m["type"] in ("insertText", "keyDown")
            )
            assert typed == "ab", f"duplicated or lost input: {typed!r}"
        finally:
            browser.close()


def test_special_keys_still_go_as_key_events():
    """Enter, Tab and Backspace have no printable text; insertText cannot carry them."""
    with sync_playwright() as pw:
        browser, page = _page(pw, touch=False)
        try:
            page.click("#screen")
            page.keyboard.press("Tab")
            page.keyboard.press("Enter")
            page.keyboard.press("Backspace")

            keys = [m["key"] for m in _sent(page) if m["type"] == "keyDown"]
            assert keys == ["Tab", "Enter", "Backspace"], f"got {keys!r}"
        finally:
            browser.close()


def test_soft_keyboard_backspace_becomes_a_key():
    """Android reports backspace as deleteContentBackward, with no key event."""
    with sync_playwright() as pw:
        browser, page = _page(pw, touch=True)
        try:
            page.tap("#screen")
            # Exactly what a soft keyboard delete emits: an input event with that
            # inputType and no accompanying keydown.
            page.evaluate("""
              var k = document.getElementById('kbd');
              k.dispatchEvent(new InputEvent('input',
                {inputType:'deleteContentBackward', bubbles:true}));
            """)

            keys = [m["key"] for m in _sent(page) if m["type"] == "keyDown"]
            assert keys == ["Backspace"], f"got {keys!r}"
        finally:
            browser.close()


def test_field_never_shows_what_was_typed():
    """It sits over the stream; a visible password there would be a leak."""
    with sync_playwright() as pw:
        browser, page = _page(pw, touch=True)
        try:
            page.tap("#screen")
            page.keyboard.type("hunter2")
            assert page.input_value("#kbd") == "", "the hidden field kept the text"
        finally:
            browser.close()


def test_server_accepts_insert_text():
    """The wire format the page now emits must exist on the server side."""
    source = (
        __import__("pathlib").Path(__file__).parent / "catknows" / "remote_login.py"
    ).read_text(encoding="utf-8")
    assert re.search(r'kind == "insertText"', source), "server has no insertText branch"
    assert "Input.insertText" in source, "insertText is not dispatched to CDP"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")

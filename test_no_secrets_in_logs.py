"""A password must never reach a log through the address line.

Found 2026-11-23: the landing's sign-in form had no method, so a submit
without the script became `GET /?email=…&password=…`, and both uvicorn and
Caddy wrote that line down. Run: python test_no_secrets_in_logs.py
"""
import logging
import re
from pathlib import Path

from catknows import dashboard

ROOT = Path(__file__).parent

# -- the form: a native submit carries the fields in the body ------------------
page = (dashboard.WEB / "index.html").read_text()
form = re.search(r'<form id="login-form"[^>]*>', page)
assert form, "sign-in form not found"
assert 'method="post"' in form[0], "without method=post a scriptless submit puts the password in the URL"
for tag in re.findall(r"<form[^>]*>", page):
    assert 'method="post"' in tag, f"a form on the landing page would submit by GET: {tag}"

# -- uvicorn: the access line stops at the question mark -----------------------
cfg = dashboard.access_log_config()
assert "no_query" in cfg["handlers"]["access"]["filters"], "the access handler must drop query strings"
flt = cfg["filters"]["no_query"]["()"]()


def line(path: str) -> str:
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:1", "GET", path, "1.1", 200), None)
    assert flt.filter(rec) is not False, "the line itself must still be logged"
    return rec.getMessage()


out = line("/?email=a%40b.c&password=hunter2")
assert "hunter2" not in out and "a%40b.c" not in out, out
assert '"GET /?' in out, "keep the path and a mark that something was cut"
assert "abc123" not in line("/auth/callback?code=abc123&state=x"), "OAuth codes are secrets too"
assert line("/connect") == '127.0.0.1:1 - "GET /connect HTTP/1.1" 200', "plain paths are untouched"
# Anything unexpected in the record must not break logging.
odd = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "plain", None, None)
assert flt.filter(odd) is not False

# -- Caddy: every site cuts the query from URI and Referer ---------------------
caddy = (ROOT / "deploy" / "Caddyfile").read_text()
blocks = re.findall(r"format filter \{.*?\n\t\t\}", caddy, re.S)
assert len(blocks) == 3, "expected one log filter per site"
for b in blocks:
    assert 'request>uri regexp "\\?.*" "?[cut]"' in b, "Caddy must cut the query string from the logged URI"
    assert "request>headers>Referer" in b, "a Referer can carry the same query"

print("no secrets in logs OK (form posts, uvicorn and Caddy cut the query)")

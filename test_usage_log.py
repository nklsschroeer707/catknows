"""One accepted request must leave one line naming the account.

Caddy logs an empty user_id on purpose: the identity is in the bearer token and
that header is stripped before writing. Without the line this test guards, the
hosted service can say how much it is used but not by whom.

Covered by deploy/PRIVACY.md §2.5 — if this assertion changes, that section has
to change with it.

    .venv/Scripts/python -m pytest test_usage_log.py      # or run directly
"""

from __future__ import annotations

import io
import time
from contextlib import redirect_stderr

from catknows.auth_oauth import KeycloakVerifier


class _FakeJWKS:
    """Stands in for the network. The signature path is jwt's job, not ours."""

    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": "test-signing-key-at-least-32-bytes-long!"})()


def _verifier(monkey_claims: dict) -> tuple[KeycloakVerifier, str]:
    v = KeycloakVerifier.__new__(KeycloakVerifier)
    v.issuer = "https://auth.catknows.app/realms/catknows"
    v.audience = "catknows-mcp"
    v.required_scope = ""
    v._jwks = _FakeJWKS()

    import jwt as pyjwt

    token = pyjwt.encode(monkey_claims, "test-signing-key-at-least-32-bytes-long!", algorithm="HS256")
    # The verifier pins RS256/ES256, so decode the HS256 test token by patching
    # only the algorithm list — everything else stays the real code path.
    orig = pyjwt.decode

    def decode(tok, key, **kw):
        kw["algorithms"] = ["HS256"]
        return orig(tok, key, **kw)

    pyjwt.decode = decode
    return v, token


def _claims(**over) -> dict:
    now = int(time.time())
    base = {
        "sub": "5f2c-test-subject",
        "iss": "https://auth.catknows.app/realms/catknows",
        "aud": "catknows-mcp",
        "exp": now + 600,
        "iat": now,
        "email_verified": True,
        "azp": "claude-ai",
        "scope": "openid email mcp:tools",
    }
    base.update(over)
    return base


def test_accepted_request_names_the_subject():
    v, token = _verifier(_claims())
    err = io.StringIO()
    with redirect_stderr(err):
        result = v._verify_sync(token)

    assert result is not None, "a valid token was refused"
    line = err.getvalue()
    assert "serving subject 5f2c-test-subject" in line, f"no usage line: {line!r}"
    assert "claude-ai" in line, f"client not named: {line!r}"


def test_refused_request_is_not_counted_as_usage():
    """An unverified account must not appear as a served request."""
    v, token = _verifier(_claims(email_verified=False))
    err = io.StringIO()
    with redirect_stderr(err):
        result = v._verify_sync(token)

    assert result is None, "an unverified account was served"
    out = err.getvalue()
    assert "refusing subject" in out, f"refusal not logged: {out!r}"
    assert "serving subject" not in out, "a refused request was logged as served"


def test_the_line_carries_no_token():
    """It goes to a log; a bearer token in there is a replayable credential."""
    v, token = _verifier(_claims())
    err = io.StringIO()
    with redirect_stderr(err):
        v._verify_sync(token)

    assert token not in err.getvalue(), "the token itself was written to the log"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")

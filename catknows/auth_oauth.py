"""OAuth token verification for the hosted MCP server.

The MCP transport has no identity of its own — anything reaching it is
trusted. This module is what turns "knows the URL" into "proved who they
are": every request must carry a JWT that our Keycloak realm signed, for
*this* server, still valid.

Only used when CATKNOWS_OAUTH_ISSUER is set. Local stdio runs are unaffected.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from mcp.server.auth.provider import AccessToken, TokenVerifier


# The claim a Keycloak account must carry to use the hosted service. Set as a
# user attribute in the admin console; deploy/keycloak/setup-realm.sh puts the
# mapper that carries it into the token.
#
# A user attribute rather than a realm role. The role looked broken on
# 2026-08-13 — no realm_access claim in the token — but it was not: roles simply
# do not ride in a DCR client's token, which gets `basic` and nothing else, and
# that was the only client anyone measured. What actually blocked both designs
# was the realm's user profile, which must declare an attribute before it can be
# stored at all; until then the mapper carried nothing and every request was
# refused here.
#
# The attribute stays, because it does not depend on realm_access reaching a DCR
# token. Granting it: deploy/keycloak/grant-service.sh.
SERVICE_CLAIM = "catknows_service"


def may_use_service(claims: dict[str, Any]) -> str:
    """Is this account entitled to the hosted service? Empty string means yes.

    Separate from proving *who* someone is, which the signature, audience and
    issuer checks already settled. This answers the next question — may they be
    here at all — and it is the whole reason registration can be open: signing
    up is free, using the service is not automatic.

    One condition today:

    * ``email_verified`` — an unconfirmed address is an unowned address, and
      account recovery goes to it.

    ``catknows_service`` used to gate this too: signing up was open, using the
    service was the operator's manual yes (``deploy/keycloak/grant-service.sh``).
    Dropped 2026-08-14 — a per-account approval nobody performs is not a gate,
    it is a dead end, and it stranded users who had done every step asked of
    them. Sign-up is now self-service end to end.

    This stays the seam where the paid check belongs once catknows bills through
    Skool: put the membership lookup here and nothing else moves. The claim is
    still minted (see setup-realm.sh) and ``_truthy`` still reads Keycloak's
    three encodings, so re-arming it is one ``if`` — but it must come back with
    something that grants it automatically, not by hand.

    Returns a short reason on refusal rather than a bool, because the caller
    logs it — "which check failed" is the difference between a two-minute
    diagnosis and an afternoon.
    """
    if not claims.get("email_verified"):
        return "email not verified"
    return ""


def _truthy(value: Any) -> bool:
    """Keycloak may deliver the attribute as bool, string, or single-item list.

    A user attribute is stored as text, so it usually arrives as "true"; with a
    jsonType of boolean it arrives as a real bool; multivalued mappers wrap it
    in a list. Anything else — absent, "false", empty — is not a yes.

    No caller since the manual entitlement gate came out (2026-08-14). Kept, and
    kept under test below, because the Skool-billing check will read a Keycloak
    attribute the same three ways — this is the part of that gate that was
    right.
    """
    if isinstance(value, list):
        return any(_truthy(v) for v in value)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


class KeycloakVerifier(TokenVerifier):
    """Verify bearer tokens against a Keycloak realm's public keys.

    Keys come from the realm's JWKS endpoint and are cached by pyjwt, so a
    rotated signing key is picked up without a restart.
    """

    def __init__(self, issuer: str, audience: str, required_scope: str = "") -> None:
        from jwt import PyJWKClient

        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.required_scope = required_scope
        self._jwks = PyJWKClient(
            f"{self.issuer}/protocol/openid-connect/certs",
            cache_keys=True,
            lifespan=300,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return the token's claims, or None if it must not be honoured.

        Returns None rather than raising on every failure path: a malformed
        token is a 401, not a server error, and the caller shouldn't have to
        tell "expired" from "forged" to answer the request.
        """
        import anyio

        # The JWKS fetch and the RSA verify are blocking; on the event loop
        # they would stall every other request during a key refresh.
        return await anyio.to_thread.run_sync(self._verify_sync, token)

    def _verify_sync(self, token: str) -> AccessToken | None:
        import jwt

        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=["RS256", "ES256"],  # never "none", never HS* — see below
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except Exception:
            # Signature, expiry, audience and issuer are all checked above.
            # Audience matters as much as the signature here: our realm signs
            # tokens for every client it serves, so a valid signature alone
            # would let a token minted for some other app act as this one.
            return None

        # Keycloak puts scopes in a space-delimited "scope" string.
        scopes = (claims.get("scope") or "").split()
        if self.required_scope and self.required_scope not in scopes:
            return None

        # Proved who they are; now, may they use this? A genuine token from a
        # genuine account is still refused here when the account isn't cleared.
        if reason := may_use_service(claims):
            # Logged, not returned: the caller gets a plain 401 either way, and
            # spelling out which check failed to an unauthenticated caller is
            # free reconnaissance. The operator can read it here.
            print(
                f"catknows: refusing subject {claims.get('sub')} — {reason}",
                file=sys.stderr,
            )
            return None

        # The counterpart to the refusal above, and the only place per-person
        # usage is visible: Caddy logs an empty user_id because the identity
        # sits in the bearer token, which it deliberately does not write down.
        # This counts requests per subject, not tool calls — the verifier never
        # sees which tool was asked for. Covered by deploy/PRIVACY.md.
        print(
            f"catknows: serving subject {claims.get('sub')} "
            f"via {claims.get('azp') or claims.get('client_id') or 'unknown client'}",
            file=sys.stderr,
        )

        return AccessToken(
            token=token,
            client_id=claims.get("azp") or claims.get("client_id") or "",
            subject=claims.get("sub"),
            scopes=scopes,
            expires_at=claims.get("exp"),
            resource=self.audience,
            claims=claims,
        )


def verifier_from_env() -> KeycloakVerifier | None:
    """Build the verifier from the environment, or None if OAuth is off."""
    issuer = os.environ.get("CATKNOWS_OAUTH_ISSUER", "").strip()
    if not issuer:
        return None
    audience = os.environ.get("CATKNOWS_OAUTH_AUDIENCE", "").strip()
    if not audience:
        raise RuntimeError(
            "CATKNOWS_OAUTH_ISSUER is set but CATKNOWS_OAUTH_AUDIENCE is not. "
            "Without an audience any token this realm signed would be accepted, "
            "including ones minted for other clients."
        )
    return KeycloakVerifier(
        issuer=issuer,
        audience=audience,
        required_scope=os.environ.get("CATKNOWS_OAUTH_SCOPE", "").strip(),
    )


def _self_check() -> None:
    """python -m catknows.auth_oauth — verifies the checks actually reject."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    issuer = "https://auth.example.app/realms/t"
    audience = "https://mcp.example.app/mcp"
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    v = KeycloakVerifier.__new__(KeycloakVerifier)  # no network: skip __init__
    v.issuer, v.audience, v.required_scope = issuer, audience, "mcp:tools"

    class _Key:
        def __init__(self, k): self.key = k

    v._jwks = type("J", (), {"get_signing_key_from_jwt": lambda s, t: _Key(priv.public_key())})()

    now = int(time.time())
    base = {"iss": issuer, "aud": audience, "sub": "user-1", "iat": now,
            "exp": now + 300, "scope": "openid mcp:tools", "azp": "client-x",
            "email_verified": True, "catknows_service": "true"}
    sign = lambda c, k=priv: jwt.encode(c, k, algorithm="RS256")

    ok = v._verify_sync(sign(base))
    assert ok is not None and ok.subject == "user-1", "a valid token must pass"
    assert "mcp:tools" in ok.scopes, ok.scopes

    assert v._verify_sync(sign({**base, "exp": now - 1})) is None, "expired must fail"
    assert v._verify_sync(sign({**base, "aud": "https://elsewhere/mcp"})) is None, \
        "wrong audience must fail — this is what stops another client's token"
    assert v._verify_sync(sign({**base, "iss": "https://evil.example"})) is None, \
        "wrong issuer must fail"
    assert v._verify_sync(sign({**base, "scope": "openid"})) is None, \
        "missing required scope must fail"
    base_no_exp = {k: val for k, val in base.items() if k != "exp"}
    assert v._verify_sync(sign(base_no_exp)) is None, "a token without exp must fail"

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert v._verify_sync(sign(base, other)) is None, "another key's signature must fail"

    assert v._verify_sync("not-a-jwt") is None, "garbage must fail"
    assert v._verify_sync(jwt.encode(base, None, algorithm="none")) is None, \
        "alg=none must never be honoured"

    # Entitlement: a confirmed address is the whole check. An unconfirmed one
    # is an unowned one — anyone could sign up as anyone — so this must hold
    # even though the service is otherwise self-service.
    assert v._verify_sync(sign({**base, "email_verified": False})) is None, \
        "an unverified email must fail — anyone could claim someone else's address"
    base_no_email = {k: val for k, val in base.items() if k != "email_verified"}
    assert v._verify_sync(sign(base_no_email)) is None, \
        "a missing email_verified claim must fail closed, not be assumed true"

    # Sign-up is self-service since 2026-08-14: a fresh account with a confirmed
    # address reaches the tools with no operator step in between. The old manual
    # grant stranded users who had done everything asked of them.
    base_no_grant = {k: val for k, val in base.items() if k != SERVICE_CLAIM}
    assert v._verify_sync(sign(base_no_grant)) is not None, \
        "a fresh confirmed signup must pass — no hand-granted attribute required"
    assert may_use_service(base_no_grant) == "", \
        "no manual entitlement may stand between signup and the tools"
    assert may_use_service({**base, SERVICE_CLAIM: "false"}) == "", \
        "a leftover false from the old gate must not lock anyone out"

    # ...and the one refusal that remains is diagnosable.
    assert may_use_service({**base, "email_verified": False}) == "email not verified"
    assert may_use_service(base) == "", "a confirmed account must pass"

    # _truthy has no caller today; the Skool-billing check will need it to read
    # a Keycloak attribute in all three encodings, so it stays verified.
    for yes in ("true", True, ["true"], "True", "1"):
        assert _truthy(yes), repr(yes)
    for no in ("false", False, [], [""], None, "", "0", "no"):
        assert not _truthy(no), repr(no)

    # The SDK awaits verify_token; a sync def there returns None and the
    # request dies as "'NoneType' object can't be awaited" — a 500, not a 401.
    import inspect
    assert inspect.iscoroutinefunction(KeycloakVerifier.verify_token),         "verify_token must be async — the SDK awaits it"

    print("auth_oauth self-check OK (13 rejection paths + entitlement + async contract)")


if __name__ == "__main__":
    _self_check()

"""OAuth token verification for the hosted MCP server.

The MCP transport has no identity of its own — anything reaching it is
trusted. This module is what turns "knows the URL" into "proved who they
are": every request must carry a JWT that our Keycloak realm signed, for
*this* server, still valid.

Only used when CATKNOWS_OAUTH_ISSUER is set. Local stdio runs are unaffected.
"""

from __future__ import annotations

import os
import time
from typing import Any

from mcp.server.auth.provider import AccessToken, TokenVerifier


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
            "exp": now + 300, "scope": "openid mcp:tools", "azp": "client-x"}
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

    # The SDK awaits verify_token; a sync def there returns None and the
    # request dies as "'NoneType' object can't be awaited" — a 500, not a 401.
    import inspect
    assert inspect.iscoroutinefunction(KeycloakVerifier.verify_token),         "verify_token must be async — the SDK awaits it"

    print("auth_oauth self-check OK (9 rejection paths + async contract)")


if __name__ == "__main__":
    _self_check()

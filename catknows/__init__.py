"""catknows — an unofficial, documented client for Skool's private API.

    from catknows import SkoolClient, login

    client = SkoolClient(login())
    members = client.members("my-community")

See docs/API.md for the full endpoint reference. This talks to Skool's
undocumented internal endpoints by driving a real browser session — it is not
an official API and may break when Skool changes their frontend.

ᓚᘏᗢ
"""

__version__ = "0.1.0"
__all__ = [
    "SkoolClient",
    "Session",
    "login",
    "session_from_cookie",
    "SkoolHTTPError",
]

# `normalize` and `vault` are stdlib-only on purpose (pure JSON→record→Markdown).
# The client/http/auth layer needs `requests` + `playwright`. Import the heavy
# names lazily so `from catknows import normalize` works before those deps are
# installed — and so `python -m catknows.normalize` (the self-check) runs clean.
def __getattr__(name: str):
    if name in ("SkoolClient",):
        from .client import SkoolClient
        return SkoolClient
    if name in ("Session", "login", "session_from_cookie"):
        from . import auth
        return getattr(auth, name)
    if name == "SkoolHTTPError":
        from .http import SkoolHTTPError
        return SkoolHTTPError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

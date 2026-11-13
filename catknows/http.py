"""The low-level Skool HTTP client — WAF-faithful requests + buildId discovery.

Everything that makes Skool's private API answer instead of returning 403.
The public API methods (members, posts, comments, ...) live in `client.py` and
call through this layer.

Two request "shapes", because Skool has two backends:
  - Next.js data endpoints  (www.skool.com/_next/data/{buildId}/...)  -> `get_next`
  - api2.skool.com endpoints (comments, likes, groups, ...)           -> `get_api2`

The header sets differ. The Next.js shape sends `x-nextjs-data: 1`; the api2
shape sends `Authorization: Bearer` + `Origin` + the `x-aws-waf-token` header.
Getting the WAF token or the TLS handshake wrong is the difference between 200
and a WAF 403; the Bearer header is catknows' own and whether api2 needs it is
open (docs/API.md §0.1).

Headers alone are not enough for api2: AWS WAF fingerprints the TLS handshake
(JA3/JA4), so plain `requests` gets a CloudFront 403 even with a valid token
and browser-identical headers. `curl_cffi` with `impersonate="chrome"` sends
a real Chrome TLS handshake, which is what actually gets api2 to answer.
"""

from __future__ import annotations

import copy
import json
import os
import random
import re
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from . import audit, normalize

SKOOL_BASE = "https://www.skool.com"
SKOOL_API2 = "https://api2.skool.com"

FETCH_TIMEOUT_S = 30
MAX_RETRIES_202 = 3          # Skool ISR returns 202 while a page is still building
RETRY_202_DELAY_S = 2

# Successful GETs are cached in-process so repeated research doesn't re-hit
# Skool. CATKNOWS_CACHE_TTL (seconds) overrides; 0 disables. Chat channels and
# notifications are never cached ("what just happened?" must be live), nor
# the dashboard analytics: their first answer is a one-shot wait token, and a
# cached token would be replayed. Any write clears the cache.
CACHE_TTL_S = float(os.environ.get("CATKNOWS_CACHE_TTL", "600"))
_CACHE_MAX_ENTRIES = 128
_NEVER_CACHE = ("/self/chat-channels", "/self/notifications", "/analytics-")

# Minimum gap between two writes to Skool, per Skool account (owner's decision
# 2026-09-22, replaces the old "no throttle" rule D5). A fixed value, no
# jitter: this is tempo hygiene, not disguise. Module-level and keyed by the
# account's auth token, so it holds for every client of that account in the
# process (a re-login, a WAF refresh that rebuilds the client, a script next
# to the MCP server) — while a hosted server's other users keep their own pace.
# CATKNOWS_WRITE_GAP_S overrides (seconds).
WRITE_GAP_S = 15.0
_write_locks: dict[str, threading.Lock] = {}
_write_locks_guard = threading.Lock()
_last_write_at: dict[str, float] = {}
_clock = time.monotonic   # swapped out by test_write_gap.py
_sleep = time.sleep


def _write_gap_s() -> float:
    try:
        return max(0.0, float(os.environ.get("CATKNOWS_WRITE_GAP_S", WRITE_GAP_S)))
    except ValueError:
        return WRITE_GAP_S


# One browser profile is picked per client session (not per request), matching
# what a real browser looks like. Each pairs a UA with the sec-ch-ua headers
# that browser actually sends — a Chrome UA with no sec-ch-ua is a tell.
# Chrome-only: the TLS handshake is impersonated as Chrome (see module
# docstring), so a Firefox UA on a Chrome handshake would be a fingerprint
# mismatch.
_BROWSER_PROFILES = [
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "lang": "en-US,en;q=0.9",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "platform": '"Windows"',
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "lang": "en-US,en;q=0.9",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "platform": '"macOS"',
    },
]


class SkoolHTTPError(RuntimeError):
    """A Skool request failed after retries. `.status` is the HTTP code (0 = network)."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class SkoolSecurityChallenge(SkoolHTTPError):
    """Skool answered HTTP 200 with a security question instead of the data.

    The body is ``{"code": "AUTH-SEC-5xx", ...}`` (docs/API.md §3): Skool wants
    a security check for that path (a nonce for 501). Today only eight
    ``/auth/*`` paths, which catknows does not call, are affected. It is no HTTP
    error, which is why it needs its own exception, or a write would pass for
    sent and a read for empty.
    ``.status`` is 200, ``.code`` the AUTH-SEC code, ``.method`` and ``.path``
    the request (path without its query string).

    catknows does not answer the question and does not retry. The message names
    the code and the path (without its query string, which can carry one-shot
    tokens) and nothing else from the answer: no nonce, no token, no personal
    data. A batch job that stops on it says so in ``stopped``, which goes in
    front of the message.
    """

    def __init__(self, code: str, method: str, path: str, *, stopped: str = ""):
        self.method, self.path = method.upper(), urlsplit(path).path
        super().__init__(
            (f"{stopped} " if stopped else "")
            + f"Skool answered {self.method} {self.path} with a security question "
            f"(code {code}) instead of the data. catknows does not answer it and "
            "does not retry: treat this request as not done (a write counts as "
            "not sent). Do not retry automatically: stop and tell the user "
            "(docs/API.md §3).",
            200,
        )
        self.code = code


# The page's own test is /^AUTH-SEC-5\d\d$/ (docs/API.md §3). Python's `$` also
# matches before a trailing newline and its `\d` matches non-ASCII digits, so
# the same test here is a `fullmatch` on [0-9].
_SECURITY_CHALLENGE = re.compile(r"AUTH-SEC-5[0-9]{2}")

# The only paths `post_api2_read` sends (docs/API.md §5.13, plan contract
# `searchUsers` and `getResourceUrl`). The file id is one segment that is not
# made only of dots (client.file_download_path encodes it), `expire` digits only.
_READ_POST_PATH = re.compile(
    r"/search/users|/files/(?!\.+/)[^/?#]+/download-url(?:\?expire=[0-9]+)?")

# A link in an error body is never repeated: an answer of `post_api2_read` can be
# an access link (the file download URL), and an error text travels to logs.
_ANY_LINK = re.compile(r"https?://\S+")


def _security_challenge_code(status: int, data) -> str:
    """The AUTH-SEC-5xx code if this answer is Skool's security question, else "".

    Only HTTP 200, only a JSON object, only its top-level ``code``, only if that
    is a text matching in full. A number, another text, a list on top, a code
    one level down, or a body that is not JSON is an ordinary answer.
    """
    if status != 200 or not isinstance(data, dict):
        return ""
    code = data.get("code")
    if isinstance(code, str) and _SECURITY_CHALLENGE.fullmatch(code):
        return code
    return ""


# The statuses with which Skool turns the account away: session or WAF rejected
# (401/403), rate limit (429). The one place that lists them.
REJECTED_STATUSES = (401, 403, 429)


def turned_away(e: BaseException) -> bool:
    """True if this error is Skool's 401, 403 or 429: the account is being turned away.

    Session or WAF rejected (401/403) or rate limit (429). A batch job stops on
    it as it does on `SkoolSecurityChallenge`; asking on for the next entry is
    the pattern of an automaton. Read from ``.status``, never from the text.

    Also true for a 202 that Skool repeated until the retries were used up, its
    other rate-limit answer (docs/API.md §3). That error keeps status 0 and
    carries ``repeated_202``; only the batch jobs ask this function about it.

    Meant for answers of Skool itself. `get_mux` raises the same error class
    with 403 for an expired playback token, which says nothing about the
    account: do not use this on errors of a Mux request.
    """
    return isinstance(e, SkoolHTTPError) and (
        e.status in REJECTED_STATUSES or getattr(e, "repeated_202", False))


# Skool's api2 wording for "you may see this endpoint, but not this object":
# a membership gate, not an auth failure. Measured 2026-08-19 on
# /groups/{gid}/courses and /courses/{id} — an account that is NOT in the
# community gets exactly this body, while every course in a community it IS in
# answers 200 across all privacy levels.
_NOT_PERMITTED_MARKER = "action not permitted"


def _auth_rejected(code: int, url: str, body: str = "") -> SkoolHTTPError:
    """The 401/403 error. Two very different causes share these codes.

    A stored session's WAF token ages even while the year-long login stays
    valid, and AWS WAF then rejects the paginated/filtered endpoints first
    (measured 2026-08-15: a stale token turns members.json into
    202-challenges/403s). But Skool ALSO answers 401 ``action not permitted``
    when the login is perfectly fine and the account simply isn't in the
    community. Telling a user to re-login for that sends them in circles, so
    the body decides which advice we give.
    """
    if _NOT_PERMITTED_MARKER in body.lower():
        return SkoolHTTPError(
            f"HTTP {code} on {url} — Skool says \"action not permitted\". The "
            "session is fine; the logged-in Skool account just isn't in that "
            "community (classroom and member data are members-only). Joining "
            "it, or connecting the account that is in it, is the fix — "
            "re-logging in will not change this. Public info (about, "
            "discovery) works without membership.",
            code,
        )
    return SkoolHTTPError(
        f"HTTP {code} on {url} — auth or WAF rejected. The stored Skool "
        "session has likely gone stale (its WAF token expires even while the "
        "login itself is still valid). Refresh it: hosted users reconnect "
        "their Skool login (forget_skool_session, then log in again); local "
        "users re-run the login (delete the profile dir if it persists).",
        code,
    )


def _answer_excerpt(path_and_query: str, text: str, limit: int) -> str:
    """The start of an answer, for an error text. Every error text takes it from here.

    An error text travels to logs (hosted: the journal), and an answer can name a
    person or repeat a request. So this one place decides what of it is shown:

    - nothing, when the answer is a credential (`audit.secret_answer`): on the
      share-key request the body is the key, so the error says that it is held back;
    - otherwise the first ``limit`` characters, with every e-mail address masked
      (`normalize.mask_email`) and every secret query value replaced
      (`audit.redact`). Masked before it is cut: an address the cut runs through
      would lose its domain and no longer look like one.

    Only the start of the answer is worked on, not all of it: an error body can
    be megabytes. The window is the excerpt plus a margin wider than any address,
    so what is shown is what masking the whole answer would show; it grows when
    masking made the text so much shorter that the window's own edge came near.

    The excerpt stays, because without Skool's reason an error cannot be read.
    ``path_and_query`` may be empty when the request is not a Skool path.
    """
    if audit.secret_answer(path_and_query):
        return "(answer not shown: it can hold a credential)"
    text = text or ""
    window = limit + _EXCERPT_WINDOW_EXTRA
    while True:
        shown = audit.redact(normalize.mask_email(text[:window]))
        if window >= len(text) or len(shown) >= limit + _EXCERPT_EDGE:
            return shown[:limit]
        window *= 2


# An address is at most 254 characters long. The edge of the window can cut one,
# and that piece must stay behind the excerpt: so this many characters of the
# worked-on text lie between the excerpt's end and the window's edge.
_EXCERPT_EDGE = 300
_EXCERPT_WINDOW_EXTRA = 400

# What a storage's XML error answer says about the failure. The rest of it
# repeats the request: the access key id, the signed text, the signature.
_STORAGE_CODE = re.compile(r"<Code>([^<]{1,80})</Code>")
_STORAGE_MESSAGE = re.compile(r"<Message>([^<]{1,300})</Message>")


def _storage_answer(text: str) -> str:
    """What an error text shows of a foreign storage's answer (the upload).

    `audit.redact` does not know a foreign service's names, so the start of the
    answer is not shown here. A storage error in the usual XML form names a
    ``<Code>`` and a ``<Message>``: these two are shown (addresses masked, links
    removed), nothing else. Any other answer is not shown at all.
    """
    head = (text or "")[:4000]
    code = _STORAGE_CODE.search(head)
    if not code:
        return "(answer not shown: it can repeat the access of the upload link)"
    message = _STORAGE_MESSAGE.search(head)
    said = code.group(1).strip() + (f": {message.group(1).strip()}" if message else "")
    return _ANY_LINK.sub("[link removed]", normalize.mask_email(said))[:300]


def _foreign_said(url: str, named: str, failed: RequestException) -> str:
    """The library's message about a request to a foreign host, without its URL.

    The query of such a URL is the access (a presigned upload link, a signed
    playback link). The URL, with or without its scheme, is replaced by
    ``named``, and any other link is removed.
    """
    said = str(failed)
    for piece in (url, url.split("://", 1)[-1]):
        said = said.replace(piece, named)
    return _ANY_LINK.sub("[link removed]", audit.redact(said))


def _url_for_error(url: str) -> tuple[str, bool]:
    """How a request URL is named in an error text, and whether it carries a credential.

    A URL whose query holds one (`audit.carries_secret`: the one-shot token of the
    dashboard numbers, ``?token=``) is named without its query: scheme, host, path.
    Any other URL is named as it is, its query explains the request.
    """
    parts = urlsplit(url)
    if not audit.carries_secret(parts.path + (f"?{parts.query}" if parts.query else "")):
        return url, False
    return f"{parts.scheme}://{parts.netloc}{parts.path}", True


def _network_error(url: str,
                   failed: RequestException) -> tuple[SkoolHTTPError, BaseException | None]:
    """The error for a request that never got an answer, and the cause to raise it from.

    The library's own message can repeat the URL, so it goes through `audit.redact`.
    When the URL carries a credential, or the redaction had something to replace,
    the cause is ``None``: a logger prints the whole chain, and the library's
    unredacted message would be in it. Raise it OUTSIDE the ``except`` block
    (``raise error from cause``), so that ``__context__`` stays empty as well.
    """
    shown, secret = _url_for_error(url)
    said = str(failed)
    text = audit.redact(said)
    return (SkoolHTTPError(f"Network error on {shown}: {text}"),
            None if secret or text != said else failed)


class SkoolHTTP:
    # Where the Next.js buildId comes from, when someone else already knows it.
    # `provider(False)` returns the current value, `provider(True)` fetches it
    # anew (the caller just saw a 404). Set from outside, on the finished
    # instance, and only read when a request needs the id — never at
    # construction. A class attribute so an instance built without `__init__`
    # still has the default: no provider, the HTML page is used as always.
    build_id_provider: Callable[[bool], str] | None = None

    def __init__(self, session):
        """`session` is a catknows.auth.Session."""
        self.session = session
        self._build_id = ""
        self._cache: dict[str, tuple[float, dict]] = {}  # url -> (fetched_at, data)
        self._profile = random.choice(_BROWSER_PROFILES)
        # impersonate="chrome": real Chrome TLS handshake — required, AWS WAF
        # rejects the default python TLS fingerprint on api2 regardless of headers.
        self._http = requests.Session(impersonate="chrome")

    # -- buildId ---------------------------------------------------------------

    def build_id(self, community_slug: str) -> str:
        """The Next.js buildId, discovered from any community page's HTML.

        Skool embeds `"buildId":"XXXX"` in the __NEXT_DATA__ script on every
        page (even 404s). It changes on every Skool deploy, so we discover it
        rather than hardcode it, and cache it until a request 404s — `get_next`
        then clears the cache and re-discovers once (a deploy mid-session would
        otherwise 404 every URL until the process restarts).

        With a `build_id_provider` set, the provider is the only source: its
        current value is returned and no HTML page is ever loaded, not even
        when it gives nothing back (that is an error, not a reason to fall
        back). The provider keeps the value; nothing is cached here.
        """
        provider = self.build_id_provider
        if provider is not None:
            return self._provided_build_id(provider, False)
        if self._build_id:
            return self._build_id

        url = f"{SKOOL_BASE}/{community_slug}"
        failed: RequestException | None = None
        try:
            resp = self._http.get(
                url,
                headers={
                    "Cookie": self._cookie_header(),
                    "User-Agent": self._profile["ua"],
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                timeout=FETCH_TIMEOUT_S,
            )
        except RequestException as e:
            failed = e
        if failed is not None:
            error, cause = _network_error(url, failed)
            raise error from cause
        self._build_id = _extract_build_id(resp.text)
        if not self._build_id:
            raise SkoolHTTPError(
                f"Could not find buildId in {url} (HTTP {resp.status_code}). "
                "Is the community slug correct and are you logged in?",
                resp.status_code,
            )
        return self._build_id

    @staticmethod
    def _provided_build_id(provider: Callable[[bool], str], refresh: bool) -> str:
        """Ask the provider; an empty answer is an error. Its own exceptions pass through."""
        value = provider(refresh)
        if not isinstance(value, str) or not value.strip():
            got = "an empty text" if isinstance(value, str) else type(value).__name__
            raise SkoolHTTPError(
                f"build_id_provider gave no buildId ({'refresh' if refresh else 'current value'}"
                f": expected a non-empty text, got {got}). With a provider set the "
                "HTML page is never loaded to find it, so there is no fallback."
            )
        return value.strip()

    # -- request shapes --------------------------------------------------------

    def get_next(self, path_and_query: str, community_slug: str = "") -> dict:
        """GET a www.skool.com/_next/data/... endpoint (Next.js shape).

        `path_and_query` is everything after the buildId, e.g.
        "/{slug}/-/members.json?sortType=...". Retries on Skool's 202/empty
        (ISR deferred) responses.
        """
        provider = self.build_id_provider
        if provider is not None or community_slug:
            build_id = self.build_id(community_slug)
        else:
            build_id = self._build_id
        url = f"{SKOOL_BASE}/_next/data/{build_id}{path_and_query}"
        referer = f"{SKOOL_BASE}/{community_slug}" if community_slug else f"{SKOOL_BASE}/"

        headers = {
            "Cookie": self._cookie_header(),
            "User-Agent": self._profile["ua"],
            "Accept": "*/*",
            "Accept-Language": self._profile["lang"],
            "Accept-Encoding": "identity",
            "Referer": referer,
            "x-nextjs-data": "1",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        self._add_sec_ch_ua(headers)
        try:
            data = self._get_with_retry(url, headers)
        except SkoolHTTPError as e:
            # Skool deploys a new buildId and every URL carrying the old one 404s.
            # We cache the id for the client's lifetime, so a long-running server
            # would stay broken until restarted. Re-discover once and retry; if
            # the id didn't actually change it's a real 404, so re-raise.
            if e.status != 404:
                raise
            if provider is not None:
                # The provider owns the id: ONE provider(True), ONE new try, and
                # never the HTML page. A second 404 is final; there is no loop.
                fresh = self._provided_build_id(provider, True)
                if fresh == build_id:
                    raise
            elif community_slug:
                stale, self._build_id = build_id, ""
                fresh = self.build_id(community_slug)
                if fresh == stale:
                    raise
            else:
                raise
            url = f"{SKOOL_BASE}/_next/data/{fresh}{path_and_query}"
            data = self._get_with_retry(url, headers)
        # Members-only routes answer 200 with a redirect stub (pageProps holds
        # ONLY __N_REDIRECT*) when the account isn't in the community. Without
        # this check callers see an empty list and think "0 members". Next.js
        # emits the same stub shape for ANY server-side redirect though — the
        # membership gate always bounces to the about page, while navigation
        # redirects (classroom course -> first lesson ?md=...) point deeper
        # into the route and must pass through untouched. Since 2026-10 the
        # stub also carries Sentry's tracing keys; demanding the bare two-key
        # shape let every gated page through as empty (test_member_gate.py).
        pp = data.get("pageProps") if isinstance(data, dict) else None
        if (isinstance(pp, dict) and "__N_REDIRECT" in pp
                and {k for k in pp if not k.startswith("_sentry")}
                <= {"__N_REDIRECT", "__N_REDIRECT_STATUS"}):
            target = str(pp.get("__N_REDIRECT") or "")
            if target.split("?", 1)[0].rstrip("/").endswith("/about"):
                where = f"'{community_slug}'" if community_slug else "this community"
                raise SkoolHTTPError(
                    f"Skool redirected this members-only page — the logged-in "
                    f"account has no access to {where}. It may not have joined, "
                    "or it may be banned or removed (Skool bounces both to the "
                    "about page identically). Use an account that's in it. "
                    "Public info (about, discovery) works without membership.",
                    307,
                )
        return data

    def get_api2(self, path_and_query: str) -> dict:
        """GET an api2.skool.com endpoint (Bearer + WAF-header shape).

        Used for comments, likes, groups, discovery, admin-metrics, chat.
        `path_and_query` is everything after the host, e.g.
        "/posts/{id}/comments?group-id={gid}&limit=25".
        """
        url = f"{SKOOL_API2}{path_and_query}"
        return self._get_with_retry(url, self._api2_headers(), api2=True)

    def get_api2_text(self, path_and_query: str) -> str:
        """GET an api2 endpoint that answers plain text, not JSON.

        Only ``/wait?token=`` does, as far as measured: ``in-progress`` or
        ``completed`` (docs/API.md §1.9). Never cached, no retry.
        """
        url = f"{SKOOL_API2}{path_and_query}"
        shown, _ = _url_for_error(url)  # the token is one-shot, but no error text names it
        failed: RequestException | None = None
        try:
            resp = self._http.get(url, headers=self._api2_headers(), timeout=FETCH_TIMEOUT_S)
        except RequestException as e:
            failed = e
        if failed is not None:
            error, cause = _network_error(url, failed)
            raise error from cause
        if resp.status_code in (401, 403):
            raise _auth_rejected(resp.status_code, shown, resp.text)
        if not (200 <= resp.status_code < 300):
            raise SkoolHTTPError(
                f"HTTP {resp.status_code} on {shown}: "
                f"{_answer_excerpt(path_and_query, resp.text, 300)}", resp.status_code)
        if resp.status_code == 200:
            try:
                parsed = json.loads(resp.text)
            except ValueError:
                parsed = None       # plain text, the normal answer here
            code = _security_challenge_code(200, parsed)
            if code:
                raise SkoolSecurityChallenge(code, "GET", path_and_query)
        return resp.text.strip().strip('"')

    def get_mux(self, url: str) -> str:
        """GET a signed Mux playback URL and return its body as TEXT.

        Skool's video CDN, not Skool itself: no cookies, no Bearer — the signed
        token in the URL is the whole authorization. It carries a playback
        restriction, so the request MUST look like it came from the Skool
        player; without a Skool Referer Mux answers 403 ``E184-1``. Returns
        text (HLS manifests and WebVTT), never JSON.

        A network error names the URL without its query (the token), repeats the
        library's own message without the URL, and has no exception chain.
        """
        failed: RequestException | None = None
        try:
            r = self._http.get(url, headers={
                "User-Agent": self._profile["ua"],
                "Accept": "*/*",
                "Accept-Language": self._profile["lang"],
                "Referer": f"{SKOOL_BASE}/",
                "Origin": SKOOL_BASE,
            }, timeout=FETCH_TIMEOUT_S)
        except RequestException as e:
            failed = e
        if failed is not None:
            shown = url.split("?")[0]
            raise SkoolHTTPError(
                f"Network error on {shown}: {_foreign_said(url, '(the URL)', failed)}") from None
        if r.status_code != 200:
            raise SkoolHTTPError(
                f"HTTP {r.status_code} from Mux ({url.split('?')[0]}). A 403 "
                "here usually means the playback token expired — re-read the "
                "post page to get a fresh one.",
                r.status_code,
            )
        return r.text

    def post_api2(self, path_and_query: str, body: dict) -> dict:
        """POST to an api2.skool.com endpoint — the write shape (docs/API.md §5).

        Same header set as `get_api2`, JSON body. Deliberately NO automatic
        retry: a retried write could double-post. Callers handle failures.
        """
        return self._write_api2("post", path_and_query, body)

    def post_api2_read(self, path_and_query: str, body: dict | None = None):
        """POST to an api2.skool.com endpoint that only READS (docs/API.md §5.13).

        A few of Skool's reads are POSTs (the @mention member search, the file
        download link). They are not writes, so they do not go through
        `_write_api2`: no write-audit line, no 15 s write gap, and the read
        cache is left alone. Nothing else changes: same headers, no automatic
        retry, and Skool's security question (HTTP 200, ``code: AUTH-SEC-5xx``)
        stops the call with `SkoolSecurityChallenge`, found by the same
        `_security_challenge_code` as everywhere else. Never use this for a
        call that changes something: that one belongs in `post_api2`, where it
        is spaced and logged.

        The answer is returned as it comes: the parsed JSON, or the text when it
        is not JSON (the file link may be plain text), or ``{}`` when empty.
        A successful answer is never echoed into an error message, and a link in
        an error body is replaced by ``[link removed]``: an answer here can be an
        access link.

        Only the two read-POSTs of docs/API.md §5.13 are sent: ``/search/users``
        and ``/files/{id}/download-url`` (optionally ``?expire={n}``). Any other
        path raises ``ValueError`` before anything is sent, so a write cannot
        slip past the write gap and the audit log through this door.
        """
        if not _READ_POST_PATH.fullmatch(path_and_query):
            raise ValueError(
                "post_api2_read only sends the documented read-POSTs (/search/users, "
                "/files/{id}/download-url); anything else belongs in post_api2.")
        url = f"{SKOOL_API2}{path_and_query}"
        kwargs: dict = {"headers": self._api2_headers(), "timeout": FETCH_TIMEOUT_S}
        if body is not None:
            kwargs["json"] = body
        failed: RequestException | None = None
        try:
            resp = self._http.post(url, **kwargs)
        except RequestException as e:
            failed = e
        if failed is not None:
            error, cause = _network_error(url, failed)
            raise error from cause
        code, text = resp.status_code, resp.text or ""
        if not (200 <= code < 300):
            if code == 401 or code == 403:
                raise _auth_rejected(code, _url_for_error(url)[0], text)
            excerpt = _answer_excerpt(path_and_query, text, 300)
            raise SkoolHTTPError(
                f"HTTP {code} on {url}: {_ANY_LINK.sub('[link removed]', excerpt)}", code)
        if not text.strip():
            return {}
        try:
            data = json.loads(text)
        except ValueError:
            return text.strip()
        challenge = _security_challenge_code(code, data)
        if challenge:
            raise SkoolSecurityChallenge(challenge, "POST", path_and_query)
        return data

    def put_api2(self, path_and_query: str, body: dict) -> dict:
        """PUT to an api2.skool.com endpoint (updates — docs/API.md §7)."""
        return self._write_api2("put", path_and_query, body)

    def delete_api2(self, path_and_query: str, body: dict | None = None) -> dict:
        """DELETE an api2.skool.com resource. Success is usually an empty 200.

        No JSON body unless one is passed. catknows sends a body on exactly one
        DELETE, ``deletePost`` (at least ``{}``, as Skool's own client does
        there); ``deleteCourse`` and the other DELETEs it builds send none.
        """
        return self._write_api2("delete", path_and_query, body)

    def _write_api2(self, method: str, path_and_query: str, body: dict | None) -> dict:
        """Every write catknows sends to Skool goes through here.

        Which makes it the one place the write-audit log belongs (board D8):
        the write tools, the CLI and the doc examples all funnel into these
        three verbs, so a log here cannot be forgotten by a caller or by the
        next write method somebody adds. See `catknows/audit.py`.

        For the same reason the write gap lives here: at least `WRITE_GAP_S`
        between two writes of the same account, failed ones included, since
        Skool saw them too.

        A credential in the request or the answer (the course share key and the
        verified client id, `audit.redact` and `audit.carries_secret`) is kept out
        of everything this method writes or raises: the audit line redacts its own
        path, each error message goes through `audit.redact`, an error about a
        request whose ANSWER is the credential shows no piece of that answer
        (`_answer_excerpt`), and such an error has no exception chain at all, since
        a logger prints the chain and the library's own message can repeat the URL.
        The request itself carries the real value.
        """
        url = f"{SKOOL_API2}{path_and_query}"
        # On such a path an error carries no chain (`from None`); elsewhere it does.
        secret = audit.carries_secret(path_and_query)
        account = self.session.auth_token or self.session.cookie_header
        with _write_locks_guard:
            lock = _write_locks.setdefault(account, threading.Lock())
        kwargs: dict = {"headers": self._api2_headers(), "timeout": FETCH_TIMEOUT_S}
        if body is not None:
            kwargs["json"] = body
        failed: RequestException | None = None
        with lock:
            last = _last_write_at.get(account)
            if last is not None:
                wait = last + _write_gap_s() - _clock()
                if wait > 0:
                    _sleep(wait)
            try:
                resp = getattr(self._http, method)(url, **kwargs)
            except RequestException as e:
                audit.record(method=method, path=path_and_query, status="network_error")
                failed = e
            finally:
                _last_write_at[account] = _clock()
        if failed is not None:
            # Raised here, outside the handler: `__context__` stays empty, so with
            # `from None` the error of a credential path carries no chain at all.
            raise SkoolHTTPError(
                audit.redact(f"Network error on {url}: {failed}")) from (None if secret else failed)

        code = resp.status_code
        if not (200 <= code < 300):
            # Logged before raising: a rejected write is exactly the kind of
            # thing we are otherwise left guessing about. No body goes in, it
            # can echo the content we just tried to send.
            audit.record(method=method, path=path_and_query, status=code)
            if code == 401 or code == 403:
                raise _auth_rejected(code, audit.redact(url), resp.text)
            raise SkoolHTTPError(
                audit.redact(f"HTTP {code} on {url}: "
                             f"{_answer_excerpt(path_and_query, resp.text, 300)}"), code)
        data: object = {}
        bad_json: json.JSONDecodeError | None = None
        if resp.text.strip():
            try:
                data = json.loads(resp.text)
            except json.JSONDecodeError as e:
                bad_json = e
        challenge = _security_challenge_code(code, data) if bad_json is None else ""
        if challenge:
            # Skool answered the write with its security question: it did not
            # take it, so it is not a sent write. No "ok" line (the line says
            # why, with the code and no body), and the cache stays as it was,
            # because nothing was written that could have made it stale. No
            # retry, no attempt to answer the question.
            audit.record(method=method, path=path_and_query,
                         status="security_challenge", note=challenge)
            raise SkoolSecurityChallenge(challenge, method, path_and_query)
        self._cache.clear()  # a write invalidates anything we read before it
        if bad_json is None and not resp.text.strip():
            audit.record(method=method, path=path_and_query, status=code, response={})
            return {}
        if bad_json is not None:
            audit.record(method=method, path=path_and_query, status=code, note="bad_json")
            raise SkoolHTTPError(
                audit.redact(f"Bad JSON from {url}: {bad_json} | "
                             f"{_answer_excerpt(path_and_query, resp.text, 200)}"),
                code) from (None if secret else bad_json)
        audit.record(method=method, path=path_and_query, status=code, response=data)
        return data

    def put_bytes(self, url: str, data: bytes, headers: dict) -> None:
        """PUT raw bytes to a presigned upload URL (docs/API.md §5.3).

        Not a Skool endpoint — the presigned S3 URL carries its own auth in the
        query string, so this sends NO cookies, bearer or WAF token. Like
        `post_api2` it does not retry.

        That query is the access to the upload, and `audit.redact` does not know a
        foreign service's parameter names. So a network error names the host and
        nothing else of the URL, the library's own message is repeated without
        the URL and without any other link, and the error has no exception chain.
        An HTTP error shows the storage's ``<Code>`` and ``<Message>`` and nothing
        else of its answer, which repeats the request (`_storage_answer`).
        """
        failed: RequestException | None = None
        try:
            resp = self._http.put(url, headers=headers, data=data, timeout=FETCH_TIMEOUT_S)
        except RequestException as e:
            failed = e
        if failed is not None:
            host = urlsplit(url).hostname or "(unknown host)"
            raise SkoolHTTPError(f"Network error uploading to {host}: "
                                 f"{_foreign_said(url, host, failed)}") from None
        if not (200 <= resp.status_code < 300):
            raise SkoolHTTPError(
                f"HTTP {resp.status_code} uploading bytes: {_storage_answer(resp.text)}",
                resp.status_code,
            )

    def _api2_headers(self) -> dict:
        headers = {
            "Cookie": self._cookie_header(),
            "Content-Type": "application/json",
            "User-Agent": self._profile["ua"],
            "Origin": SKOOL_BASE,
            "Referer": f"{SKOOL_BASE}/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": self._profile["lang"],
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",  # www.skool.com -> api2.skool.com
        }
        if self.session.auth_token:
            headers["Authorization"] = f"Bearer {self.session.auth_token}"
        if self.session.waf_token:
            headers["x-aws-waf-token"] = self.session.waf_token
        self._add_sec_ch_ua(headers)
        return headers

    # -- internals -------------------------------------------------------------

    def _cookie_header(self) -> str:
        return self.session.cookie_header

    def _add_sec_ch_ua(self, headers: dict) -> None:
        if self._profile["sec_ch_ua"]:
            headers["sec-ch-ua"] = self._profile["sec_ch_ua"]
            headers["sec-ch-ua-mobile"] = "?0"
            headers["sec-ch-ua-platform"] = self._profile["platform"]

    def _cacheable(self, url: str) -> bool:
        return CACHE_TTL_S > 0 and not any(p in url for p in _NEVER_CACHE)

    def _get_with_retry(self, url: str, headers: dict, *, api2: bool = False) -> dict:
        """GET with the 202/empty retry and the cache.

        `api2=True` also stops on Skool's security question (HTTP 200 with
        ``code: AUTH-SEC-5xx``) before anything is cached, and never retries it.
        """
        if self._cacheable(url):
            hit = self._cache.get(url)
            if hit and time.time() - hit[0] < CACHE_TTL_S:
                # deepcopy: callers may mutate (pagination merges) — the cached
                # original must stay pristine.
                return copy.deepcopy(hit[1])

        # What an error text names: the URL, without its query when that holds a
        # credential (the one-shot token of the dashboard numbers). On such a URL
        # an error has no exception chain either.
        shown, secret = _url_for_error(url)
        path = urlsplit(url).path
        last_err = "unknown"
        for attempt in range(1, MAX_RETRIES_202 + 1):
            failed: RequestException | None = None
            try:
                resp = self._http.get(url, headers=headers, timeout=FETCH_TIMEOUT_S)
            except RequestException as e:
                failed = e
            if failed is not None:
                error, cause = _network_error(url, failed)
                raise error from cause

            code = resp.status_code
            body = resp.text

            if code == 401 or code == 403:
                raise _auth_rejected(code, shown, body)
            if code == 429:
                # The rate limit, also without a body: never asked again
                # (checked before the empty-body retry below).
                raise SkoolHTTPError(
                    f"HTTP {code} on {shown}: {_answer_excerpt(path, body, 300)}", code)
            if code == 202 or not body.strip():
                # ISR deferred: page still building. Wait and retry.
                last_err = f"HTTP {code} deferred/empty"
                if attempt < MAX_RETRIES_202:
                    time.sleep(RETRY_202_DELAY_S)
                    continue
                break
            if code == 404 and '"notFound":true' in body:
                # Skool answers notFound both for data you may not see and for
                # a query it doesn't understand. On members.json an UNKNOWN
                # filter value (e.g. a typo'd t=) is a flat 404 — measured
                # 2026-08-15; the known t-values answer 200 for every member,
                # admin or not. If a 404 shows up on some communities but not
                # others, suspect the query before concluding it's permissions.
                extra = (
                    "If posts/comments work for this community, this is a query "
                    "problem, not access — check for unknown filter values in "
                    "the URL (see AGENTS.md).\n"
                    if "/-/members.json" in url else ""
                )
                raise SkoolHTTPError(
                    f"HTTP 404 on {shown}: not found.\n{extra}"
                    "Otherwise the logged-in Skool account has no access to this "
                    "community — not joined, or banned/removed (member/post data "
                    "is members-only). Use an account that's in it. Public info "
                    "(about, discovery) works without membership.",
                    code,
                )
            if not (200 <= code < 300):
                raise SkoolHTTPError(
                    f"HTTP {code} on {shown}: {_answer_excerpt(path, body, 300)}", code)

            bad_json: json.JSONDecodeError | None = None
            try:
                data = json.loads(body)
            except json.JSONDecodeError as e:
                bad_json = e
            if bad_json is not None:
                raise SkoolHTTPError(
                    f"Bad JSON from {shown}: {bad_json} | "
                    f"{_answer_excerpt(path, body, 200)}",
                    code) from (None if secret else bad_json)

            if api2:
                challenge = _security_challenge_code(code, data)
                if challenge:
                    raise SkoolSecurityChallenge(challenge, "GET", url)

            if self._cacheable(url):
                if len(self._cache) >= _CACHE_MAX_ENTRIES:
                    # ponytail: drop the oldest entry; LRU if this ever matters
                    self._cache.pop(min(self._cache, key=lambda k: self._cache[k][0]))
                self._cache[url] = (time.time(), copy.deepcopy(data))
            return data

        err = SkoolHTTPError(f"{last_err} after {MAX_RETRIES_202} retries: {shown}")
        err.repeated_202 = code == 202   # rate limit, not an empty body: see turned_away
        raise err


def _extract_build_id(html: str) -> str:
    """Pull the Next.js buildId out of a Skool page's HTML."""
    m = re.search(r'"buildId":"([^"]+)"', html)
    if m:
        return m.group(1)
    # Fallback: /_next/static/{buildId}/_buildManifest.js
    m = re.search(r"/_next/static/([^/]+)/", html)
    return m.group(1) if m else ""

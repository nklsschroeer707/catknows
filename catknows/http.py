"""The low-level Skool HTTP client — WAF-faithful requests + buildId discovery.

Everything that makes Skool's private API answer instead of returning 403.
The public API methods (members, posts, comments, ...) live in `client.py` and
call through this layer.

Two request "shapes", because Skool has two backends:
  - Next.js data endpoints  (www.skool.com/_next/data/{buildId}/...)  -> `get_next`
  - api2.skool.com endpoints (comments, likes, groups, ...)           -> `get_api2`

The header sets differ. The Next.js shape sends `x-nextjs-data: 1`; the api2
shape sends `Authorization: Bearer` + `Origin` + the `x-aws-waf-token` header.
Getting these wrong is the difference between 200 and a WAF 403.

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
import time

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

SKOOL_BASE = "https://www.skool.com"
SKOOL_API2 = "https://api2.skool.com"

FETCH_TIMEOUT_S = 30
MAX_RETRIES_202 = 3          # Skool ISR returns 202 while a page is still building
RETRY_202_DELAY_S = 2

# Successful GETs are cached in-process so repeated research doesn't re-hit
# Skool. CATKNOWS_CACHE_TTL (seconds) overrides; 0 disables. Chat channels are
# never cached (unread state must be live), and any write clears the cache.
CACHE_TTL_S = float(os.environ.get("CATKNOWS_CACHE_TTL", "600"))
_CACHE_MAX_ENTRIES = 128
_NEVER_CACHE = ("/self/chat-channels",)

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


def _auth_rejected(code: int, url: str) -> SkoolHTTPError:
    """The 401/403 error, worded for both deployments: a stored session's WAF
    token ages even while the year-long login stays valid, and AWS WAF then
    rejects exactly the paginated/filtered endpoints first (measured
    2026-08-15: a stale token turns members.json into 202-challenges/403s)."""
    return SkoolHTTPError(
        f"HTTP {code} on {url} — auth or WAF rejected. The stored Skool "
        "session has likely gone stale (its WAF token expires even while the "
        "login itself is still valid). Refresh it: hosted users reconnect "
        "their Skool login (forget_skool_session, then log in again); local "
        "users re-run the login (delete the profile dir if it persists).",
        code,
    )


class SkoolHTTP:
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
        """
        if self._build_id:
            return self._build_id

        url = f"{SKOOL_BASE}/{community_slug}"
        resp = self._http.get(
            url,
            headers={
                "Cookie": self._cookie_header(),
                "User-Agent": self._profile["ua"],
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=FETCH_TIMEOUT_S,
        )
        self._build_id = _extract_build_id(resp.text)
        if not self._build_id:
            raise SkoolHTTPError(
                f"Could not find buildId in {url} (HTTP {resp.status_code}). "
                "Is the community slug correct and are you logged in?",
                resp.status_code,
            )
        return self._build_id

    # -- request shapes --------------------------------------------------------

    def get_next(self, path_and_query: str, community_slug: str = "") -> dict:
        """GET a www.skool.com/_next/data/... endpoint (Next.js shape).

        `path_and_query` is everything after the buildId, e.g.
        "/{slug}/-/members.json?sortType=...". Retries on Skool's 202/empty
        (ISR deferred) responses.
        """
        build_id = self.build_id(community_slug) if community_slug else self._build_id
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
            if e.status != 404 or not community_slug:
                raise
            stale, self._build_id = build_id, ""
            fresh = self.build_id(community_slug)
            if fresh == stale:
                raise
            url = f"{SKOOL_BASE}/_next/data/{fresh}{path_and_query}"
            data = self._get_with_retry(url, headers)
        # Members-only routes answer 200 with a redirect stub (pageProps holds
        # ONLY __N_REDIRECT*) when the account isn't in the community. Without
        # this check callers see an empty list and think "0 members". Next.js
        # emits the same stub shape for ANY server-side redirect though — the
        # membership gate always bounces to the about page, while navigation
        # redirects (classroom course -> first lesson ?md=...) point deeper
        # into the route and must pass through untouched.
        pp = data.get("pageProps") if isinstance(data, dict) else None
        if (isinstance(pp, dict) and "__N_REDIRECT" in pp
                and set(pp) <= {"__N_REDIRECT", "__N_REDIRECT_STATUS"}):
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
        return self._get_with_retry(url, self._api2_headers())

    def get_mux(self, url: str) -> str:
        """GET a signed Mux playback URL and return its body as TEXT.

        Skool's video CDN, not Skool itself: no cookies, no Bearer — the signed
        token in the URL is the whole authorization. It carries a playback
        restriction, so the request MUST look like it came from the Skool
        player; without a Skool Referer Mux answers 403 ``E184-1``. Returns
        text (HLS manifests and WebVTT), never JSON.
        """
        r = self._http.get(url, headers={
            "User-Agent": self._profile["ua"],
            "Accept": "*/*",
            "Accept-Language": self._profile["lang"],
            "Referer": f"{SKOOL_BASE}/",
            "Origin": SKOOL_BASE,
        }, timeout=FETCH_TIMEOUT_S)
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

    def put_api2(self, path_and_query: str, body: dict) -> dict:
        """PUT to an api2.skool.com endpoint (updates — docs/API.md §7)."""
        return self._write_api2("put", path_and_query, body)

    def delete_api2(self, path_and_query: str) -> dict:
        """DELETE an api2.skool.com resource. Success is usually an empty 200."""
        return self._write_api2("delete", path_and_query, None)

    def _write_api2(self, method: str, path_and_query: str, body: dict | None) -> dict:
        url = f"{SKOOL_API2}{path_and_query}"
        kwargs: dict = {"headers": self._api2_headers(), "timeout": FETCH_TIMEOUT_S}
        if body is not None:
            kwargs["json"] = body
        try:
            resp = getattr(self._http, method)(url, **kwargs)
        except RequestException as e:
            raise SkoolHTTPError(f"Network error on {url}: {e}") from e

        code = resp.status_code
        if code == 401 or code == 403:
            raise _auth_rejected(code, url)
        if not (200 <= code < 300):
            raise SkoolHTTPError(f"HTTP {code} on {url}: {resp.text[:300]}", code)
        self._cache.clear()  # a write invalidates anything we read before it
        if not resp.text.strip():
            return {}
        try:
            return json.loads(resp.text)
        except json.JSONDecodeError as e:
            raise SkoolHTTPError(f"Bad JSON from {url}: {e} | {resp.text[:200]}", code) from e

    def put_bytes(self, url: str, data: bytes, headers: dict) -> None:
        """PUT raw bytes to a presigned upload URL (docs/API.md §5.3).

        Not a Skool endpoint — the presigned S3 URL carries its own auth in the
        query string, so this sends NO cookies, bearer or WAF token. Like
        `post_api2` it does not retry.
        """
        try:
            resp = self._http.put(url, headers=headers, data=data, timeout=FETCH_TIMEOUT_S)
        except RequestException as e:
            raise SkoolHTTPError(f"Network error uploading to {url[:80]}...: {e}") from e
        if not (200 <= resp.status_code < 300):
            raise SkoolHTTPError(
                f"HTTP {resp.status_code} uploading bytes: {resp.text[:300]}",
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

    def _get_with_retry(self, url: str, headers: dict) -> dict:
        if self._cacheable(url):
            hit = self._cache.get(url)
            if hit and time.time() - hit[0] < CACHE_TTL_S:
                # deepcopy: callers may mutate (pagination merges) — the cached
                # original must stay pristine.
                return copy.deepcopy(hit[1])

        last_err = "unknown"
        for attempt in range(1, MAX_RETRIES_202 + 1):
            try:
                resp = self._http.get(url, headers=headers, timeout=FETCH_TIMEOUT_S)
            except RequestException as e:
                raise SkoolHTTPError(f"Network error on {url}: {e}") from e

            code = resp.status_code
            body = resp.text

            if code == 401 or code == 403:
                raise _auth_rejected(code, url)
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
                    f"HTTP 404 on {url}: not found.\n{extra}"
                    "Otherwise the logged-in Skool account has no access to this "
                    "community — not joined, or banned/removed (member/post data "
                    "is members-only). Use an account that's in it. Public info "
                    "(about, discovery) works without membership.",
                    code,
                )
            if not (200 <= code < 300):
                raise SkoolHTTPError(f"HTTP {code} on {url}: {body[:300]}", code)

            try:
                data = json.loads(body)
            except json.JSONDecodeError as e:
                raise SkoolHTTPError(f"Bad JSON from {url}: {e} | {body[:200]}", code) from e

            if self._cacheable(url):
                if len(self._cache) >= _CACHE_MAX_ENTRIES:
                    # ponytail: drop the oldest entry; LRU if this ever matters
                    self._cache.pop(min(self._cache, key=lambda k: self._cache[k][0]))
                self._cache[url] = (time.time(), copy.deepcopy(data))
            return data

        raise SkoolHTTPError(f"{last_err} after {MAX_RETRIES_202} retries: {url}")


def _extract_build_id(html: str) -> str:
    """Pull the Next.js buildId out of a Skool page's HTML."""
    m = re.search(r'"buildId":"([^"]+)"', html)
    if m:
        return m.group(1)
    # Fallback: /_next/static/{buildId}/_buildManifest.js
    m = re.search(r"/_next/static/([^/]+)/", html)
    return m.group(1) if m else ""

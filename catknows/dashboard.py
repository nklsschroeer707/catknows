"""The dashboard: log in with Keycloak, connect Skool via the streamed browser.

    catknows-dashboard        # or: python -m catknows.dashboard

A small Starlette app, deliberately separate from the MCP server. They speak to
different audiences with different credentials: the MCP endpoint authenticates
machines with bearer tokens, this one authenticates a person holding a cookie.
Folding them together would mean one app with two auth models.

What it serves:
    GET  /                  the landing page and the connect button
    GET  /auth/login        start the Keycloak code flow (PKCE)
    GET  /auth/callback     finish it, set the session cookie
    POST /auth/logout       drop the cookie
    GET  /connect           the streamed-login screen
    WS   /connect/ws        CDP screencast out, user input in
    POST /session/delete    remove the stored Skool session (GDPR art. 17)

No new dependencies: Starlette and uvicorn already come with the MCP SDK,
Starlette does WebSockets natively, and the OAuth code flow is short enough to
write against urllib than to add a client library for.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import sessions

WEB = Path(__file__).resolve().parent.parent / "deploy" / "web"

COOKIE = "catknows_session"
# Short enough that a shared or forgotten browser stops mattering quickly, long
# enough to finish a login without re-authenticating mid-flow.
COOKIE_TTL = 8 * 3600

# In-process login state. Fine for one worker, which is what runs here; a second
# worker would need this in Redis or the DB.
# ponytail: dict, not a store. Move it out when this runs more than one process.
_pending: dict[str, dict] = {}
_logins: dict[str, dict] = {}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _issuer() -> str:
    return _env("CATKNOWS_OAUTH_ISSUER").rstrip("/")


def _base_url() -> str:
    """Public origin of this app, for building the redirect URI."""
    return _env("CATKNOWS_DASHBOARD_URL", "https://catknows.app").rstrip("/")


def _client() -> tuple[str, str]:
    cid = _env("CATKNOWS_DASHBOARD_CLIENT_ID", "catknows-dashboard")
    return cid, _env("CATKNOWS_DASHBOARD_CLIENT_SECRET")


def _oidc(path: str) -> str:
    return f"{_issuer()}/protocol/openid-connect/{path}"


# -- session cookie ------------------------------------------------------------
# The cookie holds a random id; the subject stays server-side. A signed cookie
# would work too, but then the secret has to be right or identity is forgeable —
# an opaque id is one less thing that can be wrong.


def _new_session(subject: str, email: str, cleared: bool = False) -> str:
    sid = secrets.token_urlsafe(32)
    _logins[sid] = {"subject": subject, "email": email, "cleared": cleared,
                    "exp": time.time() + COOKIE_TTL}
    _sweep()
    return sid


def _sweep() -> None:
    now = time.time()
    for sid, s in list(_logins.items()):
        if s["exp"] < now:
            _logins.pop(sid, None)
    for state, p in list(_pending.items()):
        if p["exp"] < now:
            _pending.pop(state, None)


def _current(request) -> dict | None:
    sid = request.cookies.get(COOKIE)
    if not sid:
        return None
    s = _logins.get(sid)
    if not s or s["exp"] < time.time():
        _logins.pop(sid, None)
        return None
    return s


def _require(request) -> dict:
    s = _current(request)
    if s is None:
        raise PermissionError("not signed in")
    return s


# -- OAuth code flow -----------------------------------------------------------


async def login(request):
    """Redirect to Keycloak with PKCE. State and verifier stay here."""
    if not _issuer():
        return JSONResponse({"error": "CATKNOWS_OAUTH_ISSUER is not set"}, status_code=500)

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    _pending[state] = {"verifier": verifier, "exp": time.time() + 600}
    _sweep()

    cid, _ = _client()
    q = urllib.parse.urlencode({
        "client_id": cid,
        "response_type": "code",
        # mcp:tools carries the entitlement claim the panel needs; without it
        # the access token has no catknows_service and every account would
        # render as "awaiting approval".
        "scope": "openid email mcp:tools",
        "redirect_uri": f"{_base_url()}/auth/callback",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    # ?register=1 lands on Keycloak's registration form instead of the login
    # form — same parameters, different endpoint. The landing page's
    # "Get started" goes here so a stranger isn't greeted by a password field.
    endpoint = "registrations" if request.query_params.get("register") else "auth"
    return RedirectResponse(f"{_oidc(endpoint)}?{q}", status_code=302)


async def callback(request):
    """Exchange the code for tokens, verify the id_token, set the cookie."""
    if err := request.query_params.get("error"):
        return HTMLResponse(_page_error(f"Keycloak said: {err}"), status_code=400)

    state = request.query_params.get("state", "")
    code = request.query_params.get("code", "")
    pending = _pending.pop(state, None)
    if not pending or pending["exp"] < time.time():
        # Also the CSRF check: a callback we didn't initiate has no state here.
        return HTMLResponse(
            _page_error("That login link expired or didn't come from here. Try again."),
            status_code=400,
        )

    cid, secret = _client()
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": f"{_base_url()}/auth/callback",
        "client_id": cid,
        "code_verifier": pending["verifier"],
    }
    if secret:
        form["client_secret"] = secret

    try:
        import anyio

        payload = await anyio.to_thread.run_sync(_post_form, _oidc("token"), form)
    except Exception as e:  # network, 4xx from Keycloak, malformed JSON
        return HTMLResponse(_page_error(f"Could not reach the login server: {e}"),
                            status_code=502)

    claims = _verify_id_token(payload.get("id_token", ""))
    if claims is None:
        return HTMLResponse(_page_error("The login response didn't verify."),
                            status_code=400)

    # Same function the MCP server gates on, so the panel can never show a green
    # dot for an account whose tool calls are being refused.
    #
    # Read from the *access* token, not the id_token verified above: the
    # entitlement mapper lives on the mcp:tools scope, and /auth/login requests
    # that scope precisely so this answer is available here. Signature-checked
    # all the same — this decides what the user is told, and a
    # decoded-but-unverified token is a claim, not a fact.
    from .auth_oauth import may_use_service

    access = _verify_access_token(payload.get("access_token", ""))
    cleared = access is not None and not may_use_service(access)

    sid = _new_session(claims.get("sub", ""), claims.get("email", ""), cleared=cleared)
    # Back to the homepage, not /connect: the landing's account button shows the
    # state, and connecting Skool stays a deliberate click from there.
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        COOKIE, sid,
        max_age=COOKIE_TTL, httponly=True, samesite="lax",
        secure=not _base_url().startswith("http://localhost"),
        path="/",
    )
    return resp


def _post_form(url: str, form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _verify_id_token(token: str) -> dict | None:
    """Verify against the realm's JWKS. Audience here is our own client id."""
    if not token:
        return None
    import jwt
    from jwt import PyJWKClient

    cid, _ = _client()
    try:
        key = PyJWKClient(_oidc("certs"), cache_keys=True, lifespan=300)
        return jwt.decode(
            token, key.get_signing_key_from_jwt(token).key,
            algorithms=["RS256", "ES256"],
            audience=cid, issuer=_issuer(),
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except Exception:
        return None


def _verify_access_token(token: str) -> dict | None:
    """Verify the access token from our own code exchange, for its role claims.

    Audience is not checked here, and that is deliberate rather than an
    oversight: this token came back over TLS from the token endpoint in direct
    response to a code *we* issued with a verifier only we held. Its audience is
    whatever Keycloak minted for this client — often just "account" — so
    demanding one would reject every valid login. Signature, issuer and expiry
    still have to hold.

    The MCP server's check is the strict one and stays strict: there the token
    arrives from an untrusted caller, so `aud` is what proves it was minted for
    that server and not merely by this realm.
    """
    if not token:
        return None
    import jwt
    from jwt import PyJWKClient

    try:
        key = PyJWKClient(_oidc("certs"), cache_keys=True, lifespan=300)
        return jwt.decode(
            token, key.get_signing_key_from_jwt(token).key,
            algorithms=["RS256", "ES256"],
            issuer=_issuer(),
            options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": False},
        )
    except Exception:
        return None


async def logout(request):
    if sid := request.cookies.get(COOKIE):
        _logins.pop(sid, None)
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(COOKIE, path="/")
    return resp


async def status(request):
    """The landing page's account button asks here. Read-only, never a 401 —
    an anonymous visitor is an answer, not an error. Approval state stays on
    /connect on purpose; this only says signed-in and Skool-connected."""
    s = _current(request)
    if s is None:
        return JSONResponse({"signed_in": False, "skool_connected": False})
    stored = sessions.enabled() and sessions.load(s["subject"]) is not None
    return JSONResponse(
        {"signed_in": True, "email": s["email"], "skool_connected": stored})


async def password_login(request):
    """Inline sign-in from the landing page's account popover.

    Relays the credentials to Keycloak's token endpoint on this same box
    (direct access grant) and sets the same cookie the OAuth callback sets.
    The password passes through this process once and is never stored or
    logged. Registration and password reset stay on the Keycloak pages —
    email verification lives there. Keycloak's brute-force counter sees
    these attempts like any others."""
    # Same-origin only: a cross-site form must not be able to sign a browser
    # into an account of its choosing (login CSRF).
    origin = request.headers.get("origin")
    if origin is not None and origin != _base_url():
        return JSONResponse({"ok": False, "error": "Wrong origin."}, status_code=403)

    form = await request.form()
    email = str(form.get("email") or "").strip()
    password = str(form.get("password") or "")
    if not email or not password:
        return JSONResponse({"ok": False, "error": "Email and password, please."},
                            status_code=400)

    cid, secret = _client()
    body = {
        "grant_type": "password",
        "client_id": cid,
        "username": email,
        "password": password,
        "scope": "openid email mcp:tools",
    }
    if secret:
        body["client_secret"] = secret

    import urllib.error

    import anyio

    try:
        payload = await anyio.to_thread.run_sync(_post_form, _oidc("token"), body)
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("error_description", "")
        except Exception:
            detail = ""
        if "not fully set up" in detail:
            msg = "Confirm your email first — the link is in your inbox."
        else:
            msg = "Wrong email or password."
        return JSONResponse({"ok": False, "error": msg}, status_code=401)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"Could not reach the login server: {e}"},
            status_code=502)

    claims = _verify_id_token(payload.get("id_token", ""))
    if claims is None:
        return JSONResponse({"ok": False, "error": "The login response didn't verify."},
                            status_code=400)

    # Same entitlement read as the OAuth callback, for the same reason.
    from .auth_oauth import may_use_service

    access = _verify_access_token(payload.get("access_token", ""))
    cleared = access is not None and not may_use_service(access)

    sid = _new_session(claims.get("sub", ""), claims.get("email", ""), cleared=cleared)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        COOKIE, sid,
        max_age=COOKIE_TTL, httponly=True, samesite="lax",
        secure=not _base_url().startswith("http://localhost"),
        path="/",
    )
    return resp


# -- pages ---------------------------------------------------------------------


async def home(request):
    index = WEB / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse("<h1>catknows</h1><p><a href='/auth/login'>Sign in</a></p>")


async def static_page(request):
    """Serve the legal pages by name, extension optional."""
    name = request.path_params["name"]
    # Only the pages we published — never an arbitrary path off disk.
    allowed = {"privacy", "dpa", "legal", "impressum"}
    if name not in allowed:
        return HTMLResponse(_page_error("No such page."), status_code=404)
    f = WEB / f"{name}.html"
    return FileResponse(f) if f.exists() else HTMLResponse(
        _page_error("That page isn't published yet."), status_code=404)


async def connect(request):
    try:
        me = _require(request)
    except PermissionError:
        return RedirectResponse("/auth/login", status_code=302)

    stored = sessions.enabled() and sessions.load(me["subject"]) is not None
    return HTMLResponse(_page_connect(
        me["email"], stored,
        skool=sessions.label(me["subject"]) if stored else "",
        cleared=bool(me.get("cleared")),
    ))


async def delete_session(request):
    """GDPR art. 17, self-service."""
    try:
        me = _require(request)
    except PermissionError:
        return JSONResponse({"error": "not signed in"}, status_code=401)
    if not sessions.enabled():
        return JSONResponse({"error": "per-user sessions are off"}, status_code=400)

    from .remote_login import manager

    await manager.finish(me["subject"])
    existed = sessions.delete(me["subject"])
    return JSONResponse({"deleted": existed})


# -- the streamed login --------------------------------------------------------


async def connect_ws(ws: WebSocket):
    """Frames out, gestures in, until the cookie shows up or the user leaves."""
    from .remote_login import LoginError, manager

    # Authenticate before accepting: an unauthenticated socket must never get
    # far enough to start a browser.
    sid = ws.cookies.get(COOKIE)
    me = _logins.get(sid or "")
    if not me or me["exp"] < time.time():
        await ws.close(code=4401)
        return
    subject = me["subject"]

    await ws.accept()
    try:
        session = await manager.start(subject)
    except LoginError as e:
        await ws.send_json({"type": "error", "message": str(e)})
        await ws.close()
        return
    except Exception as e:
        await ws.send_json({"type": "error",
                            "message": f"Could not start the browser: {e}"})
        await ws.close()
        return

    await ws.send_json({"type": "ready", "width": session.page.viewport_size["width"],
                        "height": session.page.viewport_size["height"]})

    import anyio

    async def pump_frames() -> None:
        """Newest frame wins; check for a finished login between frames."""
        while True:
            data = await session.frames.get()
            await ws.send_json({"type": "frame", "data": data})
            if cookie := await manager.check_login(subject):
                # Plan §2a: check which Skool account this actually is, while
                # the browser is still open to ask.
                who = await manager.whoami(subject)
                sessions.save(subject, cookie)
                # Kept so the panel can name the account later, when the browser
                # that could answer this is long gone.
                sessions.save_label(subject, who.get("name") or who.get("email") or "")
                await manager.finish(subject)
                await ws.send_json({
                    "type": "done",
                    "skool": who.get("name") or who.get("email") or "",
                    # Surfaced, not enforced: a different address at Skool than
                    # at catknows is perfectly normal, so blocking on it would
                    # lock out real users. Naming the account lets the person
                    # themselves catch the case that matters — the wrong login.
                    "mismatch": bool(
                        who.get("email") and me.get("email")
                        and who["email"].lower() != me["email"].lower()
                    ),
                })
                return

    async def pump_input() -> None:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "bye":
                return
            try:
                await manager.input(subject, msg)
            except LoginError:
                return  # session went away underneath us

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(pump_frames)
            tg.start_soon(pump_input)
            # Whichever finishes first cancels the other.
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        await manager.finish(subject)
        with contextlib.suppress(Exception):
            await ws.close()


# -- markup --------------------------------------------------------------------
# Two small pages inline rather than a template engine: jinja2 isn't installed,
# and two f-strings don't justify adding it.

_SHELL = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>catknows &mdash; {title}</title><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
background:#1a1a2e;color:#e0e0e0;min-height:100vh;padding:40px 16px;line-height:1.65}}
.shell{{max-width:760px;margin:0 auto}}
h1{{font-size:1.6rem;color:#e94560;margin-bottom:6px;letter-spacing:-0.02em}}
.sub{{color:#a8adc0;font-size:0.92rem;margin-bottom:26px}}
.card{{background:#16213e;border:1px solid #0f3460;border-radius:10px;
padding:22px 24px;margin-bottom:18px}}
h2{{font-size:1.05rem;margin-bottom:10px}}
p{{color:#a8adc0;font-size:0.95rem;margin-bottom:10px}}
p strong{{color:#e0e0e0}}
a{{color:#e94560}}
button{{font:inherit;font-weight:600;padding:10px 18px;border-radius:7px;
border:1px solid #e94560;background:none;color:#e94560;cursor:pointer;
transition:background .3s,color .3s}}
button:hover{{background:#e94560;color:#12121f}}
button.ghost{{border-color:#0f3460;color:#a8adc0}}
button.ghost:hover{{background:#0f3460;color:#e0e0e0}}
button:disabled{{opacity:.5;cursor:default}}
#stage{{margin-top:16px;border:1px solid #0f3460;border-radius:10px;
overflow:hidden;background:#0f1729;display:none}}
#stage img{{display:block;width:100%;touch-action:none;cursor:default}}
#msg{{font-size:0.9rem;color:#a8adc0;margin-top:12px;min-height:1.4em}}
.ok{{color:#5ad07a}}.bad{{color:#e94560}}
.foot{{margin-top:28px;font-size:0.85rem;color:#a8adc0}}
.foot a{{margin-right:18px}}
/* The account panel: small, top right, out of the way of the streamed login. */
#panel{{position:fixed;top:14px;right:14px;z-index:10;background:#16213e;
border:1px solid #0f3460;border-radius:9px;padding:11px 14px;max-width:290px;
font-size:0.85rem;box-shadow:0 6px 20px rgba(0,0,0,.35)}}
#panel .row{{display:flex;align-items:center;gap:8px}}
#panel .who{{color:#e0e0e0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
#panel .mail{{color:#a8adc0;font-size:0.78rem;margin-top:3px;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}}
#panel a{{font-size:0.78rem}}
.dot{{width:9px;height:9px;border-radius:50%;flex:0 0 auto;background:#6b7280}}
.dot.on{{background:#5ad07a;box-shadow:0 0 0 3px rgba(90,208,122,.16)}}
.dot.off{{background:#e9a545}}
@media (max-width:640px){{#panel{{position:static;max-width:none;margin-bottom:18px}}}}
</style></head><body><div class="shell">{body}
<div class="foot"><a href="/privacy">Privacy</a><a href="/dpa">Processing agreement</a>
<a href="/impressum">Impressum</a></div></div>{script}</body></html>"""


def _page_error(msg: str) -> str:
    from html import escape

    return _SHELL.format(
        title="Problem", script="",
        body=f'<h1>catknows</h1><div class="card"><h2>That didn\'t work</h2>'
             f'<p>{escape(msg)}</p><p><a href="/">Back to the start</a></p></div>',
    )


def _panel(email: str, stored: bool, skool: str, cleared: bool) -> str:
    """The little account window, top right: what is connected, and is it live.

    Green means a Skool session is stored *and* the account is cleared — the
    two conditions a tool call actually needs. Amber means one of them is
    missing, and the card below says which. It deliberately does not claim the
    Skool cookie is still valid: only a request to Skool could answer that, and
    a green dot bought with a request on every page load is a poor trade.
    """
    from html import escape

    if stored and cleared:
        cls, who = "on", skool or "Skool connected"
    elif stored:
        cls, who = "off", (skool or "Skool connected") + " — awaiting approval"
    else:
        cls, who = "off", "No Skool account connected"

    return (
        f'<div id="panel"><div class="row"><span class="dot {cls}"></span>'
        f'<span class="who">{escape(who)}</span></div>'
        f'<div class="mail">{escape(email or "signed in")} &middot; '
        f'<a href="/auth/logout">sign out</a></div></div>'
    )


def _page_connect(email: str, stored: bool, skool: str = "", cleared: bool = True) -> str:
    from html import escape

    if not cleared:
        # The account is real and the address confirmed; what's missing is the
        # operator's yes. Saying so beats letting them connect Skool and meet an
        # unexplained 401 at the first tool call.
        state = ('<p class="ok">Your email is confirmed and your account is waiting '
                 'to be switched on. You will be able to use the tools once it is. '
                 'You can connect Skool now either way.</p>')
    elif stored:
        state = ('<p class="ok">A Skool session is stored for your account. '
                 'Connecting again replaces it.</p>')
    else:
        state = ('<p>No Skool session stored yet &mdash; the tools stay unavailable '
                 'until you connect one.</p>')

    body = f"""{_panel(email, stored, skool, cleared)}
<h1><a href="/" style="color:inherit;text-decoration:none">catknows</a></h1>
<div class="sub"><a href="/">&larr; back to the homepage</a> &middot;
Signed in as {escape(email or 'your account')} &mdash;
<a href="/auth/logout">sign out</a></div>

<div class="card">
  <h2>Connect Skool</h2>
  {state}
  <p>
    A browser opens <strong>on the server</strong> and is streamed here. You log
    in to Skool inside it &mdash; email and password, or Google/Apple. Your
    password goes to Skool, never to us; we only keep the session cookie the
    login produces, encrypted.
  </p>
  <p>
    <button id="go">Open Skool login</button>
    {'<button class="ghost" id="del">Delete stored session</button>' if stored else ''}
  </p>
  <div id="stage"><img id="screen" alt="Skool login, streamed from the server"></div>
  <div id="msg"></div>
</div>"""

    script = """<script>
(function(){
var go=document.getElementById('go'), del=document.getElementById('del'),
    stage=document.getElementById('stage'), img=document.getElementById('screen'),
    msg=document.getElementById('msg'), ws=null, size=null;

function say(t,cls){ msg.textContent=t; msg.className=cls||''; }

// The stream is scaled to fit; clicks must be mapped back to browser pixels.
function at(e){
  var r=img.getBoundingClientRect();
  var t=(e.touches&&e.touches[0])||e;
  return { x:(t.clientX-r.left)/r.width*size.width,
           y:(t.clientY-r.top)/r.height*size.height };
}
function send(m){ if(ws&&ws.readyState===1) ws.send(JSON.stringify(m)); }

go.onclick=function(){
  go.disabled=true; say('Starting a browser on the server…');
  ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/connect/ws');

  ws.onmessage=function(ev){
    var m=JSON.parse(ev.data);
    if(m.type==='ready'){ size={width:m.width,height:m.height}; stage.style.display='block';
      say('Log in to Skool in the window above.'); img.focus(); }
    else if(m.type==='frame'){ img.src='data:image/jpeg;base64,'+m.data; }
    else if(m.type==='done'){
      var who=m.skool?' as '+m.skool:'';
      if(m.mismatch){
        // Not an error: worth a look, because it's how you notice you signed
        // into the wrong Skool account.
        say('Connected'+who+' — note that this is a different email than your '
            +'catknows account. If that was not intended, delete the session '
            +'and connect again.','');
        stage.style.display='none';
      } else {
        say('Connected'+who+'. Your Skool session is stored, encrypted.','ok');
        stage.style.display='none'; setTimeout(function(){location.reload();},1400);
      }
    }
    else if(m.type==='error'){ say(m.message,'bad'); go.disabled=false; }
  };
  ws.onclose=function(){ if(!msg.className) say('Stream closed.'); go.disabled=false; };
  ws.onerror=function(){ say('Connection failed.','bad'); go.disabled=false; };
};

// Pointer and touch both map to the same mouse events server-side.
img.addEventListener('pointerdown',function(e){ e.preventDefault(); img.focus();
  var p=at(e); send({type:'mousePressed',x:p.x,y:p.y}); });
img.addEventListener('pointerup',function(e){ e.preventDefault();
  var p=at(e); send({type:'mouseReleased',x:p.x,y:p.y}); });
img.addEventListener('pointermove',function(e){ if(e.buttons){ var p=at(e);
  send({type:'mouseMoved',x:p.x,y:p.y}); } });
img.addEventListener('wheel',function(e){ e.preventDefault(); var p=at(e);
  send({type:'scroll',x:p.x,y:p.y,deltaX:e.deltaX,deltaY:e.deltaY}); },{passive:false});

// An <img> can't take keyboard focus without this, and without focus there is
// nothing to type into.
img.tabIndex=0;
img.addEventListener('keydown',function(e){
  e.preventDefault();
  send({type:'keyDown',key:e.key,code:e.code,keyCode:e.keyCode,
        text:e.key.length===1?e.key:''});
});
img.addEventListener('keyup',function(e){ e.preventDefault();
  send({type:'keyUp',key:e.key,code:e.code,keyCode:e.keyCode}); });

if(del) del.onclick=function(){
  if(!confirm('Delete the stored Skool session? The tools stop working until you connect again.')) return;
  fetch('/session/delete',{method:'POST'}).then(function(r){return r.json();})
    .then(function(){ location.reload(); });
};
})();
</script>"""
    return _SHELL.format(title="Connect Skool", body=body, script=script)


# -- app -----------------------------------------------------------------------

@contextlib.asynccontextmanager
async def _lifespan(app):
    """Kill any live browser on shutdown — orphaned Chromiums eat the box."""
    yield
    from .remote_login import manager

    await manager.shutdown()


app = Starlette(
    lifespan=_lifespan,
    routes=[
        Route("/", home),
        Route("/auth/login", login),
        Route("/auth/callback", callback),
        Route("/auth/logout", logout, methods=["GET", "POST"]),
        Route("/auth/status", status),
        Route("/auth/password", password_login, methods=["POST"]),
        Route("/connect", connect),
        Route("/session/delete", delete_session, methods=["POST"]),
        WebSocketRoute("/connect/ws", connect_ws),
        # Last: a bare name falls through to the published legal pages.
        Route("/{name:str}", static_page),
    ],
)


def main() -> None:
    import uvicorn

    if not sessions.enabled():
        raise SystemExit(
            "CATKNOWS_SESSION_DIR is not set — the dashboard exists to fill the "
            "per-user session store, so without it there is nothing to do."
        )

    # Starlette defines the WebSocket API but uvicorn speaks the protocol, and
    # without wsproto/websockets it answers every upgrade with a plain 404 —
    # which reads like a routing bug and costs an hour to find. Fail loudly here
    # instead: the streamed login is the whole point of this service.
    try:
        import wsproto  # noqa: F401
    except ImportError:
        try:
            import websockets  # noqa: F401
        except ImportError:
            raise SystemExit(
                "No WebSocket library installed, so the streamed login would 404 "
                "on every connection attempt. Install one:\n"
                "    pip install wsproto"
            ) from None
    uvicorn.run(
        app,
        host=_env("CATKNOWS_DASHBOARD_HOST", "127.0.0.1"),
        port=int(_env("CATKNOWS_DASHBOARD_PORT", "8001")),
        # Behind Caddy: it terminates TLS and sets the forwarded headers.
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )


def _self_check() -> None:
    """python -m catknows.dashboard --self-check"""
    import asyncio

    os.environ["CATKNOWS_OAUTH_ISSUER"] = "https://auth.example.app/realms/t"
    os.environ["CATKNOWS_DASHBOARD_URL"] = "https://catknows.app"

    # Session cookies: opaque id in, subject stays server-side.
    sid = _new_session("subject-1", "a@b.c")
    assert sid not in ("subject-1", "a@b.c")
    assert _logins[sid]["subject"] == "subject-1"

    class Req:
        def __init__(self, sid=None):
            self.cookies = {COOKIE: sid} if sid else {}
            self.query_params = {}

    assert _current(Req(sid))["subject"] == "subject-1"
    assert _current(Req("forged")) is None, "an unknown cookie must not authenticate"
    assert _current(Req()) is None
    _logins[sid]["exp"] = time.time() - 1
    assert _current(Req(sid)) is None, "an expired session must not authenticate"
    assert sid not in _logins, "expiry must evict, not just reject"

    # PKCE: verifier stays here, only the S256 challenge goes out.
    r = asyncio.run(login(Req()))
    loc = r.headers["location"]
    assert "code_challenge_method=S256" in loc and "code_challenge=" in loc
    state = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["state"][0]
    verifier = _pending[state]["verifier"]
    assert verifier not in loc, "the verifier must never leave the server"
    expect = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert f"code_challenge={expect}" in loc
    assert "redirect_uri=https%3A%2F%2Fcatknows.app%2Fauth%2Fcallback" in loc

    # A callback without matching state is refused (CSRF).
    class QReq:
        def __init__(self, **q):
            self.query_params = q
            self.cookies = {}

    out = asyncio.run(callback(QReq(state="never-issued", code="x")))
    assert out.status_code == 400, out.status_code
    # ...and a replayed state is gone after first use.
    _pending[state] = {"verifier": verifier, "exp": time.time() + 60}
    asyncio.run(callback(QReq(state=state, code="bogus")))
    assert state not in _pending, "state must be single-use"

    # An unverifiable id_token yields no session.
    assert _verify_id_token("") is None
    assert _verify_id_token("not-a-jwt") is None

    # /auth/status: the landing page's account button. Anonymous is an answer
    # (both false), never an error; signed-in carries the email.
    import json

    sid2 = _new_session("subject-2", "s@t.u")
    anon = json.loads(asyncio.run(status(Req())).body)
    assert anon == {"signed_in": False, "skool_connected": False}, anon
    known = json.loads(asyncio.run(status(Req(sid2))).body)
    assert known["signed_in"] is True and known["email"] == "s@t.u", known

    # ?register=1 must land on Keycloak's registration form, not the login form.
    reg = asyncio.run(login(QReq(register="1")))
    assert "/registrations?" in reg.headers["location"]

    # /auth/password: cross-site posts are refused (login CSRF), empty fields
    # are a 400, and neither path crashes. The happy path needs a live realm;
    # check-realm.sh plus a browser cover that.
    class FReq:
        def __init__(self, origin=None, **fields):
            self.headers = {"origin": origin} if origin else {}
            self.cookies = {}
            self._fields = fields

        async def form(self):
            return self._fields

    foreign = asyncio.run(password_login(FReq(origin="https://evil.example",
                                              email="a@b.c", password="x")))
    assert foreign.status_code == 403, foreign.status_code
    empty = asyncio.run(password_login(FReq()))
    assert empty.status_code == 400 and json.loads(empty.body)["ok"] is False

    # The panel must never promise more than the MCP server will honour.
    green = _panel("a@b.c", stored=True, skool="Ada L.", cleared=True)
    assert 'class="dot on"' in green and "Ada L." in green
    for html in (_panel("a@b.c", stored=True, skool="Ada L.", cleared=False),
                 _panel("a@b.c", stored=False, skool="", cleared=True)):
        assert 'class="dot on"' not in html, \
            "green requires BOTH a stored session and a cleared account"
    assert "awaiting approval" in _panel("a@b.c", True, "Ada L.", False)

    # It renders user-controlled text, so it has to escape it.
    assert "<script>" not in _panel("<script>x</script>", True, "<script>y</script>", True)

    # A user who is not cleared yet must be told, not left to hit a silent 401.
    waiting = _page_connect("a@b.c", stored=False, cleared=False)
    assert "waiting to be switched on" in waiting

    # Static pages: only the published names, no path traversal.
    for bad in ("../PRIVACY", "..%2Fetc%2Fpasswd", "index", "secret"):
        class PReq:
            path_params = {"name": bad}
        assert asyncio.run(static_page(PReq())).status_code == 404, bad

    print("dashboard self-check OK (opaque sessions, PKCE, CSRF, page allowlist, panel)")


if __name__ == "__main__":
    import sys

    if "--self-check" in sys.argv:
        _self_check()
    else:
        main()

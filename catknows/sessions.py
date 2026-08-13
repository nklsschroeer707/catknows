"""Per-user Skool sessions for the hosted server, encrypted at rest.

One file per OAuth subject under CATKNOWS_SESSION_DIR, encrypted with a key
from CATKNOWS_SESSION_KEY. Without both, the hosted server has no business
storing anyone's Skool cookie — so both are required before this module does
anything.

Why encrypted at all: the stored value *is* the Skool session. Anyone reading
the file is logged in as that user. Disk-at-rest encryption means a stray
backup or a snapshot of the volume isn't a pile of live sessions.

Deleting is as first-class as writing: a user must be able to take their
session back out (GDPR art. 17).
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


def store_dir() -> Path | None:
    """Where sessions live, or None when per-user sessions are off."""
    d = os.environ.get("CATKNOWS_SESSION_DIR", "").strip()
    return Path(d) if d else None


def enabled() -> bool:
    return store_dir() is not None


def _fernet():
    from cryptography.fernet import Fernet

    key = os.environ.get("CATKNOWS_SESSION_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "CATKNOWS_SESSION_DIR is set but CATKNOWS_SESSION_KEY is not. "
            "Generate one with: python -c \"from cryptography.fernet import "
            'Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def _path(subject: str) -> Path:
    """File for this subject — hashed, so no user id ends up in a filename.

    Keycloak subjects are UUIDs today, but the filename is derived rather than
    trusted: a subject with a slash or ".." in it must not escape the store.
    """
    if not subject:
        raise ValueError("no subject — the token carried no identity")
    name = hashlib.sha256(subject.encode()).hexdigest()[:32]
    return store_dir() / f"{name}.session"


def save(subject: str, cookie_header: str) -> None:
    d = store_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = _path(subject)
    p.write_bytes(_fernet().encrypt(cookie_header.encode()))
    p.chmod(0o600)  # ponytail: file perms only; full-disk encryption is the host's job


def load(subject: str) -> str | None:
    """Return the stored cookie header, or None if there is none / it's unreadable."""
    p = _path(subject)
    if not p.exists():
        return None
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(p.read_bytes()).decode()
    except InvalidToken:
        # Key rotated or file tampered with — treat as "no session" so the
        # user can just store a new one instead of hitting a 500 forever.
        return None


def delete(subject: str) -> bool:
    """Remove this user's session. True if there was one."""
    p = _path(subject)
    _label_path(subject).unlink(missing_ok=True)
    if not p.exists():
        return False
    p.unlink()
    return True


# -- the connected account's name ----------------------------------------------
# So the dashboard can say *which* Skool account is connected instead of just
# "one is". Deliberately a separate file from the session: this is a display
# label, not a credential, and it must never be the reason the encrypted blob
# gets opened. Plain text for the same reason — it holds nothing that isn't
# already on the user's own screen.


def _label_path(subject: str) -> Path:
    return _path(subject).with_suffix(".who")


def save_label(subject: str, label: str) -> None:
    if not label:
        return
    p = _label_path(subject)
    p.write_text(label[:200], encoding="utf-8")  # bounded: it goes into a web page
    p.chmod(0o600)


def label(subject: str) -> str:
    """The connected Skool account's name, or empty if unknown.

    Empty is normal, not an error: sessions stored before this existed have no
    label, and Skool's page payload is not an API contract.
    """
    p = _label_path(subject)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _cli(argv: list[str]) -> int:
    """Admin-side session management, so a cookie never has to cross a chat.

        catknows-session store <subject>    # cookie on stdin, never in argv
        catknows-session list
        catknows-session delete <subject>

    Run as the service user on the box that holds the store. Reads stdin so
    the cookie stays out of the shell history and out of `ps`.
    """
    usage = "usage: catknows-session {store <subject>|list|delete <subject>|self-check}"
    if not argv or argv[0] not in ("store", "list", "delete", "self-check"):
        print(usage, file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]

    if cmd == "self-check":
        _self_check()
        return 0

    if not enabled():
        print("CATKNOWS_SESSION_DIR is not set — per-user sessions are off.",
              file=sys.stderr)
        return 1

    if cmd == "list":
        d = store_dir()
        files = sorted(d.glob("*.session")) if d.exists() else []
        # Subjects are hashed, so this can only ever count — by design.
        print(f"{len(files)} stored session(s) in {d}")
        return 0

    if not rest:
        print(usage, file=sys.stderr)
        return 2
    subject = rest[0]

    if cmd == "delete":
        print("deleted" if delete(subject) else "nothing stored for that subject")
        return 0

    import getpass

    cookie = getpass.getpass("Paste the Skool cookie header (input hidden): ").strip()
    if "auth_token=" not in cookie:
        print("error: that has no auth_token= in it.", file=sys.stderr)
        return 1
    save(subject, cookie)
    print(f"stored, encrypted, for subject {subject}")
    return 0


def main() -> int:
    return _cli(sys.argv[1:])


def _self_check() -> None:
    """python -m catknows.sessions — round-trip, isolation, deletion."""
    import tempfile

    from cryptography.fernet import Fernet

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CATKNOWS_SESSION_DIR"] = tmp
        os.environ["CATKNOWS_SESSION_KEY"] = Fernet.generate_key().decode()

        assert enabled()
        assert load("alice") is None, "unknown subject has no session"

        save("alice", "auth_token=aaa; aws-waf-token=x")
        save("bob", "auth_token=bbb")
        assert load("alice") == "auth_token=aaa; aws-waf-token=x"
        assert load("bob") == "auth_token=bbb", "bob must not see alice's session"

        raw = _path("alice").read_bytes()
        assert b"aaa" not in raw, "the cookie must not sit on disk in the clear"
        assert "alice" not in _path("alice").name, "the subject must not be the filename"

        # Path traversal: a hostile subject must stay inside the store.
        assert _path("../../etc/passwd").parent == Path(tmp)

        # The display label lives beside the session, never inside it.
        assert label("alice") == "", "no label is the normal case, not an error"
        save_label("alice", "Alice A.")
        save_label("bob", "Bob B.")
        assert label("alice") == "Alice A." and label("bob") == "Bob B."
        assert b"aaa" not in _label_path("alice").read_bytes(), \
            "the label file must never carry the session"

        assert delete("alice") is True and load("alice") is None, "deletion must stick"
        assert label("alice") == "", "deleting must take the label with it (art. 17)"
        assert delete("alice") is False, "deleting twice is not an error"
        assert load("bob") == "auth_token=bbb", "deleting alice must not touch bob"
        assert label("bob") == "Bob B.", "deleting alice must not touch bob's label"

        # A rotated key reads as "no session", not as a crash.
        os.environ["CATKNOWS_SESSION_KEY"] = Fernet.generate_key().decode()
        assert load("bob") is None, "unreadable session must degrade to None"

    print("sessions self-check OK (isolation, encryption at rest, deletion, key rotation)")


if __name__ == "__main__":
    raise SystemExit(main())

"""The docs must say what the server does. Run: python test_mcp_tools_doc.py

Three checks, all against the real server, not against a list kept by hand:

1. Every @mcp.tool() appears in mcp-tools.md. That cheatsheet is the only thing
   workspaces read, so a tool missing there is a tool no agent knows about.
   Word boundaries matter: a naive `name in doc` counts get_post as documented
   because get_post_comments contains it.
2. The tool counts in README.md, AGENTS.md and the catknows-update skill
   (written `**31 tools**` and `**62 tools**`) equal what the server registers
   without and with CATKNOWS_ALLOW_WRITE=1.
3. Every tool that exists only with that flag is named in AGENTS.md, so the
   list of writers there cannot fall behind.
"""
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent

# Files that state the tool counts, as `**N tools**`: first the number without
# the write flag, then the number with it.
COUNT_DOCS = ("README.md", "AGENTS.md", ".claude/skills/catknows-update/SKILL.md")
COUNT_RE = re.compile(r"\*\*(\d+) tools\*\*")

_LIST_TOOLS = (
    "import asyncio, json; from catknows import mcp_server as m; "
    "print(json.dumps(sorted(t.name for t in asyncio.run(m.mcp.list_tools()))))"
)


def _server_tools(allow_write: bool) -> list[str]:
    """Tool names the server registers, from a fresh process (the flag is read at import)."""
    env = dict(os.environ)
    env["CATKNOWS_ALLOW_WRITE"] = "1" if allow_write else "0"
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH", "")]))
    done = subprocess.run([sys.executable, "-c", _LIST_TOOLS], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, f"could not list the server's tools:\n{done.stderr[-800:]}"
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_every_tool_documented():
    src = (ROOT / "catknows" / "mcp_server.py").read_text(encoding="utf-8")
    doc = (ROOT / "workspaces" / "_shared" / "references" / "mcp-tools.md").read_text(encoding="utf-8")
    tools = re.findall(r"@mcp\.tool\(\)\s*\n\s*def\s+(\w+)", src)
    assert tools, "no tools found — did the @mcp.tool() decorator change?"
    missing = [t for t in tools if not re.search(rf"\b{t}\b", doc)]
    assert not missing, f"undocumented tools: {missing}"
    # The source scan and the running server must agree, or this check is blind.
    served = _server_tools(allow_write=True)
    assert sorted(tools) == served, (
        f"source scan and server differ: {sorted(set(tools) ^ set(served))}")
    return len(tools)


def test_tool_counts_match_the_server():
    default = len(_server_tools(allow_write=False))
    with_write = len(_server_tools(allow_write=True))
    assert default < with_write, "the write flag should add tools"
    for rel in COUNT_DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        found = [int(n) for n in COUNT_RE.findall(text)]
        assert found == [default, with_write], (
            f"{rel} says {found} tools (as `**N tools**`), the server has "
            f"{[default, with_write]} (without, with CATKNOWS_ALLOW_WRITE=1)")


def test_every_write_tool_is_named_in_agents_md():
    writers = sorted(set(_server_tools(allow_write=True)) - set(_server_tools(allow_write=False)))
    assert writers, "no write tools found — did the write flag change?"
    doc = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    missing = [t for t in writers if not re.search(rf"`{t}`", doc)]
    assert not missing, f"write tools missing from AGENTS.md: {missing}"


if __name__ == "__main__":
    n = test_every_tool_documented()
    test_tool_counts_match_the_server()
    test_every_write_tool_is_named_in_agents_md()
    print(f"ok — all {n} tools documented, counts match the server, writers listed")

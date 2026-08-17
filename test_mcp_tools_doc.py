"""Every @mcp.tool() must appear in mcp-tools.md. Run: python test_mcp_tools_doc.py

The cheatsheet is the only thing workspaces read — a tool missing here is a
tool no agent knows about. Word boundaries matter: a naive `name in doc`
counts get_post as documented because get_post_comments contains it.
"""
import re
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def test_every_tool_documented():
    src = (ROOT / "catknows" / "mcp_server.py").read_text(encoding="utf-8")
    doc = (ROOT / "workspaces" / "_shared" / "references" / "mcp-tools.md").read_text(encoding="utf-8")
    tools = re.findall(r"@mcp\.tool\(\)\s*\n\s*def\s+(\w+)", src)
    assert tools, "no tools found — did the @mcp.tool() decorator change?"
    missing = [t for t in tools if not re.search(rf"\b{t}\b", doc)]
    assert not missing, f"undocumented tools: {missing}"
    return len(tools)


if __name__ == "__main__":
    n = test_every_tool_documented()
    print(f"ok — all {n} tools documented")

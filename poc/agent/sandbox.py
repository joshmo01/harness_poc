"""Code mode: run agent-written Python against a generated tool SDK.

POC: a separate `python -E -s` process in a temp directory with a timeout.
Production: an AgentCore Code Interpreter session.

The SDK functions are thin stubs that call the *gateway* with a short-lived
token scoped to one case and the granted read tools, so every call from code
gets the same registry, pin, Cedar and audit checks as a direct call. Only the
script's printed output returns to the model.
"""
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from poc.common.config import GATEWAY_URL, MCP_PROTOCOL_VERSION
from poc.common.tokens import issue_code_token

SDK_HEADER = '''"""Generated Yutrix tool SDK (code-mode session). Every function calls the MCP gateway."""
import json, os, urllib.request, uuid

_GATEWAY = os.environ["YUTRIX_GATEWAY_URL"]
_TOKEN = os.environ["YUTRIX_CODE_TOKEN"]


class ToolDenied(Exception):
    pass


def _call(name, arguments):
    body = json.dumps({"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}}).encode()
    req = urllib.request.Request(_GATEWAY + "/mcp", data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + _TOKEN,
        "MCP-Protocol-Version": "%s", "Mcp-Method": "tools/call", "Mcp-Name": name,
        "X-Trace-Id": os.environ.get("YUTRIX_TRACE_ID", "")})
    with urllib.request.urlopen(req, timeout=20) as r:
        result = json.loads(r.read())["result"]
    if result.get("isError"):
        raise ToolDenied(result["content"][0]["text"])
    return result.get("structuredContent")
''' % MCP_PROTOCOL_VERSION

MAX_OUTPUT = 3000


def _sdk(tools: list[str]) -> str:
    funcs = []
    for t in tools:
        fn = t.replace(".", "__")
        funcs.append(f"\n\ndef {fn}(**arguments):\n    return _call({t!r}, arguments)\n")
    return SDK_HEADER + "".join(funcs)


def run_code(code: str, *, user_claims: dict, case_id: str, tools: list[str], trace_id: str,
             timeout: int = 20) -> dict:
    token = issue_code_token(user_claims, case_id, tools)
    with tempfile.TemporaryDirectory(prefix="code-session-") as d:
        Path(d, "yutrix_tools.py").write_text(_sdk(tools))
        Path(d, "main.py").write_text(textwrap.dedent(code))
        try:
            p = subprocess.run([sys.executable, "-E", "-s", "-B", "main.py"], cwd=d, capture_output=True, text=True,
                               timeout=timeout, env={"YUTRIX_GATEWAY_URL": GATEWAY_URL, "YUTRIX_CODE_TOKEN": token,
                                                     "YUTRIX_TRACE_ID": trace_id, "PATH": "/usr/bin:/bin"})
        except subprocess.TimeoutExpired:
            return {"ok": False, "stdout": "", "stderr": f"timed out after {timeout}s"}
    out = p.stdout[-MAX_OUTPUT:]
    parsed = None
    try:
        parsed = json.loads(out)
    except ValueError:
        pass
    return {"ok": p.returncode == 0, "stdout": out, "stderr": p.stderr[-1500:], "json": parsed,
            "sdk_functions": [t.replace(".", "__") for t in tools]}

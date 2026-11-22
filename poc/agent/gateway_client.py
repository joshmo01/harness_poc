"""The agent's view of the gateway: three meta-tools plus run_code.

The agent starts with only `search_tools`, `get_tool_schema` and `run_code`
in context. Real tool schemas load on demand (progressive discovery).
"""
import json
import uuid

import httpx

from poc.common.config import GATEWAY_URL, MCP_PROTOCOL_VERSION

META_TOOLS = [
    {"name": "search_tools", "description": "Find tools you may use, by describing the task.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}},
    {"name": "get_tool_schema", "description": "Load full schemas for named tools.",
     "inputSchema": {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "run_code", "description": "Run Python that calls granted tools via the yutrix_tools SDK; only printed output returns.",
     "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}, "tools": {"type": "array"}}}},
]


class GatewayClient:
    def __init__(self, token: str, trace_id: str | None = None, case_id: str | None = None):
        self.token, self.trace_id, self.case_id = token, trace_id or uuid.uuid4().hex[:12], case_id
        self.loaded_schemas: dict[str, dict] = {}

    def _post(self, method: str, params: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.token}", "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
                   "Mcp-Method": method, "X-Trace-Id": self.trace_id}
        if params.get("name"):
            headers["Mcp-Name"] = params["name"]
        if self.case_id:
            headers["X-Case-Id"] = self.case_id
        r = httpx.post(f"{GATEWAY_URL}/mcp", headers=headers, timeout=30,
                       json={"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params})
        r.raise_for_status()
        return r.json()["result"]

    def search_tools(self, query: str) -> list[dict]:
        return self._post("tools/call", {"name": "x_search_tools", "arguments": {"query": query}})["structuredContent"]["tools"]

    def get_tool_schema(self, names: list[str]) -> dict[str, dict]:
        tools = {t["name"]: t for t in self._post("tools/list", {})["tools"]}
        found = {n: tools[n] for n in names if n in tools}
        self.loaded_schemas.update(found)
        return found

    def call_tool(self, name: str, args: dict) -> dict:
        res = self._post("tools/call", {"name": name, "arguments": args})
        data = res.get("structuredContent") or {}
        if res.get("isError") and not data:
            data = {"denied": False, "error": res.get("content", [{}])[0].get("text")}
        return {"ok": not res.get("isError"), "data": data, "chars": len(json.dumps(data))}

    def context_stats(self, total_tools: int) -> dict:
        meta = len(json.dumps(META_TOOLS))
        loaded = len(json.dumps(list(self.loaded_schemas.values())))
        return {"meta_tool_chars": meta, "loaded_schema_chars": loaded,
                "schemas_loaded": len(self.loaded_schemas), "tools_available": total_tools}

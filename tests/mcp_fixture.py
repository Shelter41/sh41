"""Small deterministic MCP fixture, usable over stdio or by the HTTP test handler."""
import json
import sys
from pathlib import Path


def reply(request):
    method = request.get("method")
    if "id" not in request:
        return None
    if method == "initialize":
        result = {"protocolVersion": request.get("params", {}).get("protocolVersion", "2025-03-26"),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "sh41-test", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "stamp", "description": "Return a deterministic acceptance marker",
                             "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "MCP_ACCEPTANCE_OK"}], "isError": False}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": request["id"],
                "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request["id"], "result": result}


if __name__ == "__main__":
    for line in sys.stdin:
        request = json.loads(line)
        if request.get("method") == "tools/call" and len(sys.argv) > 1:
            Path(sys.argv[1]).write_text("called")
        response = reply(request)
        if response:
            print(json.dumps(response), flush=True)

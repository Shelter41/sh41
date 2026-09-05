import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sh41_local.credentials import import_native
from sh41_local.service import AgentService
from sh41_local.spec import AgentSpec, Inference, MCP, Source

from mcp_fixture import reply
from test_live import finish
from test_opencode import CompletionHandler


class MCPHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if request.get("method") == "tools/call":
            self.server.calls += 1
        response = reply(request)
        if response is None:
            self.send_response(202)
            self.end_headers()
            return
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_response(405)
        self.end_headers()


@pytest.mark.docker
@pytest.mark.live
@pytest.mark.skipif(os.environ.get("SH41_TEST_MCP") != "1", reason="Set SH41_TEST_MCP=1")
@pytest.mark.parametrize("harness", ["opencode", "codex", "claude-code"])
def test_both_mcp_transports_through_real_harness(tmp_path, harness):
    remote = ThreadingHTTPServer(("127.0.0.1", 0), MCPHandler)
    remote.calls = 0
    completion = ThreadingHTTPServer(("127.0.0.1", 0), CompletionHandler)
    completion.requests, completion.use_tools = [], True
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (remote, completion)]
    for thread in threads:
        thread.start()
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy2(Path(__file__).with_name("mcp_fixture.py"), source / "mcp_fixture.py")
    root = tmp_path / "runtime"
    service = AgentService(root)
    deployment = None
    try:
        host = os.environ.get("SH41_TEST_HOST", "host.docker.internal")
        inference = Inference()
        if harness == "opencode":
            inference = Inference(provider="openai-compatible", base_url=f"http://{host}:{completion.server_port}/v1")
        else:
            import_native(root, harness)
        spec = AgentSpec(agent="mcp-test", harness=harness, source=Source(path=str(source)),
            model="fixture-model" if harness == "opencode" else None, inference=inference,
            mcp={"local": MCP(transport="stdio", command=["python", "/workspace/agent/mcp_fixture.py",
                                                         "/workspace/agent/local-called.txt"]),
                 "remote": MCP(transport="http", url=f"http://{host}:{remote.server_port}/mcp")})
        deployment = service.deploy(spec)
        finish(service, spec.agent, "Call the stamp tool from BOTH configured MCP servers (local and remote), exactly once each. Then reply COMPLETE. Do not use other tools.")
        assert remote.calls > 0, "Remote MCP tool was not called"
        work = root / "agents" / deployment["agent_id"] / "work"
        assert (work / "local-called.txt").exists(), "Stdio MCP tool was not called"
    finally:
        if deployment:
            service.provider.remove(deployment)
        for server in (remote, completion):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()
        shutil.rmtree(root, ignore_errors=True)

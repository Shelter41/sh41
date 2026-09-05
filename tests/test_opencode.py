import json
import os
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sh41_local.service import AgentService
from sh41_local.spec import AgentSpec, Inference

from test_live import finish


class CompletionHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        data = json.dumps({"object": "list", "data": [{"id": "fixture-model", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        time.sleep(getattr(self.server, "delay", 0))
        text = "SH41_FIXTURE_OK"
        chunk = {"id": "chatcmpl-fixture", "object": "chat.completion.chunk", "created": 1,
                 "model": "fixture-model", "choices": [{"index": 0,
                     "delta": {"role": "assistant", "content": text}, "finish_reason": None}]}
        end = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        if getattr(self.server, "use_tools", False):
            names = [tool["function"]["name"] for tool in body.get("tools", [])
                     if "stamp" in tool.get("function", {}).get("name", "")]
            used = sum(message.get("role") == "tool" for message in body.get("messages", []))
            if used < len(names):
                chunk["choices"][0]["delta"] = {"role": "assistant", "tool_calls": [{"index": 0,
                    "id": f"call_{used}", "type": "function", "function": {"name": names[used], "arguments": "{}"}}]}
                end["choices"][0]["finish_reason"] = "tool_calls"
        if body.get("stream"):
            payload = ("data: " + json.dumps(chunk) + "\n\ndata: " + json.dumps(end) + "\n\ndata: [DONE]\n\n").encode()
            content_type = "text/event-stream"
        else:
            payload = json.dumps({"id": "chatcmpl-fixture", "object": "chat.completion", "created": 1,
                "model": "fixture-model", "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                                        "finish_reason": "stop"}]}).encode()
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.mark.docker
@pytest.mark.skipif(os.environ.get("SH41_TEST_DOCKER") != "1", reason="Set SH41_TEST_DOCKER=1")
def test_real_opencode_with_compatible_protocol_fixture(tmp_path):
    server = ThreadingHTTPServer((os.environ.get("SH41_TEST_BIND", "127.0.0.1"), 0), CompletionHandler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = tmp_path / "runtime"
    service = AgentService(root)
    deployments = []
    try:
        host = os.environ.get("SH41_TEST_HOST", "host.docker.internal")
        spec = AgentSpec(agent="open-test", harness="opencode", model="fixture-model",
            inference=Inference(provider="openai-compatible", base_url=f"http://{host}:{server.server_port}/v1"))
        deployments.append(service.deploy(spec))
        result = finish(service, spec.agent, "Reply with a short greeting.")
        assert "SH41_FIXTURE_OK" in json.dumps(result["events"])
        original = service.store.session(spec.agent)
        assert original["native_id"]
        service.lifecycle(spec.agent, "park")
        deployments.append(service.deploy(spec))
        finish(service, spec.agent, "Continue the conversation.")
        assert service.store.session(spec.agent)["native_id"] == original["native_id"]
        assert server.requests
        assert any("Reply with a short greeting" in json.dumps(r) for r in server.requests)
    finally:
        for deployment in deployments:
            service.provider.remove(deployment)
        server.shutdown()
        server.server_close()
        thread.join()
        shutil.rmtree(root, ignore_errors=True)

"""Test-only, fixed-upstream model relay for an internal Docker network."""
import http.client
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class Relay(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        if self.path not in {"/v1/models", "/v1/chat/completions"}:
            self.send_error(403)
            return
        target = urlsplit(os.environ["MODEL_UPSTREAM"])
        connection = http.client.HTTPConnection(target.hostname, target.port, timeout=600)
        try:
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            connection.request(self.command, self.path, body=data,
                               headers={"Content-Type": "application/json"})
            result = connection.getresponse()
            self.send_response(result.status)
            self.send_header("Content-Type", result.getheader("Content-Type", "application/json"))
            self.end_headers()
            while chunk := result.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            connection.close()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Relay).serve_forever()

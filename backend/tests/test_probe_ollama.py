import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from conftest import free_port
from modeldock.probe import probe


class Handler(BaseHTTPRequestHandler):
    def _json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            self._json({"object": "list", "data": [{"id": "qwen3.5:9b"}]})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.path == "/api/show":
            self._json({"capabilities": ["completion", "vision", "thinking", "tools"]})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def test_probe_reads_ollama_capabilities():
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = asyncio.run(probe(f"http://127.0.0.1:{port}", model_name="qwen3.5:9b"))
        assert result.reachable
        assert "ollama" in result.kinds
        vision = [c for c in result.capabilities if c.type == "vision"]
        assert vision and vision[0].enabled
        assert any("思考" in note for note in result.notes)
    finally:
        server.shutdown()

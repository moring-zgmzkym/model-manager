import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from conftest import free_port
from modeldock import invoke
from modeldock.schemas import Capability, ModelConfig


class Handler(BaseHTTPRequestHandler):
    seen = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        Handler.seen.update(body)
        lines = [
            {"message": {"role": "assistant", "thinking": "先想一想"}, "done": False},
            {"message": {"role": "assistant", "content": "你好"}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True,
             "prompt_eval_count": 9, "eval_count": 5},
        ]
        payload = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def test_ollama_native_chat_sends_images_and_num_ctx():
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        model = ModelConfig(name="Qwen", path=".", defaults={"model": "qwen3.5:9b", "num_ctx": 8192},
                            capabilities=[Capability(type="chat", binding={"protocol": "ollama_chat"})])
        model.launch.port = port
        cap = model.capabilities[0]

        async def collect():
            return [chunk async for chunk in invoke.ollama_chat_stream(model, cap, {
                "messages": [{"role": "user", "content": "看图", "images": ["data:image/png;base64,AAAA"]}],
                "max_tokens": 128, "temperature": 0.5, "think": False,
            })]

        chunks = asyncio.run(collect())
        events = [json.loads(chunk.decode("utf-8")[5:].strip()) for chunk in chunks]

        assert Handler.seen["options"]["num_ctx"] == 8192
        assert Handler.seen["messages"][0]["images"] == ["AAAA"]
        assert Handler.seen["think"] is False
        assert [event["type"] for event in events] == ["reasoning", "delta", "done"]
        assert events[1]["text"] == "你好"
        assert events[2]["usage"]["completion_tokens"] == 5
    finally:
        server.shutdown()


def test_context_error_is_explained():
    message = json.dumps({"error": {"message": "request (4483 tokens) exceeds the available context size (4096 tokens)"}})
    text = invoke._ollama_error_message(message, 4096)
    assert "上下文超出" in text and "4483" in text and "4096" in text

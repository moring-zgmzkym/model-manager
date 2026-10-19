import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from conftest import free_port
from modeldock import invoke
from modeldock.outputs import save_upload
from modeldock.schemas import Capability, ModelConfig


def test_gpt_sovits_tts_posts_reference_audio(tmp_path):
    """Our GPT-SoVITS call shape must match api_v2 (/tts with ref_audio_path)."""
    port = free_port()
    wav = invoke.pcm16_to_wav(b"\x00\x00" * 1600)
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            seen.update(payload)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        upload = save_upload(b"RIFF" + b"0" * 200, "ref.wav")
        model = ModelConfig(name="SoVITS", path=str(tmp_path), capabilities=[
            Capability(type="voice_clone", binding={"protocol": "gpt_sovits", "api": "v2"}),
        ])
        model.launch.port = port

        result = asyncio.run(invoke.tts(model, {
            "text": "你好", "ref_audio": upload["id"], "prompt_text": "参考", "text_lang": "zh",
        }))

        assert result["outputs"][0]["url"].endswith(".wav")
        assert seen["text"] == "你好"
        assert seen["ref_audio_path"].endswith(".wav")
        assert seen["media_type"] == "wav"
    finally:
        server.shutdown()

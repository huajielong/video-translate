import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pipeline  # noqa: E402


class ModelsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") != "/v1/models":
            self.send_error(404)
            return
        body = json.dumps({"object": "list",
                           "data": [{"id": m} for m in self.server.models]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class LocalLLMTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {k: os.environ.get(k) for k in
                    ("PYVIDEOTRANS_HOME", "DEEPSEEK_API_KEY",
                     "VTV_LOCAL_LLM_API", "VTV_LOCAL_LLM_MODEL")}
        os.environ["PYVIDEOTRANS_HOME"] = self.tmp.name
        for key in ("VTV_LOCAL_LLM_API", "VTV_LOCAL_LLM_MODEL", "DEEPSEEK_API_KEY"):
            os.environ.pop(key, None)
        self.addCleanup(self._restore_env)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ModelsHandler)
        self.server.models = ["qwen"]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.api = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()

    def _restore_env(self):
        for key, value in self.env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def cfg(self, **overrides):
        cfg = dict(pipeline.DEFAULTS)
        cfg["pyvideotrans_home"] = self.tmp.name
        cfg["local_llm_api"] = self.api
        cfg["local_llm_model"] = ""
        cfg["local_llm_candidates"] = ""
        cfg["local_llm_timeout"] = 1.0
        cfg.update(overrides)
        return cfg

    def test_single_model_server_is_used(self):
        local = pipeline.local_llm(self.cfg())
        self.assertEqual(local, {"api": self.api, "model": "qwen"})

    def test_multi_model_server_needs_a_hint(self):
        self.server.models = ["small:4b", "big:35b"]
        self.assertIsNone(pipeline.local_llm(self.cfg()))
        local = pipeline.local_llm(self.cfg(local_llm_model="big:35b"))
        self.assertEqual(local["model"], "big:35b")

    def test_auto_prefers_local(self):
        cfg = self.cfg(translate_channel="auto")
        self.assertEqual(pipeline.resolve_mt_channel(cfg), "ollama")
        self.assertEqual(cfg["_mt_channel"], "ollama")
        self.assertEqual(cfg["_mt_local"]["model"], "qwen")

    def test_auto_falls_back_to_deepseek(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        cfg = self.cfg(translate_channel="auto",
                       local_llm_api="http://127.0.0.1:9/v1",
                       local_llm_candidates="")
        self.assertEqual(pipeline.resolve_mt_channel(cfg), "deepseek")

    def test_explicit_deepseek_requires_key(self):
        cfg = self.cfg(translate_channel="deepseek",
                       local_llm_api="http://127.0.0.1:9/v1")
        self.assertEqual(pipeline.resolve_mt_channel(cfg), "")

    def test_local_only_does_not_fall_back(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        cfg = self.cfg(translate_channel="local",
                       local_llm_api="http://127.0.0.1:9/v1",
                       local_llm_candidates="")
        self.assertEqual(pipeline.resolve_mt_channel(cfg), "")

    def test_review_providers_order(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        providers = pipeline.review_providers(self.cfg())
        self.assertEqual([p["name"] for p in providers], ["local", "deepseek"])
        self.assertEqual(providers[0]["key"], "local")


if __name__ == "__main__":
    unittest.main()

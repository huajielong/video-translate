import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import resegment_check as rc  # noqa: E402


class ParamsPathTest(unittest.TestCase):
    ENV_KEYS = ("PYVIDEOTRANS_HOME", "VTV_RESEGMENT_API", "VTV_RESEGMENT_MODEL",
                "VTV_RESEGMENT_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY")

    def setUp(self):
        self.old_env = {k: os.environ.get(k) for k in self.ENV_KEYS}
        self.old_skill = rc.SKILL
        for key in self.ENV_KEYS:
            os.environ.pop(key, None)
        self.addCleanup(self._restore)

    def _restore(self):
        rc.SKILL = self.old_skill
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_env_home_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PYVIDEOTRANS_HOME"] = tmp
            self.assertEqual(rc.params_path(),
                             Path(tmp) / "videotrans" / "params.json")

    def test_config_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config.json").write_text(
                json.dumps({"pyvideotrans_home": "D:/custom/pvt"}),
                encoding="utf-8")
            rc.SKILL = Path(tmp)
            self.assertEqual(rc.params_path(),
                             Path("D:/custom/pvt") / "videotrans" / "params.json")

    def test_credentials_read_from_installed_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "videotrans").mkdir()
            (home / "videotrans" / "params.json").write_text(
                json.dumps({"deepseek_key": "sk-test", "deepseek_model": "m1"}),
                encoding="utf-8")
            os.environ["PYVIDEOTRANS_HOME"] = str(home)
            key, model, base = rc.load_credentials()
            self.assertEqual(key, "sk-test")
            self.assertEqual(model, "m1")
            self.assertTrue(base.startswith("https://"))

    def test_missing_params_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PYVIDEOTRANS_HOME"] = tmp
            key, model, _ = rc.load_credentials()
            self.assertEqual(key, "")
            self.assertEqual(model, rc.MODEL_DEFAULT)

    def test_pipeline_local_provider_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PYVIDEOTRANS_HOME"] = tmp
            os.environ["VTV_RESEGMENT_API"] = "http://127.0.0.1:8081/v1"
            os.environ["VTV_RESEGMENT_MODEL"] = "qwen"
            os.environ["VTV_RESEGMENT_KEY"] = "local"
            key, model, base = rc.load_credentials()
            self.assertEqual(key, "local")
            self.assertEqual(model, "qwen")
            self.assertEqual(base, "http://127.0.0.1:8081/v1")

    def test_local_params_beat_deepseek(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "videotrans").mkdir()
            (home / "videotrans" / "params.json").write_text(
                json.dumps({"deepseek_key": "sk-test",
                            "localllm_api": "http://127.0.0.1:11434/v1",
                            "localllm_model": "qwen3.6:35b"}),
                encoding="utf-8")
            os.environ["PYVIDEOTRANS_HOME"] = str(home)
            key, model, base = rc.load_credentials()
            self.assertEqual(key, "local")
            self.assertEqual(model, "qwen3.6:35b")
            self.assertEqual(base, "http://127.0.0.1:11434/v1")


if __name__ == "__main__":
    unittest.main()

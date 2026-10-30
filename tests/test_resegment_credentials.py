import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import resegment_check as rc  # noqa: E402


class ParamsPathTest(unittest.TestCase):
    def setUp(self):
        self.old_env = os.environ.get("PYVIDEOTRANS_HOME")
        self.old_skill = rc.SKILL
        os.environ.pop("PYVIDEOTRANS_HOME", None)
        self.addCleanup(self._restore)

    def _restore(self):
        rc.SKILL = self.old_skill
        if self.old_env is None:
            os.environ.pop("PYVIDEOTRANS_HOME", None)
        else:
            os.environ["PYVIDEOTRANS_HOME"] = self.old_env

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


if __name__ == "__main__":
    unittest.main()

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import bootstrap_vendor  # noqa: E402
import pipeline  # noqa: E402
import vendor_paths  # noqa: E402


class VendorRootTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("VTV_VENDOR_DIR")
        os.environ.pop("VTV_VENDOR_DIR", None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self.old is None:
            os.environ.pop("VTV_VENDOR_DIR", None)
        else:
            os.environ["VTV_VENDOR_DIR"] = self.old

    def test_default_is_outside_the_skill(self):
        root = vendor_paths.vendor_root()
        skill = Path(__file__).resolve().parents[1]
        self.assertNotIn(str(skill), str(root))
        self.assertEqual(root.name, "vendor")
        self.assertEqual(root.parent.name, "video-translate")

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["VTV_VENDOR_DIR"] = tmp
            self.assertEqual(vendor_paths.vendor_root(), Path(tmp).resolve())

    def test_explicit_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["VTV_VENDOR_DIR"] = "D:/ignored"
            self.assertEqual(vendor_paths.vendor_root(tmp), Path(tmp).resolve())


class SetupPathTest(unittest.TestCase):
    def test_vendor_dir_selects_managed_root(self):
        args = argparse.Namespace(reuse_pvt="", reuse_itt="", vendor_dir="D:/vt")
        cfg = {"pyvideotrans_home": "X:/old/pvt", "index_tts2_home": "X:/old/itt"}
        root, pvt, itt, managed_pvt, managed_itt = pipeline.resolve_setup_paths(args, cfg)
        self.assertEqual(root, Path("D:/vt").resolve())
        self.assertEqual(pvt, managed_pvt)
        self.assertEqual(itt, managed_itt)
        self.assertEqual(pvt.name, "pyvideotrans")
        self.assertEqual(itt.name, "index-tts2-cli")

    def test_reuse_beats_vendor_dir(self):
        args = argparse.Namespace(reuse_pvt="D:/ext/pvt", reuse_itt="",
                                  vendor_dir="D:/vt")
        cfg = {"pyvideotrans_home": "", "index_tts2_home": ""}
        _, pvt, itt, managed_pvt, managed_itt = pipeline.resolve_setup_paths(args, cfg)
        self.assertEqual(pvt, Path("D:/ext/pvt").resolve())
        self.assertNotEqual(pvt, managed_pvt)
        self.assertEqual(itt, managed_itt)

    def test_config_path_outside_root_is_user_managed(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["VTV_VENDOR_DIR"] = tmp
            try:
                args = argparse.Namespace(reuse_pvt="", reuse_itt="", vendor_dir="")
                cfg = {"pyvideotrans_home": "D:/custom/pvt",
                       "index_tts2_home": "D:/custom/itt"}
                root, pvt, itt, managed_pvt, _ = pipeline.resolve_setup_paths(args, cfg)
                self.assertEqual(root, Path(tmp).resolve())
                self.assertEqual(pvt, Path("D:/custom/pvt").resolve())
                self.assertNotEqual(pvt, managed_pvt)  # setup must not download
                self.assertEqual(itt, Path("D:/custom/itt").resolve())
            finally:
                os.environ.pop("VTV_VENDOR_DIR", None)


class EnsureVendorTest(unittest.TestCase):
    def test_existing_installation_is_not_downloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pvt = root / "pyvideotrans"
            pvt.mkdir()
            (pvt / "cli.py").write_text("")
            itt = root / "index-tts2-cli"
            (itt / "scripts").mkdir(parents=True)
            (itt / "scripts" / "indextts2.py").write_text("")
            (itt / "indextts").mkdir()
            bootstrap_vendor.ensure_vendor(vendor=root)  # raises if it clones


if __name__ == "__main__":
    unittest.main()

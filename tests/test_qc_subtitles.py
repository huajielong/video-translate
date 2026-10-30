import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import qc  # noqa: E402


def write_srt(path: Path, text: str) -> None:
    path.write_text(f"1\n00:00:00,000 --> 00:00:02,000\n{text}\n", encoding="utf-8")


class CheckSubtitlesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_22_chars_fit_a_16x9_budget(self):
        srt = self.dir / "a.srt"
        write_srt(srt, "中" * 22)
        result = qc.check_subtitles(str(srt), None, None, 22)
        self.assertEqual(result["max_lines"], 1)
        self.assertEqual(result["long_lines"], 0)

    def test_same_line_overflows_a_narrow_frame(self):
        srt = self.dir / "a.srt"
        write_srt(srt, "中" * 22)
        result = qc.check_subtitles(str(srt), None, None, 14)
        self.assertEqual(result["long_lines"], 1)
        self.assertEqual(result["max_lines"], 2)
        self.assertTrue(any("frame allows 14" in issue for issue in result["issues"]))

    def test_reports_budget(self):
        srt = self.dir / "a.srt"
        write_srt(srt, "短句")
        self.assertEqual(qc.check_subtitles(str(srt), None, None, 14)["max_chars"], 14)


class ScriptDetectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_chinese_srt(self):
        p = self.dir / "zh.srt"
        write_srt(p, "这是中文字幕")
        self.assertTrue(qc.subtitles_are_cjk(str(p)))

    def test_english_srt(self):
        p = self.dir / "en.srt"
        write_srt(p, "This is an English subtitle line")
        self.assertFalse(qc.subtitles_are_cjk(str(p)))


if __name__ == "__main__":
    unittest.main()

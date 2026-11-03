# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import batch_courses as bc  # noqa: E402
import prep_check  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ParseUnitsTest(unittest.TestCase):
    def test_ignores_timestamps_numbers_and_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "zh-cn.tts.srt"
            write(p, "1\n00:00:00,000 --> 00:00:02,000\n你好世界\n\n"
                     "2\n00:00:02,000 --> 00:00:04,000\n<b>第二句</b>\n")
            self.assertEqual(prep_check.parse_units(p), ["你好世界", "第二句"])


class CheckCourseTest(unittest.TestCase):
    def _course(self, tmp: str, tts_text: str | None) -> Path:
        out = Path(tmp) / "课程" / "_中文译制"
        d = out / "_work" / "prep" / "001_x"
        write(d / "en.srt",
              "1\n00:00:00,000 --> 00:00:02,000\nHello world\n")
        write(d / "zh-cn.srt",
              "1\n00:00:00,000 --> 00:00:02,000\n你好世界\n")
        if tts_text is not None:
            write(d / "zh-cn.tts.srt", tts_text)
        return out

    def test_missing_tts_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._course(tmp, None)
            stats, issues = prep_check.check_course(out, "", "en", "zh-cn", 44)
            self.assertEqual(stats["videos"], 1)
            self.assertEqual(stats["missing"], 1)
            self.assertTrue(any("tts.srt" in i for i in issues))

    def test_overlong_unit_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._course(
                tmp, "1\n00:00:00,000 --> 00:00:02,000\n" + "长" * 45 + "\n")
            stats, issues = prep_check.check_course(out, "", "en", "zh-cn", 44)
            self.assertEqual(stats["missing"], 0)
            self.assertEqual(stats["over"], 1)
            self.assertTrue(any(i.startswith("OVER") for i in issues))

    def test_clean_course_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._course(
                tmp, "1\n00:00:00,000 --> 00:00:02,000\n你好世界\n")
            stats, issues = prep_check.check_course(out, "", "en", "zh-cn", 44)
            self.assertEqual(stats["ok"], 1)
            self.assertEqual(issues, [])


class DeliveryVerifiedTest(unittest.TestCase):
    def _item(self, out: Path, **kw) -> dict:
        item = {"ok": True, "out": str(out), "expected": 3, "outputs": 3,
                "qc_total": 3, "qc_passed": 3, "pending": []}
        item.update(kw)
        return item

    def test_delivered_course_is_cleanable(self):
        out = Path(tempfile.gettempdir()) / "course" / "_中文译制"
        self.assertTrue(bc.delivery_verified(self._item(out), out))

    def test_unfinished_course_is_kept(self):
        out = Path(tempfile.gettempdir()) / "course" / "_中文译制"
        self.assertFalse(bc.delivery_verified(self._item(out, ok=False), out))
        self.assertFalse(bc.delivery_verified(self._item(out, pending=["001_x"]), out))
        self.assertFalse(bc.delivery_verified(self._item(out, qc_passed=2), out))
        self.assertFalse(bc.delivery_verified(self._item(out, outputs=2), out))

    def test_other_output_dir_is_kept(self):
        out = Path(tempfile.gettempdir()) / "course" / "_中文译制"
        other = Path(tempfile.gettempdir()) / "other" / "_中文译制"
        self.assertFalse(bc.delivery_verified(self._item(other), out))

    def test_missing_status_is_kept(self):
        out = Path(tempfile.gettempdir()) / "course" / "_中文译制"
        self.assertFalse(bc.delivery_verified(None, out))


if __name__ == "__main__":
    unittest.main()

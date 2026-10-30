import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import display_fit  # noqa: E402


class FrameCharLimitTest(unittest.TestCase):
    def test_16x9_keeps_design_cap(self):
        self.assertEqual(display_fit.frame_char_limit(1920, 1080), 22)
        self.assertEqual(display_fit.frame_char_limit(1280, 720), 22)

    def test_narrow_frames_are_tightened(self):
        # 960x1080 and 640x720 both overflow a fixed 22-char line.
        self.assertEqual(display_fit.frame_char_limit(960, 1080), 14)
        self.assertEqual(display_fit.frame_char_limit(640, 720), 14)
        self.assertEqual(display_fit.frame_char_limit(1080, 1920), 8)
        self.assertEqual(display_fit.frame_char_limit(360, 640), 8)

    def test_unknown_size_falls_back_to_cap(self):
        self.assertEqual(display_fit.frame_char_limit(0, 0), 22)
        self.assertEqual(display_fit.frame_char_limit(0, 1080), 22)

    def test_floor(self):
        self.assertGreaterEqual(display_fit.frame_char_limit(100, 4000), 8)

    def test_safety_shrinks_limit(self):
        loose = display_fit.frame_char_limit(960, 1080, safety=1.0)
        safe = display_fit.frame_char_limit(960, 1080, safety=0.9)
        self.assertGreater(loose, safe)

    def test_custom_ass_style(self):
        big = {"Fontsize": 24}
        self.assertLess(display_fit.frame_char_limit(1920, 1080, big),
                        display_fit.frame_char_limit(1920, 1080))
        wide_margin = {"MarginL": 40, "MarginR": 40}
        self.assertLess(display_fit.frame_char_limit(960, 1080, wide_margin), 14)

    def test_scale_is_height_based(self):
        # 960x1080 and 640x720 are both 8:9; same limit at different sizes.
        self.assertEqual(display_fit.frame_char_limit(960, 1080),
                         display_fit.frame_char_limit(640, 720))

    def test_latin_budget_is_wider(self):
        self.assertGreater(display_fit.frame_char_limit(960, 1080, cjk=False),
                           display_fit.frame_char_limit(960, 1080, cjk=True))


class DisplayLimitTest(unittest.TestCase):
    def test_fit_can_be_disabled(self):
        cfg = {"subtitle_max_chars": 22, "subtitle_fit_frame": False}
        self.assertEqual(display_fit.display_limit(960, 1080, cfg), 22)

    def test_fit_uses_frame(self):
        cfg = {"subtitle_max_chars": 22, "subtitle_fit_frame": True}
        self.assertEqual(display_fit.display_limit(960, 1080, cfg), 14)

    def test_cap_limits_wide_frame(self):
        cfg = {"subtitle_max_chars": 16, "subtitle_fit_frame": True}
        self.assertEqual(display_fit.display_limit(1920, 1080, cfg), 16)

    def test_non_cjk_target_keeps_cap(self):
        # pyVideoTrans uses other_len for non-CJK; VTV_CJK_LEN does not apply.
        cfg = {"subtitle_max_chars": 22, "subtitle_fit_frame": True,
               "target_lang": "en"}
        self.assertEqual(display_fit.display_limit(960, 1080, cfg), 22)


if __name__ == "__main__":
    unittest.main()

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import qc  # noqa: E402


class DurationBoundsTest(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in (
            "VTV_DURATION_TOL_S",
            "VTV_MAX_VIDEO_SPEEDUP_RATE",
            "VTV_MAX_VIDEO_PTS_RATE",
        )}
        for key in self._old:
            os.environ.pop(key, None)
        self._old_tol = qc.DURATION_TOL_S
        qc.DURATION_TOL_S = 15.0

    def tearDown(self):
        qc.DURATION_TOL_S = self._old_tol
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_default_is_absolute_floor(self):
        lower, upper = qc.duration_bounds(600.0)
        self.assertAlmostEqual(lower, 585.0)
        self.assertAlmostEqual(upper, 615.0)

    def test_speedup_sets_lower_bound(self):
        os.environ["VTV_MAX_VIDEO_SPEEDUP_RATE"] = "1.25"
        lower, _ = qc.duration_bounds(600.0)
        self.assertAlmostEqual(lower, 600.0 / 1.25 - 2.0)

    def test_slowdown_sets_upper_bound(self):
        os.environ["VTV_MAX_VIDEO_PTS_RATE"] = "1.5"
        _, upper = qc.duration_bounds(600.0)
        self.assertAlmostEqual(upper, 600.0 * 1.5 + 2.0)

    def test_zero_source_is_safe(self):
        self.assertEqual(qc.duration_bounds(0.0), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()

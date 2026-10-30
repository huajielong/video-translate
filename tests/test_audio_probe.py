import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audio_probe  # noqa: E402


def make_levels(background=1e-3, n=200):
    return [background] * n


class FindPeaksTest(unittest.TestCase):
    def test_isolated_pulse_is_reported(self):
        levels = make_levels()
        levels[87] = 5e-2
        found = audio_probe.find_peaks(levels)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["time_s"], 0.087)
        self.assertGreater(found[0]["ratio"], 20)

    def test_sustained_fricative_is_not_reported(self):
        levels = make_levels()
        for i in range(60, 100):          # 40ms of band energy
            levels[i] = 5e-2
        self.assertEqual(audio_probe.find_peaks(levels), [])

    def test_quiet_pulse_is_ignored(self):
        levels = make_levels(background=1e-5)
        levels[50] = 5e-4                 # above the local median, below 1e-3
        self.assertEqual(audio_probe.find_peaks(levels), [])

    def test_two_separate_peaks_both_reported(self):
        levels = make_levels()
        levels[30] = 5e-2
        levels[150] = 5e-2
        self.assertEqual(len(audio_probe.find_peaks(levels)), 2)

    def test_sorted_by_level(self):
        levels = make_levels()
        levels[30] = 2e-2
        levels[150] = 5e-2
        self.assertEqual([c["time_s"] for c in audio_probe.find_peaks(levels)],
                         [0.150, 0.030])

    def test_empty_input(self):
        self.assertEqual(audio_probe.find_peaks([]), [])


class CompareLevelsTest(unittest.TestCase):
    def test_rebuilt_window_is_reported(self):
        before = make_levels(n=1000)
        before[868] = 1.4e-1
        after = make_levels(n=1000)
        drops = audio_probe.compare_levels(before, after)
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0]["time_s"], 0.868)
        self.assertGreater(drops[0]["drop"], 100)

    def test_unchanged_take_reports_nothing(self):
        data = make_levels()
        self.assertEqual(audio_probe.compare_levels(data, list(data)), [])

    def test_quieter_take_is_not_a_drop(self):
        before = make_levels()
        after = make_levels()
        after[10] = 5e-1
        self.assertEqual(audio_probe.compare_levels(before, after), [])

    def test_short_inputs_are_compared_by_length(self):
        drops = audio_probe.compare_levels([1e-2, 1e-2], [1e-2])
        self.assertEqual(drops, [])


class WindowRmsTest(unittest.TestCase):
    def test_constant_signal(self):
        samples = audio_probe.array("f", [0.5] * 96)
        levels = audio_probe.window_rms(samples, sr=48000, win_ms=1.0)
        self.assertEqual(len(levels), 2)
        self.assertAlmostEqual(levels[0], 0.5, places=4)

    def test_partial_window_is_dropped(self):
        samples = audio_probe.array("f", [0.1] * 100)
        self.assertEqual(len(audio_probe.window_rms(samples, 48000, 1.0)), 2)


if __name__ == "__main__":
    unittest.main()

"""Test radial direction of gauge needle reading tips."""

import unittest


import cv2
import numpy as np

from gauge_dial import DialGeometry
from gauge_geometry import point_at_angle
from gauge_needle import (
    detect_needle,
    select_needle,
)
from gauge_types import (
    FailureReason,
    ReaderConfig,
)


class GaugeNeedleTests(unittest.TestCase):
    """Describe the expected behaviour of radial search for needle detection."""

    def setUp(self) -> None:
        """Create a kown dial and permissive synthetic test thresholds."""

        self.geometry = DialGeometry(
            centre=(256.0, 256.0),
            radius=190.0,
        )

        self.config = ReaderConfig(
            minimum_needle_score=0.05,
            minimum_peak_margin=0.005,
        )

    def test_long_tip_beats_short_thick_tail(self):
        """Outer reach should distinguish the tip from its counterweight."""

        image = np.full(
            (512, 512),
            255,
            dtype=np.uint8,
        )

        centre = (256, 256)

        # Place the reading tip at 300 degrees and its shorter tail opposite it.
        reading_tip = point_at_angle(
            self.geometry.centre,
            distance=150.0,
            angle_degrees=300.0,
        )
        tail = point_at_angle(
            self.geometry.centre,
            distance=95.0,
            angle_degrees=120.0,
        )

        # Give the synthetic needle a long thin tip and a short thick tail.
        cv2.line(
            image,
            centre,
            tuple(map(round, reading_tip)),
            color=0,
            thickness=3,
        )
        cv2.line(
            image,
            centre,
            tuple(map(round, tail)),
            color=0,
            thickness=9,
        )

        result = detect_needle(
            image,
            self.geometry,
            self.config,
        )

        self.assertTrue(result.valid)

        # Allow nearby sampled angles to represent the same drawn line.
        self.assertAlmostEqual(
            result.angle_degrees,
            300.0,
            delta=3.0,
        )

    def test_equal_opposite_peaks_are_ambiguous(self):
        """Equal evidence in opposite direction must fail safely."""

        # Supply one score per degree so indices 30 and 210 represent opposite rays.
        scores = np.zeros(
            360,
            dtype=np.float64,
        )

        # Give both rays equal scores so selection must return AMBIGUOUS_NEEDLE.
        scores[30] = 0.8
        scores[210] = 0.8

        result = select_needle(
            scores,
            self.geometry,
            self.config,
        )

        self.assertFalse(result.valid)
        self.assertEqual(
            result.winning_score,
            0.80,
        )
        self.assertEqual(
            result.peak_margin,
            0.0,
        )
        self.assertEqual(
            result.failure_reason,
            FailureReason.AMBIGUOUS_NEEDLE,
        )

    def test_weak_peak_returns_no_needle(self):
        """A peak below the score floor is not a needle detection."""

        strict_config = ReaderConfig(
            minimum_needle_score=0.20,
            minimum_peak_margin=0.005,
        )

        scores = np.zeros(
            360,
            dtype=np.float64,
        )

        # Set the strongest score to 0.10, below the 0.20 required for a needle.
        scores[75] = 0.10

        result = select_needle(
            scores,
            self.geometry,
            strict_config,
        )

        self.assertFalse(result.valid)
        self.assertIsNone(result.angle_degrees)
        self.assertIsNone(result.tip)
        self.assertEqual(result.quality, 0.00)
        self.assertEqual(result.winning_score, 0.10)
        self.assertEqual(
            result.failure_reason,
            FailureReason.NO_NEEDLE,
        )

    def test_empty_scores_return_no_needle(self):
        """An empty score array must fail without calling argmax."""

        scores = np.array(
            [],
            dtype=np.float64,
        )

        result = select_needle(
            scores,
            self.geometry,
            self.config,
        )

        self.assertFalse(result.valid)
        self.assertIsNone(result.angle_degrees)
        self.assertIsNone(result.tip)
        self.assertEqual(result.quality, 0.00)
        self.assertEqual(result.winning_score, 0.00)
        self.assertEqual(
            result.failure_reason,
            FailureReason.NO_NEEDLE,
        )

    def test_quality_combines_strength_and_peak_separation(self):
        """Quality must reward both strength and unambiguous evidence."""

        quality_config = ReaderConfig(
            minimum_needle_score=0.20,
            minimum_peak_margin=0.10,
        )

        scores = np.zeros(
            360,
            dtype=np.float64,
        )

        # Separate these peaks enough that they count as competing directions.
        scores[40] = 0.60
        scores[200] = 0.30

        result = select_needle(
            scores,
            self.geometry,
            quality_config,
        )

        self.assertTrue(result.valid)
        self.assertAlmostEqual(
            result.winning_score,
            0.60,
        )
        self.assertAlmostEqual(
            result.peak_margin,
            0.30,
        )

        # Strength is 0.50 and separation is 0.60.
        # The weighted quality is 0.70 * 0.50 + 0.30 * 0.60 = 0.53.
        self.assertAlmostEqual(
            result.quality,
            0.53,
        )


if __name__ == "__main__":
    unittest.main()

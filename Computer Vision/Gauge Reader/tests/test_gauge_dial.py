"""Test the localisation and validation of circular gauge dials."""

import unittest
from unittest.mock import patch

import cv2
import numpy as np

from gauge_dial import detect_dial, select_best_circle
from gauge_types import FailureReason, ReaderConfig


class GaugeDialTests(unittest.TestCase):
    """Describe how circular dial candidates should be evaluated."""

    def setUp(self):
        """Create a predictable working image and configuration"""

        # Create a blank working image so each test controls the available rim edges.
        self.gray = np.full(
            (512, 512),
            255,
            dtype=np.uint8,
        )

        self.config = ReaderConfig(
            minimum_circle_quality=0.0,
        )

    def test_prefers_large_circle_near_crop_centre(self):
        """The likely dial should outrank a small off-centre circle."""

        circles = np.array(
            [
                [
                    [80.0, 70.0, 40.0],
                    [260.0, 250.0, 190.0],
                ]
            ],
            dtype=np.float32,
        )

        result = select_best_circle(
            circles,
            self.gray,
            self.config,
        )

        self.assertTrue(result.valid)
        self.assertIsNotNone(result.geometry)
        self.assertAlmostEqual(result.geometry.centre[0], 260.0)
        self.assertAlmostEqual(result.geometry.centre[1], 250.0)
        self.assertAlmostEqual(result.geometry.radius, 190.0)

    def test_prefers_circle_with_circumference_edge_support(self):
        """A visible circular rim should be the tiebreaker of a geometric scoring."""

        image_with_rim = self.gray.copy()

        cv2.circle(
            image_with_rim,
            center=(376, 256),
            radius=110,
            color=0,
            thickness=3,
        )

        # Equal size and centre distance leave the visible rim as the tiebreaker.
        circles = np.array(
            [
                [
                    [136.0, 256.0, 110.0],
                    [376.0, 256.0, 110.0],
                ]
            ],
            dtype=np.float32,
        )

        result = select_best_circle(
            circles,
            image_with_rim,
            self.config,
        )

        self.assertTrue(result.valid)
        self.assertIsNotNone(result.geometry)
        self.assertAlmostEqual(result.geometry.centre[0], 376.0)
        self.assertAlmostEqual(result.geometry.centre[1], 256.0)

    def test_rejects_best_candidate_below_quality_threshold(self):
        """Good geometry alone must not override a quality floor."""

        # Require a score of 0.80 so this rimless candidate must return NO_DIAL.
        strict_config = ReaderConfig(
            minimum_circle_quality=0.80,
        )

        circles = np.array(
            [
                [
                    [256.0, 256.0, 190.0],
                ]
            ],
            dtype=np.float32,
        )

        result = select_best_circle(
            circles,
            self.gray,
            strict_config,
        )

        self.assertFalse(result.valid)
        self.assertIsNone(result.geometry)
        self.assertGreater(result.quality, 0.0)
        self.assertLess(result.quality, 0.8)
        self.assertEqual(
            result.failure_reason,
            FailureReason.NO_DIAL,
        )

    @patch(
        "gauge_dial.cv2.HoughCircles",
        return_value=None,
    )
    def test_no_circle_candidates_returns_no_dial(
        self,
        hough_circles,
    ):
        """A crop with no circular candidates must fail."""

        result = detect_dial(
            self.gray,
            self.config,
        )

        self.assertFalse(result.valid)
        self.assertIsNone(result.geometry)
        self.assertEqual(result.quality, 0.0)
        self.assertEqual(
            result.failure_reason,
            FailureReason.NO_DIAL,
        )

    @patch("gauge_dial.cv2.HoughCircles")
    def test_detected_circle_candidates_are_ranked(
        self,
        hough_circles,
    ):
        """Candidates returned by OpenCV must enter the ranking process."""

        hough_circles.return_value = np.array(
            [
                [
                    [260.0, 250.0, 190.0],
                ]
            ],
            dtype=np.float32,
        )

        result = detect_dial(
            self.gray,
            self.config,
        )

        self.assertTrue(result.valid)
        self.assertIsNotNone(result.geometry)
        self.assertAlmostEqual(
            result.geometry.centre[0],
            260.0,
        )
        self.assertAlmostEqual(
            result.geometry.centre[1],
            250.0,
        )
        self.assertAlmostEqual(
            result.geometry.radius,
            190.0,
        )


if __name__ == "__main__":
    unittest.main()

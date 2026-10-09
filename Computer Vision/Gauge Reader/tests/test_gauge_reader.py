"""Test orchestration of the complete one-candidate gauge reader."""

import unittest
from unittest.mock import patch

import numpy as np

from gauge_dial import (
    DialDetection,
    DialGeometry,
)
from gauge_needle import NeedleDetection
from gauge_preprocess import (
    LetterboxTransform,
    PreprocessOutcome,
)
from gauge_reader import read_gauge
from gauge_types import FailureReason


class GaugeReaderTests(unittest.TestCase):
    """Describe how stage results become one public gauge result."""

    def test_invalid_image_returns_invalid_input(self):
        """An invalid candidate must stop before dial detection."""

        result = read_gauge(None)

        self.assertFalse(result.valid)
        self.assertIsNone(result.value_bar)
        self.assertEqual(
            result.failure_reason,
            FailureReason.INVALID_INPUT,
        )

    @patch("gauge_reader.detect_dial")
    @patch("gauge_reader.preprocess_candidate")
    def test_located_blurred_dial_retains_geometry(
        self,
        preprocess_candidate,
        detect_dial,
    ):
        """A blurred but located dial must retain its geometric evidence."""

        source = np.full(
            (100, 200, 3),
            255,
            dtype=np.uint8,
        )
        preprocess_candidate.return_value = PreprocessOutcome(
            valid=True,
            failure_reason=None,
            working_colour=np.full(
                (512, 512, 3),
                255,
                dtype=np.uint8,
            ),
            working_gray=np.full(
                (512, 512),
                255,
                dtype=np.uint8,
            ),
            transform=LetterboxTransform(
                scale=2.56,
                pad_x=0.0,
                pad_y=128.0,
            ),
            # Force a blur rejection by supplying 10.0 against the 20.0 minimum.
            blur_variance=10.0,
        )

        detect_dial.return_value = DialDetection(
            valid=True,
            geometry=DialGeometry(
                centre=(256.0, 256.0),
                radius=128.0,
            ),
            quality=0.80,
            failure_reason=None,
        )

        result = read_gauge(
            source,
        )

        self.assertFalse(result.valid)
        self.assertEqual(
            result.failure_reason,
            FailureReason.DIAL_TOO_BLURRED,
        )
        self.assertEqual(result.dial_quality, 0.80)
        self.assertEqual(result.centre, (100.0, 50.0))
        self.assertEqual(result.radius, 50.0)
        self.assertIsNone(result.tip)
        self.assertIsNone(result.value_bar)

    @patch("gauge_reader.detect_dial")
    @patch("gauge_reader.preprocess_candidate")
    def test_no_circle_returns_no_dial_and_preserves_metadata(
        self,
        preprocess_candidate,
        detect_dial,
    ):
        """A dial stage rejection must retain its evidence and identity."""

        source = np.full(
            (100, 200, 3),
            255,
            dtype=np.uint8,
        )

        preprocess_candidate.return_value = PreprocessOutcome(
            valid=True,
            failure_reason=None,
            working_colour=np.full(
                (512, 512, 3),
                255,
                dtype=np.uint8,
            ),
            working_gray=np.full(
                (512, 512),
                255,
                dtype=np.uint8,
            ),
            transform=LetterboxTransform(
                scale=2.56,
                pad_x=0.0,
                pad_y=128.0,
            ),
            blur_variance=100.0,
        )

        detect_dial.return_value = DialDetection(
            valid=False,
            geometry=None,
            quality=0.10,
            failure_reason=FailureReason.NO_DIAL,
        )

        result = read_gauge(
            source,
            metadata={
                "candidate_id": "candidate-7",
            },
        )

        self.assertFalse(result.valid)
        self.assertIsNone(result.value_bar)
        self.assertEqual(
            result.failure_reason,
            FailureReason.NO_DIAL,
        )
        self.assertEqual(
            result.dial_quality,
            0.10,
        )
        self.assertEqual(
            result.metadata["candidate_id"],
            "candidate-7",
        )

    @patch("gauge_reader.detect_needle")
    @patch("gauge_reader.detect_dial")
    @patch("gauge_reader.preprocess_candidate")
    def test_ambiguous_needle_retains_stage_evidence(
        self,
        preprocess_candidate,
        detect_dial,
        detect_needle,
    ):
        """An ambiguous needle must not produce a pressure reading."""

        source = np.full(
            (100, 200, 3),
            255,
            dtype=np.uint8,
        )

        # Supply sharpness of 100.0 so this candidate reaches needle detection.
        preprocess_candidate.return_value = PreprocessOutcome(
            valid=True,
            failure_reason=None,
            working_colour=np.full(
                (512, 512, 3),
                255,
                dtype=np.uint8,
            ),
            working_gray=np.full(
                (512, 512),
                255,
                dtype=np.uint8,
            ),
            transform=LetterboxTransform(
                scale=2.56,
                pad_x=0.0,
                pad_y=128.0,
            ),
            blur_variance=100.0,
        )

        # Circle detection has accepted a dial in working image coordinates.
        detect_dial.return_value = DialDetection(
            valid=True,
            geometry=DialGeometry(
                centre=(256.0, 256.0),
                radius=128.0,
            ),
            quality=0.80,
            failure_reason=None,
        )

        # The 0.01 margin is below the required 0.02, so this needle is ambiguous.
        detect_needle.return_value = NeedleDetection(
            valid=False,
            angle_degrees=30.0,
            tip=None,
            quality=0.0,
            winning_score=0.74,
            peak_margin=0.01,
            failure_reason=FailureReason.AMBIGUOUS_NEEDLE,
        )

        result = read_gauge(
            source,
            metadata={
                "candidate_id": "candidate-8",
            },
        )

        # Check that an ambiguous needle returns its failure reason and no pressure.
        self.assertFalse(result.valid)
        self.assertIsNone(result.value_bar)
        self.assertEqual(
            result.failure_reason,
            FailureReason.AMBIGUOUS_NEEDLE,
        )

        # Check that the rejected result retains the angle, dial geometry and case ID.
        self.assertEqual(result.dial_quality, 0.80)
        self.assertEqual(result.needle_quality, 0.0)
        self.assertEqual(result.angle_degrees, 30.0)
        self.assertEqual(result.centre, (100.0, 50.0))
        self.assertEqual(result.radius, 50.0)
        self.assertIsNone(result.tip)
        self.assertEqual(
            result.metadata["candidate_id"],
            "candidate-8",
        )

    @patch("gauge_reader.detect_needle")
    @patch("gauge_reader.detect_dial")
    @patch("gauge_reader.preprocess_candidate")
    def test_needle_in_dead_zone_returns_angle_outside_scale(
        self,
        preprocess_candidate,
        detect_dial,
        detect_needle,
    ):
        """A detected needle outside the calibrated sweep has no value"""

        source = np.full(
            (100, 200, 3),
            255,
            dtype=np.uint8,
        )

        # Supply valid preprocessing with enough sharpness to reach angle conversion.
        preprocess_candidate.return_value = PreprocessOutcome(
            valid=True,
            failure_reason=None,
            working_colour=np.full(
                (512, 512, 3),
                255,
                dtype=np.uint8,
            ),
            working_gray=np.full(
                (512, 512),
                255,
                dtype=np.uint8,
            ),
            transform=LetterboxTransform(
                scale=2.56,
                pad_x=0.0,
                pad_y=128.0,
            ),
            blur_variance=100.0,
        )

        # A circular gauge has been accepted in working-image coordinates.
        detect_dial.return_value = DialDetection(
            valid=True,
            geometry=DialGeometry(
                centre=(256.0, 256.0),
                radius=128.0,
            ),
            quality=0.80,
            failure_reason=None,
        )

        # The 180-degree ray is in the scale gap. Its tip sits at 82% of the radius.
        # In working pixels, tip y = 256 + 128 * 0.82 = 360.96.
        detect_needle.return_value = NeedleDetection(
            valid=True,
            angle_degrees=180.0,
            tip=(256.0, 360.96),
            quality=0.75,
            winning_score=0.74,
            peak_margin=0.10,
            failure_reason=None,
        )

        result = read_gauge(
            source,
            metadata={
                "candidate_id": "candidate-9",
            },
        )

        # Check that the dead-zone angle returns ANGLE_OUTSIDE_SCALE and no pressure.
        self.assertFalse(result.valid)
        self.assertIsNone(result.value_bar)
        self.assertEqual(
            result.failure_reason,
            FailureReason.ANGLE_OUTSIDE_SCALE,
        )

        # Keep the rejected angle and both detection qualities with the source circle.
        self.assertEqual(result.angle_degrees, 180.0)
        self.assertEqual(result.dial_quality, 0.80)
        self.assertEqual(result.needle_quality, 0.75)
        self.assertEqual(result.centre, (100.0, 50.0))
        self.assertEqual(result.radius, 50.0)

        # Remove 128 pixels of vertical padding, then divide both axes by 2.56.
        self.assertIsNotNone(result.tip)
        assert result.tip is not None
        self.assertAlmostEqual(result.tip[0], 100.0)
        self.assertAlmostEqual(result.tip[1], 91.0)

        self.assertEqual(
            result.metadata["candidate_id"],
            "candidate-9",
        )

    @patch("gauge_reader.detect_needle")
    @patch("gauge_reader.detect_dial")
    @patch("gauge_reader.preprocess_candidate")
    def test_valid_needle_returns_midscale_pressure(
        self,
        preprocess_candidate,
        detect_dial,
        detect_needle,
    ):
        """A valid zero degree needle must return the expected pressure."""

        source = np.full(
            (100, 200, 3),
            255,
            dtype=np.uint8,
        )

        # The candidate has passed validation and the sharpness gate.
        preprocess_candidate.return_value = PreprocessOutcome(
            valid=True,
            failure_reason=None,
            working_colour=np.full(
                (512, 512, 3),
                255,
                dtype=np.uint8,
            ),
            working_gray=np.full(
                (512, 512),
                255,
                dtype=np.uint8,
            ),
            transform=LetterboxTransform(
                scale=2.56,
                pad_x=0.0,
                pad_y=128.0,
            ),
            blur_variance=100.0,
        )

        # The dial is accepted in the 512x512 working image.
        detect_dial.return_value = DialDetection(
            valid=True,
            geometry=DialGeometry(
                centre=(256.0, 256.0),
                radius=128.0,
            ),
            quality=0.80,
            failure_reason=None,
        )

        # Up is zero degrees. In working pixels, tip y = 256 - 128 * 0.82 = 151.04.
        detect_needle.return_value = NeedleDetection(
            valid=True,
            angle_degrees=0.0,
            tip=(256.0, 151.04),
            quality=0.75,
            winning_score=0.74,
            peak_margin=0.10,
            failure_reason=None,
        )

        result = read_gauge(
            source,
            metadata={
                "candidate_id": "candidate-10",
            },
        )

        self.assertTrue(result.valid)

        # Zero degrees is halfway through the 270-degree sweep, giving 5 bar.
        self.assertIsNotNone(result.value_bar)
        assert result.value_bar is not None
        self.assertAlmostEqual(result.value_bar, 5.0)

        # Check the accepted angle, stage qualities and circle in original crop pixels.
        self.assertEqual(result.angle_degrees, 0.0)
        self.assertEqual(result.dial_quality, 0.80)
        self.assertEqual(result.needle_quality, 0.75)
        self.assertEqual(result.centre, (100.0, 50.0))
        self.assertEqual(result.radius, 50.0)

        # Undoing the scale and padding gives a source tip at (100, 9).
        self.assertIsNotNone(result.tip)
        assert result.tip is not None
        self.assertAlmostEqual(result.tip[0], 100.0)
        self.assertAlmostEqual(result.tip[1], 9.0)

        self.assertEqual(
            result.metadata["candidate_id"],
            "candidate-10",
        )

    @patch("gauge_reader.detect_needle")
    @patch("gauge_reader.detect_dial")
    @patch("gauge_reader.preprocess_candidate")
    def test_valid_needle_without_angle_fails_closed(
        self,
        preprocess_candidate,
        detect_dial,
        detect_needle,
    ):
        """A malformed needle result must become a structured rejection."""

        source = np.full(
            (100, 200, 3),
            255,
            dtype=np.uint8,
        )

        preprocess_candidate.return_value = PreprocessOutcome(
            valid=True,
            failure_reason=None,
            working_colour=np.full(
                (512, 512, 3),
                255,
                dtype=np.uint8,
            ),
            working_gray=np.full(
                (512, 512),
                255,
                dtype=np.uint8,
            ),
            transform=LetterboxTransform(
                scale=2.56,
                pad_x=0.0,
                pad_y=128.0,
            ),
            blur_variance=100.0,
        )

        detect_dial.return_value = DialDetection(
            valid=True,
            geometry=DialGeometry(
                centre=(256.0, 256.0),
                radius=128.0,
            ),
            quality=0.80,
            failure_reason=None,
        )

        # Claim success without an angle or tip to check that the reader rejects it.
        detect_needle.return_value = NeedleDetection(
            valid=True,
            angle_degrees=None,
            tip=None,
            quality=0.40,
            winning_score=0.50,
            peak_margin=0.10,
            failure_reason=None,
        )

        result = read_gauge(
            source,
            metadata={
                "candidate_id": "candidate-malformed",
            },
        )

        self.assertFalse(result.valid)
        self.assertIsNone(result.value_bar)
        self.assertEqual(
            result.failure_reason,
            FailureReason.NO_NEEDLE,
        )

        # Keep the dial geometry and reported qualities when the needle angle is absent.
        self.assertEqual(result.dial_quality, 0.80)
        self.assertEqual(result.needle_quality, 0.40)
        self.assertEqual(result.centre, (100.0, 50.0))
        self.assertEqual(result.radius, 50.0)

        # Missing needle geometry must remain missing.
        self.assertIsNone(result.angle_degrees)
        self.assertIsNone(result.tip)

        self.assertEqual(
            result.metadata["candidate_id"],
            "candidate-malformed",
        )


if __name__ == "__main__":
    unittest.main()

"""Tests for the visual explanation drawn over a gauge candidate image."""

import unittest

import numpy as np

from gauge_overlay import render_overlay
from gauge_types import FailureReason, GaugeReadResult

from unittest.mock import patch


class GaugeOverlayTests(unittest.TestCase):
    """Describe the externally visible behaviour of the overlay renderer."""

    def setUp(self) -> None:
        """Create a simple image whose contents make comparison easy."""

        self.image = np.full(
            shape=(120, 120, 3),
            fill_value=255,
            dtype=np.uint8,
        )

    def test_valid_overlay_is_a_changed_copy(self) -> None:
        """Rendering must annotate a copy without changing the input image"""

        # Keep the original pixels to catch accidental drawing on the input.
        before = self.image.copy()

        result = GaugeReadResult.success(
            value_bar=2.5,
            angle_degrees=292.5,
            dial_quality=0.8,
            needle_quality=0.7,
            centre=(60.0, 60.0),
            tip=(30.0, 35.0),
            radius=50.0,
        )

        overlay = render_overlay(
            image=self.image,
            result=result,
        )

        np.testing.assert_array_equal(self.image, before)

        self.assertFalse(np.array_equal(self.image, overlay))

    @patch("gauge_overlay.cv2.putText")
    def test_invalid_overlay_names_failure_without_bar_value(
        self,
        put_text,
    ) -> None:
        """A rejected result must explain the failure without inventing a value."""

        # A missing needle should show a reason, with no pressure value.
        result = GaugeReadResult.failure(
            FailureReason.NO_NEEDLE,
        )

        render_overlay(
            image=self.image,
            result=result,
        )

        # Inspect the text sent to OpenCV without depending on font pixels.
        put_text.assert_called_once()

        label = put_text.call_args.args[1]

        self.assertIn("NO_NEEDLE", label)
        self.assertNotIn("bar", label.lower())

    @patch("gauge_overlay.cv2.putText")
    def test_valid_overlay_reports_value_and_quality(
        self,
        put_text,
    ) -> None:
        """An accepted result must display its value and evidence quality."""

        result = GaugeReadResult.success(
            value_bar=2.5,
            angle_degrees=292.5,
            dial_quality=0.8,
            needle_quality=0.7,
            centre=(60.0, 60.0),
            tip=(30.0, 35.0),
            radius=50.0,
        )

        render_overlay(
            image=self.image,
            result=result,
        )

        # Inspect the text sent to OpenCV without depending on font pixels.
        put_text.assert_called_once()

        label = put_text.call_args.args[1]

        self.assertEqual(label, "2.5 bar | Q 0.70")

    def test_valid_overlay_draws_needle_from_centre_to_tip(self) -> None:
        """The overlay must visibly connect the dial centre to the needle tip."""

        result = GaugeReadResult.success(
            value_bar=5.0,
            angle_degrees=270.0,
            dial_quality=0.8,
            needle_quality=0.7,
            centre=(60.0, 80.0),
            tip=(30.0, 80.0),
            radius=30.0,
        )

        overlay = render_overlay(
            self.image,
            result,
        )

        # Inspect the rendered pivot at x=60, y=80 and compare it with the source.
        centre_pixel = overlay[80, 60]
        original_centre_pixel = self.image[80, 60]

        # This pixel is away from the rim and label, so only the needle changes it.
        midpoint_pixel = overlay[80, 45]
        original_midpoint_pixel = self.image[80, 45]

        self.assertFalse(
            np.array_equal(
                centre_pixel,
                original_centre_pixel,
            )
        )
        self.assertFalse(
            np.array_equal(
                midpoint_pixel,
                original_midpoint_pixel,
            )
        )

    def test_detected_dial_marks_centre_without_needle(self) -> None:
        """Known dial geometry must remain visible when the needle is missing."""

        result = GaugeReadResult.failure(
            FailureReason.NO_NEEDLE,
            dial_quality=0.8,
            centre=(60.0, 80.0),
            radius=30.0,
        )

        overlay = render_overlay(self.image, result)

        # The rim and label cannot change this pixel; the pivot marker must do it.
        centre_pixel = overlay[80, 60]
        original_centre_pixel = self.image[80, 60]

        self.assertFalse(
            np.array_equal(
                centre_pixel,
                original_centre_pixel,
            )
        )

    def test_grayscale_input_is_converted_to_bgr(self) -> None:
        """Status colours require a three-channel output image."""

        grayscale = np.full(
            shape=(120, 120),
            fill_value=255,
            dtype=np.uint8,
        )

        result = GaugeReadResult.failure(
            FailureReason.NO_DIAL,
        )

        overlay = render_overlay(grayscale, result)

        # Confirm rendering leaves the original greyscale array as a single channel.
        self.assertEqual(grayscale.shape, (120, 120))

        # Confirm the returned overlay has three channels for the status colours.
        self.assertEqual(overlay.shape, (120, 120, 3))

    def test_large_dial_uses_visible_centre_marker(self) -> None:
        """A fixed size pivot may not be visible on larger dials."""

        large_image = np.full(
            shape=(800, 800, 3),
            fill_value=255,
            dtype=np.uint8,
        )

        result = GaugeReadResult.failure(
            FailureReason.NO_NEEDLE,
            dial_quality=0.8,
            centre=(400.0, 400.0),
            radius=300.0,
        )

        overlay = render_overlay(
            large_image,
            result,
        )

        # Sample five pixels from the pivot to check that its marker scales up.
        marker_edge_pixel = overlay[400, 405]
        original_edge_pixel = large_image[400, 405]

        self.assertFalse(
            np.array_equal(
                marker_edge_pixel,
                original_edge_pixel,
            )
        )

    def test_large_dial_uses_scaled_geometry_strokes(self) -> None:
        """Circumference and needle lines must remain visible on larger images."""

        large_image = np.full(
            shape=(800, 800, 3),
            fill_value=255,
            dtype=np.uint8,
        )

        result = GaugeReadResult.success(
            value_bar=5.0,
            angle_degrees=270.0,
            dial_quality=0.8,
            needle_quality=0.7,
            centre=(400.0, 400.0),
            tip=(100.0, 400.0),
            radius=300.0,
        )

        overlay = render_overlay(
            large_image,
            result,
        )

        # Sample beside the ray and rim to check the stroke width.
        ray_edge_pixel = overlay[403, 275]
        original_ray_pixel = large_image[403, 275]

        rim_edge_pixel = overlay[103, 400]
        original_rim_pixel = large_image[103, 400]

        self.assertFalse(
            np.array_equal(
                ray_edge_pixel,
                original_ray_pixel,
            )
        )
        self.assertFalse(
            np.array_equal(
                rim_edge_pixel,
                original_rim_pixel,
            )
        )


if __name__ == "__main__":
    unittest.main()

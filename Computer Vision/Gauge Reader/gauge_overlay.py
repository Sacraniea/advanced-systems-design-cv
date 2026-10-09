"""Render an explainable visual overlay for a gauge-reading result."""

from __future__ import annotations

import cv2
import numpy as np

from gauge_types import GaugeReadResult


def render_overlay(
    image: np.ndarray,
    result: GaugeReadResult,
) -> np.ndarray:
    """Return an annotated copy of a gauge candidate image."""

    # Draw on the original candidate crop because the result geometry uses crop pixels.
    # Convert greyscale input to colour, or copy an existing colour image for drawing.
    if image.ndim == 2:
        overlay = cv2.cvtColor(
            image,
            cv2.COLOR_GRAY2BGR,
        )

    else:
        overlay = image.copy()

    # Choose green for an accepted reading and orange for a rejected reading.
    colour = (0, 180, 0) if result.valid else (0, 140, 255)

    # Draw the circle whenever centre and radius are available, including rejected
    # readings.
    if (
        result.centre is not None
        and result.radius is not None
    ):
        # Round the detected centre and radius to whole pixels for the drawing calls.
        centre = (
            int(round(result.centre[0])),
            int(round(result.centre[1])),
        )
        radius = max(1, int(round(result.radius)))

        # Calculate marker and line sizes from the dial radius, with minimum pixel
        # widths.
        centre_marker_radius = max(
            5,
            int(round(0.02 * radius)),
        )
        geometry_stroke_width = max(
            3,
            int(round(0.008 * radius)),
        )

        cv2.circle(
            overlay,
            centre,
            radius,
            colour,
            thickness=geometry_stroke_width,
            lineType=cv2.LINE_AA,
        )

        # Draw a filled centre marker whether or not a needle tip was detected.
        cv2.circle(
            overlay,
            centre,
            radius=centre_marker_radius,
            color=colour,
            thickness=-1,
            lineType=cv2.LINE_AA,
        )

        if result.tip is not None:
            tip = (
                int(round(result.tip[0])),
                int(round(result.tip[1])),
            )

            cv2.line(
                overlay,
                centre,
                tip,
                colour,
                thickness=(geometry_stroke_width * 2),
                lineType=cv2.LINE_AA,
            )

    if result.valid:
        # Label an accepted reading with pressure in bar and the needle quality.
        label = f"{result.value_bar:.1f} bar" f" | Q {result.needle_quality:.2f}"

    else:
        # Label a rejected reading with its failure reason instead of a pressure value.
        reason = (
            result.failure_reason.value
            if result.failure_reason is not None
            else "UNKNOWN"
        )
        label = f"Rejected: {reason}"

    # Draw the reading or rejection label at the top left of the overlay.
    cv2.putText(
        overlay,
        label,
        (10, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        colour,
        thickness=2,
        lineType=cv2.LINE_AA,
    )

    return overlay

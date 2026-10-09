"""Visually inspect real Hough-circle detection on a synthetic dial."""

from pathlib import Path

import cv2
import numpy as np

from gauge_dial import detect_dial
from gauge_types import ReaderConfig

OUTPUT_DIRECTORY = Path(__file__).resolve().parent


def main() -> None:
    """Generate, detect, annotate, and save a singular synthetic dial image."""

    # Draw a known 190-pixel rim so the detected centre and radius can be checked.
    synthetic = np.full(
        (512, 512),
        255,
        dtype=np.uint8,
    )

    cv2.circle(
        synthetic,
        center=(256, 256),
        radius=190,
        color=0,
        thickness=3,
    )

    result = detect_dial(
        synthetic,
        ReaderConfig(),
    )

    print("Valid: ", result.valid)
    print("Quality: ", round(result.quality, 3))
    print("Failure reason: ", result.failure_reason)

    overlay = cv2.cvtColor(
        synthetic,
        cv2.COLOR_GRAY2BGR,
    )

    # Draw the detected geometry only when the dial result contains an accepted circle.
    if result.valid and result.geometry is not None:

        centre = (
            round(result.geometry.centre[0]),
            round(result.geometry.centre[1]),
        )

        radius = round(result.geometry.radius)

        print("detected centre: ", centre)
        print("detected radius: ", radius)

        # Green marks the detected circle and its centre.
        cv2.circle(
            overlay,
            centre,
            radius,
            color=(0, 180, 0),
            thickness=2,
        )
        cv2.circle(
            overlay,
            centre,
            radius=4,
            color=(0, 180, 0),
            thickness=-1,
        )

    # Save the unmarked test image and annotated copy; stop if either write fails.
    input_path = OUTPUT_DIRECTORY / "synthetic_dial.png"
    overlay_path = OUTPUT_DIRECTORY / "synthetic_dial_overlay.png"

    if not cv2.imwrite(str(input_path), synthetic):
        raise RuntimeError(f"Failed to write {input_path}")

    if not cv2.imwrite(str(overlay_path), overlay):
        raise RuntimeError(f"Failed to write {overlay_path}")

    print("Wrote overlay to: ", overlay_path)


if __name__ == "__main__":
    main()

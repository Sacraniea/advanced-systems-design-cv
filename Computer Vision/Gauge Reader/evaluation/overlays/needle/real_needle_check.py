"""Evaluate radial needle detection on the two test images"""

from pathlib import Path

import cv2
import numpy as np

from gauge_dial import detect_dial
from gauge_geometry import point_at_angle
from gauge_needle import radial_scores, select_needle
from gauge_types import ReaderConfig

NEEDLE_DIRECTORY = Path(__file__).resolve().parent
PREPROCESS_DIRECTORY = NEEDLE_DIRECTORY.parent / "preprocess"


def pixel_point(point: tuple[float, float]) -> tuple[int, int]:
    """Round a continuous image point into pixel coordinates for OpenCV."""
    return (
        round(point[0]),
        round(point[1]),
    )


def inspect_gauge(
    label: str,
    image_path: Path,
) -> None:
    """Detect the dial and needle, then save numerical and visual evidence."""

    # Load the saved working image used by the dial and needle stages.
    gray = cv2.imread(
        str(image_path),
        cv2.IMREAD_GRAYSCALE,
    )

    if gray is None:
        raise RuntimeError(f"Failed to read {image_path}")

    config = ReaderConfig()
    dial = detect_dial(
        gray,
        config,
    )

    print(f"Label: {label}")
    print(f"Dial valid: {dial.valid}")
    print(f"Dial quality: {dial.quality:.3f}")

    # Print the dial failure and stop before needle scoring if no circle was accepted.
    if not dial.valid or dial.geometry is None:
        print(f"Dial failure reason: {dial.failure_reason}")
        return

    # Score the radial directions and select a needle from the strongest peaks.
    scores = radial_scores(
        gray,
        dial.geometry,
        config,
    )

    needle = select_needle(
        scores,
        dial.geometry,
        config,
    )

    # Convert each score index to the angle it represents in the radial scan.
    angles = np.arange(scores.size) * config.radial_angle_step_degrees

    # Save every angle score so competing peaks can be inspected.
    csv_path = NEEDLE_DIRECTORY / f"{label}_radial_scores.csv"

    np.savetxt(
        csv_path,
        np.column_stack((angles, scores)),
        delimiter=",",
        header="angle_degrees,score",
        comments="",
        fmt="%.6f",
    )

    print(f"Needle valid: {needle.valid}")
    print(f"Angle: {needle.angle_degrees}")
    print(f"Winning score: {needle.winning_score:.3f}")
    print(f"Peak margin: {needle.peak_margin:.3f}")
    print(f"Needle quality: {needle.quality:.3f}")
    print(f"Needle failure: {needle.failure_reason}")
    print(f"Score CSV: {csv_path}")

    overlay = cv2.cvtColor(
        gray,
        cv2.COLOR_GRAY2BGR,
    )

    centre = pixel_point(dial.geometry.centre)
    radius = round(dial.geometry.radius)

    cv2.circle(
        overlay,
        centre,
        radius,
        color=(0, 180, 0),
        thickness=2,
    )

    # Draw any reported direction, including a rejected peak, for visual inspection.
    if needle.angle_degrees is not None:
        reading_tip = point_at_angle(
            dial.geometry.centre,
            distance=(config.outer_radius_fraction * dial.geometry.radius),
            angle_degrees=needle.angle_degrees,
        )

        # Calculate the ray 180 degrees opposite the selected peak to show the tail.
        opposite_angle = (needle.angle_degrees + 180.0) % 360.0

        opposite_tip = point_at_angle(
            dial.geometry.centre,
            distance=(config.outer_radius_fraction * dial.geometry.radius),
            angle_degrees=opposite_angle,
        )

        # Draw the selected direction in red from the centre to the scanned outer
        # radius.
        cv2.line(
            overlay,
            centre,
            pixel_point(reading_tip),
            color=(0, 0, 255),
            thickness=2,
        )

        # Blue shows the opposite direction for comparison with the tail.
        cv2.line(
            overlay,
            centre,
            pixel_point(opposite_tip),
            color=(255, 0, 0),
            thickness=1,
        )

        cv2.circle(
            overlay,
            centre,
            radius=4,
            color=(0, 180, 0),
            thickness=-1,
        )

    overlay_path = NEEDLE_DIRECTORY / f"{label}_needle_overlay.png"

    if not cv2.imwrite(
        str(overlay_path),
        overlay,
    ):
        raise RuntimeError(f"Failed to write {overlay_path}")

    print(f"Overlay image: {overlay_path}")
    print()


def main() -> None:
    """Evaluate both real gauge examples."""

    inspect_gauge(
        "festo",
        PREPROCESS_DIRECTORY / "festo_working_gray.jpg",
    )
    inspect_gauge(
        "bourdon",
        PREPROCESS_DIRECTORY / "bourdon_working_gray.jpg",
    )


if __name__ == "__main__":
    main()

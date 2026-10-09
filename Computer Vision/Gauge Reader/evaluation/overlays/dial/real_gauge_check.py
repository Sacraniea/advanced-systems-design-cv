"""Inspect dial localisation on the two preprocessed real gauge images."""

from pathlib import Path

import cv2

from gauge_dial import detect_dial
from gauge_types import ReaderConfig

DIAL_DIRECTORY = Path(__file__).resolve().parent
PREPROCESS_DIRECTORY = DIAL_DIRECTORY.parent / "preprocess"


def inspect_gauge(
    label: str,
    image_path: Path,
) -> None:
    """Detect a dial, print its evidence, and save the visual overlay."""

    # Load the preprocessed greyscale image and stop if the file cannot be decoded.
    gray = cv2.imread(
        str(image_path),
        cv2.IMREAD_GRAYSCALE,
    )

    if gray is None:
        raise FileNotFoundError(f"Could not load image: {image_path}")

    result = detect_dial(
        gray,
        ReaderConfig(),
    )

    print(f"Label: {label}")
    print(f"Valid: {result.valid}")
    print(f"Quality: {result.quality:.3f}")
    print(f"Failure reason: {result.failure_reason}")

    overlay = cv2.cvtColor(
        gray,
        cv2.COLOR_GRAY2BGR,
    )

    # Save an annotated image only when circle detection accepts a dial.
    if result.valid and result.geometry is not None:

        centre = (
            round(result.geometry.centre[0]),
            round(result.geometry.centre[1]),
        )

        radius = round(result.geometry.radius)

        print(f"Detected centre: {centre}")
        print(f"Detected radius: {radius}")

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

        output_path = DIAL_DIRECTORY / f"{label}_dial_overlay.png"

        if not cv2.imwrite(str(output_path), overlay):
            raise RuntimeError(f"Failed to write {output_path}")

        print(f"Overlay saved to: {output_path}")


def main() -> None:
    """Evaluate both available gauge examples."""

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

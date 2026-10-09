"""Generate visual preprocessing evidence from two sampled gauge crops."""

from pathlib import Path
import os

import cv2

from gauge_preprocess import preprocess_candidate
from gauge_types import ReaderConfig

# Point GAUGE_DATA_ROOT at your local sample videos before running this experiment.
DATA_ROOT = Path(os.environ["GAUGE_DATA_ROOT"])

OUTPUT_DIRECTORY = Path(__file__).resolve().parent


# Temporary detector boxes use (x_min, y_min, x_max, y_max) in source pixels.
SAMPLES = {
    "festo": {
        "video": DATA_ROOT / "Gauge Video 5.mp4",
        "crop": (200, 400, 1650, 1900),
    },
    "bourdon": {
        "video": DATA_ROOT / "Gauge Video 7.mp4",
        "crop": (0, 850, 2160, 3250),
    },
}


def read_middle_frame(video_path: Path):
    """Read the midpoint frame from a video file."""

    capture = cv2.VideoCapture(str(video_path))

    if not capture.isOpened():
        raise RuntimeError(f"Failed to open video file {video_path}")

    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    middle_frame_index = frame_count // 2

    # Seek to the midpoint instead of decoding every earlier frame.
    capture.set(
        cv2.CAP_PROP_POS_FRAMES,
        middle_frame_index,
    )
    success, frame = capture.read()
    capture.release()

    if not success:
        raise RuntimeError(
            f"Failed to read frame {middle_frame_index} " f"from {video_path}"
        )

    return frame


def main() -> None:
    """Preprocess both samples and save their diagnostic images."""

    config = ReaderConfig()

    # Extract each fixed video crop and save its preprocessing results beside this
    # script.
    for sample_name, sample in SAMPLES.items():
        frame = read_middle_frame(sample["video"])
        x_min, y_min, x_max, y_max = sample["crop"]

        # Extract the configured gauge rectangle from the midpoint video frame.
        crop = frame[
            y_min:y_max,
            x_min:x_max,
        ]

        outcome = preprocess_candidate(crop, config)

        # Stop this check if preprocessing rejected the crop or omitted required
        # outputs.
        if (
            not outcome.valid
            or outcome.working_colour is None
            or outcome.working_gray is None
            or outcome.transform is None
        ):
            raise RuntimeError(
                f"{sample_name} preprocessing failed: " f"{outcome.failure_reason}"
            )

        # Save the source and both working images for a visual comparison.
        outputs = {
            "source": crop,
            "working_colour": outcome.working_colour,
            "working_gray": outcome.working_gray,
        }

        # Write one file per image stage and report the exact path if a save fails.
        for output_name, output_image in outputs.items():
            output_path = OUTPUT_DIRECTORY / (f"{sample_name}_{output_name}.jpg")

            if not cv2.imwrite(
                str(output_path),
                output_image,
            ):
                raise RuntimeError(f"Failed to write {output_path}")

        source_height, source_width = crop.shape[:2]

        print(f"{sample_name}:")
        print(f"  source size: " f"{source_width}x{source_height}")
        print(f"  scale: {outcome.transform.scale:.6f}")
        print(
            f"  padding: "
            f"x={outcome.transform.pad_x:.1f}, "
            f"y={outcome.transform.pad_y:.1f}"
        )
        print(f"  blur variance: " f"{outcome.blur_variance:.3f}")


if __name__ == "__main__":
    main()

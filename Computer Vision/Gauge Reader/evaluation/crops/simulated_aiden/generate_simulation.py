"""Create ignored, lightweight simulations of Aiden's candidate crops.

This is deliberately an evaluation-only helper.  It does not reproduce
Aiden's detector and must not be presented as detector evidence.  It takes
real source pixels, makes a plausible loose crop around each supplied gauge,
and records exactly what was done in a JSON manifest.

The clean images under ``inputs/`` are the files intended for the gauge
reader.  The disclosure banner appears only in ``simulation_overview.jpg`` so
text and banner edges cannot influence the computer-vision result.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
CROPS_ROOT = HERE.parent
SOURCE_ROOT = CROPS_ROOT / "source_frames"
EXTERNAL_ROOT = CROPS_ROOT / "external_challenges"
INPUT_ROOT = HERE / "inputs"

# Radius limits use pixels in the 800-pixel-wide crop-preparation image.
# They keep large background arcs out of this helper, not the gauge reader.
VIDEO_RADIUS_RANGES = {
    1: (45, 110),
    2: (65, 150),
    3: (110, 230),
    5: (140, 300),
    6: (180, 330),
    7: (180, 330),
    8: (180, 330),
}

# Cycle the dial position to imitate loose detector boxes while keeping the rim.
# Coordinates are fractions of the crop width and height.
DIAL_POSITIONS: Tuple[Tuple[str, float, float], ...] = (
    ("centre", 0.50, 0.50),
    ("left", 0.40, 0.50),
    ("right", 0.60, 0.50),
    ("upper", 0.50, 0.40),
    ("lower", 0.50, 0.60),
    ("upper-left", 0.42, 0.42),
    ("lower-right", 0.58, 0.58),
)

# Hand-reviewed boxes use original source pixels in (x0, y0, x1, y1) order.
# These were chosen without pressure labels or reader outputs.
EXTERNAL_CROP_BOXES: Dict[str, Tuple[int, int, int, int]] = {
    "DIal2.jpg": (115, 20, 431, 336),
    "Dial-angle.jpg": (430, 0, 1000, 563),
    "Dial.jpg": (35, 5, 430, 430),
    "Dial3.jpg": (40, 10, 340, 310),
    "Tilted-dial.png": (215, 10, 690, 485),
    "darkdial.jpg": (25, 5, 546, 526),
}


def _video_number(path: Path) -> int:
    """Read the ``vNN`` provenance token from an extracted-frame filename."""

    match = re.search(r"-v(\d+)-", path.name)

    if match is None:
        raise ValueError(f"missing video number in {path.name}")

    return int(match.group(1))


def _locate_video_dial(
    image: np.ndarray, video_number: int
) -> Tuple[float, float, float]:
    """Locate the dominant dial circle for crop preparation only.

    This intentionally uses a constrained Hough search instead of calling the
    gauge reader.  Using the reader to manufacture its own test inputs would
    make the evaluation circular.
    """

    # Resize the full frame to 800 pixels wide before applying the per-video radius
    # limits.
    height, width = image.shape[:2]
    working_width = 800
    scale = working_width / float(width)
    working_height = int(round(height * scale))
    working = cv2.resize(image, (working_width, working_height))
    grey = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    grey = cv2.GaussianBlur(grey, (9, 9), 2)

    minimum_radius, maximum_radius = VIDEO_RADIUS_RANGES[video_number]
    circles = cv2.HoughCircles(
        grey,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=120,
        param1=140,
        param2=45,
        minRadius=minimum_radius,
        maxRadius=maximum_radius,
    )

    if circles is None:
        raise RuntimeError(
            f"no crop-preparation circle found for video {video_number}"
        )

    candidates = [
        tuple(float(value) for value in circle)
        for circle in circles[0]
    ]

    # Prefer circles near the expected gauge position; a larger radius reduces the
    # score.
    def preparation_score(circle: Tuple[float, float, float]) -> float:
        x, y, radius = circle
        return (
            abs(x - working_width * 0.50)
            + 0.35 * abs(y - working_height * 0.43)
            - 0.30 * radius
        )

    # Choose the lowest-scoring circle and map its centre and radius back to source
    # pixels.
    x, y, radius = min(candidates, key=preparation_score)
    return x / scale, y / scale, radius / scale


def _square_crop_box(
    image: np.ndarray,
    centre_x: float,
    centre_y: float,
    radius: float,
    dial_x_fraction: float,
    dial_y_fraction: float,
) -> Tuple[int, int, int, int]:
    """Create a loose square box while retaining the complete dial rim."""

    height, width = image.shape[:2]
    # Allow space around the dial, with at least 320 pixels unless the frame is smaller.
    side = int(round(max(320.0, 2.8 * radius)))
    side = min(side, width, height)

    # Offset the crop so the dial sits at the requested fraction of its width and
    # height.
    x0 = int(round(centre_x - dial_x_fraction * side))
    y0 = int(round(centre_y - dial_y_fraction * side))

    # Shift the box inside the frame without changing its size.
    x0 = min(max(0, x0), width - side)
    y0 = min(max(0, y0), height - side)
    return x0, y0, x0 + side, y0 + side


def _resize_lightweight(image: np.ndarray, maximum_side: int = 640) -> np.ndarray:
    """Downscale large crops without ever enlarging small source images."""

    height, width = image.shape[:2]
    scale = min(1.0, maximum_side / float(max(width, height)))

    if scale == 1.0:
        return image.copy()

    size = (int(round(width * scale)), int(round(height * scale)))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def _preview_bgr(image: np.ndarray) -> np.ndarray:
    """Convert supported clean-input formats into BGR for the contact sheet."""

    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

    return image


def _write_input(path: Path, image: np.ndarray) -> None:
    """Write one clean PNG and fail loudly if OpenCV cannot save it."""

    if not cv2.imwrite(
        str(path),
        image,
        [cv2.IMWRITE_PNG_COMPRESSION, 4],
    ):
        raise RuntimeError(f"could not write {path}")


def _create_overview(records: List[Dict[str, object]]) -> None:
    """Create one clearly disclosed human-review sheet outside model inputs."""

    columns = 4
    cell_width = 270
    cell_height = 310
    banner_height = 92
    rows = (len(records) + columns - 1) // columns
    sheet = np.full(
        (banner_height + rows * cell_height, columns * cell_width, 3),
        244,
        dtype=np.uint8,
    )

    # Draw the simulation disclosure into the overview banner above the crop thumbnails.
    sheet[:banner_height] = (42, 30, 18)
    cv2.putText(
        sheet,
        "AI-ASSISTED SYNTHETIC TEST CROP SIMULATION",
        (24, 36),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.78,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        sheet,
        "Simulates Aiden's candidate source crop - NOT FOR SUBMISSION",
        (24, 70),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.57,
        (130, 220, 255),
        1,
        cv2.LINE_AA,
    )

    # Reopen each clean crop, resize its preview and add its case and placement labels.
    for index, record in enumerate(records):
        image = cv2.imread(
            str(HERE / str(record["input_image"])),
            cv2.IMREAD_UNCHANGED,
        )

        if image is None:
            raise RuntimeError(f"could not reopen {record['input_image']}")

        image = _preview_bgr(image)

        scale = min(238 / image.shape[1], 220 / image.shape[0])
        thumbnail = cv2.resize(
            image,
            (
                max(1, int(round(image.shape[1] * scale))),
                max(1, int(round(image.shape[0] * scale))),
            ),
            interpolation=cv2.INTER_AREA,
        )

        # Place each thumbnail in its grid cell, leaving room for the labels.
        column = index % columns
        row = index // columns
        cell_x = column * cell_width
        cell_y = banner_height + row * cell_height
        x = cell_x + (cell_width - thumbnail.shape[1]) // 2
        y = cell_y + 10
        sheet[y : y + thumbnail.shape[0], x : x + thumbnail.shape[1]] = thumbnail

        label = str(record["case_id"])
        position = str(record["simulated_dial_position"])
        cv2.putText(
            sheet,
            label[:34],
            (cell_x + 8, cell_y + 252),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.39,
            (25, 25, 25),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            sheet,
            f"dial placement: {position}",
            (cell_x + 8, cell_y + 278),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (120, 55, 15),
            1,
            cv2.LINE_AA,
        )

    output = HERE / "simulation_overview.jpg"

    if not cv2.imwrite(
        str(output),
        sheet,
        [cv2.IMWRITE_JPEG_QUALITY, 90],
    ):
        raise RuntimeError(f"could not write {output}")


def main() -> None:
    """Generate clean inputs, an audit manifest, and one disclosed preview."""

    INPUT_ROOT.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, object]] = []

    # Locate each video dial and cycle its position within a loose simulated detector
    # crop.
    for index, source in enumerate(sorted(SOURCE_ROOT.glob("*.png"))):
        image = cv2.imread(str(source), cv2.IMREAD_COLOR)

        if image is None:
            raise RuntimeError(f"could not read {source}")

        video_number = _video_number(source)
        centre_x, centre_y, radius = _locate_video_dial(
            image,
            video_number,
        )
        position_name, position_x, position_y = DIAL_POSITIONS[
            index % len(DIAL_POSITIONS)
        ]
        x0, y0, x1, y1 = _square_crop_box(
            image,
            centre_x,
            centre_y,
            radius,
            position_x,
            position_y,
        )
        crop = _resize_lightweight(image[y0:y1, x0:x1])
        output_name = f"sim-{source.stem}-{position_name}.png"
        _write_input(INPUT_ROOT / output_name, crop)

        records.append(
            {
                "case_id": output_name.removesuffix(".png"),
                "input_image": f"inputs/{output_name}",
                "source_image": str(source.relative_to(CROPS_ROOT)),
                "source_type": "supplied_video_frame",
                "crop_box_xyxy": [x0, y0, x1, y1],
                "located_circle_xyr": [centre_x, centre_y, radius],
                "simulated_dial_position": position_name,
                "expected_use": "candidate_pressure_reading",
            }
        )

    # Build the external examples using their reviewed boxes instead of circle
    # detection.
    for source in sorted(EXTERNAL_ROOT.iterdir()):
        if not source.is_file():
            continue

        image = cv2.imread(str(source), cv2.IMREAD_UNCHANGED)

        if image is None or image.size == 0:
            raise RuntimeError(f"could not read {source}")

        if source.name not in EXTERNAL_CROP_BOXES:
            raise RuntimeError(f"external crop box not reviewed for {source.name}")

        x0, y0, x1, y1 = EXTERNAL_CROP_BOXES[source.name]
        height, width = image.shape[:2]

        # Reject an external crop box that is empty or extends beyond its source image.
        if not (
            0 <= x0 < x1 <= width
            and 0 <= y0 < y1 <= height
        ):
            raise RuntimeError(
                f"external crop box {(x0, y0, x1, y1)} is outside "
                f"{source.name} dimensions {(width, height)}"
            )

        # Cut out the reviewed rectangle, shrink it if needed and save the clean input.
        crop = _resize_lightweight(image[y0:y1, x0:x1])
        output_name = f"sim-external-{source.stem}.png"
        _write_input(INPUT_ROOT / output_name, crop)
        records.append(
            {
                "case_id": output_name.removesuffix(".png"),
                "input_image": f"inputs/{output_name}",
                "source_image": str(source.relative_to(CROPS_ROOT)),
                "source_type": "external_candidate_crop",
                "crop_box_xyxy": [x0, y0, x1, y1],
                "located_circle_xyr": None,
                "simulated_dial_position": "manually-bounded",
                "expected_use": "geometry_or_rejection_challenge",
            }
        )

    # Record each crop source and transformation before generating the labelled
    # overview.
    manifest = {
        "notice": (
            "AI-assisted synthetic test simulation of Aiden's candidate crop; "
            "not detector evidence and not for submission."
        ),
        "clean_inputs_contain_banner": False,
        "maximum_input_side_px": 640,
        "cases": records,
    }
    (HERE / "simulation_manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _create_overview(records)
    print(f"created {len(records)} simulated candidate inputs")
    print(HERE / "simulation_manifest.json")
    print(HERE / "simulation_overview.jpg")


if __name__ == "__main__":
    main()

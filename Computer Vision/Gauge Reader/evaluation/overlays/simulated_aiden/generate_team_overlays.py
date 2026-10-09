"""Generate ignored reader overlays for the temporary candidate-crop set.

The output is demonstration material for the team.  It is explicitly labelled
as an AI-assisted simulation of Aiden's future detector crop and must not be
presented as detector or independent holdout evidence.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
GAUGE_READER_ROOT = HERE.parents[2]
SIMULATION_ROOT = GAUGE_READER_ROOT / "evaluation" / "crops" / "simulated_aiden"
INDIVIDUAL_ROOT = HERE / "individual"

# Load the reader from this separate demonstration folder.
sys.path.insert(0, str(GAUGE_READER_ROOT))
from gauge_overlay import render_overlay  # noqa: E402
from gauge_reader import read_gauge  # noqa: E402

GROUP_DETAILS = {
    "development": {
        "title": "SUPPLIED-VIDEO DEVELOPMENT CROPS",
        "subtitle": (
            "AI-assisted simulation of Aiden's candidate crop | "
            "Green = accepted, orange = rejected"
        ),
        "columns": 4,
        "filename": "01_development_overlays.jpg",
    },
    "demonstration_challenge": {
        "title": "DEMONSTRATION / CHALLENGE CROPS",
        "subtitle": (
            "Outcomes have now been viewed: these are NOT blind holdout evidence"
        ),
        "columns": 3,
        "filename": "02_demonstration_challenge_overlays.jpg",
    },
    "external_robustness": {
        "title": "EXTERNAL ROBUSTNESS CROPS",
        "subtitle": (
            "Different scales are geometry challenges; displayed bar values "
            "are NOT accuracy claims"
        ),
        "columns": 3,
        "filename": "03_external_robustness_overlays.jpg",
    },
}


def _group_for_case(case: Dict[str, object]) -> str:
    """Keep demonstration categories separate so results are not overstated."""

    # Route external crops to robustness examples regardless of their filenames.
    if case["source_type"] == "external_candidate_crop":
        return "external_robustness"

    source = str(case["source_image"])

    if "-development.png" in source:
        return "development"

    # All remaining video cases have viewed outcomes and go on the challenge sheet.
    return "demonstration_challenge"


def _display_name(case_id: str, simulated_position: str) -> str:
    """Turn the auditable case identifier into a compact team-facing label."""

    video = re.match(r"sim-(bourdon|festo)-v(\d+)-t(\d{6})-", case_id)

    if video is not None:
        # Decode the video number and millisecond timestamp into a readable source
        # label.
        family, number, timestamp_ms = video.groups()
        return (
            f"{family.title()} | Video {int(number)} | "
            f"{int(timestamp_ms) / 1000:.1f} s | {simulated_position}"
        )

    if case_id.startswith("sim-external-"):
        return "External | " + case_id.removeprefix("sim-external-")

    return case_id


def _as_bgr(image: np.ndarray) -> np.ndarray:
    """Give the colour overlay renderer a consistent three-channel canvas."""

    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

    return image


def _fit_image(image: np.ndarray, width: int, height: int) -> np.ndarray:
    """Letterbox one overlay into a fixed-size contact-sheet viewport."""

    # Use one scale for both axes so the entire overlay fits the available rectangle.
    scale = min(width / image.shape[1], height / image.shape[0])
    resized = cv2.resize(
        image,
        (
            max(1, int(round(image.shape[1] * scale))),
            max(1, int(round(image.shape[0] * scale))),
        ),
        interpolation=cv2.INTER_AREA,
    )

    # Centre the overlay without stretching its circles.
    canvas = np.full((height, width, 3), 236, dtype=np.uint8)
    x = (width - resized.shape[1]) // 2
    y = (height - resized.shape[0]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    return canvas


def _make_contact_sheet(
    group: str,
    records: List[Dict[str, object]],
) -> Path:
    """Compose one high-resolution display sheet for a result category."""

    details = GROUP_DETAILS[group]
    columns = int(details["columns"])
    rows = (len(records) + columns - 1) // columns
    cell_width = 390
    cell_height = 430
    banner_height = 112
    sheet = np.full(
        (banner_height + rows * cell_height, columns * cell_width, 3),
        246,
        dtype=np.uint8,
    )

    # Draw the group title, simulation disclosure and submission warning in the banner.
    sheet[:banner_height] = (42, 30, 18)
    cv2.putText(
        sheet,
        str(details["title"]),
        (24, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.86,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        sheet,
        str(details["subtitle"]),
        (24, 76),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.53,
        (130, 220, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        sheet,
        "TEAM DEMONSTRATION ONLY - NOT FOR SUBMISSION",
        (24, 101),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.46,
        (180, 180, 255),
        1,
        cv2.LINE_AA,
    )

    # Reopen each saved overlay and place it beside its source label and reader outcome.
    for index, record in enumerate(records):
        overlay = cv2.imread(str(HERE / str(record["overlay_image"])))

        if overlay is None:
            raise RuntimeError(f"could not reopen {record['overlay_image']}")

        # Leave space below each overlay for its source and outcome labels.
        column = index % columns
        row = index // columns
        cell_x = column * cell_width
        cell_y = banner_height + row * cell_height
        viewport = _fit_image(overlay, 350, 330)
        sheet[cell_y + 12 : cell_y + 342, cell_x + 20 : cell_x + 370] = viewport

        colour = (0, 160, 0) if record["valid"] else (0, 120, 230)
        cv2.rectangle(
            sheet,
            (cell_x + 18, cell_y + 10),
            (cell_x + 372, cell_y + 344),
            colour,
            3,
        )
        cv2.putText(
            sheet,
            str(record["display_name"])[:50],
            (cell_x + 18, cell_y + 375),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.43,
            (25, 25, 25),
            1,
            cv2.LINE_AA,
        )
        outcome = (
            f"Accepted: {record['value_bar']:.2f} bar"
            if record["valid"]
            else f"Rejected: {record['failure_reason']}"
        )
        cv2.putText(
            sheet,
            outcome,
            (cell_x + 18, cell_y + 407),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            colour,
            1,
            cv2.LINE_AA,
        )

    output = HERE / str(details["filename"])

    if not cv2.imwrite(
        str(output),
        sheet,
        [cv2.IMWRITE_JPEG_QUALITY, 92],
    ):
        raise RuntimeError(f"could not write {output}")

    return output


def main() -> None:
    """Run the reader once per crop and save individual and grouped overlays."""

    INDIVIDUAL_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(
        (SIMULATION_ROOT / "simulation_manifest.json").read_text(encoding="utf-8")
    )
    records: List[Dict[str, object]] = []

    # Run the reader on each clean crop and save its annotated image and result fields.
    for case in manifest["cases"]:
        image_path = SIMULATION_ROOT / str(case["input_image"])
        image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)

        if image is None or image.size == 0:
            raise RuntimeError(f"could not decode {image_path}")

        result = read_gauge(image)
        overlay = render_overlay(_as_bgr(image), result)
        output_name = f"{case['case_id']}-overlay.png"
        output_path = INDIVIDUAL_ROOT / output_name

        if not cv2.imwrite(
            str(output_path),
            overlay,
            [cv2.IMWRITE_PNG_COMPRESSION, 4],
        ):
            raise RuntimeError(f"could not write {output_path}")

        records.append(
            {
                "case_id": case["case_id"],
                "display_name": _display_name(
                    str(case["case_id"]),
                    str(case["simulated_dial_position"]),
                ),
                "group": _group_for_case(case),
                "source_image": case["source_image"],
                "input_image": case["input_image"],
                "overlay_image": f"individual/{output_name}",
                "valid": result.valid,
                "failure_reason": (
                    None
                    if result.failure_reason is None
                    else result.failure_reason.value
                ),
                "value_bar": result.value_bar,
                "angle_degrees": result.angle_degrees,
                "dial_quality": result.dial_quality,
                "needle_quality": result.needle_quality,
            }
        )

    # Collect each result category and render one contact sheet for that category.
    sheet_paths = []

    for group in GROUP_DETAILS:
        group_records = [
            record
            for record in records
            if record["group"] == group
        ]
        sheet_paths.append(_make_contact_sheet(group, group_records))

    # Save the case results in JSON with the demonstration limits, then export a CSV
    # view.
    result_manifest = {
        "notice": (
            "AI-assisted candidate-crop simulation for team demonstration only; "
            "not Aiden detector evidence and not for submission."
        ),
        "independent_holdout_claim": False,
        "external_pressure_accuracy_claim": False,
        "cases": records,
    }
    (HERE / "overlay_results.json").write_text(
        json.dumps(result_manifest, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    with (HERE / "overlay_results.csv").open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "case_id",
                "display_name",
                "group",
                "valid",
                "failure_reason",
                "value_bar",
                "angle_degrees",
                "dial_quality",
                "needle_quality",
                "overlay_image",
            ),
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(records)

    print(f"created {len(records)} individual overlays")

    for path in sheet_paths:
        print(path)

    print(HERE / "overlay_results.json")
    print(HERE / "overlay_results.csv")


if __name__ == "__main__":
    main()

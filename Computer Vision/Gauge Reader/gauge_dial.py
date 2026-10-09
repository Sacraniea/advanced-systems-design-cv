"""Locate and validate the circular face of a candidate gauge crop."""

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from gauge_types import FailureReason, Point, ReaderConfig


@dataclass(frozen=True)
class DialGeometry:
    """Describe the position and size of a detected circular dial."""

    centre: Point
    radius: float


@dataclass(frozen=True)
class DialDetection:
    """Represent either an accepted dial or an explainable rejection."""

    valid: bool
    geometry: Optional[DialGeometry]
    quality: float
    failure_reason: Optional[FailureReason]


def _clamp_unit(value: float) -> float:
    """Restrict a score to the inclusive range from zero to one."""
    return max(0.0, min(1.0, value))


def _circumference_edge_support(
    edges: np.ndarray,
    centre: Point,
    radius: float,
) -> float:
    """Measure how much of a proposed circumference has nearby edges."""

    # Generate 720 positions around the proposed circle to check its visible rim.
    angles = np.linspace(
        0.0,
        2.0 * np.pi,
        720,
        endpoint=False,
    )

    # Convert each circular sample into the nearest image pixel.
    xs = np.rint(centre[0] + radius * np.cos(angles)).astype(int)
    ys = np.rint(centre[1] + radius * np.sin(angles)).astype(int)

    height, width = edges.shape
    supported_samples = []

    for x, y in zip(xs, ys):
        # Check a five-pixel square around this sample for an edge on the rim.
        x_start = max(0, x - 2)
        x_end = min(width, x + 3)
        y_start = max(0, y - 2)
        y_end = min(height, y + 3)

        neighbourhood = edges[
            y_start:y_end,
            x_start:x_end,
        ]

        supported_samples.append(bool(np.any(neighbourhood)))

    # Return the fraction of circle samples whose neighbourhood contained an edge.
    return float(np.mean(supported_samples))


def _candidate_quality(
    edges: np.ndarray,
    geometry: DialGeometry,
) -> float:
    """Score a circle using its centre position and expected radius."""

    height, width = edges.shape
    shortest_side = float(min(height, width))

    # Measure how far the candidate centre is from the middle of the supplied crop.

    image_centre = (
        width / 2.0,
        height / 2.0,
    )

    centre_distance = float(
        np.hypot(
            geometry.centre[0] - image_centre[0],
            geometry.centre[1] - image_centre[1],
        )
    )

    # Reduce the centre score as the circle moves away from the crop centre.
    centre_score = _clamp_unit(
        1.0 - centre_distance / (0.5 * shortest_side)
    )

    # Compare the radius with the expected 38% of the crop's shortest side.
    preferred_radius = 0.38 * shortest_side

    # Use this tolerance to reduce the score when the radius differs from that size.
    radius_tolerance = 0.22 * shortest_side

    radius_score = _clamp_unit(
        1.0 - abs(geometry.radius - preferred_radius) / radius_tolerance
    )

    edge_score = _circumference_edge_support(
        edges,
        geometry.centre,
        geometry.radius,
    )

    # Combine centre position, radius and rim evidence into one candidate score.
    return 0.45 * centre_score + 0.20 * radius_score + 0.35 * edge_score


def select_best_circle(
    circles: np.ndarray,
    gray: np.ndarray,
    config: ReaderConfig,
) -> DialDetection:
    """Select the most plausible dial from the supplied candidates"""

    # Find image edges once so every circle is scored against the same rim evidence.
    edges = cv2.Canny(
        gray,
        60,
        160,
    )

    candidates = np.asarray(
        circles,
        dtype=np.float64,
    ).reshape(-1, 3)

    ranked_candidates = []

    # Score each supplied circle and retain its geometry beside the score.
    for centre_x, centre_y, radius in candidates:
        geometry = DialGeometry(
            centre=(
                float(centre_x),
                float(centre_y),
            ),
            radius=float(radius),
        )

        quality = _candidate_quality(
            edges,
            geometry,
        )

        ranked_candidates.append((quality, geometry))

    # Select the highest-scoring circle from the candidates.
    quality, geometry = max(
        ranked_candidates,
        key=lambda candidate: candidate[0],
    )

    # Return NO_DIAL if even the best circle falls below the configured quality.
    if quality < config.minimum_circle_quality:
        return DialDetection(
            valid=False,
            geometry=None,
            quality=quality,
            failure_reason=FailureReason.NO_DIAL,
        )

    return DialDetection(
        valid=True,
        geometry=geometry,
        quality=quality,
        failure_reason=None,
    )


def detect_dial(
    gray: np.ndarray,
    config: ReaderConfig,
) -> DialDetection:
    """Search a normalised greyscale crop for circular dial candidates"""

    # Derive the circle radius and spacing limits from the image's shortest side.
    side = min(gray.shape)

    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=0.25 * side,
        param1=160,
        param2=35,
        minRadius=int(0.20 * side),
        maxRadius=int(0.49 * side),
    )

    # If OpenCV finds no circles, return NO_DIAL without trying to rank candidates.
    if circles is None:
        return DialDetection(
            valid=False,
            geometry=None,
            quality=0.0,
            failure_reason=FailureReason.NO_DIAL,
        )

    return select_best_circle(
        circles,
        gray,
        config,
    )

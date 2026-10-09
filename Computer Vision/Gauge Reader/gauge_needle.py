"""Detect a gauge needle by scoring radial image directions."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from gauge_dial import DialGeometry
from gauge_geometry import point_at_angle
from gauge_types import FailureReason, Point, ReaderConfig


@dataclass(frozen=True)
class NeedleDetection:
    """Represent a selected needle direction and its supporting evidence."""

    valid: bool
    angle_degrees: Optional[float]
    tip: Optional[Point]
    quality: float
    winning_score: float
    peak_margin: float
    failure_reason: Optional[FailureReason]


def _clamp_unit(value: float) -> float:
    """Restrict a score to the inclusive range from zero to one."""
    return max(0.0, min(1.0, value))


def _longest_true_run(mask: np.ndarray) -> int:
    """Count the longest uninterrupted sequence of true samples."""

    longest = 0
    current = 0

    # Count consecutive dark samples, resetting the current run at each light sample.
    for value in mask:
        if bool(value):
            current += 1
            longest = max(longest, current)

        else:
            current = 0

    return longest


def radial_scores(
    gray: np.ndarray,
    geometry: DialGeometry,
    config: ReaderConfig,
) -> np.ndarray:
    """Calculate needle evidence for every candidate radial direction."""

    angles = np.arange(
        0.0,
        360.0,
        config.radial_angle_step_degrees,
        dtype=np.float64,
    )

    # Limit each scan to the band between the hub and the outer scale markings.
    inner_radius = config.inner_radius_fraction * geometry.radius
    outer_radius = config.outer_radius_fraction * geometry.radius

    # Choose roughly one sample per pixel along that band, with a minimum of 16.
    sample_count = max(
        16,
        int(round(outer_radius - inner_radius)) + 1,
    )

    radii = np.linspace(
        inner_radius,
        outer_radius,
        sample_count,
    )

    # Sample parallel offsets on both sides of each ray to include the needle width.
    offsets = np.arange(
        -config.radial_half_width_px,
        config.radial_half_width_px + 1,
        dtype=np.float64,
    )

    # Increase the weight toward the outer end so a long tip scores above a short tail.
    radial_weights = np.linspace(
        0.5,
        1.5,
        sample_count,
    )

    height, width = gray.shape
    scores = np.zeros(
        len(angles),
        dtype=np.float64,
    )

    # Calculate one needle score for each candidate angle around the dial.
    for index, angle_degrees in enumerate(angles):
        angle_radians = np.radians(angle_degrees)

        # Convert this clockwise angle into horizontal and vertical image directions.
        direction_x = np.sin(angle_radians)
        direction_y = -np.cos(angle_radians)

        # Calculate the perpendicular direction for the samples across the ray.
        perpendicular_x = np.cos(angle_radians)
        perpendicular_y = np.sin(angle_radians)

        xs = (
            geometry.centre[0]
            + radii[:, None] * direction_x
            + offsets[None, :] * perpendicular_x
        )
        ys = (
            geometry.centre[1]
            + radii[:, None] * direction_y
            + offsets[None, :] * perpendicular_y
        )

        # Round the sample positions to pixels and clamp them to the image bounds.
        xs = np.clip(
            np.rint(xs).astype(int),
            0,
            width - 1,
        )
        ys = np.clip(
            np.rint(ys).astype(int),
            0,
            height - 1,
        )

        # Average the strip width into one brightness value at each distance from the
        # hub.
        profile = gray[ys, xs].mean(axis=1)

        # Convert brightness to a darkness score: white becomes 0 and black becomes 1.
        darkness = (255.0 - profile) / 255.0

        weighted_darkness = float(
            np.average(
                darkness,
                weights=radial_weights,
            )
        )

        # Mark the dark radial samples and measure their longest uninterrupted run.
        dark_mask = profile < config.dark_pixel_threshold

        continuity = _longest_true_run(dark_mask) / float(sample_count)

        # Average darkness over the outer quarter to check how far the needle extends.
        outer_start = int(0.75 * sample_count)
        outer_reach = float(np.mean(darkness[outer_start:]))

        # Combine average darkness, continuity and outer reach for this angle.
        scores[index] = _clamp_unit(
            0.45 * weighted_darkness + 0.35 * continuity + 0.20 * outer_reach
        )

    return scores


def select_needle(
    scores: np.ndarray,
    geometry: DialGeometry,
    config: ReaderConfig,
) -> NeedleDetection:
    """Select the strongest radial direction unless it is ambiguous."""

    # An empty scan cannot supply a direction, so return NO_NEEDLE.
    if scores.size == 0:
        return NeedleDetection(
            valid=False,
            angle_degrees=None,
            tip=None,
            quality=0.0,
            winning_score=0.0,
            peak_margin=0.0,
            failure_reason=FailureReason.NO_NEEDLE,
        )

    # Find the strongest scan and convert its array index into an angle in degrees.
    winning_index = int(np.argmax(scores))
    winning_score = float(scores[winning_index])
    winning_angle = winning_index * config.radial_angle_step_degrees

    angles = np.arange(scores.size) * config.radial_angle_step_degrees

    # Measure the shortest angular distance of every scan from the winning direction.
    angular_distance = np.abs(
        (angles - winning_angle + 180.0) % 360.0 - 180.0
    )

    # Copy the scan scores before excluding directions next to the winning peak.
    competing_scores = scores.astype(
        np.float64,
        copy=True,
    )

    # Exclude nearby scans so the runner-up represents a separate possible direction.
    competing_scores[angular_distance <= config.peak_exclusion_degrees] = -np.inf

    # Find the highest remaining competitor after removing the neighbouring scans.
    finite_competitors = competing_scores[np.isfinite(competing_scores)]

    second_score = (
        float(np.max(finite_competitors))
        if finite_competitors.size
        else 0.0
    )

    peak_margin = winning_score - second_score

    # Return NO_NEEDLE if the strongest scan is below the minimum needle score.
    if winning_score < config.minimum_needle_score:
        return NeedleDetection(
            valid=False,
            angle_degrees=None,
            tip=None,
            quality=0.0,
            winning_score=winning_score,
            peak_margin=peak_margin,
            failure_reason=FailureReason.NO_NEEDLE,
        )

    # Return AMBIGUOUS_NEEDLE if the winning score is too close to the runner-up.
    if peak_margin < config.minimum_peak_margin:
        return NeedleDetection(
            valid=False,
            angle_degrees=winning_angle,
            tip=None,
            quality=0.0,
            winning_score=winning_score,
            peak_margin=peak_margin,
            failure_reason=FailureReason.AMBIGUOUS_NEEDLE,
        )

    # Scale the winning score above the minimum into a 0-1 strength value.
    strength = _clamp_unit(
        (winning_score - config.minimum_needle_score)
        / max(1e-9, 1.0 - config.minimum_needle_score)
    )

    # Scale the peak margin to 0-1, reaching 1 at five times the required margin.
    separation = _clamp_unit(
        peak_margin
        / max(
            1e-9,
            5.0 * config.minimum_peak_margin,
        )
    )

    # Combine needle strength and separation from the runner-up into the quality score.
    quality = _clamp_unit(0.70 * strength + 0.30 * separation)

    # Place the reported tip at the outer end of the scanned band in the chosen
    # direction.
    tip = point_at_angle(
        geometry.centre,
        distance=(config.outer_radius_fraction * geometry.radius),
        angle_degrees=winning_angle,
    )

    return NeedleDetection(
        valid=True,
        angle_degrees=winning_angle,
        tip=tip,
        quality=quality,
        winning_score=winning_score,
        peak_margin=peak_margin,
        failure_reason=None,
    )


def detect_needle(
    gray: np.ndarray,
    geometry: DialGeometry,
    config: ReaderConfig,
) -> NeedleDetection:
    """Calculate radial scores and select the strongest candidate."""

    scores = radial_scores(
        gray,
        geometry,
        config,
    )

    return select_needle(
        scores,
        geometry,
        config,
    )

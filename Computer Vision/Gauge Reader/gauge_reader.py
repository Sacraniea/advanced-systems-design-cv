"""Coordinate preprocessing, dial detection, and gauge-value extraction."""

from typing import Any, Dict, Optional

import numpy as np

from gauge_dial import DialGeometry, detect_dial
from gauge_geometry import pressure_from_angle
from gauge_needle import detect_needle
from gauge_preprocess import LetterboxTransform, preprocess_candidate
from gauge_types import (
    FailureReason,
    GaugeCalibration,
    GaugeReadResult,
    Point,
    ReaderConfig,
)


def _source_geometry(
    geometry: DialGeometry,
    transform: LetterboxTransform,
) -> tuple[Point, float]:
    """Convert detected dial geometry back to the original crop coordinates."""

    # Undo the working-image padding and scale to recover the crop centre and radius.
    source_centre = transform.to_source_point(geometry.centre)
    source_radius = transform.to_source_radius(geometry.radius)

    return source_centre, source_radius


def read_gauge(
    image: Optional[np.ndarray],
    calibration: GaugeCalibration = GaugeCalibration(),
    config: ReaderConfig = ReaderConfig(),
    metadata: Optional[Dict[str, Any]] = None,
) -> GaugeReadResult:
    """Read a gauge candidate image through the full pipeline."""

    # Copy the supplied metadata into the result without changing the caller's
    # dictionary.
    evidence_metadata = dict(
        metadata or {},
    )

    # Validate the candidate crop and prepare the square greyscale image for detection.
    preprocessed = preprocess_candidate(
        image,
        config,
    )

    # If preprocessing rejected the crop, return its reason without running detection.
    if not preprocessed.valid:
        return GaugeReadResult.failure(
            (preprocessed.failure_reason or FailureReason.INVALID_INPUT),
            metadata=evidence_metadata,
        )

    # A valid preprocessing result must supply the image used by the dial search.
    assert preprocessed.working_gray is not None

    dial = detect_dial(
        preprocessed.working_gray,
        config,
    )

    # If the dial was rejected or has no circle, return its failure before searching
    # for a needle.
    if (
        not dial.valid
        or dial.geometry is None
    ):
        return GaugeReadResult.failure(
            (dial.failure_reason or FailureReason.NO_DIAL),
            dial_quality=dial.quality,
            metadata=evidence_metadata,
        )

    # A valid preprocessing result must also supply its coordinate transform.
    assert preprocessed.transform is not None

    # Map the detected working-image circle back to pixels in the original crop.
    source_centre, source_radius = _source_geometry(
        dial.geometry,
        preprocessed.transform,
    )

    # Reject a located dial below the sharpness threshold, but retain its circle.
    if preprocessed.blur_variance < config.minimum_blur_variance:
        return GaugeReadResult.failure(
            FailureReason.DIAL_TOO_BLURRED,
            dial_quality=dial.quality,
            centre=source_centre,
            radius=source_radius,
            metadata=evidence_metadata,
        )

    # Search for the needle using the working image and its detected circle.
    needle = detect_needle(
        preprocessed.working_gray,
        dial.geometry,
        config,
    )

    # Map a reported tip back to crop pixels; keep it missing if detection supplied
    # none.
    source_tip = (
        preprocessed.transform.to_source_point(needle.tip)
        if needle.tip is not None
        else None
    )

    # If the needle is rejected or has no angle, return its failure and the evidence
    # already collected for the dial.
    if (
        not needle.valid
        or needle.angle_degrees is None
    ):
        return GaugeReadResult.failure(
            (needle.failure_reason or FailureReason.NO_NEEDLE),
            dial_quality=dial.quality,
            needle_quality=needle.quality,
            angle_degrees=needle.angle_degrees,
            centre=source_centre,
            tip=source_tip,
            radius=source_radius,
            metadata=evidence_metadata,
        )

    # Convert the needle angle to pressure using the configured range and sweep.
    pressure_bar = pressure_from_angle(
        needle.angle_degrees,
        calibration,
    )

    # If the angle is outside the calibrated scale, reject the pressure reading
    # while retaining the detected angle and geometry.
    if pressure_bar is None:
        return GaugeReadResult.failure(
            FailureReason.ANGLE_OUTSIDE_SCALE,
            dial_quality=dial.quality,
            needle_quality=needle.quality,
            angle_degrees=needle.angle_degrees,
            centre=source_centre,
            tip=source_tip,
            radius=source_radius,
            metadata=evidence_metadata,
        )

    # A successful needle detection must supply a tip for the accepted result.
    assert source_tip is not None

    # Return the accepted pressure, quality scores and geometry in original crop pixels.
    return GaugeReadResult.success(
        value_bar=pressure_bar,
        angle_degrees=needle.angle_degrees,
        dial_quality=dial.quality,
        needle_quality=needle.quality,
        centre=source_centre,
        tip=source_tip,
        radius=source_radius,
        metadata=evidence_metadata,
    )

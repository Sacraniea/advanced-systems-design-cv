from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

Point = Tuple[float, float]


class FailureReason(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    NO_DIAL = "NO_DIAL"
    DIAL_TOO_SMALL = "DIAL_TOO_SMALL"
    DIAL_TOO_BLURRED = "DIAL_TOO_BLURRED"
    NO_NEEDLE = "NO_NEEDLE"
    AMBIGUOUS_NEEDLE = "AMBIGUOUS_NEEDLE"
    ANGLE_OUTSIDE_SCALE = "ANGLE_OUTSIDE_SCALE"


@dataclass(frozen=True)
class GaugeCalibration:
    # Angles run clockwise from up; these defaults assume a 0-10 bar gauge.
    minimum_bar: float = 0.0
    maximum_bar: float = 10.0
    zero_angle_degrees: float = 225.0
    full_scale_angle_degrees: float = 135.0
    clockwise_sweep_degrees: float = 270.0


@dataclass(frozen=True)
class ReaderConfig:
    # Pixel sizes below refer to the square working image unless marked source.
    working_size: int = 512
    minimum_source_side_px: int = 64
    minimum_blur_variance: float = 20.0
    minimum_circle_quality: float = 0.35
    inner_radius_fraction: float = 0.20
    outer_radius_fraction: float = 0.82
    radial_angle_step_degrees: float = 1.0
    radial_half_width_px: int = 2
    dark_pixel_threshold: int = 120
    peak_exclusion_degrees: float = 8.0
    minimum_needle_score: float = 0.18
    minimum_peak_margin: float = 0.02


@dataclass(frozen=True)
class GaugeReadResult:
    valid: bool
    value_bar: Optional[float]
    angle_degrees: Optional[float]
    dial_quality: float
    needle_quality: float

    # Geometry uses original crop pixels; add the crop origin for full-frame points.
    centre: Optional[Point]
    tip: Optional[Point]
    radius: Optional[float]
    failure_reason: Optional[FailureReason]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Reject results whose dial or needle quality falls outside the 0-1 range.
        for name, value in (
            ("dial_quality", self.dial_quality),
            ("needle_quality", self.needle_quality),
        ):
            if not (0.0 <= value <= 1.0):
                raise ValueError(
                    f"{name} must be between 0.0 and 1.0, got {value}"
                )

        # An accepted result must contain a pressure value and no failure reason.
        if (
            self.valid
            and (
                self.value_bar is None
                or self.failure_reason is not None
            )
        ):
            raise ValueError(
                "a valid result requires a value with no reason for failure."
            )

        # A rejected result must contain a failure reason and no pressure value.
        if (
            not self.valid
            and (
                self.value_bar is not None
                or self.failure_reason is None
            )
        ):
            raise ValueError(
                "an invalid result requires no value and a reason for failure."
            )

    @classmethod
    def success(
        cls,
        *,
        value_bar: float,
        angle_degrees: float,
        dial_quality: float,
        needle_quality: float,
        centre: Point,
        tip: Point,
        radius: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> GaugeReadResult:
        # Build an accepted result with pressure, geometry and a copy of the metadata.
        return cls(
            valid=True,
            value_bar=value_bar,
            angle_degrees=angle_degrees,
            dial_quality=dial_quality,
            needle_quality=needle_quality,
            centre=centre,
            tip=tip,
            radius=radius,
            failure_reason=None,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def failure(
        cls,
        reason: FailureReason,
        *,
        dial_quality: float = 0.0,
        needle_quality: float = 0.0,
        angle_degrees: Optional[float] = None,
        centre: Optional[Point] = None,
        tip: Optional[Point] = None,
        radius: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> GaugeReadResult:
        # Build a rejected result with no pressure, retaining any supplied detection
        # evidence.
        return cls(
            valid=False,
            value_bar=None,
            angle_degrees=angle_degrees,
            dial_quality=dial_quality,
            needle_quality=needle_quality,
            centre=centre,
            tip=tip,
            radius=radius,
            failure_reason=reason,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        # Convert the result to a dictionary and replace the failure enum with its text
        # value.
        document = asdict(self)
        document["failure_reason"] = (
            self.failure_reason.value if self.failure_reason is not None else None
        )
        return document

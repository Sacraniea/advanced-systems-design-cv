"""Adapter connecting the existing ArUco subsystem to the shared stream."""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

TARGET_DIRECTORY = Path(__file__).resolve().parent.parent / "Target Detection"


# Add the sibling Target Detection folder to Python's module search path if it is missing.
if str(TARGET_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TARGET_DIRECTORY))

from gauge_overlay import render_overlay
from gauge_reader import read_gauge

from stream_processing import FrameIdentity, ProcessingResult


class ArUcoProcessor:
    name = "aruco"

    def __init__(self, *, calibration, config, max_reprojection_error_px):
        # Store the camera calibration, marker settings and pose error limit.
        self.calibration = calibration
        self.config = config
        self.max_reprojection_error_px = max_reprojection_error_px

    def process(self, packet):
        # Detect markers in this packet and calculate their measurements.
        observations, record = process_aruco_frame(
            packet,
            self.calibration,
            self.config,
            max_reprojection_error_px=self.max_reprojection_error_px,
        )

        # Attach the source frame's identity to the measurements and drawing data.
        return ProcessingResult(
            self.name, FrameIdentity.from_packet(packet), record, observations
        )

    def draw(self, overlay, result):
        # Draw these marker observations on a copy of the supplied overlay.
        return draw_aruco_overlay(overlay, result.annotations)


def prepare_aruco(args, profile):
    # Build the ArUco processor and the settings to record alongside its results.
    config = config_from_args(args)
    # Leave calibration unset unless a calibration file was supplied.
    calibration = None
    evidence = None

    if args.calibration:
        native = load_calibration(args.calibration)
        # Adjust the loaded calibration for the requested stream dimensions.
        calibration = prepare_stream_calibration(
            native,
            image_size_px=(profile.width, profile.height),
            mode=args.calibration_mode,
        )
        raw = args.calibration.read_bytes()
        # Record the file hash, original calibration and values used by this stream.
        evidence = dict(
            file=str(args.calibration.resolve()),
            sha256=hashlib.sha256(raw).hexdigest(),
            original_record=json.loads(raw),
            effective=asdict(calibration),
            transform=args.calibration_mode,
        )

    # Give the processor its settings and return those settings as saved metadata.
    processor = ArUcoProcessor(
        calibration=calibration,
        config=config,
        max_reprojection_error_px=args.max_reprojection_error_px,
    )
    metadata = dict(
        dictionary_name=config.dictionary_name,
        permitted_ids=sorted(config.permitted_ids),
        marker_side_length_m=config.marker_side_length_m,
        calibration=evidence,
        max_reprojection_error_px=args.max_reprojection_error_px,
    )
    return processor, metadata

from __future__ import annotations

import argparse
import copy
import ctypes
import datetime as dt
import errno
import hashlib
import json
import math
import os
import platform
import re
import shutil
import socket
import statistics
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from oak_metrics import compare_runs, make_metric, render_markdown, summarize_frames

PROFILE_DEFINITIONS = {
    "rgb_preview_640x360_30": {
        "builder": "rgb_preview",
        "streams": {
            "rgb": {
                "kind": "image",
                "width": 640,
                "height": 360,
                "requested_fps": 30.0,
            },
            "system": {"kind": "system", "requested_fps": 1.0},
        },
        "markers": ["*"],
    },
    "rgb_video_1280x720_30": {
        "builder": "rgb_video_720",
        "streams": {
            "rgb": {
                "kind": "image",
                "width": 1280,
                "height": 720,
                "requested_fps": 30.0,
            },
            "system": {"kind": "system", "requested_fps": 1.0},
        },
        "markers": ["*"],
    },
    "rgb_video_1920x1080_30": {
        "builder": "rgb_video_1080",
        "streams": {
            "rgb": {
                "kind": "image",
                "width": 1920,
                "height": 1080,
                "requested_fps": 30.0,
            },
            "system": {"kind": "system", "requested_fps": 1.0},
        },
        "markers": ["*"],
    },
    "mono_pair_640x480_30": {
        "builder": "mono_pair",
        "streams": {
            "left": {
                "kind": "image",
                "width": 640,
                "height": 480,
                "requested_fps": 30.0,
            },
            "right": {
                "kind": "image",
                "width": 640,
                "height": 480,
                "requested_fps": 30.0,
            },
            "system": {"kind": "system", "requested_fps": 1.0},
        },
        "markers": ["*"],
    },
    "stereo_depth_640x480_30": {
        "builder": "stereo_depth",
        "streams": {
            "depth": {
                "kind": "image",
                "width": 640,
                "height": 480,
                "requested_fps": 30.0,
            },
            "system": {"kind": "system", "requested_fps": 1.0},
        },
        "markers": ["*"],
    },
    "h264_rgb_1920x1080_30": {
        "builder": "h264",
        "streams": {
            "h264": {
                "kind": "encoded",
                "width": 1920,
                "height": 1080,
                "requested_fps": 30.0,
            },
            "system": {"kind": "system", "requested_fps": 1.0},
        },
        "markers": ["*"],
    },
    "combined_h264_depth_system_30": {
        "builder": "combined",
        "streams": {
            "h264": {
                "kind": "encoded",
                "width": 1920,
                "height": 1080,
                "requested_fps": 30.0,
            },
            "depth": {
                "kind": "image",
                "width": 640,
                "height": 480,
                "requested_fps": 30.0,
            },
            "system": {"kind": "system", "requested_fps": 1.0},
        },
        "markers": ["*"],
    },
}
BENCHMARK_VERSION = "1.0.0"

SOURCE_AMBIGUITIES = [
    "The supplied datasheet conflicts between USB 3.1 Gen1 and USB 3.1 Gen2.",
    "The supplied datasheet's ‘900 mA USB2 max’ wording conflicts with USB power terminology.",
]

PROFILE_TEACHING = {
    "rgb_preview_640x360_30": (
        "preview-stream timing and payload",
        "establishes a low-bandwidth RGB reference",
        "near-configured frame rate under an uncongested link",
        "does not establish application detection accuracy",
    ),
    "rgb_video_1280x720_30": (
        "raw 720p video timing and payload",
        "shows how resolution changes host-link demand",
        "more payload than preview",
        "does not locate any observed loss",
    ),
    "rgb_video_1920x1080_30": (
        "raw 1080p video timing and payload",
        "exposes link pressure at the project video resolution",
        "USB 2 may constrain delivered payload",
        "does not measure total USB bus utilisation",
    ),
    "mono_pair_640x480_30": (
        "synchronised left/right mono delivery",
        "supports later stereo and target-observation work",
        "two raw streams increase aggregate payload",
        "does not prove where sequence loss occurred",
    ),
    "stereo_depth_640x480_30": (
        "on-device stereo-depth output",
        "separates OAK processing from host aggregation",
        "depth arrives without transferring both raw mono inputs",
        "does not establish scene-specific depth accuracy",
    ),
    "h264_rgb_1920x1080_30": (
        "on-device H.264 encoded output",
        "tests the project delivery-oriented video path",
        "encoded payload can fit USB 2 better than raw 1080p",
        "does not measure browser or network delivery latency",
    ),
    "combined_h264_depth_system_30": (
        "combined encoded video, depth, and system telemetry",
        "approximates the intended concurrent device workload",
        "resource and delivery evidence across all three streams",
        "does not establish application acceptance criteria",
    ),
}


def profile_definitions() -> dict[str, dict[str, Any]]:
    # Return a separate copy so caller edits cannot alter later benchmark profiles.
    return copy.deepcopy(PROFILE_DEFINITIONS)


def benchmark_provenance(
    source_root: Path | None = None,
    *,
    profiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Record the benchmark version and hashes identifying its source and profile
    # settings.
    root = (
        Path(source_root)
        if source_root is not None
        else Path(__file__).resolve().parent
    )

    # Hash the three source files byte-for-byte; comment edits change these hashes too.
    source_hashes = {
        filename: hashlib.sha256((root / filename).read_bytes()).hexdigest()
        for filename in ("oak_benchmark.py", "oak_metrics.py", "requirements.txt")
    }

    # Serialize profiles with sorted keys and fixed separators before hashing their
    # values.
    canonical_profiles = json.dumps(
        PROFILE_DEFINITIONS if profiles is None else profiles,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "version": BENCHMARK_VERSION,
        "source_sha256": source_hashes,
        "profile_definitions_sha256": hashlib.sha256(canonical_profiles).hexdigest(),
    }


def _xlink(pipeline: Any, stream_name: str, output: Any) -> None:
    # Expose a pipeline output as the named host stream.
    xlink = pipeline.createXLinkOut()
    xlink.setStreamName(stream_name)
    output.link(xlink.input)


def _color_camera(pipeline: Any, dai_module: Any, width: int, height: int) -> Any:
    # Configure CAM_A at 1080p and 30 fps, with the requested video dimensions.
    camera = pipeline.createColorCamera()
    camera.setBoardSocket(dai_module.CameraBoardSocket.CAM_A)
    camera.setResolution(dai_module.ColorCameraProperties.SensorResolution.THE_1080_P)
    camera.setFps(30.0)
    camera.setVideoSize(width, height)
    return camera


def _mono_pair(pipeline: Any, dai_module: Any) -> tuple[Any, Any]:
    # Create the CAM_B and CAM_C mono cameras at 480p and 30 frames per second.
    cameras = []

    for socket in (
        dai_module.CameraBoardSocket.CAM_B,
        dai_module.CameraBoardSocket.CAM_C,
    ):
        camera = pipeline.createMonoCamera()
        camera.setBoardSocket(socket)
        camera.setResolution(dai_module.MonoCameraProperties.SensorResolution.THE_480_P)
        camera.setFps(30.0)
        cameras.append(camera)

    return cameras[0], cameras[1]


def _add_system(pipeline: Any) -> None:
    # Send one SystemLogger sample per second to the host through the system stream.
    logger = pipeline.createSystemLogger()
    logger.setRate(1.0)
    _xlink(pipeline, "system", logger.out)


def _build_rgb_preview(pipeline: Any, dai_module: Any) -> None:
    # Send the 640-by-360 preview from the RGB camera to the host.
    camera = _color_camera(
        pipeline,
        dai_module,
        1920,
        1080,
    )
    camera.setPreviewSize(640, 360)
    _xlink(pipeline, "rgb", camera.preview)


def _build_rgb_video(pipeline: Any, dai_module: Any, width: int, height: int) -> None:
    # Send raw RGB video at the requested width and height to the host.
    camera = _color_camera(
        pipeline,
        dai_module,
        width,
        height,
    )
    _xlink(pipeline, "rgb", camera.video)


def _build_mono_pair(pipeline: Any, dai_module: Any) -> None:
    # Expose separate left and right mono image streams.
    left, right = _mono_pair(pipeline, dai_module)
    _xlink(pipeline, "left", left.out)
    _xlink(pipeline, "right", right.out)


def _stereo_nodes(pipeline: Any, dai_module: Any) -> Any:
    # Feed both mono cameras into the high-density stereo depth pipeline.
    left, right = _mono_pair(pipeline, dai_module)
    stereo = pipeline.createStereoDepth()
    stereo.setDefaultProfilePreset(dai_module.node.StereoDepth.PresetMode.HIGH_DENSITY)
    left.out.link(stereo.left)
    right.out.link(stereo.right)
    return stereo


def _build_stereo_depth(pipeline: Any, dai_module: Any) -> None:
    _xlink(pipeline, "depth", _stereo_nodes(pipeline, dai_module).depth)


def _h264_nodes(pipeline: Any, dai_module: Any) -> Any:
    # Prepare 1080p RGB video for the device-side H.264 encoder.
    camera = _color_camera(
        pipeline,
        dai_module,
        1920,
        1080,
    )

    # Encode on the OAK so the host receives H.264 bytes instead of raw RGB frames.
    encoder = pipeline.createVideoEncoder()
    encoder.setDefaultProfilePreset(
        30.0,
        dai_module.VideoEncoderProperties.Profile.H264_MAIN,
    )
    camera.video.link(encoder.input)
    return encoder


def _build_h264(pipeline: Any, dai_module: Any) -> None:
    _xlink(pipeline, "h264", _h264_nodes(pipeline, dai_module).bitstream)


def _build_combined(pipeline: Any, dai_module: Any) -> None:
    # Expose H.264 video and stereo depth in the same pipeline.
    _xlink(pipeline, "h264", _h264_nodes(pipeline, dai_module).bitstream)
    _xlink(pipeline, "depth", _stereo_nodes(pipeline, dai_module).depth)


def build_pipeline(profile_id: str, dai_module: Any) -> tuple[Any, dict[str, Any]]:
    # Find the selected profile or reject its ID before constructing the pipeline.
    try:
        contract = PROFILE_DEFINITIONS[profile_id]

    except KeyError as exc:
        raise ValueError(f"Unknown profile ID: {profile_id}") from exc

    pipeline = dai_module.Pipeline()
    builders: dict[str, Callable[[Any, Any], None]] = {
        "rgb_preview": _build_rgb_preview,
        "rgb_video_720": lambda value, module: _build_rgb_video(
            value,
            module,
            1280,
            720,
        ),
        "rgb_video_1080": lambda value, module: _build_rgb_video(
            value,
            module,
            1920,
            1080,
        ),
        "mono_pair": _build_mono_pair,
        "stereo_depth": _build_stereo_depth,
        "h264": _build_h264,
        "combined": _build_combined,
    }

    # Build the requested streams, then give every profile the same telemetry feed.
    builders[contract["builder"]](pipeline, dai_module)
    _add_system(pipeline)
    return pipeline, copy.deepcopy(contract)


def enum_name(value: Any) -> str:
    # Use the enum name when exposed, otherwise take the final part of its text.
    name = getattr(value, "name", None)

    if name is not None:
        return str(name)

    return str(value).rsplit(".", 1)[-1]


def camera_feature_to_dict(feature: Any) -> dict[str, Any]:
    return {
        "socket": enum_name(feature.socket),
        "sensor_name": feature.sensorName,
        "width": feature.width,
        "height": feature.height,
        "orientation": enum_name(feature.orientation),
        "supported_types": [enum_name(value) for value in feature.supportedTypes],
        "has_autofocus": feature.hasAutofocus,
        "has_autofocus_ic": feature.hasAutofocusIC,
        "name": feature.name,
    }


def memory_to_dict(memory: Any) -> dict[str, Any]:
    return {
        "used_bytes": memory.used,
        "total_bytes": memory.total,
        "remaining_bytes": memory.remaining,
    }


def temperature_to_dict(temperature: Any) -> dict[str, Any]:
    return {
        "average_c": temperature.average,
        "css_c": temperature.css,
        "mss_c": temperature.mss,
        "upa_c": temperature.upa,
        "dss_c": temperature.dss,
    }


def message_sample(message: object, host_s: float) -> dict[str, float | int]:
    # Keep payload bytes and sequence numbers with the arrival and frame timestamps.
    return {
        "host_s": host_s,
        "device_s": message.getTimestamp().total_seconds(),
        "sequence": int(message.getSequenceNum()),
        "payload_bytes": len(message.getData()),
    }


def system_sample(message: object, host_s: float) -> dict[str, object]:
    # Extract component temperatures, memory usage and CPU fractions from one sample.
    return {
        "host_s": host_s,
        "temperature_c": temperature_to_dict(message.chipTemperature),
        "ddr_memory": memory_to_dict(message.ddrMemoryUsage),
        "cmx_memory": memory_to_dict(message.cmxMemoryUsage),
        "leon_css_memory": memory_to_dict(message.leonCssMemoryUsage),
        "leon_mss_memory": memory_to_dict(message.leonMssMemoryUsage),
        "leon_css_cpu_fraction": float(message.leonCssCpuUsage.average),
        "leon_mss_cpu_fraction": float(message.leonMssCpuUsage.average),
    }


def validate_run_limits(
    duration_s: float,
    allow_long_test: bool,
    temperature_stop_c: float,
) -> tuple[float, float]:
    # Reject nonpositive durations, unapproved long runs and unsafe temperature limits.
    duration = float(duration_s)
    temperature_stop = float(temperature_stop_c)

    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be finite and greater than 0")

    if duration > 300 and not allow_long_test:
        raise ValueError("runs longer than 300 seconds require --allow-long-test")

    if not math.isfinite(temperature_stop):
        raise ValueError("temperature stop must be finite")

    if temperature_stop > 100:
        raise ValueError(
            "temperature stop maximum allowed is 100 C; must remain below 105 C"
        )

    return duration, temperature_stop


USB_NOMINAL_SIGNALLING_RATE_MBPS = {
    "LOW": 1.5,
    "FULL": 12.0,
    "HIGH": 480.0,
    "SUPER": 5_000.0,
    "SUPER_PLUS": 10_000.0,
}
SYSTEM_LOGGER_GRACE_S = 3.0


def _utc_text(now: dt.datetime) -> str:
    return now.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _aggregate_system_samples(samples: list[dict[str, object]]) -> dict[str, object]:
    # Summarize temperature, memory and CPU samples, keeping empty readings as no-data.
    markers = ("*",)
    status = "measured" if samples else "no-data"
    method = "DepthAI SystemLogger samples"
    temperature_metrics: dict[str, object] = {}

    # Calculate minimum, mean and maximum temperature for each monitored component.
    for component in ("average", "css", "mss", "upa", "dss"):
        values = [
            float(sample["temperature_c"][f"{component}_c"]) for sample in samples
        ]
        temperature_metrics[component] = {
            "min_c": make_metric(
                min(values) if values else None,
                "C",
                status,
                markers,
                method,
                [],
            ),
            "mean_c": make_metric(
                statistics.fmean(values) if values else None,
                "C",
                status,
                markers,
                method,
                [],
            ),
            "max_c": make_metric(
                max(values) if values else None,
                "C",
                status,
                markers,
                method,
                [],
            ),
        }

    # Record the highest used-byte count seen for each memory resource.
    memory_metrics = {}

    for resource in ("ddr", "cmx", "leon_css", "leon_mss"):
        values = [int(sample[f"{resource}_memory"]["used_bytes"]) for sample in samples]
        memory_metrics[resource] = make_metric(
            max(values) if values else None,
            "bytes",
            status,
            markers,
            method,
            [],
        )

    # Calculate mean and peak CPU usage fractions for both LEON processors.
    cpu_metrics = {}

    for processor in ("css", "mss"):
        values = [float(sample[f"leon_{processor}_cpu_fraction"]) for sample in samples]
        cpu_metrics[processor] = {
            "mean": make_metric(
                statistics.fmean(values) if values else None,
                "fraction",
                status,
                markers,
                method,
                [],
            ),
            "peak": make_metric(
                max(values) if values else None,
                "fraction",
                status,
                markers,
                method,
                [],
            ),
        }

    return {
        "temperature_c": temperature_metrics,
        "peak_memory_bytes": memory_metrics,
        "leon_cpu_fraction": cpu_metrics,
    }


def _disconnected_exception(exception: Exception) -> bool:
    # Classify an error as a disconnect when its type or message names a link failure.
    text = f"{type(exception).__name__} {exception}".lower()
    return any(
        indicator in text
        for indicator in ("disconnect", "detached", "xlink", "x_link", "device closed")
    )


def _unsupported_exception(exception: Exception, phase: str) -> bool:
    # Reject unsupported classification outside pipeline creation or device opening.
    if phase not in {"pipeline-build", "device-open"}:
        return False

    text = f"{type(exception).__name__} {exception}".lower()
    return (
        re.search(r"\bunsupported\b|\bnot supported\b|\bincompatib(?:le|ility)\b", text)
        is not None
    )


def _link_capabilities(
    negotiated_usb_speed: str | None,
    inventory_usb_speed: str | None,
    payload_bytes: int | None,
    polling_started: bool,
) -> dict[str, object]:
    # Look up the nominal rate for the observed USB mode and retain the payload total.
    nominal_rate = USB_NOMINAL_SIGNALLING_RATE_MBPS.get(negotiated_usb_speed or "")
    observed = "observed" if negotiated_usb_speed is not None else "no-data"
    inventory_status = "recorded" if inventory_usb_speed is not None else "not-recorded"
    return {
        "negotiated_usb_speed": make_metric(
            negotiated_usb_speed,
            "DepthAI enum",
            observed,
            ("*",),
            "DepthAI Device.getUsbSpeed after pipeline boot",
            [negotiated_usb_speed] if negotiated_usb_speed is not None else [],
        ),
        "nominal_signalling_rate_mbps": make_metric(
            nominal_rate,
            "Mbit/s nominal",
            observed if nominal_rate is not None else "unsupported",
            ("*",),
            "USB nominal signalling rate for negotiated DepthAI enum",
            [negotiated_usb_speed] if negotiated_usb_speed is not None else [],
        ),
        "inventory_usb_speed": make_metric(
            inventory_usb_speed,
            "DepthAI enum",
            inventory_status,
            ("*",),
            "explicit inventory USB speed supplied by caller",
            [inventory_usb_speed] if inventory_usb_speed is not None else [],
        ),
        "changed_from_inventory": (
            inventory_usb_speed is not None
            and negotiated_usb_speed is not None
            and inventory_usb_speed != negotiated_usb_speed
        ),
        "host_to_device_throughput": make_metric(
            None,
            "byte/s",
            "unsupported",
            ("*",),
            "DepthAI 2.32 exposes no generic host-to-device byte counter used by this harness",
            [],
        ),
        "device_to_host_application_payload_bytes": make_metric(
            payload_bytes,
            "bytes",
            "measured" if polling_started else "no-data",
            ("*",),
            "application payload only; excludes USB framing, protocol overhead, and unreceived data",
            [],
        ),
    }


def run_profile(
    device_info: object,
    profile_id: str,
    duration_s: float,
    temperature_stop_c: float,
    *,
    dai_module: Any = None,
    pipeline_builder: Callable[[str, Any], tuple[Any, dict[str, Any]]] = build_pipeline,
    device_factory: Callable[[Any, object], Any] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    utcnow: Callable[[], dt.datetime] | None = None,
    allow_long_test: bool = False,
    inventory_usb_speed: str | None = None,
) -> dict[str, object]:
    # Run one bounded profile and retain its samples, timings and any stop or failure.
    duration, temperature_stop = validate_run_limits(
        duration_s,
        allow_long_test=allow_long_test,
        temperature_stop_c=temperature_stop_c,
    )
    now = utcnow or (lambda: dt.datetime.now(dt.timezone.utc))
    started_utc = _utc_text(now())
    contract = copy.deepcopy(PROFILE_DEFINITIONS.get(profile_id, {"streams": {}}))

    # Create a sample list per frame stream so a build failure still records empty
    # streams.
    frame_samples: dict[str, list[dict[str, float | int]]] = {
        name: []
        for name, stream in contract.get("streams", {}).items()
        if stream.get("kind") != "system"
    }
    system_samples: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    status = "complete"
    stop_reason: str | None = None
    device_disconnected = False
    negotiated_usb_speed: str | None = None
    exception_phase: str | None = None
    current_phase = "pipeline-build"
    execution = {
        "pipeline_built": False,
        "device_open": False,
        "polling_started": False,
        "polling_finished": False,
    }
    lifecycle_start = monotonic()
    polling_start: float | None = None
    polling_duration: float | None = None

    try:
        if dai_module is None:
            import depthai as dai_module

        # Match the arrival clock to DepthAI message timestamps when available.
        depthai_clock = getattr(dai_module, "Clock", None)
        arrival_timestamp = (
            (lambda: depthai_clock.now().total_seconds())
            if depthai_clock is not None
            else monotonic
        )

        pipeline, contract = pipeline_builder(profile_id, dai_module)
        execution["pipeline_built"] = True
        frame_samples = {
            name: []
            for name, stream in contract["streams"].items()
            if stream["kind"] != "system"
        }
        factory = device_factory or dai_module.Device
        current_phase = "device-open"

        with factory(pipeline, device_info) as device:
            execution["device_open"] = True
            current_phase = "queue-setup"
            negotiated_usb_speed = enum_name(device.getUsbSpeed())

            # Open each host stream with a four-message queue and non-blocking reads.
            queues = {
                name: device.getOutputQueue(name=name, maxSize=4, blocking=False)
                for name in contract["streams"]
            }
            current_phase = "polling"
            polling_start = monotonic()
            execution["polling_started"] = True

            # End the measurement window after the requested seconds of polling.
            run_deadline = polling_start + duration

            # Allow at most three seconds for the first SystemLogger sample.
            telemetry_deadline = polling_start + SYSTEM_LOGGER_GRACE_S
            thermal_stop = False

            try:
                while True:
                    active_deadline = (
                        run_deadline
                        if system_samples
                        else min(run_deadline, telemetry_deadline)
                    )

                    if monotonic() >= active_deadline:
                        if (
                            not system_samples
                            and duration >= SYSTEM_LOGGER_GRACE_S
                        ):
                            stop_reason = "telemetry-missing"

                        break

                    received = False
                    deadline_reached = False

                    # Read at most one message per stream, checking the deadline before
                    # each.
                    for name, stream in contract["streams"].items():
                        active_deadline = (
                            run_deadline
                            if system_samples
                            else min(run_deadline, telemetry_deadline)
                        )

                        if monotonic() >= active_deadline:
                            deadline_reached = True
                            break

                        message = queues[name].tryGet()

                        if message is None:
                            continue

                        received = True
                        host_s = arrival_timestamp()

                        if stream["kind"] == "system":
                            sample = system_sample(message, host_s)
                            system_samples.append(sample)
                            temperatures = sample["temperature_c"]

                            # Record thermal-stop and leave polling if any component
                            # reaches the limit.
                            if any(
                                float(temperatures[f"{component}_c"])
                                >= temperature_stop
                                for component in (
                                    "css",
                                    "mss",
                                    "upa",
                                    "dss",
                                    "average",
                                )
                            ):
                                status = "thermal-stop"
                                stop_reason = "thermal-stop"
                                thermal_stop = True
                                break

                        else:
                            # Store this frame's timestamps, sequence number and payload
                            # size.
                            frame_samples[name].append(message_sample(message, host_s))

                    if thermal_stop:
                        break

                    if deadline_reached:
                        continue

                    # If every queue was empty, wait up to 1 ms before polling again.
                    if not received:
                        active_deadline = (
                            run_deadline
                            if system_samples
                            else min(run_deadline, telemetry_deadline)
                        )
                        remaining = active_deadline - monotonic()

                        if remaining > 0:
                            sleep(min(0.001, remaining))

            except Exception:
                # Record polling time before device cleanup, then pass the error
                # outward.
                polling_duration = max(0.0, monotonic() - polling_start)
                execution["polling_finished"] = True
                raise

            else:
                polling_duration = max(0.0, monotonic() - polling_start)
                execution["polling_finished"] = True
                current_phase = "device-close"

    except Exception as exc:
        # Preserve partial samples and the phase that failed for the saved evidence.
        exception_phase = current_phase
        device_disconnected = _disconnected_exception(exc)

        if _unsupported_exception(exc, current_phase):
            status = "unsupported"

        else:
            status = "disconnected" if device_disconnected else "error"

        errors.append({"type": type(exc).__name__, "message": str(exc)})

    lifecycle_duration = max(0.0, monotonic() - lifecycle_start)
    finished_utc = _utc_text(now())
    streams = {
        name: {
            "samples": samples,
            "metrics": summarize_frames(
                samples,
                float(contract["streams"][name]["requested_fps"]),
            ),
        }
        for name, samples in frame_samples.items()
    }
    payload_bytes = (
        sum(
            int(sample["payload_bytes"])
            for samples in frame_samples.values()
            for sample in samples
        )
        if execution["polling_started"]
        else None
    )

    # List each expected frame or system stream that produced no samples.
    missing_expected_streams = [
        name
        for name, stream in contract.get("streams", {}).items()
        if (
            not system_samples
            if stream.get("kind") == "system"
            else not frame_samples.get(name)
        )
    ]
    inconclusive_evidence = [
        (
            "no valid SystemLogger sample"
            if contract["streams"][name].get("kind") == "system"
            else f"expected stream {name} produced no messages"
        )
        for name in missing_expected_streams
    ]

    if (
        execution["polling_started"]
        and missing_expected_streams
        and status == "complete"
    ):
        status = "inconclusive"

    # Retain the inventory USB mode separately from the mode observed during this run.
    recorded_inventory_usb_speed = (
        enum_name(inventory_usb_speed) if inventory_usb_speed is not None else None
    )
    return {
        "profile_id": profile_id,
        "status": status,
        "stop_reason": stop_reason,
        "exception_phase": exception_phase,
        "execution": execution,
        "device_disconnected": device_disconnected,
        "errors": errors,
        "started_utc": started_utc,
        "finished_utc": finished_utc,
        "requested_duration_s": duration,
        "polling_duration_s": polling_duration,
        "lifecycle_duration_s": lifecycle_duration,
        "missing_expected_streams": missing_expected_streams,
        "inconclusive_evidence": inconclusive_evidence,
        "streams": streams,
        "system": {
            "samples": system_samples,
            "metrics": _aggregate_system_samples(system_samples),
        },
        "link_capabilities": _link_capabilities(
            negotiated_usb_speed,
            recorded_inventory_usb_speed,
            payload_bytes,
            execution["polling_started"],
        ),
    }


def collect_host_power(
    command_exists: Callable[[str], Any] = shutil.which,
    run_command: Callable[..., Any] = subprocess.run,
) -> dict[str, dict[str, Any]]:
    # Read the Pi throttling bitmask when available and label it as host-only evidence.
    method = "host vcgencmd; not an OAK brownout measurement"

    # Report unsupported when this host has no Pi telemetry command.
    if not command_exists("vcgencmd"):
        return {
            "raspberry_pi_throttled": make_metric(
                None,
                "hex bitmask",
                "unsupported",
                (),
                method,
                ["vcgencmd is not available on this host"],
            )
        }

    try:
        result = run_command(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )

    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "raspberry_pi_throttled": make_metric(
                None,
                "hex bitmask",
                "unsupported",
                (),
                method,
                [str(exc)],
            )
        }

    match = re.search(r"throttled=(0x[0-9a-fA-F]+)", result.stdout)

    # Preserve command output as evidence if execution failed or no bitmask was found.
    if result.returncode != 0 or match is None:
        evidence = [
            value for value in (result.stdout.strip(), result.stderr.strip()) if value
        ]
        return {
            "raspberry_pi_throttled": make_metric(
                None,
                "hex bitmask",
                "unsupported",
                (),
                method,
                evidence,
            )
        }

    mask = int(match.group(1), 16)
    return {
        "raspberry_pi_throttled": make_metric(
            f"0x{mask:x}",
            "hex bitmask",
            "observed",
            (),
            method,
            [
                "Raspberry Pi current/history throttling state only; not proof of OAK power quality",
                result.stdout.strip(),
            ],
        )
    }


def collect_host(
    power_collector: Callable[[], dict[str, dict[str, Any]]] = collect_host_power,
) -> dict[str, Any]:
    # Record host identity, UTC/local timestamps and available Pi throttling evidence.
    now_utc = dt.datetime.now(dt.timezone.utc)
    now_local = now_utc.astimezone()
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "hostname": socket.gethostname(),
        "timestamps": {
            "utc": now_utc.isoformat().replace("+00:00", "Z"),
            "local": now_local.isoformat(),
        },
        "power": power_collector(),
    }


def capture_read(name: str, reader: Callable[[], Any]) -> Any:
    # Return the optional reading, or an unsupported record containing its read error.
    try:
        return reader()

    except Exception as exc:
        return {"status": "unsupported", "error": str(exc)}


def _calibration_metric(name: str, reader: Callable[[], Any]) -> dict[str, Any]:
    # Wrap a successful calibration read as a metric and preserve unsupported errors.
    value = capture_read(name, reader)

    if (
        isinstance(value, dict)
        and value.get("status") == "unsupported"
    ):
        return value

    return make_metric(
        value,
        "",
        "calibrated",
        (),
        name,
        [],
    )


def collect_device(
    device: Any, dai_module: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    # Read device identity, camera features, resource usage and available calibration.
    usb_speed = enum_name(device.getUsbSpeed())
    camera_features = device.getConnectedCameraFeatures()
    connected_cameras = list(device.getConnectedCameras())
    device_data = {
        "mxid": make_metric(
            device.getMxId(),
            "",
            "observed",
            (),
            "DepthAI Device.getMxId",
            [],
        ),
        "product_name": make_metric(
            device.getProductName(),
            "",
            "observed",
            (),
            "DepthAI Device.getProductName",
            [],
        ),
        "device_name": make_metric(
            device.getDeviceName(),
            "",
            "observed",
            (),
            "DepthAI Device.getDeviceName",
            [],
        ),
        "usb_speed": make_metric(
            usb_speed,
            "DepthAI enum",
            "observed",
            ("*",),
            "DepthAI Device.getUsbSpeed",
            [usb_speed],
        ),
        "camera_features": [
            camera_feature_to_dict(feature) for feature in camera_features
        ],
        "connected_cameras": [enum_name(value) for value in connected_cameras],
        "connected_imu": enum_name(device.getConnectedIMU()),
        "memory": {
            "ddr": memory_to_dict(device.getDdrMemoryUsage()),
            "cmx": memory_to_dict(device.getCmxMemoryUsage()),
            "leon_css_heap": memory_to_dict(device.getLeonCssHeapUsage()),
            "leon_mss_heap": memory_to_dict(device.getLeonMssHeapUsage()),
        },
        "chip_temperature": temperature_to_dict(device.getChipTemperature()),
    }

    # Try to read calibration; return the device inventory with its error if
    # unavailable.
    calibration_handler = capture_read(
        "DepthAI Device.readCalibration2",
        device.readCalibration2,
    )

    if (
        isinstance(calibration_handler, dict)
        and calibration_handler.get("status") == "unsupported"
    ):
        return device_data, calibration_handler

    calibration: dict[str, Any] = {
        "eeprom_json": _calibration_metric(
            "DepthAI CalibrationHandler.eepromToJson",
            calibration_handler.eepromToJson,
        ),
        "cameras": {},
    }

    # Read each connected camera's intrinsics, distortion and field-of-view values.
    for camera_socket in connected_cameras:
        socket_name = enum_name(camera_socket)
        calibration["cameras"][socket_name] = {
            "intrinsics": _calibration_metric(
                f"DepthAI CalibrationHandler.getCameraIntrinsics({socket_name})",
                lambda value=camera_socket: calibration_handler.getCameraIntrinsics(
                    value
                ),
            ),
            "distortion_coefficients": _calibration_metric(
                f"DepthAI CalibrationHandler.getDistortionCoefficients({socket_name})",
                lambda value=camera_socket: calibration_handler.getDistortionCoefficients(
                    value
                ),
            ),
            "horizontal_fov_specification": _calibration_metric(
                f"DepthAI CalibrationHandler.getFov({socket_name}, True)",
                lambda value=camera_socket: calibration_handler.getFov(value, True),
            ),
            "horizontal_fov_calculated": _calibration_metric(
                f"DepthAI CalibrationHandler.getFov({socket_name}, False)",
                lambda value=camera_socket: calibration_handler.getFov(value, False),
            ),
        }

    # Record the stereo transform and both rectification rotations for CAM_B and CAM_C.
    cam_b = dai_module.CameraBoardSocket.CAM_B
    cam_c = dai_module.CameraBoardSocket.CAM_C
    calibration["stereo_cam_b_to_cam_c"] = {
        "extrinsics": _calibration_metric(
            "DepthAI CalibrationHandler.getCameraExtrinsics(CAM_B, CAM_C, False)",
            lambda: calibration_handler.getCameraExtrinsics(cam_b, cam_c, False),
        ),
        "left_rectification_rotation": _calibration_metric(
            "DepthAI CalibrationHandler.getStereoLeftRectificationRotation",
            calibration_handler.getStereoLeftRectificationRotation,
        ),
        "right_rectification_rotation": _calibration_metric(
            "DepthAI CalibrationHandler.getStereoRightRectificationRotation",
            calibration_handler.getStereoRightRectificationRotation,
        ),
    }
    return device_data, calibration


def build_parser() -> argparse.ArgumentParser:
    # Define the profile listing, inventory, timed run and saved-run comparison
    # commands.
    parser = argparse.ArgumentParser(
        description="Collect immutable OAK-D Lite benchmark evidence."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("profiles", help="list the seven stable benchmark profiles")

    # Record the physical test setup alongside both inventory and timed runs.
    def add_conditions(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--mxid",
            default=None,
            help="select exactly one discovered device by DeviceInfo.getMxId()",
        )
        command_parser.add_argument("--host-label", default="not recorded")
        command_parser.add_argument("--hub", default="not recorded")
        command_parser.add_argument("--port", default="not recorded")
        command_parser.add_argument("--cable", default="not recorded")
        command_parser.add_argument("--power", default="not recorded")
        command_parser.add_argument("--ambient-c", type=float, default=None)
        command_parser.add_argument("--notes", default="not recorded")
        command_parser.add_argument(
            "--results-root",
            type=Path,
            default=Path(__file__).resolve().parent / "results",
        )

    inventory = subparsers.add_parser(
        "inventory",
        help="record host, software, device, and calibration evidence",
    )
    add_conditions(inventory)

    run = subparsers.add_parser("run", help="run bounded benchmark profiles")
    add_conditions(run)
    run.add_argument("--duration-s", type=float, default=10.0)
    run.add_argument("--temperature-stop-c", type=float, default=100.0)
    run.add_argument("--profiles", default="all")
    run.add_argument("--stability-duration-s", type=float, default=60.0)
    run.add_argument("--allow-long-test", action="store_true")

    compare = subparsers.add_parser(
        "compare",
        help="compare two schema 1.0 raw JSON evidence files",
    )
    compare.add_argument("baseline_json", type=Path)
    compare.add_argument("candidate_json", type=Path)
    compare.add_argument(
        "--results-root",
        type=Path,
        default=Path(__file__).resolve().parent / "results",
    )
    return parser


def _write_fsynced(path: Path, content: str) -> None:
    # Create a new evidence file, then flush its contents to disk before publication.
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    # Flush directory changes and close the descriptor even if syncing fails.
    descriptor = os.open(path, os.O_RDONLY)

    try:
        os.fsync(descriptor)

    finally:
        os.close(descriptor)


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    # Publish the staging directory using the platform's no-replacement rename.
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    system = platform.system()

    if system == "Darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int

        # Darwin RENAME_EXCL refuses to replace even an empty destination.
        result = rename(source_bytes, destination_bytes, 0x00000004)

    elif system == "Linux":
        libc = ctypes.CDLL(None, use_errno=True)

        try:
            rename = libc.renameat2

        except AttributeError as exc:
            raise NotImplementedError(
                "atomic no-replace directory rename requires renameat2"
            ) from exc

        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int

        # Linux uses AT_FDCWD (-100) and RENAME_NOREPLACE (1) for the same guarantee.
        result = rename(
            -100,
            source_bytes,
            -100,
            destination_bytes,
            0x00000001,
        )

    elif os.name == "nt":
        os.rename(source, destination)
        return

    else:
        raise NotImplementedError(
            f"atomic no-replace directory rename is unsupported on {system}"
        )

    if result == 0:
        return

    error_number = ctypes.get_errno()

    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error_number, os.strerror(error_number), destination)

    raise OSError(error_number, os.strerror(error_number), destination)


def _publish_evidence_pair(
    final_directory: Path,
    files: dict[str, str],
    *,
    publication_fault: Callable[[str, Path], None] | None = None,
) -> Path:
    # Save the supplied evidence files together without replacing an existing result.
    final_directory = Path(final_directory)
    parent = final_directory.parent
    parent.mkdir(parents=True, exist_ok=True)

    if final_directory.exists():
        raise FileExistsError(f"Evidence directory already exists: {final_directory}")

    # Create a unique staging folder beside the intended result folder.
    staging = parent / f".{final_directory.name}.staging-{uuid.uuid4().hex}"
    staging.mkdir(exist_ok=False)

    # Run the optional test callback at each publication step so failures can be
    # simulated.
    def checkpoint(step: str) -> None:
        if publication_fault is not None:
            publication_fault(step, staging)

    # Write and sync every evidence file before publishing the result folder.
    for filename, content in files.items():
        _write_fsynced(staging / filename, content)
        checkpoint(f"after-write:{filename}")

    _fsync_directory(staging)
    checkpoint("after-staging-fsync")
    checkpoint("before-rename")

    if final_directory.exists():
        raise FileExistsError(f"Evidence directory already exists: {final_directory}")

    # Publish both files together without overwriting an earlier run.
    _rename_directory_no_replace(staging, final_directory)
    _fsync_directory(parent)
    return final_directory


def _strict_json_text(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(
        value,
        indent=indent,
        sort_keys=True,
        allow_nan=False,
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON numeric constant: {value}")


def _require_finite_json_numbers(value: Any) -> None:
    # Visit nested JSON values and raise if any floating-point value is not finite.
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON numeric values must be finite")

    if isinstance(value, dict):
        for child in value.values():
            _require_finite_json_numbers(child)

    elif isinstance(value, list):
        for child in value:
            _require_finite_json_numbers(child)


def _strict_json_loads(text: str) -> Any:
    # Parse strict JSON, rejecting NaN and Infinity before checking nested numbers.
    value = json.loads(text, parse_constant=_reject_json_constant)

    # Reject parsed infinities, including values produced by overflowing JSON exponents.
    _require_finite_json_numbers(value)
    return value


def write_run_atomically(
    run: dict[str, Any],
    results_root: Path,
    *,
    renderer: Callable[[dict[str, Any]], str] = render_markdown,
    publication_fault: Callable[[str, Path], None] | None = None,
) -> Path:
    # Copy the run, render its summary and publish both files under its run ID.
    run_directory = Path(results_root) / str(run["run_id"])
    evidence = copy.deepcopy(run)

    try:
        summary = renderer(evidence)

    except Exception as exc:
        # Mark the run failed, retain the rendering error and create a fallback summary.
        evidence["status"] = "failed"
        evidence.setdefault("errors", []).append(_error_record(exc))
        evidence.setdefault("warnings", []).append(
            "Markdown rendering failed; raw JSON preserves the exact rendering error."
        )
        summary = (
            "# OAK-D Lite Benchmark Result\n\n"
            f"Run: `{evidence['run_id']}`\n\n"
            f"Markdown rendering failed: {type(exc).__name__}: {exc}\n"
        )

    files = {
        "raw.json": _strict_json_text(evidence, indent=2) + "\n",
        "summary.md": summary,
    }
    return _publish_evidence_pair(
        run_directory,
        files,
        publication_fault=publication_fault,
    )


def _manufacturer_claims() -> dict[str, Any]:
    # Return the fixed datasheet values with claimed status and their source wording.
    method = "December 2021 manufacturer datasheet claim; not a measurement"

    def claim(
        value: Any, unit: str, evidence: list[str], markers: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        return make_metric(
            value,
            unit,
            "claimed",
            markers,
            method,
            evidence,
        )

    return {
        "dimensions": claim([91, 28, 17.5], "mm (L×W×H)", ["91×28×17.5 mm"]),
        "stereo_baseline": claim(75, "mm", ["stereo baseline 75 mm"]),
        "recommended_input_voltage": claim(
            [5, 5.25],
            "V",
            ["recommended input 5–5.25 V"],
        ),
        "maximum_input_current": claim(1.5, "A", ["maximum input current 1.5 A"]),
        "power": {
            "operating": claim([4, 6], "W", ["power 4–6 W"]),
            "idle": claim(2.5, "W", ["idle power 2.5 W"]),
        },
        "ambient_max": claim(50, "C ambient", ["ambient maximum 50 C"]),
        "rgb_camera": {
            "sensor": claim("IMX214", "", ["IMX214"]),
            "resolution": claim([4208, 3120], "pixels", ["4208×3120"]),
            "max_fps": claim(60, "frames/s", ["4208×3120@60"]),
            "focus": claim("autofocus", "", ["autofocus"]),
        },
        "mono_camera": {
            "sensor": claim("OV7251", "", ["OV7251"]),
            "resolution": claim([640, 480], "pixels", ["640×480"]),
            "max_fps": claim(120, "frames/s", ["640×480@120"]),
            "shutter": claim("global shutter", "", ["global shutter"]),
            "focus": claim("fixed focus", "", ["fixed focus"]),
        },
        "usb_interface": claim(
            ["USB 3.1 Gen1", "USB 3.1 Gen2"],
            "datasheet wording",
            ["conflicting interface descriptions"],
            ("‡",),
        ),
        "usb2_max_wording": claim(
            "900 mA USB2 max",
            "verbatim datasheet wording",
            ["ambiguous USB power terminology"],
            ("‡",),
        ),
    }


def _operator_conditions(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "host_label": args.host_label,
        "hub": args.hub,
        "port": args.port,
        "cable": args.cable,
        "power": args.power,
        "ambient_c": args.ambient_c,
        "notes": args.notes,
    }


def _error_record(exc: Exception) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)}


def _validate_cli_numbers(args: argparse.Namespace) -> None:
    # Check supplied temperatures and run durations before device discovery begins.
    if (
        args.ambient_c is not None
        and not math.isfinite(args.ambient_c)
    ):
        raise ValueError("ambient temperature must be finite when supplied")

    if args.command != "run":
        return

    validate_run_limits(
        args.duration_s,
        allow_long_test=args.allow_long_test,
        temperature_stop_c=args.temperature_stop_c,
    )
    stability_duration = float(args.stability_duration_s)

    if (
        not math.isfinite(stability_duration)
        or stability_duration < 0
    ):
        raise ValueError("stability duration must be finite and at least 0")

    if stability_duration > 0:
        validate_run_limits(
            stability_duration,
            allow_long_test=args.allow_long_test,
            temperature_stop_c=args.temperature_stop_c,
        )


def _require_run_dependencies() -> None:
    # Reject timed runs when NumPy is unavailable for stream payload measurement.
    try:
        __import__("numpy")

    except ImportError as exc:
        raise RuntimeError(
            "NumPy is required for stream payload measurement; "
            "install the pinned requirements before running profiles"
        ) from exc


def _selected_profile_ids(selection: str) -> list[str]:
    # Expand all or split the requested IDs, rejecting duplicates and unknown profiles.
    selected = list(PROFILE_DEFINITIONS) if selection == "all" else selection.split(",")
    duplicates = sorted(
        profile_id for profile_id in set(selected) if selected.count(profile_id) > 1
    )

    if duplicates:
        raise ValueError(f"Duplicate profile ID(s): {', '.join(duplicates)}")

    invalid = [
        profile_id for profile_id in selected if profile_id not in PROFILE_DEFINITIONS
    ]

    if invalid:
        raise ValueError(f"Unknown profile ID(s): {', '.join(invalid)}")

    if not selected:
        raise ValueError("At least one profile must be selected")

    return selected


def _select_device_info(
    available: list[Any], requested_mxid: str | None
) -> tuple[Any, str]:
    # Select the only discovered device or the single device matching the requested
    # MXID.
    if requested_mxid is None:
        if len(available) != 1:
            raise RuntimeError(
                f"Expected exactly one discovered device without --mxid; found {len(available)}"
            )

        selected = available[0]
        return selected, str(selected.getMxId())

    matches = [
        device_info
        for device_info in available
        if str(device_info.getMxId()) == requested_mxid
    ]

    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one discovered device matching --mxid {requested_mxid!r}; found {len(matches)}"
        )

    return matches[0], requested_mxid


def _inventory_identity_verification(
    device: dict[str, Any], selected_mxid: str
) -> dict[str, Any]:
    # Check that the inventoried unit and camera sensors match the intended OAK-D Lite.
    observed_mxid = device.get("mxid", {}).get("value")
    observed_product = device.get("product_name", {}).get("value")
    observed_sensors = {
        str(feature.get("socket")): feature.get("sensor_name")
        for feature in device.get("camera_features", [])
        if isinstance(feature, dict)
    }

    # Record mismatches against the selected MXID, product name and expected sensors.
    expected_sensors = {
        "CAM_A": "IMX214",
        "CAM_B": "OV7251",
        "CAM_C": "OV7251",
    }
    errors = []

    if observed_mxid != selected_mxid:
        errors.append(f"selected MXID {selected_mxid}")

    if observed_product != "OAK-D-LITE":
        errors.append("product OAK-D-LITE")

    for socket_name, expected_sensor in expected_sensors.items():
        if observed_sensors.get(socket_name) != expected_sensor:
            errors.append(f"{socket_name} sensor {expected_sensor}")

    return {
        "status": "failed" if errors else "verified",
        "selected_mxid": selected_mxid,
        "observed_mxid": observed_mxid,
        "expected_product": "OAK-D-LITE",
        "observed_product": observed_product,
        "expected_sensors": expected_sensors,
        "observed_sensors": observed_sensors,
        "errors": errors,
    }


def _print_profile_lesson(profile_id: str, printer: Callable[[str], None]) -> None:
    measurement, relevance, expected, limit = PROFILE_TEACHING[profile_id]
    printer(f"Measurement: {measurement}")
    printer(f"Project relevance: {relevance}")
    printer(f"Expected observation: {expected}")
    printer(f"Conclusion limit: {limit}")


def _failed_profile(
    profile_id: str, duration_s: float, exc: Exception
) -> dict[str, Any]:
    # Convert an unexpected profile exception into a saved result with no stream
    # samples.
    return {
        "profile_id": profile_id,
        "status": "error",
        "requested_duration_s": float(duration_s),
        "errors": [_error_record(exc)],
        "streams": {},
        "system": {},
    }


def create_run_document(
    args: argparse.Namespace,
    *,
    dai_module: Any = None,
    host_collector: Callable[[], dict[str, Any]] = collect_host,
    device_collector: Callable[
        [Any, Any], tuple[dict[str, Any], dict[str, Any]]
    ] = collect_device,
    profile_runner: Callable[..., dict[str, Any]] = run_profile,
    device_discovery: Callable[[], list[Any]] | None = None,
    inventory_device_factory: Callable[[Any], Any] | None = None,
    now: Callable[[], dt.datetime] | None = None,
    printer: Callable[[str], None] = print,
) -> dict[str, Any]:
    # Collect inventory and selected profiles into one record, preserving failures as
    # evidence.
    if args.command not in {"inventory", "run"}:
        raise ValueError("create_run_document accepts only inventory and run commands")

    _validate_cli_numbers(args)
    selected_profile_ids = (
        _selected_profile_ids(args.profiles) if args.command == "run" else []
    )

    if args.command == "run":
        _require_run_dependencies()

    if dai_module is None:
        import depthai as dai_module

    clock = now or (lambda: dt.datetime.now(dt.timezone.utc))
    started = clock()
    run_id = (
        f"{started.strftime('%Y%m%dT%H%M%S%fZ')}_{args.command}_{uuid.uuid4().hex[:8]}"
    )
    run: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": "complete",
        "timestamps": {
            "started_utc": _utc_text(started),
            "finished_utc": None,
            "local": started.astimezone().isoformat(),
        },
        "operator_conditions": _operator_conditions(args),
        "host": host_collector(),
        "software": {
            "depthai_version": getattr(dai_module, "__version__", "not recorded"),
            "schema_version": "1.0",
        },
        "benchmark_provenance": benchmark_provenance(),
        "device": {},
        "calibration": {},
        "tests": {},
        "manufacturer_claims": _manufacturer_claims(),
        "warnings": [
            "DepthAI 2.32 may emit a HIGH_DENSITY stereo preset deprecation warning; the pinned API remains intentional for this harness."
        ],
        "source_ambiguities": list(SOURCE_AMBIGUITIES),
        "pending_application_tests": [
            "200 mm ArUco identification and pose estimation†",
            "pressure-gauge detection and reading†",
            "valve detection and open/closed classification†",
            "encoded video, overlay, and web-delivery latency†",
        ],
    }

    discover = device_discovery or dai_module.Device.getAllAvailableDevices

    try:
        available = list(discover())

        if not available:
            raise RuntimeError("No OAK device discovered")

        device_info, selected_mxid = _select_device_info(available, args.mxid)
        make_inventory_device = inventory_device_factory or dai_module.Device

        with make_inventory_device(device_info) as inventory_device:
            run["device"], run["calibration"] = device_collector(
                inventory_device,
                dai_module,
            )

    except Exception as exc:
        run["status"] = "failed"
        run["device"] = {"status": "unavailable", "error": _error_record(exc)}
        run["calibration"] = {"status": "unavailable"}
        run["warnings"].append(
            "Inventory attempt failed; this records access evidence, not a hardware constraint."
        )
        run["timestamps"]["finished_utc"] = _utc_text(clock())
        return run

    # Verify the collected device identity after closing the inventory connection.
    identity_verification = _inventory_identity_verification(
        run["device"],
        selected_mxid,
    )
    run["device"]["identity_verification"] = identity_verification

    # Return a failed run without opening profiles if the unit or camera layout differs.
    if identity_verification["status"] != "verified":
        run["status"] = "failed"
        run["warnings"].append(
            "Inventory identity verification failed; no benchmark profiles were opened."
        )
        run["timestamps"]["finished_utc"] = _utc_text(clock())
        return run

    if args.command == "run":
        inventory_usb_speed = run["device"].get("usb_speed", {}).get("value")
        work = [
            (profile_id, args.duration_s, profile_id)
            for profile_id in selected_profile_ids
        ]

        # Append a separate combined-stream stability run unless its duration is zero.
        if args.stability_duration_s != 0:
            work.append(
                (
                    "combined_h264_depth_system_30",
                    args.stability_duration_s,
                    "combined_h264_depth_system_30_stability",
                )
            )

        # Run each selected profile and save its result, continuing after profile
        # errors.
        for profile_id, duration_s, result_id in work:
            _print_profile_lesson(profile_id, printer)

            try:
                result = profile_runner(
                    device_info,
                    profile_id,
                    duration_s,
                    args.temperature_stop_c,
                    dai_module=dai_module,
                    allow_long_test=args.allow_long_test,
                    inventory_usb_speed=inventory_usb_speed,
                )

            except Exception as exc:
                result = _failed_profile(profile_id, duration_s, exc)

            run["tests"][result_id] = result
            printer(f"Measured status: {result.get('status', 'unknown')}")
            printer(
                "Evidence records what this run observed; interpretation and causal inference remain separate."
            )

            # Mark unsupported profiles without clearing a failure from an earlier
            # profile.
            if result.get("status") == "unsupported":
                if run["status"] == "complete":
                    run["status"] = "complete-with-unsupported"

            elif result.get("status") != "complete":
                run["status"] = "failed"

    run["timestamps"]["finished_utc"] = _utc_text(clock())
    return run


def _comparison_markdown(document: dict[str, Any]) -> str:
    # List both source files and every metric comparison in a Markdown summary.
    lines = [
        "# OAK-D Lite Benchmark Comparison",
        "",
        f"Comparison: `{document['comparison_id']}`",
        f"Baseline source: `{document['baseline_source']}`",
        f"Candidate source: `{document['candidate_source']}`",
        "",
        "This is comparative evidence for student interpretation, not an application acceptance conclusion.",
        "",
        "## Results",
    ]

    for metric_id, value in document["comparison"].items():
        lines.append(f"- `{metric_id}`: `{_strict_json_text(value)}`")

    return "\n".join(lines) + "\n"


def _write_comparison_atomically(
    document: dict[str, Any],
    results_root: Path,
    *,
    publication_fault: Callable[[str, Path], None] | None = None,
) -> Path:
    # Publish the comparison JSON and Markdown together in a new result directory.
    directory = Path(results_root) / document["comparison_id"]
    files = {
        "comparison.json": _strict_json_text(document, indent=2) + "\n",
        "comparison.md": _comparison_markdown(document),
    }
    return _publish_evidence_pair(directory, files, publication_fault=publication_fault)


def _run_comparison(args: argparse.Namespace) -> Path:
    # Read both saved runs and reject unsupported schemas before comparing their
    # metrics.
    baseline = _strict_json_loads(args.baseline_json.read_text(encoding="utf-8"))
    candidate = _strict_json_loads(args.candidate_json.read_text(encoding="utf-8"))

    for label, document in (("baseline", baseline), ("candidate", candidate)):
        if document.get("schema_version") != "1.0":
            raise ValueError(f"{label} schema_version must be 1.0")

    # Give this comparison its own ID and save the changes with both source paths.
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    document = {
        "schema_version": "1.0",
        "comparison_id": f"{timestamp}_comparison_{uuid.uuid4().hex[:8]}",
        "baseline_source": str(args.baseline_json.resolve()),
        "candidate_source": str(args.candidate_json.resolve()),
        "comparison": compare_runs(baseline, candidate),
    }
    return _write_comparison_atomically(document, args.results_root)


def _print_profiles(printer: Callable[[str], None]) -> None:
    # Print each stable profile ID, its stream names and its project purpose.
    for profile_id, definition in PROFILE_DEFINITIONS.items():
        streams = ", ".join(definition["streams"])
        printer(
            f"{profile_id}\n  streams: {streams}\n  why: {PROFILE_TEACHING[profile_id][1]}"
        )


def main(
    argv: list[str] | None = None,
    *,
    document_creator: Callable[
        [argparse.Namespace], dict[str, Any]
    ] = create_run_document,
    run_writer: Callable[[dict[str, Any], Path], Path] = write_run_atomically,
    run_renderer: Callable[[dict[str, Any]], str] | None = None,
    printer: Callable[[str], None] = print,
) -> int:
    # Dispatch the requested command and return a failure code if its evidence cannot be
    # saved.
    args = build_parser().parse_args(argv)

    if args.command == "profiles":
        _print_profiles(printer)
        return 0

    if args.command == "compare":
        try:
            directory = _run_comparison(args)

        except Exception as exc:
            printer(f"Comparison failed: {type(exc).__name__}: {exc}")
            return 1

        printer(str(directory))
        return 0

    try:
        run = document_creator(args)

        if run_renderer is None:
            directory = run_writer(run, args.results_root)

        else:
            directory = write_run_atomically(
                run,
                args.results_root,
                renderer=run_renderer,
            )

        # Read the saved status so a summary-rendering failure also produces exit code
        # 1.
        stored_run = _strict_json_loads(
            (directory / "raw.json").read_text(encoding="utf-8")
        )

    except Exception as exc:
        printer(f"Evidence write failed: {type(exc).__name__}: {exc}")
        return 1

    printer(str(directory))
    return (
        0
        if stored_run.get("status") in {"complete", "complete-with-unsupported"}
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import math
import statistics
from typing import Any, Iterable

VALID_MARKERS = frozenset({"*", "†", "‡"})


def make_metric(
    value: Any,
    unit: str,
    status: str,
    markers: Iterable[str],
    method: str,
    evidence: Iterable[str],
) -> dict[str, Any]:
    # Reject unknown result markers, then attach units and evidence to the value.
    marker_list = list(markers)
    unknown = set(marker_list) - VALID_MARKERS

    if unknown:
        raise ValueError(f"Unknown result marker: {sorted(unknown)}")

    return {
        "value": value,
        "unit": unit,
        "status": status,
        "markers": marker_list,
        "method": method,
        "evidence": list(evidence),
    }


def nearest_rank(values: Iterable[float], percentile: float) -> float | None:
    # Validate the percentile and return None when there are no samples.
    if (
        not math.isfinite(percentile)
        or not 0 < percentile <= 100
    ):
        raise ValueError("percentile must be greater than 0 and at most 100")

    # Sort the samples so the requested percentile can select an observed value.
    ordered = sorted(float(value) for value in values)

    if not ordered:
        return None

    # Round the percentile rank upward and convert it to a zero-based sample index.
    index = max(0, math.ceil(percentile / 100 * len(ordered)) - 1)
    return ordered[index]


def summarize_frames(
    samples: list[dict[str, float | int]], requested_fps: float
) -> dict[str, dict[str, Any]]:
    # Convert recorded arrivals, sequence numbers and payload sizes into stream metrics.
    markers = ("*",)
    method = "host arrival times and DepthAI message metadata"
    count = len(samples)

    # Return no frame rate or bitrate when fewer than two arrivals were recorded.
    if count < 2:
        duration = 0.0
        fps = None
        bitrate = None
        inter_arrivals: list[float] = []

    else:
        # Divide the number of arrival intervals by the first-to-last elapsed time.
        duration = float(samples[-1]["host_s"]) - float(samples[0]["host_s"])
        fps = (count - 1) / duration if duration > 0 else None

        # Convert received payload bytes to Mbit/s; USB overhead is not included.
        bitrate = (
            sum(int(sample["payload_bytes"]) for sample in samples)
            * 8
            / duration
            / 1_000_000
            if duration > 0
            else None
        )
        inter_arrivals = [
            float(current["host_s"]) - float(previous["host_s"])
            for previous, current in zip(samples, samples[1:])
        ]

    # Count missing sequence numbers without guessing where those frames were lost.
    gaps = sum(
        max(0, int(current["sequence"]) - int(previous["sequence"]) - 1)
        for previous, current in zip(samples, samples[1:])
    )

    # Subtract each frame timestamp from its arrival time and clamp latency at zero.
    latencies_ms = [
        max(0.0, (float(sample["host_s"]) - float(sample["device_s"])) * 1000)
        for sample in samples
    ]

    # Mark an empty stream as no-data and measure variation between arrival intervals.
    measured = "measured" if samples else "no-data"
    jitter_ms = (
        statistics.pstdev(inter_arrivals) * 1000 if len(inter_arrivals) > 1 else 0.0
    )
    return {
        "requested_fps": make_metric(
            requested_fps,
            "frames/s",
            "configured",
            markers,
            "pipeline configuration",
            [],
        ),
        "frames_received": make_metric(
            count,
            "frames",
            measured,
            markers,
            method,
            [],
        ),
        "sequence_gaps": make_metric(
            gaps,
            "frames",
            measured,
            markers,
            method,
            [],
        ),
        "received_fps": make_metric(
            fps,
            "frames/s",
            measured,
            markers,
            method,
            [],
        ),
        "payload_bitrate_mbps": make_metric(
            bitrate,
            "Mbit/s",
            measured,
            markers,
            method,
            [],
        ),
        "latency_mean_ms": make_metric(
            statistics.fmean(latencies_ms) if latencies_ms else None,
            "ms",
            measured,
            markers,
            method,
            [],
        ),
        "latency_p95_ms": make_metric(
            nearest_rank(latencies_ms, 95) if latencies_ms else None,
            "ms",
            measured,
            markers,
            method,
            [],
        ),
        "interarrival_jitter_ms": make_metric(
            jitter_ms if samples else None,
            "ms",
            measured,
            markers,
            method,
            [],
        ),
    }


def _is_metric(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and {"value", "unit", "status", "markers", "method", "evidence"} <= value.keys()
    )


def flatten_metrics(value: Any, prefix: str = "") -> dict[str, dict[str, Any]]:
    # Return each complete metric under its dotted path; recurse through other
    # dictionaries.
    if _is_metric(value):
        return {prefix: value}

    flattened: dict[str, dict[str, Any]] = {}

    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(flatten_metrics(child, child_prefix))

    return flattened


def compare_runs(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    # Compare matching measurements and record whether the USB retest and provenance
    # agree.
    old = flatten_metrics(baseline)
    new = flatten_metrics(candidate)
    result: dict[str, dict[str, Any]] = {}

    # Collect value, status and marker changes for metrics present in both runs.
    for metric_id in sorted(old.keys() & new.keys()):
        old_metric = old[metric_id]
        new_metric = new[metric_id]
        old_value = old_metric["value"]
        new_value = new_metric["value"]
        numeric = isinstance(old_value, (int, float)) and isinstance(
            new_value,
            (int, float),
        )
        # Compute numeric changes; leave percentages absent when the baseline is zero.
        absolute = new_value - old_value if numeric else None
        percentage = absolute / old_value * 100 if numeric and old_value != 0 else None
        result[metric_id] = {
            "baseline": old_value,
            "candidate": new_value,
            "unit": new_metric["unit"],
            "absolute_change": absolute,
            "percentage_change": percentage,
            "status_change": [old_metric["status"], new_metric["status"]],
            "markers": sorted(set(old_metric["markers"]) | set(new_metric["markers"])),
        }

    baseline_usb_speed = baseline.get("device", {}).get("usb_speed", {}).get("value")
    candidate_usb_speed = candidate.get("device", {}).get("usb_speed", {}).get("value")
    expected_usb_retest = (
        baseline_usb_speed == "HIGH" and candidate_usb_speed == "SUPER"
    )

    # Compare the recorded source/profile hashes; comment edits also change source
    # hashes.
    provenance_match = baseline.get("benchmark_provenance") == candidate.get(
        "benchmark_provenance"
    )
    warnings = []

    # Record a warning when this pair is not a HIGH-to-SUPER USB retest.
    if not expected_usb_retest:
        warnings.append(
            "Comparison is not the expected USB 2 (HIGH) to USB 3 (SUPER) retest."
        )

    # Keep the comparison but warn when the recorded benchmark versions or hashes
    # differ.
    if not provenance_match:
        warnings.append(
            "Benchmark provenance differs; interpret changes with the recorded revisions."
        )

    result["_comparison"] = {
        "baseline_usb_speed": baseline_usb_speed,
        "candidate_usb_speed": candidate_usb_speed,
        "provenance_match": provenance_match,
        "status": (
            "expected_usb2_to_usb3_retest"
            if expected_usb_retest
            else "not_expected_usb2_to_usb3_retest"
        ),
        "warnings": warnings,
    }
    return result


def _metric_text(metric: dict[str, Any]) -> str:
    # Format a measurement with its unit, markers and status for the Markdown summary.
    marker_text = "".join(metric["markers"])
    value = "not measured" if metric["value"] is None else str(metric["value"])
    unit = f" {metric['unit']}" if metric["unit"] else ""
    marker_suffix = f" {marker_text}" if marker_text else ""
    return f"{value}{unit}{marker_suffix} ({metric['status']})"


def _append_errors(lines: list[str], errors: Any) -> None:
    # Append recorded error dictionaries and ignore entries without structured error
    # fields.
    for error in errors if isinstance(errors, list) else []:
        if isinstance(error, dict):
            lines.append(
                f"  - {error.get('type', 'Error')}: {error.get('message', '')}"
            )


def render_markdown(run: dict[str, Any]) -> str:
    # Build a run summary containing execution failures, measurements and their
    # limitations.
    usb_metric = run.get("device", {}).get("usb_speed", {})

    # Add the provisional USB 2 notice for HIGH, FULL or LOW negotiated links.
    usb2 = usb_metric.get("value") in {"HIGH", "FULL", "LOW"}
    heading = "# OAK-D Lite Benchmark Result"
    notice = (
        "\n> **PROVISIONAL USB 2 BASELINE:** Every `*` value must be repeated with the certified USB 3 cable.\n"
        if usb2
        else ""
    )
    lines = [
        heading,
        notice,
        f"Run: `{run['run_id']}`",
        f"Overall status: `{run.get('status', 'not recorded')}`",
        "",
        "## Execution status",
    ]
    device = run.get("device", {})

    if isinstance(device, dict) and "status" in device:
        lines.append(f"- Device/access status: `{device['status']}`")
        error = device.get("error")

        if isinstance(error, dict):
            lines.append(
                f"  - {error.get('type', 'Error')}: {error.get('message', '')}"
            )

    _append_errors(lines, run.get("errors", []))

    # List each recorded profile status and its exact error type and message.
    for profile_id, result in run.get("tests", {}).items():
        if not isinstance(result, dict) or "status" not in result:
            continue

        lines.append(f"- `{profile_id}`: `{result['status']}`")
        _append_errors(lines, result.get("errors", []))

    # List device metrics without a link marker under fixed constraints.
    lines.extend(["", "## Fixed constraints"])

    for metric_id, metric in flatten_metrics(run.get("device", {})).items():
        if "*" not in metric["markers"]:
            lines.append(f"- `{metric_id}`: {_metric_text(metric)}")

    lines.extend(["", "## Manufacturer claims"])

    for metric_id, metric in flatten_metrics(
        run.get("manufacturer_claims", {})
    ).items():
        lines.append(f"- `{metric_id}`: {_metric_text(metric)}")

    lines.extend(["", "## Calibrated values"])

    for metric_id, metric in flatten_metrics(run.get("calibration", {})).items():
        lines.append(f"- `{metric_id}`: {_metric_text(metric)}")

    # Put link-dependent device metrics and profile results in the measured section.
    lines.extend(["", "## Measured baseline"])

    for metric_id, metric in flatten_metrics(run.get("device", {})).items():
        if "*" in metric["markers"]:
            lines.append(f"- `device.{metric_id}`: {_metric_text(metric)}")

    for metric_id, metric in flatten_metrics(run.get("tests", {})).items():
        lines.append(f"- `{metric_id}`: {_metric_text(metric)}")

    lines.extend(["", "## Pending application tests"])
    lines.extend(f"- {item}" for item in run.get("pending_application_tests", []))
    lines.extend(
        [
            "",
            "## Marker key",
            "",
            "- `*` link-dependent",
            "- `†` scene/target-dependent",
            "- `‡` ambiguous or conflicting source",
        ]
    )

    if run.get("warnings"):
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in run["warnings"])

    if run.get("source_ambiguities"):
        lines.extend(["", "## Source ambiguities"])
        lines.extend(f"- {item} ‡" for item in run["source_ambiguities"])

    return "\n".join(lines).rstrip() + "\n"

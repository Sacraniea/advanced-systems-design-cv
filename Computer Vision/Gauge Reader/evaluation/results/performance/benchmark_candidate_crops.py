"""Measure the in-memory latency of the temporary candidate-crop simulation.

The timed boundary contains only ``read_gauge(image)``.  Image decoding is
deliberately performed before timing because the future interface with Aiden's
detector is expected to pass an already-decoded NumPy image crop.

This helper and its outputs live under the ignored ``evaluation/results``
directory.  They are local engineering evidence, not submission artifacts.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
GAUGE_READER_ROOT = HERE.parents[2]
SIMULATION_ROOT = GAUGE_READER_ROOT / "evaluation" / "crops" / "simulated_aiden"

# Load the reader from this separate evaluation folder.
sys.path.insert(0, str(GAUGE_READER_ROOT))
from gauge_reader import read_gauge  # noqa: E402  (intentional late import)


def _nearest_rank(values: Iterable[float], percentile: float) -> float:
    """Return a transparent nearest-rank percentile for a non-empty sample."""

    ordered = sorted(values)

    if not ordered:
        raise ValueError("cannot summarise an empty timing sample")

    # Round the requested fraction up to a sample rank, then use its zero-based index.
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _summary(samples_ms: List[float]) -> Dict[str, float]:
    """Summarise latency without discarding slow calls as outliers."""

    median_ms = statistics.median(samples_ms)
    return {
        "calls": len(samples_ms),
        "minimum_ms": min(samples_ms),
        "mean_ms": statistics.fmean(samples_ms),
        "median_ms": median_ms,
        "p95_ms_nearest_rank": _nearest_rank(samples_ms, 0.95),
        "maximum_ms": max(samples_ms),
        "serial_fps_equivalent_from_median": 1000.0 / median_ms,
    }


def _result_signature(result: object) -> Tuple[object, ...]:
    """Capture outcome fields that must not change between repeated calls."""

    failure = getattr(result, "failure_reason")
    return (
        getattr(result, "valid"),
        None if failure is None else failure.value,
        getattr(result, "value_bar"),
        getattr(result, "angle_degrees"),
    )


def _load_workloads() -> Dict[str, List[Tuple[Dict[str, object], np.ndarray]]]:
    """Preload development and external cases while excluding holdout cases."""

    manifest_path = SIMULATION_ROOT / "simulation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    workloads: Dict[str, List[Tuple[Dict[str, object], np.ndarray]]] = {
        "supplied_video_development": [],
        "external_robustness": [],
    }

    for case in manifest["cases"]:
        source_type = str(case["source_type"])
        source_image = str(case["source_image"])

        if source_type == "supplied_video_frame":
            # The filename marks development frames; keep holdout frames out.
            if "-development.png" not in source_image:
                continue

            workload = "supplied_video_development"

        elif source_type == "external_candidate_crop":
            workload = "external_robustness"

        else:
            raise ValueError(f"unsupported source type: {source_type}")

        input_path = SIMULATION_ROOT / str(case["input_image"])
        image = cv2.imread(str(input_path), cv2.IMREAD_UNCHANGED)

        if image is None or image.size == 0:
            raise RuntimeError(f"could not decode {input_path}")

        workloads[workload].append((case, image))

    # Reject a changed case set so these timing results retain the intended workload.
    expected_counts = {
        "supplied_video_development": 10,
        "external_robustness": 6,
    }
    observed_counts = {name: len(cases) for name, cases in workloads.items()}

    if observed_counts != expected_counts:
        raise RuntimeError(
            f"unexpected workload composition: {observed_counts}; "
            f"expected {expected_counts}"
        )

    return workloads


def _benchmark_workload(
    cases: List[Tuple[Dict[str, object], np.ndarray]],
    repetitions: int,
    random_seed: int,
) -> Dict[str, object]:
    """Warm the workload and retain every measured call rather than one mean."""

    representative_signatures: Dict[str, Tuple[object, ...]] = {}

    # Warm OpenCV once per image and keep the result for later consistency checks.
    for case, image in cases:
        result = read_gauge(image)
        representative_signatures[str(case["case_id"])] = _result_signature(result)

    samples_by_case: Dict[str, List[float]] = {
        str(case["case_id"]): [] for case, _ in cases
    }
    all_samples_ms: List[float] = []

    # Shuffle repeatably so each case gets a different place in the run.
    for repetition in range(repetitions):
        order = list(range(len(cases)))
        random.Random(random_seed + repetition).shuffle(order)

        for index in order:
            case, image = cases[index]
            started_ns = time.perf_counter_ns()
            result = read_gauge(image)

            # Convert nanoseconds to milliseconds without timing image decoding.
            elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0

            case_id = str(case["case_id"])

            # Stop if a repeated call changes the acceptance, pressure, angle or reason.
            if _result_signature(result) != representative_signatures[case_id]:
                raise RuntimeError(f"non-deterministic outcome for {case_id}")

            samples_by_case[case_id].append(elapsed_ms)
            all_samples_ms.append(elapsed_ms)

    outcomes = []

    # Pair each case outcome with its complete timing samples and latency summary.
    for case, _ in cases:
        case_id = str(case["case_id"])
        valid, failure_reason, value_bar, angle_degrees = representative_signatures[
            case_id
        ]
        outcomes.append(
            {
                "case_id": case_id,
                "valid": valid,
                "failure_reason": failure_reason,
                "value_bar": value_bar,
                "angle_degrees": angle_degrees,
                "latency": _summary(samples_by_case[case_id]),
                "raw_latency_ms": samples_by_case[case_id],
            }
        )

    return {
        "case_count": len(cases),
        "repetitions_per_case": repetitions,
        "summary": _summary(all_samples_ms),
        "outcomes": outcomes,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark in-memory gauge reading on simulated candidate crops"
    )
    parser.add_argument("--repetitions", type=int, default=20)
    return parser


def main(argv: List[str] | None = None) -> int:
    # Load the fixed workloads, measure reader calls and write one latency record.
    args = build_parser().parse_args(argv)

    if args.repetitions < 2:
        raise ValueError("repetitions must be at least 2")

    workloads = _load_workloads()

    # Report the cold first call separately from the warmed timings.
    first_case, first_image = workloads["supplied_video_development"][0]
    cold_started_ns = time.perf_counter_ns()
    read_gauge(first_image)
    cold_first_call_ms = (time.perf_counter_ns() - cold_started_ns) / 1_000_000.0

    results = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "read_gauge(image) only; image decoding excluded",
        "accuracy_claim": "none; human ground truth has not been labelled",
        "holdout_policy": (
            "provisional-holdout and related-source-challenge cases excluded"
        ),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "logical_cpu_count": os.cpu_count(),
            "opencv_threads": cv2.getNumThreads(),
        },
        "cold_first_call": {
            "case_id": first_case["case_id"],
            "latency_ms": cold_first_call_ms,
        },
        "workloads": {},
    }

    # Time each workload with a separate repeatable shuffle seed.
    for offset, (name, cases) in enumerate(workloads.items()):
        results["workloads"][name] = _benchmark_workload(
            cases,
            repetitions=args.repetitions,
            random_seed=455 + 1000 * offset,
        )

    output_path = HERE / "candidate_crop_latency.json"
    output_path.write_text(
        json.dumps(results, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print(f"cold first call: {cold_first_call_ms:.3f} ms")

    for name, workload in results["workloads"].items():
        summary = workload["summary"]
        print(
            f"{name}: {workload['case_count']} cases x {args.repetitions}; "
            f"median={summary['median_ms']:.3f} ms; "
            f"p95={summary['p95_ms_nearest_rank']:.3f} ms; "
            f"max={summary['maximum_ms']:.3f} ms; "
            f"serial-equivalent={summary['serial_fps_equivalent_from_median']:.2f} fps"
        )

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

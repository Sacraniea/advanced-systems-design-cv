# OAK-D Lite evidence benchmark

For Aidan's model integration, use the [computer vision handover](../../Computer%20Vision/README.md).
This folder is the separate hardware benchmark, not the live application entry point.

This harness records repeatable OAK-D Lite inventory and stream evidence as immutable JSON plus a readable Markdown summary. Generated reports are evidence for the student to interpret, not assessment prose and not application-acceptance conclusions. The code is read-only with respect to the camera: it does not flash firmware, write calibration, retry a failed profile, or adapt a profile after failure. JSON and Markdown are fsynced in a unique sibling staging directory and published together with one atomic, no-replace directory rename; an existing evidence directory is never deleted or overwritten.

## Measurement boundary

The OAK runs sensor capture, StereoDepth, H.264 encoding, and the SystemLogger node on its RVC2. The host opens bounded non-blocking queues, records host arrival times, retains DepthAI message timestamps and sequence numbers, aggregates samples, and writes JSON/Markdown. Luxonis explains this device/host split in its [DepthAI node documentation](https://docs.luxonis.com/software-v3/depthai/depthai-components/nodes/) and the telemetry fields in [SystemInformation](https://docs.luxonis.com/software-v3/depthai/depthai-components/messages/system_information/).

DepthAI reports the negotiated link as an enum. `HIGH` means USB 2 at a nominal signalling rate of 480 Mbit/s; `SUPER` means USB 3 at 5 Gbit/s. These are negotiated interface categories, not throughput measurements. Application payload is necessarily lower than the nominal signalling rate, and this harness's raw payload bitrate is not total bus utilisation: USB framing, XLink/protocol overhead, metadata, host-to-device traffic, and data that never reached a host queue are excluded. Luxonis's [device documentation](https://docs.luxonis.com/software-v3/depthai/depthai-components/device/) shows `getUsbSpeed()`, while its [performance guidance](https://docs.luxonis.com/software-v3/depthai/tutorials/optimizing/) discusses latency and bandwidth.

H.264 compression happens on the OAK, so encoded 1080p can work well over USB 2. Raw 1080p and concurrent raw/multi-stream profiles deliberately expose link pressure because they transfer much more application payload. A sequence-number gap is evidence that a message was lost, skipped, or not observed; it is not proof of location. The cause could be camera/pipeline production, USB/XLink transfer, or host queue handling.

`message.getTimestamp()` supplies a DepthAI timestamp aligned to its host-synchronised clock domain, and host-side `dai.Clock.now()` records queue arrival in that same domain. Their difference supports a software latency estimate. Python `time.monotonic()` remains separate and controls only run deadlines and durations. This is not a wire-level measurement: scheduling, clock synchronisation error, queue residence, and the timestamp's point in the pipeline remain part of the uncertainty. See Luxonis's [Device clock and host-sync explanation](https://docs.luxonis.com/software-v3/depthai/depthai-components/device/#clock).

Results use three visible safeguards:

- `*` means link-dependent and must be repeated when the USB path changes.
- `†` means scene/target-dependent and remains pending until the relevant application test exists.
- `‡` means the supplied source is ambiguous or conflicting.

These markers prevent provisional observations from quietly becoming false fixed specifications. In particular, the December 2021 datasheet conflicts between USB 3.1 Gen1 and Gen2 and contains the ambiguous wording “900 mA USB2 max”; both remain `‡` evidence rather than being resolved by assumption. Datasheet values appear under manufacturer claims, never as direct measurements.

## Setup and safe checks

From the repository root:

```bash
cd 'Advanced Systems Design/Hardware Diagnostics/OAK-D-Lite'
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run all camera-free automated tests, compile checks, list the CLI, and inspect the seven profiles:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m py_compile oak_benchmark.py oak_metrics.py
.venv/bin/python oak_benchmark.py --help
.venv/bin/python oak_benchmark.py profiles
```

DepthAI 2.32.0.0 is intentionally pinned for repeatability. It can emit a known `HIGH_DENSITY` StereoDepth preset deprecation warning. The warning is recorded transparently; it is not hidden and does not authorize changing a profile during a run.

## Collect evidence

Close DepthAI Viewer before inventory or test execution because it may own the device. Record connection descriptions literally. If `--mxid` is omitted, discovery must return exactly one device. If supplied, exactly one discovered `DeviceInfo.getMxId()` must match. Before profiles, inventory must verify the selected MXID, product `OAK-D-LITE`, CAM_A `IMX214`, and CAM_B/C `OV7251`; a mismatch is published as failed inventory evidence and exits 1:

```bash
.venv/bin/python oak_benchmark.py inventory \
  --mxid "DEVICE_MXID" \
  --host-label "Raspberry Pi 5" --hub "none" --port "blue USB-A" \
  --cable "current USB2 cable" --power "official 27W PSU" \
  --ambient-c 24 --notes "bench inventory before USB2 run"
```

A complete USB 2 baseline runs all seven profiles for 10 seconds and then repeats only the combined profile for 60 seconds:

```bash
.venv/bin/python oak_benchmark.py run \
  --mxid "DEVICE_MXID" \
  --host-label "Raspberry Pi 5" --hub "none" --port "blue USB-A" \
  --cable "current USB2 cable" --power "official 27W PSU" \
  --ambient-c 24 --notes "full USB2 baseline" \
  --profiles all --duration-s 10 --stability-duration-s 60 \
  --temperature-stop-c 100
```

Run an exact comma-separated subset and disable only the final stability repetition with `0`:

```bash
.venv/bin/python oak_benchmark.py run \
  --mxid "DEVICE_MXID" \
  --host-label "Raspberry Pi 5" --hub "none" --port "blue USB-A" \
  --cable "certified USB3 cable" --power "official 27W PSU" \
  --ambient-c 24 --notes "selected USB3 retest" \
  --profiles rgb_video_1920x1080_30,combined_h264_depth_system_30 \
  --duration-s 10 --stability-duration-s 0 --temperature-stop-c 100
```

Duplicate comma-separated profile IDs are rejected before device discovery. Durations above 300 seconds are rejected unless `--allow-long-test` is explicitly present. That flag changes only authorization; it does not relax the temperature guard. The harness permits at most a finite 100 °C chip guard, retaining margin below Luxonis's documented 105 °C automatic device shutdown. The supplied datasheet's 50 °C maximum is an ambient-air claim, whereas SystemLogger reports internal chip components; ambient and chip temperatures are not interchangeable. Luxonis documents the distinction on the [OAK-D Lite product page](https://docs.luxonis.com/hardware/products/OAK-D%20Lite) and in its [operating-temperature guidance](https://docs.luxonis.com/hardware/platform/environmental-specifications/operating-temperature).

Every profile has a fixed 3.0-second SystemLogger grace. A run lasting at least three seconds stops at that deadline as `inconclusive` with `telemetry-missing` if no valid telemetry arrives. A shorter run may reach its requested deadline, but is still `inconclusive` if telemetry or any expected non-system stream is absent. Pipeline build, device open, polling start, and polling finish are recorded separately. Application payload is `no-data`/`null` before polling; zero bytes becomes a measurement only after polling starts. Polling duration excludes device-context teardown, while lifecycle duration includes it. The 100 °C guard is evaluated whenever valid telemetry arrives.

An exception is `unsupported` only when unambiguous incompatibility wording occurs during pipeline build or device open. Queue/polling exceptions remain exact `error` or `disconnected` evidence. A run containing only complete and unsupported profiles is `complete-with-unsupported` and exits 0; inconclusive, thermal, disconnected, and error outcomes still exit 1.

Compare two immutable schema 1.0 raw records without modifying either source:

```bash
.venv/bin/python oak_benchmark.py compare \
  results/BASELINE_RUN_ID/raw.json \
  results/CANDIDATE_RUN_ID/raw.json
```

The comparison is written as a separate atomic `comparison.json` and `comparison.md` pair. A comparison other than `HIGH` to `SUPER` retains an explicit warning rather than being presented as the intended USB 2-to-USB 3 retest. Every run records a benchmark version, SHA-256 hashes of `oak_benchmark.py`, `oak_metrics.py`, and `requirements.txt`, plus the canonical profile-definition hash. Comparison does not reject different provenance, but warns so revision changes cannot be mistaken for cable-only effects.

## USB 3 retest controls

The source fingerprints include comments and whitespace. The readability pass
changes those fingerprints even though the executable logic and profile settings
are unchanged. A new run compared with older evidence can therefore produce a
provenance warning. Keep the archived records as collected; do not rewrite their
hashes to make revisions appear identical.

Before interpreting differences, keep these controls the same and record any exception:

- DepthAI 2.32.0.0 and identical recorded benchmark provenance (version, source hashes, and profile-definition hash); investigate any comparison warning.
- The host, port, power arrangement, and hub where practical; change only the certified cable/link needed for USB 3.
- Profile IDs, per-profile duration, stability duration, and 100 °C guard.
- Scene, lighting, camera placement, and target distance.
- Connection notes, ambient temperature, and competing host workload.
- Confirm inventory reports `SUPER`; never infer USB 3 solely from cable colour or packaging.

Preserve both runs. Interpret measured payload, frame delivery, sequence gaps, latency estimates, and system telemetry together, while keeping causal claims and application performance for later student analysis.

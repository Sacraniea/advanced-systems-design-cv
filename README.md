# Advanced Systems Design — Computer Vision

Public handover snapshot for Aidan, prepared 9 October 2026. Start with the
[Computer Vision handover](Computer%20Vision/README.md) for the frame flow,
processor contract, coordinates, and the connection to Lachlan's GCS.

## Where to start

| Folder | Purpose | Entry point |
| --- | --- | --- |
| Camera Stream | OAK frames, shared processing, overlays, video and MQTT | [run_camera_stream.py](Computer%20Vision/Camera%20Stream/run_camera_stream.py) |
| ArUco Detection | Marker IDs, calibrated pose and structured rejection | [README](Computer%20Vision/ArUco%20Detection/README.md) |
| Gauge Reader | Pressure reading from one gauge crop | [gauge_reader.py](Computer%20Vision/Gauge%20Reader/gauge_reader.py) |
| Target Detection | Aidan's YOLO predictor, weights and crop experiments | [predict.py](Computer%20Vision/Target%20Detection/src/predict.py) |
| Hardware Diagnostics | Separate OAK benchmark and report tools | [README](Hardware%20Diagnostics/OAK-D-Lite/README.md) |

Keep the four Computer Vision folders together: they import from sibling folders.

## Current integration status

The shared stream runs ArUco and produces overlays, structured frame records,
video, and GCS log/photo forwarding. The gauge reader has been exercised on
Aidan's target crops. **Target detection and gauge readings are not yet connected
to the shared live stream or its GCS output.**

[stream_target_detection.py](Computer%20Vision/Camera%20Stream/stream_target_detection.py)
is unfinished copied scaffolding. It is retained to show work in progress and
is not registered by the runner. It still has ArUco names and unresolved
references; do not use it as a working target adapter. Follow the working
[stream_aruco.py](Computer%20Vision/Camera%20Stream/stream_aruco.py) and
[stream_processing.py](Computer%20Vision/Camera%20Stream/stream_processing.py)
contracts instead.

Before connecting the model, replace its cached last-gauge crop path with crops
from each current frame's detections. The cache can retain an older crop and
only holds one gauge. Preserve each detection when pressure is unavailable:
use `value_bar=None` and its failure reason. Draw on an overlay copy, keeping
the original frame unchanged.

## Try the stream without a camera

From the repository root, with Python 3.12 installed:

```sh
cd "Computer Vision/Camera Stream"
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-network.txt
.venv/bin/python -B run_local_demo.py --max-frames 300 --open-browser
```

This uses a generated ArUco scene and invented calibration. It does not verify
real camera accuracy. See the [network walkthrough](Computer%20Vision/Camera%20Stream/docs/local-network-walkthrough.md)
for the camera, browser stream and local MQTT routes. The IP addresses in
existing examples describe a previous test network; replace them with the
current machine or broker address.

## Target model environment

Use Target Detection's existing Pixi environment definition separately for now:

```sh
cd "Computer Vision/Target Detection"
pixi install
```

Its manifest supports Linux x86-64 and macOS ARM. Camera Stream pins NumPy 2.5.2,
while Target Detection currently requires NumPy below 2.4. Those requirements
need reconciling before a single environment runs both.

The model `models/50_epoch_best.pt` and runtime class configuration
`data/v2_EGH455-3/data.yaml` are included. Class order is `closedValve`, `gauge`,
`openValve`. Dataset images and labels are excluded. Target's dataset tests need
those files restored, and several tests open windows and wait for a keypress.
The crop demonstration is a manual experiment, not a pressure-accuracy test.

The dataset downloader reads `ROBOFLOW_API_KEY` from the environment. Supply
your own authorised key locally; do not commit it. The training notebook was
excluded because it contained a credential and stored outputs.

## Snapshot contents

Included: source code, tests, dependency manifests, handover documentation, model
weights and class configuration. Source authorship is retained; Aidan/Yidann
provided the target detector. No additional project-wide licence is assigned
by this snapshot. Third-party dependencies and datasets retain their own terms. The supplied
Ultralytics checkpoint embeds AGPL-3.0 attribution; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Excluded: Git history, Python/Pixi environments, credentials, training datasets,
captured photos/video, generated evaluation results, hardware run records,
camera-specific calibration, coursework submissions and assessment reports.
Historical references to those reports/evidence in component documents are
context only; those artifacts are not included in this public repository.
Evaluation scripts that depend on excluded assets require those assets locally.
The preprocessing video experiment uses `GAUGE_DATA_ROOT` for the sample-video
folder. Hardware report tests also require excluded archived evidence and
`python-docx`; they are separate from the camera-free benchmark unit tests.

For real metric pose, export calibration from the camera you actually use and
pass its path explicitly. A missing calibration still permits marker detection
but withholds pose. Match the configured marker size to the measured outer
black square (the assignment specifies 200 mm).

This is a handover snapshot. Changes in the original working folder are not
automatically synchronised here.

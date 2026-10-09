# Snapshot verification

Checked on 9 October 2026 using the existing macOS Python 3.12 Camera Stream
environment, with the working directory and imports pointing to this exported
copy. No physical camera or remote GCS broker was used.

| Suite | Result |
| --- | --- |
| Camera Stream | 179 tests passed |
| ArUco Detection | 85 tests passed |
| Gauge Reader | 48 tests passed |
| Hardware Diagnostics benchmark/metrics | 74 tests passed |
| Total | 386 tests passed |

Each suite was run from its component folder with
`python -B -m unittest discover -s tests`. ArUco also requires
`PYTHONPATH="../Camera Stream"`. The stream suite needs permission to open
temporary local sockets; its sandboxed attempt was blocked, and the complete
suite passed when rerun with loopback access.

All exported Python files were parsed successfully. This does not prove the
unfinished target adapter imports or runs.

Not validated in this publication step: a fresh dependency installation,
Target Detection inference or dataset/GUI tests, hardware report tests that
require excluded archived evidence, physical distance accuracy, live camera
throughput, or delivery into the remote GCS database.

## Public-copy preparation

The supplied model matches the original file byte-for-byte. Source and tests
were copied from the development worktree without its Git history. Public-copy
adjustments are limited to documentation/ignore files, removing the author's
email from the Pixi manifest, reading the dataset API key from an environment
variable, and replacing the preprocessing experiment's personal video path
with `GAUGE_DATA_ROOT`. The historical hardware report helper uses a placeholder
for its camera serial number; restore the expected device ID when supplying
your own archived evidence. The original development files were not changed.

Credentials, notebook outputs, installed environments, dataset images/labels,
local captures, generated results, camera calibration and assessment material
were excluded. The dataset class-map YAML is deliberately retained because
the predictor reads it during construction.

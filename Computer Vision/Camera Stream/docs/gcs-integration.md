# GCS integration: logs, detection images and video

This adapter follows the GCS MQTT Sender Tools README provided by Lachlan on
8 October 2026. The testing broker is `10.88.35.39:1883`; deployment uses the
Pi's reachable address. Supply the bare IP or hostname to `--broker`, not a
`mqtt://` URL or Docker's internal service name `mqtt`.

| Output | Route | Payload |
| --- | --- | --- |
| Live annotated video | Existing HTTP `/stream.mjpg` link, fed by local NetGear | JPEG video |
| Sampled ArUco observations | MQTT `gcs/drone_log` | Exactly `action` and `content` strings |
| Selected original image file, manually or once per ArUco sighting | MQTT `gcs/detection` | Exactly `type`, `description`, `mime`, `image`, `timestamp` |
| Environmental telemetry | MQTT `gcs/telemetry` | Owned by the sensor subsystem; not sent by these tools |

The video URL does not deliver structured ArUco results. NetGear remains an
image transport after composition, as described in the CV integration report.
The GCS compatibility mapping is a separate MQTT output adapter; it does not
change the camera's native result schema or make MQTT an internal frame joiner.

## Run the stream and forwarder

Keep the existing calibrated camera demo running in terminal 1. Pose requires
the actual measured marker size and matching calibration. Without calibration,
logs still show marker ID, its centre in the image and timing, with a
friendly explanation that pose is unavailable. Full corner coordinates remain
in the local frame evidence.

In terminal 2, from the same Camera Stream directory, first preview:

```sh
.venv/bin/python -B run_gcs_forwarder.py --timeout-s 30
```

This subscribes to local `127.0.0.1:18883`, topic `taip/local/results`, and saves
prepared payloads in a new `diagnostics/gcs/*.jsonl` file. It does not open any
connection to the GCS. The printed path contains the full prepared payloads;
the terminal shows only a concise summary.

To send to the agreed GCS broker:

```sh
.venv/bin/python -B run_gcs_forwarder.py \
  --publish --broker 10.88.35.39 --port 1883 \
  --interval-s 5 --timeout-s 600
```

The source port and topic can be changed with `--local-port` and `--local-topic`.
The GCS log destination topic stays fixed to the agreed contract. The forwarder
does not start or stop the camera, local broker, video receiver or GCS. Ctrl+C
stops only the forwarder. If the local demo ends, its broker disconnect will
end the forwarder with a reported error; that does not undo already acknowledged
messages. Start it again alongside the next demo session.

The current GCS connection uses MQTT 3.1.1, QoS 1, no retained messages, and no
credentials or TLS, matching the supplied README's connection example. If the
GCS requires credentials or TLS, add that configuration before publishing.
Remote socket/PUBACK failures are bounded and stop the forwarder. Hostname DNS
resolution is controlled by the operating system. No automatic stale replay
or reconnect is attempted; the camera continues independently. Incoming results
use the existing bounded subscriber queue; queue overflow reports an error.

## What the log contains

The outer payload is still exactly two strings, `action` and `content`.
`content` now contains plain text for the GCS log view, rather than a JSON
record embedded in a string.

| Action | Meaning |
| --- | --- |
| `ArUco detected` | At least one marker has an accepted pose. Other markers in the same message can still have unavailable poses. |
| `ArUco detected - pose unavailable` | Markers were decoded, but every pose was withheld. |
| `ArUco processing error` | The ArUco processor failed; this is distinct from a normal pose rejection. |

For example, an accepted pose could render as follows. These values illustrate
the format; they are not hardware measurements.

**Action:** `ArUco detected`

**Content:**

```text
Marker 7: 10.0 cm right, 20.0 cm down, 200.0 cm forward; image position (40, 40) px.
Frame 42 | Device time 12.500 s | Run run-abc
```

A rejected pose instead uses `ArUco detected - pose unavailable`, with text such
as `Marker 7: pose unavailable (corner fit error too high: 4.00 px)`, followed by
the image position and identity footer. Synthetic summaries begin with
`Synthetic demo.`.

Each marker summary identifies its ID and gives:

- An accepted camera-relative position in **centimetres**, using right/left,
  down/up and forward directions, or a friendly reason why pose is unavailable.
- Its image centre in pixels, calculated as the arithmetic mean of its detected
  corner coordinates in the source image.

The position directions follow `camera_optical`: x is right, y is down and z is
forward. A forward distance is z depth, not Euclidean range. Centimetres are
a display conversion; native pose translations remain in metres. A footer records
the frame sequence, device monotonic timestamp in seconds and the full run ID.
Those monotonic seconds are not a UTC time of day. Synthetic data is explicitly
marked so a demonstration cannot be mistaken for a camera measurement.

Empty frames generate no log. A decoded marker with a rejected pose still
appears as a detection with an explanation; a candidate pose is never presented
as an accepted position. No gauge values are fabricated.

Full native frame evidence remains in the local sender's `frames.jsonl`
(`sender/frames.jsonl` inside a demo run). It retains the source timestamps,
image geometry, complete corners, accepted/rejected pose fields and calibration
metadata. Captured image sidecars also retain the full record. Automatic GCS
image descriptions use the same readable summary, as described below.

**Receiver compatibility:** the outer MQTT JSON object is unchanged, but log
`content` is no longer JSON. Any consumer that previously called `JSON.parse`
or `json.loads` on that string must adapt. Display it as text. Structured pose
columns, filtering or pose widgets require a jointly agreed data schema; this
human-readable summary is not a replacement machine-readable pose interface.
The GCS also needs to accept the three action strings above.

Logs are sampled at most once every five seconds by default. Changing poses or
new targets within an interval can be omitted; this is a commissioning log,
not a guarantee that every target encounter is logged. The full per-frame
record stays in the camera session. Set a different positive `--interval-s`
only to match the agreed logging rate. Camera frame rate is independent.

Duplicate/out-of-order source frames are suppressed using the highest sequence
for the 16 most recently seen runs. QoS 1 can still duplicate a destination
delivery. Frame and run identity appear in the text footer for correlation,
but there are no dedicated structured deduplication fields in this two-string
schema. Agree on a machine-readable identity contract if GCS-side deduplication
is required. No global exactly-once database guarantee is claimed across bridge
restarts.

Every selected event has a local audit line marked `preview`,
`broker_acknowledged`, or `failed`. Its `prepared_at_utc` is the preparation time,
not camera capture time. A broker acknowledgement does not prove the GCS
subscriber parsed the payload or saved it to MySQL.

## Send an original image file explicitly

The camera's structured MQTT record contains no image. The GCS image helper
therefore takes an existing original PNG/JPEG file and sends it only when
requested. It does not automatically capture images from the live feed.

Preview and optionally save the complete payload:

```sh
.venv/bin/python -B send_gcs_detection.py /path/to/original.png \
  --type Valve --description "Original full-frame valve capture" \
  --output /path/to/new-payload.json
```

Publish the selected file:

```sh
.venv/bin/python -B send_gcs_detection.py /path/to/original.png \
  --type Valve --description "Original full-frame valve capture" \
  --publish --broker 10.88.35.39 --port 1883
```

The helper preserves original file bytes, image resolution and embedded file
metadata. It validates PNG/JPEG bytes, then Base64-encodes those same bytes.
It does not resize, crop, rotate, annotate or re-encode the image. Preview prints
the byte length and SHA-256 without dumping Base64 into the terminal. Existing
output files are never overwritten.

The optional `--timestamp` accepts a known timezone-aware ISO 8601 event time.
If omitted, the field is current UTC payload-preparation/publication time;
it is not inferred from the file or a monotonic camera timestamp. Confirm this
meaning and allowed `type` values with the GCS receiver.

## Automatic images: one per ArUco sighting

The user selected one image per sighting. The camera can now retain an original
full-resolution PNG with the same frame's result, independently of the annotated
MJPEG preview. A second process reads completed capture events and uploads them.
One capture can represent several newly sighted markers in the same frame.

Add `--capture-detections` to the existing camera demo command, retaining the
same marker size, calibration and HTTP host options. The launcher prints the
capture directory, underneath `sender/detections` in that run's evidence folder.
For a repeatable example, choose a new output directory each run:

```sh
.venv/bin/python -B run_local_demo.py \
  --camera --profile 1080p30 --http-host 10.88.38.220 \
  --capture-detections --max-frames 18000 \
  --output-dir diagnostics/local-network/sighting-demo-001
```

This example retains decoded marker evidence but withholds pose because it omits
calibration. Add the existing calibration options when metric pose is required.
Use the Mac's current LAN address if it has changed.

Preview pending images without contacting the GCS:

```sh
.venv/bin/python -B run_gcs_image_forwarder.py \
  --capture-dir diagnostics/local-network/sighting-demo-001/sender/detections \
  --timeout-s 30
```

To upload the captures, explicitly enable publication:

```sh
.venv/bin/python -B run_gcs_image_forwarder.py \
  --capture-dir diagnostics/local-network/sighting-demo-001/sender/detections \
  --publish --broker 10.88.35.39 --port 1883 \
  --interval-s 10 --max-mbps 1 --publish-timeout-s 60 --timeout-s 0
```

`--publish-timeout-s` controls the image publisher's acknowledgement wait and
defaults to 60 seconds. Full-resolution images can occupy several megabytes;
their transmission and broker acknowledgement need time. This setting does
not guarantee delivery within 60 seconds, and it leaves the stream and log
publishers' two-second default unchanged.

`--timeout-s 0` runs until Ctrl+C. A positive value sets a runtime deadline
checked between uploads, with 600 seconds retained as the default. Startup
prints the broker, topic, resolved capture directory, runtime and acknowledgement
wait. Waiting or pacing status appears at 30-second intervals between uploads
with pending counts, and shutdown prints a summary. Check that the shown folder
matches the current camera run: the uploader watches only the directory passed
to `--capture-dir` and does not follow new run folders
automatically. Restart it with the new run's printed path when the camera
session changes.

The log forwarder is separate and can continue alongside this image uploader.
Automatic GCS detection `description` is a plain-text caption using the log's
same-frame ArUco summary. For example, illustrative values
could produce:

```text
Marker 7: 10.0 cm right, 20.0 cm down, 200.0 cm forward; image position (40, 40) px.
Frame 42 | Device time 12.500 s | Run run-abc
```

The caption includes one summary per marker: accepted camera-relative position
in centimetres or a friendly pose-rejection reason, plus the centre in source
pixels. Its footer retains the frame sequence, device monotonic seconds and
full run ID; synthetic captures retain the `Synthetic demo.` label. An event
without an ArUco summary is recorded as failed instead of uploading a caption
containing raw JSON.

The outer image message still has exactly `type`, `description`, `mime`, `image`
and `timestamp`. Original image bytes, MIME type and timestamp are unchanged.
The `timestamp` is the host's sighting-selection time in UTC, not a fabricated
conversion of the device monotonic timestamp to UTC exposure time. The complete
same-frame record and `event_id` remain in the local `*.event.json` sidecar;
delivery receipts retain the event identity and original image hash.

The GCS must display `description` as plain text. Consumers that parsed it as
JSON must adapt; it no longer contains structured pose fields or an `event_id`
key. Restart the image uploader in a new process to use the new captions.
Descriptions already saved in the GCS are not rewritten.

The initial capture policy is:

- Confirm the marker over a continuous 0.3-second interval.
- Capture once while it remains visible, even if pose is withheld.
- Require a two-second absence before treating a return as another sighting.
- Apply a ten-second cooldown per marker before another capture.
- Retain at most three pending image events and at most 256 MiB of original
  image bytes per session by default. `--capture-max-pending` and
  `--capture-storage-mib` configure these bounds.

The dictionary/ID is the ArUco identity. Repeated physical markers with the same
ID cannot be distinguished reliably by that key. For gauges and valves, a class
label alone is not a unique asset ID; those branches need Aidan's tracking or an
inspection-point identity before this policy can be applied to them.

PNG encoding is lossless and saves the source frame once. The upload helper
then sends those exact file bytes. Capture encoding/disk work is synchronous
and can briefly affect capture throughput; network waits happen in the separate
uploader. Metadata is committed last as an atomic `*.event.json` readiness
signal. The image is never paired with an independently fetched latest result.

Queue/storage limits deliberately trade image coverage for bounded resource
use. A capture that cannot be retained is recorded as skipped, and full frame
measurement logs remain available. Originals are never automatically deleted.
Inspect the capture audit/summary for skipped or failed events; do not claim all
target sightings have images if those counts are nonzero.

## Bandwidth and gallery thumbnails

NetGear currently transfers on loopback. Over the LAN, the HTTP MJPEG video and
MQTT uploads share the same network interface. Separate processes avoid making
the camera wait for MQTT, but do not reserve network bandwidth for video.

The uploader sends one image at a time. Its default pacing is at least ten
seconds between sends, extended according to the previous complete JSON/Base64
payload size to budget roughly 1 Mbit/s of average payload traffic. A single
MQTT message can still transmit in a burst; this is not traffic shaping or a
guarantee of uninterrupted video on every Wi-Fi network. Network framing,
retransmissions and GCS-to-browser traffic are additional costs. Measure video
latency and received FPS during commissioning and lower the upload budget if
needed.

The default maximum payload is 12 MB. Invalid or oversized events are recorded
as failed and are not retried automatically. Sent/failed receipts release
pending queue slots while preserving the original files. A preview never
consumes a pending event. Confirm the broker's packet-size limit against the
configured payload limit; a longer acknowledgement wait cannot overcome that
limit.

If connection setup fails before publication, or the publisher confirms that
the image was never handed to the MQTT client, the same event remains pending
and is retried after the configured pacing delay. The latest diagnostic is
saved in `*.event.retry.json`; it does not release the event's queue slot and is
removed when a terminal sent/failed receipt is written. Once the image has been
handed to MQTT, an error or missing acknowledgement leaves delivery uncertain.
That event receives a failed receipt and is not automatically replayed, because
the GCS may already have received it.

Run one image uploader per capture directory. Receipts prevent routine resends
after restart; MQTT QoS 1 can still duplicate delivery. The caption's full run ID
and frame sequence allow correlation with local evidence, but the five-field
image contract has no dedicated machine-readable event identity field. GCS-side
deduplication using such a field needs a jointly agreed schema. One local image
per sighting is not an exactly-once database guarantee. A sent receipt confirms
broker acknowledgement, not database ingestion. A later event can open a fresh
connection after an earlier upload failed with uncertain delivery; the earlier
failed event remains excluded from automatic replay.

Base64 uses four encoded characters for three original bytes, approximately
33% overhead ([RFC 4648](https://www.rfc-editor.org/rfc/rfc4648.html#section-4)).
For example, a 1 MB image becomes approximately 1.33 MB before the surrounding
JSON. One such image every ten seconds averages about 1.07 Mbit/s before other
network overhead; the size-based pacing extends that interval for a 1 Mbit/s
budget.

Lachlan's existing contract uploads/stores the original and uses CSS to display
it smaller. CSS sizing does not reduce the bytes downloaded by the browser.
For a large gallery, the GCS can store an additional derived thumbnail and load
the full original only on demand. That would be a GCS-side display/API change,
while keeping the original file untouched; this sender does not resize or
recompress it. See [MDN's responsive-image guidance](https://developer.mozilla.org/en-US/docs/Web/HTML/Guides/Responsive_images).

## Verify with the GCS

The local automated tests check exact outer schemas, real MQTT packet exchange,
sampling, pose rejection preservation and byte-for-byte image payloads. They
do not contact Lachlan's broker.

For commissioning, run the camera and preview first, then explicitly publish.
Lachlan should confirm an event in his log UI/database and inspect its content
against the local audit's run ID and sequence. For a selected image, confirm its
resolution and compare the decoded bytes/hash with the original file. Broker
reachability, GCS action/type acceptance, field-size limits and actual database
ingestion are only verified by that joint test.

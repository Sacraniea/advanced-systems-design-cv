# Walkthrough: follow one frame

Use this alongside the implementation. We can stop at each checkpoint and trace
an actual packet together before changing anything.

## 1. Start with the report

The integration report's sections 6 and 11 describe the controller and compositor.
Its section 13 follows a hypothetical gauge/valve/ArUco frame. The implementation
now supplies that shared path and the real ArUco branch. Aidan's model and the
conditional gauge/valve adapters are still a separate integration step.

## 2. Who opens the camera?

Read `run_camera_stream.main`, then `DepthAIFrameSource.__init__`.
The command selects a profile, reserves a session directory and opens one device.
CAM_A video feeds XLinkOut; the host queue has capacity four. Its non-blocking
setting means the device producer can drop queued frames rather than build an
unbounded backlog. The new command polls `tryGet` with a frame-arrival timeout.

**Checkpoint:** Does a 30 FPS setting guarantee that our Python model processes
30 frames each second? Which recorded fields would reveal a slower consumer?

## 3. What travels through the subsystem?

Read `FramePacket`, then `frame_packet_from_message`.
The image travels with its sequence number and two timestamps. The device
synchronised clock and host dequeue clock share a domain; UTC session times are
separate labels and must not be subtracted from those monotonic timestamps.

**Checkpoint:** Why would passing just an image to an asynchronous consumer make
it harder to attach its answer to the correct frame later?

## 4. Where does processing happen?

Read the loop in `run_stream`, then `process_frame`.
The loop retains Frame N. Each configured processor receives exactly that packet.
Currently only ArUco is enabled by the CLI. Calls are sequential; “independent
branches” describes their data dependencies, not simultaneous threads.
The pixel buffer is temporarily read-only. A frozen dataclass alone would not
prevent someone modifying pixels inside its array.

`ArUcoProcessor.process` calls the existing `process_aruco_frame`. We did not
rewrite marker detection or open a second OAK. Its result has two parts: JSON-safe
evidence for logging, and typed observations for the renderer.

**Checkpoint:** If ArUco cannot calculate pose, why should the marker ID and
corners still appear in its structured result?

## 5. How do we prevent wrong-frame annotations?

Read `FrameIdentity` and the checks in `process_frame` and `ResultCompositor`.
Identity includes sequence, both timestamps and image dimensions. A processor
returning Frame N−1's result cannot annotate Frame N, even if a sequence number
happened to match after a restart. Saved records also include a unique run ID.
The controller completes the configured result set before composing; each normal
rejection counts as a completed branch.

**Checkpoint:** Which two values would you inspect first if an annotation looked
one frame behind the object?

## 6. Why copy at composition?

Read `ResultCompositor.compose`, then `ArUcoProcessor.draw`.
The compositor copies the original. Renderers draw only on the overlay and
return it in a new packet retaining the original timestamps and sequence.
ArUco's existing renderer makes an additional temporary copy; it preserves
annotations drawn by earlier renderers and keeps its standalone API unchanged.
The display receives the overlay; the source remains suitable for verification
and every subsequent algorithm.

**Checkpoint:** Why is `overlay = packet.image` different from
`overlay = packet.image.copy()`?

## 7. What gets saved?

Read `StreamSession.write` and `finish`.
Each frame produces one strict JSON line; even a frame with no marker keeps its
identity. An unexpected branch failure marks the frame partial. A normal ArUco
rejection has its own reason inside its target record. On shutdown the manifest
records complete/interrupted/failed status and summary statistics.

No image or result history accumulates in memory. Online diagnostics keep counts,
a sum for the mean, first/last arrival samples, and a rolling window of 2048
latencies for P95. The full JSONL remains available for offline statistics.

**Checkpoint:** Why must the reported recent-window P95 be labelled differently
from an all-run percentile?

## 8. How do failures stop?

Read the `try/finally` in `run_stream` and `ExitStack` in `main`.
Before the controller starts, main owns cleanup. It then transfers ownership to
the controller. Camera or display failures still close both resources. Closing
one resource must not prevent the other from closing; a cleanup failure should
not hide the original fault.

A read timeout handles a missing camera message. A processing budget is different:
in this synchronous version it can reject a slow result when it returns, but
cannot cancel a hung model. That distinction is deliberate and documented.

**Checkpoint:** Why would adding `if elapsed > limit` after a function call not
protect us from a function that never returns?

## 9. Where does Aidan's model go later?

Implement another `FrameProcessor`, then pass both processors into `run_stream`.
That adapter owns model preprocessing, mapping detections back into source pixel
coordinates, and conditional gauge/valve work. A gauge crop is a NumPy view of
Frame N; the reader's crop-relative geometry needs the crop origin added back
before rendering. The controller and OAK source need no second frame path.

Once those calls are available, measure their combined timing on the Pi before
choosing concurrency, lower processing rates or a hard deadline policy.

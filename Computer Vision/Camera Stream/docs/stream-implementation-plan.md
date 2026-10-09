# Camera stream implementation plan

Implements the approved CV_Subsystem_Integration_Conceptual_Design, sections 6,
11–16, and Assessment 2 Phase 2 roadmap. User authorises direct implementation.
Keep existing staged work and the standalone ArUco entry point intact.

1. Add stream_processing.py: FrameIdentity, ProcessingResult, FrameProcessor,
   synchronous dispatch and copy-only ResultCompositor. Test two consumers,
   source preservation, stale results, branch failure and deadline rejection.
2. Extend stream_controller.run_stream with injected processors/compositor,
   result callback, max_frames and timing. Replace growing timing list with
   bounded online diagnostics. Preserve existing source/display API and cleanup.
3. Add ArUco processor adapter, session JSONL/manifest recorder and CLI options
   for ArUco, calibration, headless bounded trials and output directory. Test
   actual synthetic detection through CLI, identity mismatch and failures.
4. Add optional source read timeout and predictable display lifecycle. Test
   timeout and partial initialisation cleanup. No worker concurrency or network.
5. Run all Stream, ArUco and Gauge tests, inspect generated integrated overlay,
   probe camera availability, and document usage plus a guided code walkthrough.

Constraints: original frame pixels remain untouched; same-frame results only;
720p30 default / 1080p30 detail; camera_optical metres; optional branch errors
explicitly partial; no fabricated Aidan detector or MQTT/VidGear implementation.
A synchronous deadline detects overruns after return, not cancellation of a
hung third-party function. P95 uses a labelled bounded recent window; full
per-frame evidence on disk permits offline all-run analysis.


## Completion record — 5 October 2026

Tasks 1–5 implemented and verified in the existing worktree; no files staged or
committed. Final suites: Stream 44/44, ArUco 85/85, Gauge 48/48. Python syntax
and git whitespace checks passed. A complete synthetic calibrated command run
produced an accepted ID 7 pose, wrote valid session evidence, closed source/display,
and preserved all original pixels; its overlay was inspected visually.

Review found one manifest-finalisation exception-masking bug. Added a failing
regression test, fixed it, and reran the full suites. The reviewer subsequently
confirmed the fix and checked README/walkthrough accuracy.

Hardware boundary: DepthAI returned no available OAK devices. Live camera/Pi
performance and physical distance accuracy remain unverified in this session.
The new shared controller remains synchronous; hard processor cancellation and
Aidan's actual model adapter are subsequent integration work, as documented.

# Local network demonstration plan

User approved a local test setup to understand video and MQTT without building
the final web UI. Extends the existing stream outputs and report's two transport
paths. All demo endpoints bind/connect to 127.0.0.1; no LAN exposure or services.

1. MQTT publisher sends strict per-frame JSON with run ID and original metadata
   using QoS 1 and retain=false, with bounded connection/acknowledgement waits.
   Subscriber saves messages, prints a short explanation and announces SUBACK.
2. NetGear sends composed frame plus identity to a separate receiving process.
   Receiver exposes a minimal HTTP MJPEG endpoint, snapshot and health JSON for
   local browser inspection. No final dashboard, model or system UI is created.
3. Shared controller gets an optional on_output(packet,record) hook. Existing
   processing/source ownership stays unchanged. Camera CLI gets optional network
   flags and an explicitly labelled synthetic source for a camera-free demo.
4. run_local_demo.py starts a foreground loopback broker, receiver, subscriber
   and sender, verifies readiness, saves evidence, and closes only its children.
   Defaults to a finite synthetic trial; --camera selects actual hardware.
5. Real process/socket test checks received frames and MQTT records share run ID,
   sequence/timestamps; tests missing services, cleanup and no stale images.
   Documentation explains two-terminal observation and one-command usage.

Decisions: local MQTT broker uses aMQTT because Homebrew Mosquitto installation
failed in ca-certificates bottle handling. Actual report/deployment broker
remains Mosquitto. This substitutes the broker only, not the MQTT wire protocol.
Network failures fail this explicit local trial; no unverified delivery claims.
Successful MQTT acknowledgement means broker receipt, not all subscribers.
Video receipt and subscriber evidence are tested independently. Fixed loopback
ports are defaults; launch refuses occupied ports and never kills their owners.

Completed: all five steps are implemented. Verification passes 87 Camera Stream,
85 ArUco and 48 gauge tests, plus dependency consistency and whitespace checks.
The saved synthetic trial `diagnostics/local-network/verified-8362ee03` delivered
600 MQTT records and 600 video frames with matching identities. HTTP health,
snapshot and multipart MJPEG were inspected during capture, and the JPEG was
visually checked. Subsequent finalization regression tests check finalized
consumer files, partial writes, trailing records and nonzero child exits.
All local services were stopped after that verification. No physical camera or
Pi trial was claimed at that checkpoint. Review findings about receiver startup, ambiguous timeout,
synthetic timing provenance and verification ordering have been addressed.

LAN-viewer follow-up, 7 October 2026: added `--http-host` to select a concrete
HTTP listening address while preserving local-only MQTT and NetGear. The Mac's
Wi-Fi address was `192.168.68.55`. An attached-camera trial exposed a SIGINT
race during receiver finalization; cleanup now ignores SIGINT temporarily and
restores the previous handler, with a real-signal regression test. The final
600-frame trial (`diagnostics/local-network/camera-lan-verified-04c625f0`)
completed with 600 matching MQTT messages, 600 video frames, no sequence gaps,
29.95 received FPS and clean process exits. All 93 stream tests pass. HTTP was
checked through the LAN address from the Mac; second-device reachability is
pending user confirmation. The camera and all services are stopped.

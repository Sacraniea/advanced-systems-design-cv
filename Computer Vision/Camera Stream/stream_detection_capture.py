"""Save one original photo per confirmed marker sighting for later upload."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from math import isfinite
import os
from pathlib import Path

import cv2


@dataclass
class _Sighting:
    # Store when this marker appeared/disappeared and whether its photo was handled.
    visible_since: float | None = None
    absent_since: float | None = None
    handled: bool = False
    last_handled_at: float | None = None


def _write_new_atomic(path: Path, data: bytes) -> None:
    """Finish writing a new file before making its final name visible."""
    temporary = path.with_name("." + path.name + ".tmp")
    created = False

    try:
        with temporary.open("xb") as output:
            # Write all bytes to a new temporary file before exposing the final name.
            created = True
            output.write(data)

        # Link the finished file into place without overwriting an existing capture.
        os.link(temporary, path)

    finally:
        if created:
            # Remove only the temporary file created by this call.
            temporary.unlink(missing_ok=True)


class DetectionCapture:
    """Choose sightings to save while limiting pending photos and disk usage."""

    def __init__(
        self,
        directory: Path,
        *,
        stable_s=0.3,
        absence_s=2.0,
        cooldown_s=10.0,
        max_pending=3,
        max_storage_bytes=256 * 1024 * 1024,
    ):
        # Reject invalid sighting delays before creating any capture files.
        for name, value in [
            ("stable_s", stable_s),
            ("absence_s", absence_s),
            ("cooldown_s", cooldown_s),
        ]:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be finite and nonnegative")

        # Require positive whole-number limits for queued photos and stored bytes.
        for name, value in [
            ("max_pending", max_pending),
            ("max_storage_bytes", max_storage_bytes),
        ]:
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

        self.policy = dict(
            stable_s=stable_s,
            absence_s=absence_s,
            cooldown_s=cooldown_s,
            max_pending=max_pending,
            max_storage_bytes=max_storage_bytes,
        )
        self.stats = dict(
            captured_events=0,
            captured_marker_sightings=0,
            image_bytes=0,
            skipped_pending=0,
            skipped_storage=0,
            failed_events=0,
            audit_failures=0,
        )
        self.directory = Path(directory)
        # Create a new capture directory; refuse an existing folder from another run.
        self.directory.mkdir(parents=True, exist_ok=False)
        self._audit_file = (self.directory / "capture-audit.jsonl").open(
            "x", encoding="utf-8"
        )
        self._states: dict[tuple[str, int], _Sighting] = {}
        self._run_id = None
        self._closed = False
        self._last_error = None

    def _audit(self, entry):
        # Append this capture outcome to the audit log and make it visible immediately.
        try:
            self._audit_file.write(json.dumps(entry, allow_nan=False) + "\n")
            self._audit_file.flush()

        except Exception as error:
            # Keep the camera running if audit writing fails; retain the latest error.
            self.stats["audit_failures"] += 1
            self._last_error = f"{type(error).__name__}: {error}"[:1024]

    def _pending_count(self):
        # Count event files that have neither a successful-send nor failed-send receipt.
        return sum(
            not path.with_suffix(".sent.json").exists()
            and not path.with_suffix(".failed.json").exists()
            for path in self.directory.glob("*.event.json")
        )

    def _validate_identity(self, packet, record):
        # Reject records that cannot be matched to this original image and camera run.
        if record.get("producer") != "camera_stream":
            raise ValueError("Capture requires a native camera_stream record")

        # Compare frame number, both timestamps and image dimensions; reject a mismatch.
        if (
            record.get("sequence_number") != packet.sequence_number
            or record.get("device_timestamp_s") != packet.device_timestamp_s
            or record.get("host_received_timestamp_s")
            != packet.host_received_timestamp_s
            or list(record.get("image_size_px", []))
            != [packet.image.shape[1], packet.image.shape[0]]
        ):
            raise ValueError("Capture record identity does not match original packet")

        if type(packet.sequence_number) is not int or packet.sequence_number < 0:
            raise ValueError("Capture sequence_number must be a nonnegative integer")

        if not isfinite(packet.host_received_timestamp_s):
            raise ValueError("Capture host timestamp must be finite")

        run_id = record.get("run_id")

        if not isinstance(run_id, str) or not run_id:
            raise ValueError("Capture requires a nonempty run_id")

        if not isinstance(record.get("source_kind"), str) or not record["source_kind"]:
            raise ValueError("Capture requires an explicit source_kind")

        if self._run_id is not None and self._run_id != run_id:
            raise ValueError("Capture directory belongs to a different run_id")

        self._run_id = run_id

    def _observed(self, record):
        # Find the ArUco processor's results and collect its decoded marker IDs.
        processor = next(
            (p for p in record.get("processors", []) if p.get("processor") == "aruco"),
            None,
        )

        if processor is None or processor.get("failure_reason"):
            # Return no observation if ArUco was absent or the processor failed.
            return None

        data = processor["data"]
        dictionary = data["config"]["dictionary_name"]

        if dictionary != "DICT_5X5_100":
            # Reject a dictionary whose IDs this capture policy does not support.
            raise ValueError("Detection capture supports DICT_5X5_100 only")

        observed = set()

        for result in data.get("results", []):
            marker_id = result["marker_id"]

            if type(marker_id) is not int or not 0 <= marker_id < 100:
                raise ValueError("Decoded marker ID must be an integer from 0 to 99")

            # Keep the decoded marker ID even when its pose was rejected.
            observed.add((dictionary, marker_id))

        return observed

    def __call__(self, packet, record):
        # Update marker sightings and save this original frame if any sighting is due.
        if self._closed:
            return None

        selected = []
        event_id = f'{record.get("run_id")}:{packet.sequence_number}'

        try:
            self._validate_identity(packet, record)
            observed = self._observed(record)

            if observed is None:
                # Reset visibility timers when processing gives no usable observation.
                for state in self._states.values():
                    state.visible_since = state.absent_since = None

                return None

            # Use the frame's elapsed host time to measure visibility and cooldowns.
            now = packet.host_received_timestamp_s

            for key, state in self._states.items():
                if key not in observed:
                    # Clear this marker's visible timer and start its absence timer.
                    state.visible_since = None

                    if state.absent_since is None:
                        state.absent_since = now

            for key in sorted(observed):
                state = self._states.setdefault(key, _Sighting())

                if state.absent_since is not None:
                    # Allow a new photo if this marker was absent for the required time.
                    if now - state.absent_since >= self.policy["absence_s"]:
                        state.handled = False

                    state.absent_since = None

                if state.visible_since is None:
                    # Start measuring how long this marker stays continuously visible.
                    state.visible_since = now

                # Check whether this marker's cooldown has finished.
                cooldown_elapsed = (
                    state.last_handled_at is None
                    or now - state.last_handled_at >= self.policy["cooldown_s"]
                )

                # Save once it stays visible long enough and its cooldown has passed.
                if (
                    not state.handled
                    and cooldown_elapsed
                    and now - state.visible_since >= self.policy["stable_s"]
                ):
                    selected.append(key[1])
                    # Mark this sighting handled before attempting the save.
                    # Another attempt needs the marker to disappear and return.
                    state.handled = True
                    state.last_handled_at = now

            if not selected:
                return None

            entry = dict(
                event_id=event_id,
                run_id=record["run_id"],
                sequence_number=packet.sequence_number,
                marker_ids=selected,
            )

            if self._pending_count() >= self.policy["max_pending"]:
                # Record PENDING_LIMIT and skip saving when the upload queue is full.
                self.stats["skipped_pending"] += 1
                self._audit(dict(entry, status="skipped", reason="PENDING_LIMIT"))
                return None

            # Record the current UTC selection time separately from frame capture time.
            observed_at_utc = (
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            )
            # Encode the original image as PNG, then check its storage size.
            success, encoded = cv2.imencode(".png", packet.image)

            if not success:
                raise ValueError("Original PNG encoding failed")

            original = encoded.tobytes()

            if (
                self.stats["image_bytes"] + len(original)
                > self.policy["max_storage_bytes"]
            ):
                # Skip this photo and record STORAGE_LIMIT if it would exceed the limit.
                self.stats["skipped_storage"] += 1
                self._audit(
                    dict(
                        entry,
                        status="skipped",
                        reason="STORAGE_LIMIT",
                        image_bytes=len(original),
                    )
                )
                return None

            image_file = f"frame-{packet.sequence_number}.png"
            sidecar = self.directory / f"frame-{packet.sequence_number}.event.json"
            # Package the photo name, byte count and fingerprint with its frame results.
            metadata = dict(
                schema_version=1,
                **entry,
                dictionary_name="DICT_5X5_100",
                source_kind=record["source_kind"],
                observed_at_utc=observed_at_utc,
                timestamp_basis="host_selection_time_not_camera_exposure",
                image_file=image_file,
                image_sha256=hashlib.sha256(original).hexdigest(),
                image_bytes=len(original),
                record=record,
            )
            serialized = (
                json.dumps(metadata, allow_nan=False, indent=2) + "\n"
            ).encode("utf-8")

            if sidecar.exists():
                raise FileExistsError(f"Capture sidecar already exists: {sidecar.name}")

            _write_new_atomic(self.directory / image_file, original)
            # Count the saved image's bytes before attempting the companion JSON file.
            self.stats["image_bytes"] += len(original)
            # Write the event file last so the uploader only sees a completed photo.
            _write_new_atomic(sidecar, serialized)
            self.stats["captured_events"] += 1
            self.stats["captured_marker_sightings"] += len(selected)
            self._audit(
                dict(
                    entry,
                    status="captured",
                    image_file=image_file,
                    image_bytes=len(original),
                    sidecar=sidecar.name,
                )
            )
            return sidecar

        except Exception as error:
            self.stats["failed_events"] += 1
            # Keep the camera running and record why this capture couldn't be saved.
            self._last_error = f"{type(error).__name__}: {error}"[:1024]
            self._audit(
                dict(
                    event_id=event_id,
                    marker_ids=selected,
                    status="failed",
                    reason="CAPTURE_FAILED",
                    error=self._last_error,
                )
            )
            return None

    def close(self):
        """Save the capture totals and close the log, keeping every original photo."""

        if self._closed:
            return

        self._closed = True

        try:
            summary = dict(
                schema_version=1,
                status="closed",
                run_id=self._run_id,
                policy=self.policy,
                stats=self.stats,
                last_error=self._last_error,
                pending_events=self._pending_count(),
            )
            _write_new_atomic(
                self.directory / "summary.json",
                (json.dumps(summary, allow_nan=False, indent=2) + "\n").encode("utf-8"),
            )

        except Exception as error:
            self.stats["audit_failures"] += 1
            self._last_error = f"{type(error).__name__}: {error}"[:1024]
            self._audit(
                dict(status="failed", reason="SUMMARY_FAILED", error=self._last_error)
            )

        finally:
            # Close the audit file even if writing the final summary failed.
            try:
                self._audit_file.close()

            except Exception as error:
                self.stats["audit_failures"] += 1
                self._last_error = f"{type(error).__name__}: {error}"[:1024]

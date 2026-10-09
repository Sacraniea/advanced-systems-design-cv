import unittest
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import patch

import depthai as dai

from oak_benchmark import (
    _rename_directory_no_replace,
    _write_comparison_atomically,
    benchmark_provenance,
    build_parser,
    build_pipeline,
    camera_feature_to_dict,
    collect_device,
    collect_host,
    collect_host_power,
    create_run_document,
    enum_name,
    memory_to_dict,
    main,
    message_sample,
    profile_definitions,
    run_profile,
    system_sample,
    temperature_to_dict,
    validate_run_limits,
    write_run_atomically,
)
from oak_metrics import (
    compare_runs,
    make_metric,
    nearest_rank,
    render_markdown,
    summarize_frames,
)


class CliAndStorageTests(unittest.TestCase):
    def test_run_command_records_connection_description(self):
        args = build_parser().parse_args(
            [
                "run",
                "--host-label",
                "Pi 5",
                "--hub",
                "none",
                "--port",
                "blue USB-A",
                "--cable",
                "current USB2 cable",
                "--power",
                "official 27W PSU",
            ]
        )
        self.assertEqual(args.command, "run")
        self.assertEqual(args.cable, "current USB2 cable")
        self.assertEqual(args.duration_s, 10.0)

    def test_write_run_refuses_to_overwrite_an_existing_id(self):
        # Save the same run ID twice and check the second write raises without losing
        # either file.
        run = {"run_id": "fixed-id", "schema_version": "1.0"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run_atomically(run, root)

            with self.assertRaises(FileExistsError):
                write_run_atomically(run, root)

            self.assertTrue((root / "fixed-id" / "raw.json").exists())
            self.assertTrue((root / "fixed-id" / "summary.md").exists())

    def test_render_failure_keeps_unique_failed_evidence_record(self):
        # Force rendering to raise and verify the saved JSON records the error and
        # failed status.
        failure = RuntimeError("markdown renderer exploded exactly")
        run = {
            "run_id": "render-failed-id",
            "schema_version": "1.0",
            "status": "complete",
            "warnings": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            output = write_run_atomically(
                run,
                Path(directory),
                renderer=lambda document: (_ for _ in ()).throw(failure),
            )
            stored = json.loads((output / "raw.json").read_text())
            self.assertEqual(stored["schema_version"], "1.0")
            self.assertEqual(stored["status"], "failed")
            self.assertEqual(
                stored["errors"],
                [{"type": "RuntimeError", "message": str(failure)}],
            )
            self.assertTrue((output / "summary.md").exists())

    def test_command_exits_one_from_stored_render_failure(self):
        # Fail summary rendering through the CLI and check the saved failure controls
        # its exit code.
        failure = RuntimeError("command renderer exploded exactly")
        run = {
            "run_id": "command-render-failed-id",
            "schema_version": "1.0",
            "status": "complete",
            "warnings": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            exit_code = main(
                ["inventory", "--results-root", directory],
                document_creator=lambda args: run,
                run_renderer=lambda document: (_ for _ in ()).throw(failure),
                printer=lambda message: None,
            )
            output = Path(directory) / run["run_id"]
            stored = json.loads((output / "raw.json").read_text())
            summary = (output / "summary.md").read_text()
            self.assertEqual(exit_code, 1)
            self.assertEqual(stored["status"], "failed")
            self.assertEqual(
                stored["errors"],
                [{"type": "RuntimeError", "message": str(failure)}],
            )
            self.assertIn("RuntimeError: command renderer exploded exactly", summary)

    def test_run_pair_faults_before_directory_rename_leave_final_absent(self):
        # Raise at each save checkpoint and verify that no final result folder appears.
        run = {
            "run_id": "faulted-run",
            "schema_version": "1.0",
            "status": "complete",
            "warnings": [],
        }
        fault_steps = (
            "after-write:raw.json",
            "after-write:summary.md",
            "after-staging-fsync",
            "before-rename",
        )

        for fault_step in fault_steps:
            with self.subTest(
                fault_step=fault_step
            ), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)

                def inject(step, staging):
                    self.assertFalse((root / run["run_id"]).exists())

                    if step == fault_step:
                        raise RuntimeError(f"fault at {step}")

                with self.assertRaisesRegex(RuntimeError, f"fault at {fault_step}"):
                    write_run_atomically(run, root, publication_fault=inject)

                self.assertFalse((root / run["run_id"]).exists())

    def test_comparison_uses_same_sibling_staging_pair_publisher(self):
        # Fail comparison publication before rename and verify the final directory stays
        # absent.
        document = {
            "schema_version": "1.0",
            "comparison_id": "faulted-comparison",
            "baseline_source": "baseline.json",
            "candidate_source": "candidate.json",
            "comparison": {},
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed = []

            def inject(step, staging):
                observed.append((step, staging.parent, staging.name))
                self.assertFalse((root / document["comparison_id"]).exists())

                if step == "before-rename":
                    raise RuntimeError("comparison publish fault")

            with self.assertRaisesRegex(RuntimeError, "comparison publish fault"):
                _write_comparison_atomically(document, root, publication_fault=inject)

            self.assertFalse((root / document["comparison_id"]).exists())
            self.assertEqual(observed[-1][0], "before-rename")
            self.assertEqual(observed[-1][1], root)
            self.assertTrue(observed[-1][2].startswith(".faulted-comparison.staging-"))

    def test_atomic_directory_rename_never_replaces_even_an_empty_final_directory(self):
        # Pre-create an empty final folder and check that publication refuses to replace
        # it.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / ".run.staging-test"
            final = root / "run"
            staging.mkdir()
            (staging / "raw.json").write_text("evidence")
            final.mkdir()

            with self.assertRaises(FileExistsError):
                _rename_directory_no_replace(staging, final)

            self.assertTrue(staging.exists())
            self.assertEqual(list(final.iterdir()), [])


class FakeInventoryContext:
    def __init__(self, device, events):
        self.device = device
        self.events = events

    def __enter__(self):
        self.events.append("inventory-open")
        return self.device

    def __exit__(self, exc_type, exc, traceback):
        self.events.append("inventory-close")


class FakeDeviceInfo:
    def __init__(self, mxid):
        self.mxid = mxid

    def getMxId(self):
        return self.mxid


def valid_inventory(mxid="mxid-selected"):
    # Provide the expected unit ID and CAM_A/B/C sensors for orchestration tests.
    return {
        "mxid": make_metric(
            mxid,
            "",
            "observed",
            (),
            "getMxId",
            [],
        ),
        "product_name": make_metric(
            "OAK-D-LITE",
            "",
            "observed",
            (),
            "getProductName",
            [],
        ),
        "usb_speed": make_metric(
            "HIGH",
            "DepthAI enum",
            "observed",
            ("*",),
            "inventory",
            ["HIGH"],
        ),
        "camera_features": [
            {"socket": "CAM_A", "sensor_name": "IMX214"},
            {"socket": "CAM_B", "sensor_name": "OV7251"},
            {"socket": "CAM_C", "sensor_name": "OV7251"},
        ],
    }


def orchestration_args(*extra):
    # Select two short profiles and disable stability unless a test adds overriding
    # arguments.
    return build_parser().parse_args(
        [
            "run",
            "--host-label",
            "Pi 5",
            "--profiles",
            "rgb_preview_640x360_30,mono_pair_640x480_30",
            "--stability-duration-s",
            "0",
            *extra,
        ]
    )


class CliOrchestrationTests(unittest.TestCase):
    def dependencies(self, *, events=None, profile_results=None):
        # Supply fake discovery, inventory and profile results while recording their
        # call order.
        events = events if events is not None else []
        device_info = FakeDeviceInfo("mxid-selected")
        inventory_device = object()

        def collect_inventory(device, dai_module):
            self.assertIs(device, inventory_device)
            events.append("inventory-collect")
            return valid_inventory(), {}

        def run_one(selected, profile_id, duration_s, temperature_stop_c, **options):
            self.assertIs(selected, device_info)
            self.assertEqual(options["inventory_usb_speed"], "HIGH")
            events.append(
                ("profile", profile_id, duration_s, options["allow_long_test"])
            )

            # Use a supplied result or raise its supplied exception for this profile.
            if profile_results and profile_id in profile_results:
                value = profile_results[profile_id]

                if isinstance(value, Exception):
                    raise value

                return value

            return {"profile_id": profile_id, "status": "complete", "errors": []}

        return {
            "host_collector": lambda: {"platform": "test-host"},
            "device_collector": collect_inventory,
            "profile_runner": run_one,
            "device_discovery": lambda: [device_info],
            "inventory_device_factory": lambda selected: FakeInventoryContext(
                inventory_device,
                events,
            ),
            "dai_module": SimpleNamespace(__version__="2.32.0.0"),
            "printer": lambda message: None,
        }

    def test_no_device_produces_immutable_failed_record_and_exit_one(self):
        # Return no discovered devices and check the CLI saves failed access evidence.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            creator = lambda args: create_run_document(
                args,
                dai_module=SimpleNamespace(__version__="2.32.0.0"),
                host_collector=lambda: {"platform": "test-host"},
                device_discovery=lambda: [],
                printer=lambda message: None,
            )
            exit_code = main(
                ["inventory", "--results-root", str(root)],
                document_creator=creator,
                printer=lambda message: None,
            )
            run_directories = list(root.iterdir())
            self.assertEqual(exit_code, 1)
            self.assertEqual(len(run_directories), 1)
            record = json.loads((run_directories[0] / "raw.json").read_text())
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["device"]["status"], "unavailable")
            self.assertEqual(
                record["device"]["error"]["message"],
                "No OAK device discovered",
            )

    def test_inventory_remains_available_without_numpy(self):
        # Make NumPy import fail while checking the inventory command can still collect
        # evidence.
        events = []
        dependencies = self.dependencies(events=events)
        real_import = __import__

        def import_without_numpy(name, *args, **kwargs):
            if name == "numpy":
                raise ModuleNotFoundError("No module named 'numpy'")

            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_without_numpy):
            record = create_run_document(
                build_parser().parse_args(["inventory"]),
                **dependencies,
            )

        self.assertEqual(record["status"], "complete")
        self.assertEqual(
            events,
            ["inventory-open", "inventory-collect", "inventory-close"],
        )

    def test_device_owned_exception_preserves_exact_error_and_exit_one(self):
        # Raise during inventory collection and check its exact error reaches the saved
        # record.
        failure = RuntimeError("X_LINK_DEVICE_ALREADY_IN_USE: owned by DepthAI Viewer")
        device_info = FakeDeviceInfo("mxid-owned")
        args = build_parser().parse_args(["inventory"])
        record = create_run_document(
            args,
            dai_module=SimpleNamespace(__version__="2.32.0.0"),
            host_collector=lambda: {"platform": "test-host"},
            device_discovery=lambda: [device_info],
            inventory_device_factory=lambda selected: (_ for _ in ()).throw(failure),
            printer=lambda message: None,
        )
        self.assertEqual(record["status"], "failed")
        self.assertEqual(
            record["device"]["error"],
            {"type": "RuntimeError", "message": str(failure)},
        )

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                main(
                    ["inventory", "--results-root", directory],
                    document_creator=lambda parsed: record,
                    printer=lambda message: None,
                ),
                1,
            )

    def test_inventory_closes_before_profiles_and_passes_explicit_inputs(self):
        # Check that inventory closes before profiles start and the explicit run inputs
        # match.
        events = []
        dependencies = self.dependencies(events=events)
        record = create_run_document(
            orchestration_args("--allow-long-test"),
            **dependencies,
        )
        self.assertEqual(
            events,
            [
                "inventory-open",
                "inventory-collect",
                "inventory-close",
                ("profile", "rgb_preview_640x360_30", 10.0, True),
                ("profile", "mono_pair_640x480_30", 10.0, True),
            ],
        )
        self.assertEqual(
            list(record["tests"]),
            ["rgb_preview_640x360_30", "mono_pair_640x480_30"],
        )

    def test_mxid_selects_one_matching_device_and_inventory_verifies_identity(self):
        # Discover multiple devices and check the requested MXID selects and verifies
        # one unit.
        first = FakeDeviceInfo("mxid-other")
        selected = FakeDeviceInfo("mxid-selected")
        opened = []

        args = build_parser().parse_args(["inventory", "--mxid", "mxid-selected"])
        record = create_run_document(
            args,
            dai_module=SimpleNamespace(__version__="2.32.0.0"),
            host_collector=lambda: {"platform": "test-host"},
            device_collector=lambda device, module: (valid_inventory(), {}),
            device_discovery=lambda: [first, selected],
            inventory_device_factory=lambda device_info: (
                opened.append(device_info) or FakeInventoryContext(object(), [])
            ),
            printer=lambda message: None,
        )

        self.assertEqual(opened, [selected])
        self.assertEqual(record["status"], "complete")
        self.assertEqual(
            record["device"]["identity_verification"]["status"],
            "verified",
        )
        self.assertEqual(
            record["device"]["identity_verification"]["selected_mxid"],
            "mxid-selected",
        )

    def test_omitted_mxid_requires_exactly_one_discovered_device(self):
        # Omit the MXID with multiple devices and check selection fails before inventory
        # opens.
        opened = []
        record = create_run_document(
            build_parser().parse_args(["inventory"]),
            dai_module=SimpleNamespace(__version__="2.32.0.0"),
            host_collector=lambda: {"platform": "test-host"},
            device_discovery=lambda: [FakeDeviceInfo("one"), FakeDeviceInfo("two")],
            inventory_device_factory=lambda device_info: opened.append(device_info),
            printer=lambda message: None,
        )

        self.assertEqual(opened, [])
        self.assertEqual(record["status"], "failed")
        self.assertIn(
            "exactly one discovered device",
            record["device"]["error"]["message"],
        )

    def test_inventory_identity_mismatch_is_failed_evidence_and_stops_profiles(self):
        # Supply the wrong camera sensors and check the failed inventory prevents
        # profile runs.
        events = []
        dependencies = self.dependencies(events=events)

        def mismatched_inventory(device, module):
            evidence = valid_inventory()
            evidence["product_name"]["value"] = "OAK-D-PRO"
            evidence["camera_features"][2]["sensor_name"] = "WRONG-SENSOR"
            return evidence, {"eeprom_json": "retained"}

        dependencies["device_collector"] = mismatched_inventory
        record = create_run_document(orchestration_args(), **dependencies)

        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["device"]["product_name"]["value"], "OAK-D-PRO")
        self.assertEqual(record["calibration"], {"eeprom_json": "retained"})
        verification = record["device"]["identity_verification"]
        self.assertEqual(verification["status"], "failed")
        self.assertIn("product OAK-D-LITE", verification["errors"])
        self.assertIn("CAM_C sensor OV7251", verification["errors"])
        self.assertEqual(record["tests"], {})

    def test_duplicate_profiles_are_rejected_before_discovery(self):
        # Request a repeated profile ID and verify it is rejected before hardware
        # discovery.
        discoveries = []
        args = build_parser().parse_args(
            [
                "run",
                "--profiles",
                "rgb_preview_640x360_30,rgb_preview_640x360_30",
                "--stability-duration-s",
                "0",
            ]
        )

        with self.assertRaisesRegex(ValueError, "Duplicate profile ID"):
            create_run_document(
                args,
                dai_module=SimpleNamespace(__version__="2.32.0.0"),
                host_collector=lambda: {"platform": "test-host"},
                device_discovery=lambda: discoveries.append("discovery") or [],
                printer=lambda message: None,
            )

        self.assertEqual(discoveries, [])

    def test_stability_repeats_only_combined_and_zero_disables_it(self):
        # Compare enabled and zero-duration stability runs using the recorded profile
        # calls.
        for stability_duration, expected_durations in ((0, []), (42, [42.0])):
            with self.subTest(stability_duration=stability_duration):
                events = []
                dependencies = self.dependencies(events=events)
                args = build_parser().parse_args(
                    [
                        "run",
                        "--profiles",
                        "rgb_preview_640x360_30",
                        "--duration-s",
                        "3",
                        "--stability-duration-s",
                        str(stability_duration),
                    ]
                )
                record = create_run_document(args, **dependencies)
                profile_events = [event for event in events if isinstance(event, tuple)]
                self.assertEqual(
                    profile_events[0][1:3],
                    ("rgb_preview_640x360_30", 3.0),
                )
                self.assertEqual(
                    [
                        duration
                        for _, profile, duration, _ in profile_events[1:]
                        if profile == "combined_h264_depth_system_30"
                    ],
                    expected_durations,
                )
                expected_keys = ["rgb_preview_640x360_30"]

                if stability_duration:
                    expected_keys.append("combined_h264_depth_system_30_stability")

                self.assertEqual(list(record["tests"]), expected_keys)

    def test_default_durations_run_profiles_for_ten_and_stability_for_sixty(self):
        # Use CLI defaults and check regular profiles receive 10 seconds and stability
        # receives 60.
        events = []
        args = build_parser().parse_args(
            ["run", "--profiles", "rgb_preview_640x360_30"]
        )
        record = create_run_document(args, **self.dependencies(events=events))
        self.assertEqual(
            [(event[1], event[2]) for event in events if isinstance(event, tuple)],
            [
                ("rgb_preview_640x360_30", 10.0),
                ("combined_h264_depth_system_30", 60.0),
            ],
        )
        self.assertEqual(
            list(record["tests"]),
            [
                "rgb_preview_640x360_30",
                "combined_h264_depth_system_30_stability",
            ],
        )

    def test_profile_exception_is_recorded_and_later_profile_continues(self):
        # Fail the first profile and check its error is retained while the second
        # profile runs.
        events = []
        dependencies = self.dependencies(
            events=events,
            profile_results={
                "rgb_preview_640x360_30": RuntimeError("profile exploded exactly")
            },
        )
        record = create_run_document(orchestration_args(), **dependencies)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(
            record["tests"]["rgb_preview_640x360_30"]["errors"],
            [{"type": "RuntimeError", "message": "profile exploded exactly"}],
        )
        self.assertEqual(record["tests"]["mono_pair_640x480_30"]["status"], "complete")
        self.assertEqual(
            [event[1] for event in events if isinstance(event, tuple)],
            ["rgb_preview_640x360_30", "mono_pair_640x480_30"],
        )

    def test_unsupported_only_run_is_successful_complete_with_unsupported_evidence(
        self,
    ):
        # Return unsupported for a profile and check the saved overall status still
        # permits exit 0.
        unsupported = {
            "profile_id": "rgb_preview_640x360_30",
            "status": "unsupported",
            "exception_phase": "pipeline-build",
            "errors": [
                {
                    "type": "RuntimeError",
                    "message": "profile is not supported by this firmware",
                }
            ],
        }
        dependencies = self.dependencies(
            profile_results={
                "rgb_preview_640x360_30": unsupported,
                "mono_pair_640x480_30": unsupported,
            }
        )
        record = create_run_document(orchestration_args(), **dependencies)

        self.assertEqual(record["status"], "complete-with-unsupported")

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                main(
                    ["run", "--results-root", directory],
                    document_creator=lambda args: record,
                    printer=lambda message: None,
                ),
                0,
            )

    def test_each_run_contains_labeled_datasheet_claims_and_ambiguities(self):
        # Check each run stores manufacturer claims, ambiguity markers and benchmark
        # provenance.
        record = create_run_document(orchestration_args(), **self.dependencies())
        claims = record["manufacturer_claims"]
        self.assertEqual(claims["dimensions"]["value"], [91, 28, 17.5])
        self.assertEqual(claims["ambient_max"]["value"], 50)
        self.assertEqual(claims["rgb_camera"]["resolution"]["value"], [4208, 3120])
        self.assertEqual(claims["mono_camera"]["max_fps"]["value"], 120)
        self.assertTrue(
            all(
                "manufacturer datasheet claim" in metric["method"]
                for metric in (
                    claims["dimensions"],
                    claims["stereo_baseline"],
                    claims["ambient_max"],
                )
            )
        )
        self.assertEqual(claims["usb_interface"]["markers"], ["‡"])
        self.assertEqual(claims["usb2_max_wording"]["markers"], ["‡"])
        self.assertNotIn("‡ ‡", render_markdown(record))
        provenance = record["benchmark_provenance"]
        self.assertEqual(provenance["version"], "1.0.0")
        self.assertEqual(
            set(provenance["source_sha256"]),
            {"oak_benchmark.py", "oak_metrics.py", "requirements.txt"},
        )

    def test_compare_validates_schema_and_preserves_source_files(self):
        # Compare temporary run files, check both sources remain intact, then try a bad
        # schema.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            original_baseline = json.dumps(
                {
                    "schema_version": "1.0",
                    "run_id": "base",
                    "device": {
                        "usb_speed": make_metric(
                            "HIGH",
                            "DepthAI enum",
                            "observed",
                            ("*",),
                            "test",
                            [],
                        )
                    },
                }
            )
            original_candidate = json.dumps(
                {
                    "schema_version": "1.0",
                    "run_id": "candidate",
                    "device": {
                        "usb_speed": make_metric(
                            "SUPER",
                            "DepthAI enum",
                            "observed",
                            (),
                            "test",
                            [],
                        )
                    },
                }
            )
            baseline.write_text(original_baseline)
            candidate.write_text(original_candidate)
            output_root = root / "comparisons"
            exit_code = main(
                [
                    "compare",
                    str(baseline),
                    str(candidate),
                    "--results-root",
                    str(output_root),
                ],
                printer=lambda message: None,
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(baseline.read_text(), original_baseline)
            self.assertEqual(candidate.read_text(), original_candidate)
            output = next(output_root.iterdir())
            self.assertTrue((output / "comparison.json").exists())
            self.assertTrue((output / "comparison.md").exists())

            # Change the candidate schema and verify comparison returns failure.
            candidate.write_text(json.dumps({"schema_version": "2.0"}))
            self.assertEqual(
                main(
                    [
                        "compare",
                        str(baseline),
                        str(candidate),
                        "--results-root",
                        str(root / "invalid"),
                    ],
                    printer=lambda message: None,
                ),
                1,
            )


class ProfileDefinitionTests(unittest.TestCase):
    def test_required_profiles_are_stable(self):
        self.assertEqual(
            set(profile_definitions()),
            {
                "rgb_preview_640x360_30",
                "rgb_video_1280x720_30",
                "rgb_video_1920x1080_30",
                "mono_pair_640x480_30",
                "stereo_depth_640x480_30",
                "h264_rgb_1920x1080_30",
                "combined_h264_depth_system_30",
            },
        )

    def test_each_profile_builds_without_a_connected_camera(self):
        # Build each pipeline graph and check it contains nodes and the system stream.
        for profile_id in profile_definitions():
            pipeline, contract = build_pipeline(profile_id, dai)
            self.assertGreater(len(pipeline.getAllNodes()), 0)
            self.assertIn("system", contract["streams"])
            self.assertEqual(contract["markers"], ["*"])


class FakeFeature:
    socket = dai.CameraBoardSocket.CAM_A
    sensorName = "IMX214"
    width = 4208
    height = 3120
    orientation = "AUTO"
    supportedTypes = ["COLOR"]
    hasAutofocus = True
    hasAutofocusIC = True
    name = "color"


class FakeMemory:
    used = 10
    total = 100
    remaining = 90


class FakeTemperature:
    average = 45.0
    css = 44.0
    mss = 45.0
    upa = 46.0
    dss = 43.0


class FakeMessage:
    def getSequenceNum(self):
        return 7

    def getTimestamp(self):
        return timedelta(seconds=4.9)

    def getData(self):
        return bytes(1200)


class FakeCpu:
    average = 0.25


class FakeSystemMessage:
    chipTemperature = FakeTemperature()
    ddrMemoryUsage = FakeMemory()
    cmxMemoryUsage = FakeMemory()
    leonCssMemoryUsage = FakeMemory()
    leonMssMemoryUsage = FakeMemory()
    leonCssCpuUsage = FakeCpu()
    leonMssCpuUsage = FakeCpu()


class SampleExtractionTests(unittest.TestCase):
    def test_message_sample_keeps_raw_timing_inputs(self):
        sample = message_sample(FakeMessage(), host_s=5.0)
        self.assertEqual(
            sample,
            {
                "host_s": 5.0,
                "device_s": 4.9,
                "sequence": 7,
                "payload_bytes": 1200,
            },
        )

    def test_system_sample_uses_fractional_cpu_and_bytes(self):
        sample = system_sample(FakeSystemMessage(), host_s=5.0)
        self.assertEqual(sample["temperature_c"]["average_c"], 45.0)
        self.assertEqual(sample["leon_css_cpu_fraction"], 0.25)
        self.assertEqual(sample["ddr_memory"]["total_bytes"], 100)


class RunLimitTests(unittest.TestCase):
    def test_duration_must_be_finite_and_greater_than_zero_even_when_authorized(self):
        # Try invalid durations with the long-run flag enabled and check each is
        # rejected.
        for duration_s in (0, -1, float("nan"), float("inf"), float("-inf")):
            with self.subTest(duration_s=duration_s):
                with self.assertRaisesRegex(
                    ValueError,
                    "duration must be finite and greater than 0",
                ):
                    validate_run_limits(
                        duration_s=duration_s,
                        allow_long_test=True,
                        temperature_stop_c=100,
                    )

    def test_long_run_requires_explicit_permission(self):
        # Check that a duration above 300 seconds requires the explicit long-run flag.
        with self.assertRaisesRegex(ValueError, "--allow-long-test"):
            validate_run_limits(
                duration_s=301,
                allow_long_test=False,
                temperature_stop_c=100,
            )

    def test_default_guard_is_below_hardware_shutdown(self):
        result = validate_run_limits(
            duration_s=10,
            allow_long_test=False,
            temperature_stop_c=100,
        )
        self.assertEqual(result, (10.0, 100.0))

    def test_guard_cannot_disable_automatic_shutdown_margin(self):
        with self.assertRaisesRegex(ValueError, "below 105") as raised:
            validate_run_limits(
                duration_s=10,
                allow_long_test=False,
                temperature_stop_c=105,
            )

        self.assertIn("maximum allowed is 100", str(raised.exception))

    def test_guard_above_100_is_rejected(self):
        for temperature_stop_c in (100.1, 104.0):
            with self.subTest(temperature_stop_c=temperature_stop_c):
                with self.assertRaisesRegex(ValueError, "maximum allowed is 100"):
                    validate_run_limits(
                        duration_s=10,
                        allow_long_test=False,
                        temperature_stop_c=temperature_stop_c,
                    )

    def test_guard_must_be_finite(self):
        # Try each non-finite thermal limit and check validation raises before a run
        # begins.
        for temperature_stop_c in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(temperature_stop_c=temperature_stop_c):
                with self.assertRaisesRegex(ValueError, "finite"):
                    validate_run_limits(
                        duration_s=10,
                        allow_long_test=False,
                        temperature_stop_c=temperature_stop_c,
                    )


class CliNumericValidationTests(unittest.TestCase):
    def assert_rejected_before_discovery(self, arguments):
        # Replace discovery with a failing callback and verify numeric validation runs
        # first.
        discovery_calls = []

        def forbidden_discovery():
            discovery_calls.append(True)
            raise AssertionError("discovery must not run for invalid numeric input")

        args = build_parser().parse_args(arguments)

        with self.assertRaises(ValueError):
            create_run_document(
                args,
                dai_module=SimpleNamespace(__version__="2.32.0.0"),
                host_collector=lambda: {"platform": "test-host"},
                device_discovery=forbidden_discovery,
                printer=lambda message: None,
            )

        self.assertEqual(discovery_calls, [])

    def test_invalid_run_numbers_are_rejected_before_discovery(self):
        # Try invalid durations and temperatures through the CLI before allowing device
        # discovery.
        cases = (
            ["run", "--duration-s", "0"],
            ["run", "--duration-s", "-1", "--allow-long-test"],
            ["run", "--duration-s", "nan", "--allow-long-test"],
            ["run", "--duration-s", "inf", "--allow-long-test"],
            ["run", "--stability-duration-s", "-1"],
            ["run", "--stability-duration-s", "nan"],
            ["run", "--stability-duration-s", "inf", "--allow-long-test"],
            ["run", "--temperature-stop-c", "nan"],
            ["run", "--temperature-stop-c", "100.1"],
            ["run", "--ambient-c", "nan"],
        )

        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.assert_rejected_before_discovery(arguments)

    def test_invalid_inventory_ambient_is_rejected_before_discovery(self):
        # Reject a non-finite ambient temperature even for the inventory-only command.
        for argument in ("nan", "inf", "--ambient-c=-inf"):
            with self.subTest(argument=argument):
                arguments = (
                    ["inventory", argument]
                    if argument.startswith("--ambient-c=")
                    else ["inventory", "--ambient-c", argument]
                )
                self.assert_rejected_before_discovery(arguments)

    def test_run_rejects_missing_numpy_before_device_discovery(self):
        # Make NumPy unavailable and check a timed run fails before discovering devices.
        discovery_calls = []
        real_import = __import__

        def import_without_numpy(name, *args, **kwargs):
            if name == "numpy":
                raise ModuleNotFoundError("No module named 'numpy'")

            return real_import(name, *args, **kwargs)

        args = build_parser().parse_args(
            [
                "run",
                "--profiles",
                "rgb_preview_640x360_30",
                "--stability-duration-s",
                "0",
            ]
        )

        with patch("builtins.__import__", side_effect=import_without_numpy):
            with self.assertRaisesRegex(
                RuntimeError,
                "NumPy is required for stream payload measurement",
            ):
                create_run_document(
                    args,
                    dai_module=SimpleNamespace(__version__="2.32.0.0"),
                    host_collector=lambda: {"platform": "test-host"},
                    device_discovery=lambda: discovery_calls.append(True) or [],
                    printer=lambda message: None,
                )

        self.assertEqual(discovery_calls, [])

    def test_run_json_serialization_rejects_non_finite_values(self):
        # Try saving non-finite result values and check no final evidence directory is
        # published.
        run = {
            "run_id": "non-finite-run",
            "schema_version": "1.0",
            "status": "complete",
            "warnings": [],
            "unexpected_non_finite": float("nan"),
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            with self.assertRaisesRegex(ValueError, "JSON compliant"):
                write_run_atomically(run, root)

            self.assertFalse((root / run["run_id"] / "raw.json").exists())

    def test_comparison_serialization_cannot_persist_infinity(self):
        # Create a comparison containing infinity and check serialization prevents
        # publication.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            metric_prefix = (
                '{"schema_version":"1.0","device":{"usb_speed":'
                '{"value":"HIGH","unit":"DepthAI enum","status":"observed",'
                '"markers":["*"],"method":"test","evidence":[]}},'
                '"tests":{"metric":{"value":'
            )
            metric_suffix = (
                ',"unit":"count","status":"measured","markers":[],'
                '"method":"test","evidence":[]}}}'
            )
            baseline.write_text(metric_prefix + "1" + metric_suffix)
            candidate.write_text(metric_prefix + "1e309" + metric_suffix)
            output_root = root / "comparisons"
            self.assertEqual(
                main(
                    [
                        "compare",
                        str(baseline),
                        str(candidate),
                        "--results-root",
                        str(output_root),
                    ],
                    printer=lambda message: None,
                ),
                1,
            )
            self.assertEqual(list(output_root.rglob("comparison.json")), [])

    def test_compare_rejects_non_standard_json_even_when_value_is_not_compared(self):
        # Put invalid numeric JSON in an unused field and verify comparison still
        # rejects the file.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            baseline.write_text('{"schema_version":"1.0"}')
            candidate.write_text('{"schema_version":"1.0","unused_non_standard":NaN}')
            output_root = root / "comparisons"
            self.assertEqual(
                main(
                    [
                        "compare",
                        str(baseline),
                        str(candidate),
                        "--results-root",
                        str(output_root),
                    ],
                    printer=lambda message: None,
                ),
                1,
            )
            self.assertFalse(output_root.exists())


class FakeClock:
    def __init__(self):
        self.now = 5.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, duration_s):
        self.sleeps.append(duration_s)
        self.now += duration_s


class FakeQueue:
    def __init__(self, values):
        self.values = list(values)

    def tryGet(self):
        if not self.values:
            return None

        # Consume the next queued item; exception items simulate a failed queue read.
        value = self.values.pop(0)

        if isinstance(value, BaseException):
            raise value

        return value


class FakeRunDevice:
    def __init__(self, queues, usb_speed=dai.UsbSpeed.HIGH, exit_callback=None):
        self.queues = queues
        self.usb_speed = usb_speed
        self.exit_callback = exit_callback
        self.queue_requests = []
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exited = True

        if self.exit_callback is not None:
            self.exit_callback()

    def getUsbSpeed(self):
        return self.usb_speed

    def getOutputQueue(self, name, maxSize, blocking):
        # Record queue limits so tests can verify how the runner opened each stream.
        self.queue_requests.append((name, maxSize, blocking))
        return self.queues[name]


def fake_system_message(hot_component=None):
    # Build normal telemetry and optionally set one component to the 100-degree stop
    # limit.
    values = {
        "average": 45.0,
        "css": 44.0,
        "mss": 45.0,
        "upa": 46.0,
        "dss": 43.0,
    }

    if hot_component is not None:
        values[hot_component] = 100.0

    temperature = type("RunTemperature", (), values)()
    cpu = FakeCpu()
    return type(
        "RunSystemMessage",
        (),
        {
            "chipTemperature": temperature,
            "ddrMemoryUsage": FakeMemory(),
            "cmxMemoryUsage": FakeMemory(),
            "leonCssMemoryUsage": FakeMemory(),
            "leonMssMemoryUsage": FakeMemory(),
            "leonCssCpuUsage": cpu,
            "leonMssCpuUsage": cpu,
        },
    )()


class RunProfileTests(unittest.TestCase):
    profile_id = "rgb_preview_640x360_30"

    def run_fake(
        self,
        device,
        *,
        duration_s=0.001,
        device_info=None,
        allow_long_test=False,
        inventory_usb_speed=None,
    ):
        # Run the profile with fake queues and record clock advances, builds and device
        # opens.
        clock = FakeClock()
        pipeline = object()
        selected = device_info if device_info is not None else object()
        build_calls = []
        device_calls = []

        def pipeline_builder(profile_id, dai_module):
            build_calls.append((profile_id, dai_module))
            return pipeline, {
                "streams": {
                    "rgb": {"kind": "image", "requested_fps": 30.0},
                    "system": {"kind": "system", "requested_fps": 1.0},
                },
                "markers": ["*"],
            }

        def device_factory(built_pipeline, selected_device):
            device_calls.append((built_pipeline, selected_device))
            return device

        run_options = {}

        if inventory_usb_speed is not None:
            run_options["inventory_usb_speed"] = inventory_usb_speed

        result = run_profile(
            selected,
            self.profile_id,
            duration_s,
            100,
            dai_module="fake-depthai",
            pipeline_builder=pipeline_builder,
            device_factory=device_factory,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            allow_long_test=allow_long_test,
            **run_options,
        )
        return result, clock, pipeline, selected, build_calls, device_calls

    def test_opens_exact_selected_device_and_pipeline_with_bounded_queues(self):
        # Collect fake RGB/system messages and check device selection, queue limits and
        # metrics.
        selected = object()
        device = FakeRunDevice(
            {
                "rgb": FakeQueue([FakeMessage()]),
                "system": FakeQueue([fake_system_message()]),
            }
        )

        result, clock, pipeline, selected, build_calls, device_calls = self.run_fake(
            device,
            device_info=selected,
        )

        self.assertEqual(device_calls, [(pipeline, selected)])
        self.assertEqual(build_calls, [(self.profile_id, "fake-depthai")])
        self.assertEqual(
            device.queue_requests,
            [("rgb", 4, False), ("system", 4, False)],
        )
        self.assertTrue(device.exited)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["streams"]["rgb"]["samples"][0]["payload_bytes"], 1200)
        self.assertEqual(
            result["streams"]["rgb"]["metrics"]["frames_received"]["value"],
            1,
        )
        self.assertEqual(
            result["system"]["metrics"]["temperature_c"]["average"]["mean_c"]["value"],
            45.0,
        )
        self.assertEqual(
            result["system"]["metrics"]["peak_memory_bytes"]["ddr"]["value"],
            10,
        )
        self.assertEqual(
            result["system"]["metrics"]["leon_cpu_fraction"]["css"]["peak"]["value"],
            0.25,
        )
        self.assertEqual(
            result["link_capabilities"]["negotiated_usb_speed"]["value"],
            "HIGH",
        )
        self.assertEqual(
            result["link_capabilities"]["nominal_signalling_rate_mbps"]["value"],
            480.0,
        )
        self.assertEqual(
            result["link_capabilities"]["inventory_usb_speed"]["value"],
            None,
        )
        self.assertFalse(result["link_capabilities"]["changed_from_inventory"])
        self.assertEqual(
            result["link_capabilities"]["device_to_host_application_payload_bytes"][
                "value"
            ],
            1200,
        )
        self.assertIn(
            "excludes USB framing",
            result["link_capabilities"]["device_to_host_application_payload_bytes"][
                "method"
            ],
        )
        self.assertEqual(
            result["link_capabilities"]["host_to_device_throughput"]["status"],
            "unsupported",
        )
        self.assertEqual(clock.sleeps, [0.001])

    def test_frame_arrival_uses_depthai_clock_domain_for_latency(self):
        # Give the clocks different origins and check latency uses the DepthAI arrival
        # clock.
        class SynchronizedMessage(FakeMessage):
            def getTimestamp(self):
                return timedelta(seconds=99.9)

        clock = FakeClock()
        device = FakeRunDevice(
            {
                "rgb": FakeQueue([SynchronizedMessage()]),
                "system": FakeQueue([fake_system_message()]),
            }
        )
        dai_module = SimpleNamespace(
            Clock=SimpleNamespace(now=lambda: timedelta(seconds=100.0))
        )
        result = run_profile(
            object(),
            self.profile_id,
            0.001,
            100,
            dai_module=dai_module,
            pipeline_builder=lambda profile_id, module: (
                object(),
                {
                    "streams": {
                        "rgb": {"kind": "image", "requested_fps": 30.0},
                        "system": {"kind": "system", "requested_fps": 1.0},
                    }
                },
            ),
            device_factory=lambda pipeline, selected: device,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

        sample = result["streams"]["rgb"]["samples"][0]
        self.assertEqual(sample["host_s"], 100.0)
        self.assertAlmostEqual(
            result["streams"]["rgb"]["metrics"]["latency_mean_ms"]["value"],
            100.0,
        )

    def test_missing_telemetry_stops_at_fixed_three_second_grace(self):
        # Leave the system queue empty and check the run becomes inconclusive after
        # three seconds.
        device = FakeRunDevice(
            {
                "rgb": FakeQueue([FakeMessage()]),
                "system": FakeQueue([]),
            }
        )

        result, clock, *_ = self.run_fake(device, duration_s=10)

        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["stop_reason"], "telemetry-missing")
        self.assertAlmostEqual(result["polling_duration_s"], 3.0, places=6)
        self.assertLessEqual(result["polling_duration_s"], 3.001)
        self.assertTrue(result["execution"]["polling_finished"])
        self.assertEqual(result["missing_expected_streams"], ["system"])
        self.assertIn("no valid SystemLogger sample", result["inconclusive_evidence"])
        self.assertTrue(device.exited)

    def test_short_polled_run_with_missing_expected_stream_is_inconclusive(self):
        # Provide telemetry but no RGB frames and check missing image data makes the run
        # inconclusive.
        device = FakeRunDevice(
            {
                "rgb": FakeQueue([]),
                "system": FakeQueue([fake_system_message()]),
            }
        )

        result, *_ = self.run_fake(device, duration_s=0.001)

        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["missing_expected_streams"], ["rgb"])
        self.assertEqual(
            result["inconclusive_evidence"],
            ["expected stream rgb produced no messages"],
        )
        payload = result["link_capabilities"][
            "device_to_host_application_payload_bytes"
        ]
        self.assertEqual(payload["status"], "measured")
        self.assertEqual(payload["value"], 0)

    def test_pipeline_failure_records_phase_and_no_application_measurement(self):
        # Raise while building the pipeline and check the phase and absent payload are
        # recorded.
        failure = RuntimeError("pipeline exploded exactly")

        result = run_profile(
            object(),
            self.profile_id,
            10,
            100,
            dai_module="fake-depthai",
            pipeline_builder=lambda profile_id, module: (_ for _ in ()).throw(failure),
            device_factory=lambda pipeline, selected: self.fail("device must not open"),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["exception_phase"], "pipeline-build")
        self.assertEqual(
            result["execution"],
            {
                "pipeline_built": False,
                "device_open": False,
                "polling_started": False,
                "polling_finished": False,
            },
        )
        payload = result["link_capabilities"][
            "device_to_host_application_payload_bytes"
        ]
        self.assertEqual(payload["status"], "no-data")
        self.assertIsNone(payload["value"])

    def test_only_build_or_open_incompatibility_is_unsupported(self):
        # Raise incompatibility errors at several phases and check only build/open are
        # unsupported.
        build_result = run_profile(
            object(),
            self.profile_id,
            10,
            100,
            dai_module="fake-depthai",
            pipeline_builder=lambda profile_id, module: (_ for _ in ()).throw(
                RuntimeError("profile is not supported by this firmware")
            ),
        )
        open_result = run_profile(
            object(),
            self.profile_id,
            10,
            100,
            dai_module="fake-depthai",
            pipeline_builder=lambda profile_id, module: (
                object(),
                {
                    "streams": {
                        "rgb": {"kind": "image", "requested_fps": 30.0},
                        "system": {"kind": "system", "requested_fps": 1.0},
                    }
                },
            ),
            device_factory=lambda pipeline, selected: (_ for _ in ()).throw(
                RuntimeError("pipeline configuration incompatible with device")
            ),
        )
        queue_result, *_ = self.run_fake(
            FakeRunDevice(
                {
                    "rgb": FakeQueue([RuntimeError("unsupported queue read")]),
                    "system": FakeQueue([]),
                }
            ),
            duration_s=10,
        )

        self.assertEqual(build_result["status"], "unsupported")
        self.assertEqual(build_result["exception_phase"], "pipeline-build")
        self.assertEqual(
            build_result["errors"],
            [
                {
                    "type": "RuntimeError",
                    "message": "profile is not supported by this firmware",
                }
            ],
        )
        self.assertEqual(open_result["status"], "unsupported")
        self.assertEqual(open_result["exception_phase"], "device-open")
        self.assertTrue(open_result["execution"]["pipeline_built"])
        self.assertFalse(open_result["execution"]["device_open"])
        self.assertEqual(queue_result["status"], "error")
        self.assertEqual(queue_result["exception_phase"], "polling")

    def test_polling_duration_excludes_device_context_teardown(self):
        # Advance the clock during teardown and check only lifecycle duration includes
        # it.
        clock = FakeClock()
        device = FakeRunDevice(
            {
                "rgb": FakeQueue([FakeMessage()]),
                "system": FakeQueue([fake_system_message()]),
            },
            exit_callback=lambda: setattr(clock, "now", clock.now + 2.0),
        )
        pipeline = object()

        result = run_profile(
            object(),
            self.profile_id,
            0.001,
            100,
            dai_module="fake-depthai",
            pipeline_builder=lambda profile_id, module: (
                pipeline,
                {
                    "streams": {
                        "rgb": {"kind": "image", "requested_fps": 30.0},
                        "system": {"kind": "system", "requested_fps": 1.0},
                    }
                },
            ),
            device_factory=lambda built, selected: device,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

        self.assertAlmostEqual(result["polling_duration_s"], 0.001)
        self.assertAlmostEqual(result["lifecycle_duration_s"], 2.001)
        self.assertNotIn("actual_duration_s", result)

    def test_exception_preserves_samples_and_does_not_retry_or_adapt(self):
        # Raise after one frame and check its sample remains saved without rebuilding.
        failure = RuntimeError("queue read exploded exactly")
        device = FakeRunDevice(
            {
                "rgb": FakeQueue([FakeMessage(), failure]),
                "system": FakeQueue([]),
            }
        )

        result, _, _, _, build_calls, device_calls = self.run_fake(
            device,
            duration_s=10,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(
            result["errors"],
            [{"type": "RuntimeError", "message": "queue read exploded exactly"}],
        )
        self.assertEqual(len(result["streams"]["rgb"]["samples"]), 1)
        self.assertEqual(
            result["streams"]["rgb"]["metrics"]["frames_received"]["value"],
            1,
        )
        self.assertFalse(result["device_disconnected"])
        self.assertEqual(len(build_calls), 1)
        self.assertEqual(len(device_calls), 1)
        self.assertEqual(result["profile_id"], self.profile_id)
        self.assertTrue(device.exited)

    def test_disconnect_keeps_exact_exception_and_partial_payload(self):
        # Disconnect after receiving a frame and check the error and received payload
        # are retained.
        class DeviceDisconnectedError(RuntimeError):
            pass

        device = FakeRunDevice(
            {
                "rgb": FakeQueue(
                    [FakeMessage(), DeviceDisconnectedError("USB cable detached")]
                ),
                "system": FakeQueue([]),
            }
        )

        result, *_ = self.run_fake(device, duration_s=10)

        self.assertEqual(result["status"], "disconnected")
        self.assertTrue(result["device_disconnected"])
        self.assertEqual(
            result["errors"],
            [{"type": "DeviceDisconnectedError", "message": "USB cable detached"}],
        )
        self.assertEqual(
            result["link_capabilities"]["device_to_host_application_payload_bytes"][
                "value"
            ],
            1200,
        )

    def test_thermal_stop_checks_every_reported_temperature_component(self):
        # Heat each reported component in turn and check polling stops without another
        # sleep.
        for component in ("average", "css", "mss", "upa", "dss"):
            with self.subTest(component=component):
                device = FakeRunDevice(
                    {
                        "rgb": FakeQueue([]),
                        "system": FakeQueue([fake_system_message(component)]),
                    }
                )

                result, clock, *_ = self.run_fake(device, duration_s=10)

                self.assertEqual(result["status"], "thermal-stop")
                self.assertEqual(len(result["system"]["samples"]), 1)
                self.assertEqual(clock.sleeps, [])
                self.assertTrue(device.exited)

    def test_long_run_requires_permission_before_pipeline_or_device_open(self):
        # Reject an unapproved long run before either the pipeline builder or device
        # factory runs.
        build_calls = []
        device_calls = []
        selected = object()
        device = FakeRunDevice(
            {
                "rgb": FakeQueue([]),
                "system": FakeQueue([fake_system_message("average")]),
            }
        )

        def pipeline_builder(profile_id, dai_module):
            build_calls.append((profile_id, dai_module))
            return object(), {
                "streams": {
                    "rgb": {"kind": "image", "requested_fps": 30.0},
                    "system": {"kind": "system", "requested_fps": 1.0},
                }
            }

        def device_factory(pipeline, device_info):
            device_calls.append((pipeline, device_info))
            return device

        with self.assertRaisesRegex(ValueError, "--allow-long-test"):
            run_profile(
                selected,
                self.profile_id,
                301,
                100,
                dai_module="fake-depthai",
                pipeline_builder=pipeline_builder,
                device_factory=device_factory,
            )

        self.assertEqual(build_calls, [])
        self.assertEqual(device_calls, [])

    def test_explicit_permission_allows_a_bounded_long_run(self):
        # Enable the long-run flag and advance fake time to verify the requested
        # duration is bounded.
        device = FakeRunDevice(
            {
                "rgb": FakeQueue([]),
                "system": FakeQueue([fake_system_message("average")]),
            }
        )

        result, _, _, _, build_calls, device_calls = self.run_fake(
            device,
            duration_s=301,
            allow_long_test=True,
        )

        self.assertEqual(result["status"], "thermal-stop")
        self.assertEqual(result["requested_duration_s"], 301.0)
        self.assertEqual(len(build_calls), 1)
        self.assertEqual(len(device_calls), 1)

    def test_unsafe_temperature_guard_is_rejected_before_pipeline_or_device(self):
        # Try limits above the thermal maximum and check neither construction nor
        # opening occurs.
        for temperature_stop_c in (100.1, 104.0, 105.0, float("nan"), float("inf")):
            with self.subTest(temperature_stop_c=temperature_stop_c):
                build_calls = []
                device_calls = []

                def pipeline_builder(profile_id, dai_module):
                    build_calls.append((profile_id, dai_module))
                    raise AssertionError("pipeline must not be built")

                def device_factory(pipeline, device_info):
                    device_calls.append((pipeline, device_info))
                    raise AssertionError("device must not be opened")

                with self.assertRaises(ValueError):
                    run_profile(
                        object(),
                        self.profile_id,
                        10,
                        temperature_stop_c,
                        dai_module="fake-depthai",
                        pipeline_builder=pipeline_builder,
                        device_factory=device_factory,
                    )

                self.assertEqual(build_calls, [])
                self.assertEqual(device_calls, [])

    def test_explicit_inventory_speed_is_compared_without_changing_device_identity(
        self,
    ):
        # Pass the inventory USB mode separately and check comparison keeps the selected
        # device.
        selected = object()
        device = FakeRunDevice(
            {
                "rgb": FakeQueue([]),
                "system": FakeQueue([]),
            },
            usb_speed=dai.UsbSpeed.SUPER,
        )

        result, _, pipeline, _, _, device_calls = self.run_fake(
            device,
            device_info=selected,
            inventory_usb_speed="HIGH",
        )

        self.assertEqual(device_calls, [(pipeline, selected)])
        self.assertIs(device_calls[0][1], selected)
        self.assertEqual(
            result["link_capabilities"]["inventory_usb_speed"]["value"],
            "HIGH",
        )
        self.assertEqual(
            result["link_capabilities"]["negotiated_usb_speed"]["value"],
            "SUPER",
        )
        self.assertTrue(result["link_capabilities"]["changed_from_inventory"])

    def test_missing_inventory_speed_does_not_read_device_info_attributes(self):
        # Omit inventory speed and verify device-info attributes are not used as
        # invented evidence.
        selected = type("PlainDeviceInfo", (), {"usb_speed": "HIGH"})()
        device = FakeRunDevice(
            {
                "rgb": FakeQueue([]),
                "system": FakeQueue([]),
            },
            usb_speed=dai.UsbSpeed.SUPER,
        )

        result, _, pipeline, _, _, device_calls = self.run_fake(
            device,
            device_info=selected,
        )

        self.assertEqual(device_calls, [(pipeline, selected)])
        self.assertEqual(
            result["link_capabilities"]["inventory_usb_speed"]["value"],
            None,
        )
        self.assertFalse(result["link_capabilities"]["changed_from_inventory"])


class InventoryHelperTests(unittest.TestCase):
    def test_camera_feature_serialization_keeps_sensor_capability(self):
        result = camera_feature_to_dict(FakeFeature())
        self.assertEqual(result["socket"], "CAM_A")
        self.assertEqual(result["sensor_name"], "IMX214")
        self.assertTrue(result["has_autofocus"])

    def test_resource_helpers_use_bytes_and_celsius(self):
        self.assertEqual(memory_to_dict(FakeMemory())["used_bytes"], 10)
        self.assertEqual(temperature_to_dict(FakeTemperature())["average_c"], 45.0)

    def test_pi_power_diagnostic_is_kept_separate_from_camera_power(self):
        # Return a fake throttling bitmask and check the metric identifies it as Pi host
        # evidence.
        def fake_run(command, capture_output, text, timeout, check):
            self.assertEqual(command, ["vcgencmd", "get_throttled"])
            return type(
                "Result",
                (),
                {"returncode": 0, "stdout": "throttled=0x0\n", "stderr": ""},
            )()

        result = collect_host_power(
            command_exists=lambda name: True,
            run_command=fake_run,
        )
        self.assertEqual(result["raspberry_pi_throttled"]["value"], "0x0")
        self.assertEqual(
            result["raspberry_pi_throttled"]["method"],
            "host vcgencmd; not an OAK brownout measurement",
        )


class FakeCalibration:
    def eepromToJson(self):
        return {"boardName": "OAK-D-LITE", "version": 7}

    def getCameraIntrinsics(self, camera_socket):
        return [[1.0, 0.0], [0.0, 1.0]]

    def getDistortionCoefficients(self, camera_socket):
        if camera_socket == dai.CameraBoardSocket.CAM_C:
            raise RuntimeError("distortion unavailable")

        return [0.1, 0.2]

    def getFov(self, camera_socket, use_spec):
        return 72.0 if use_spec else 71.5

    def getCameraExtrinsics(self, source, destination, use_spec):
        return [[1.0, 0.0], [0.0, 1.0]]

    def getStereoLeftRectificationRotation(self):
        return [[1.0]]

    def getStereoRightRectificationRotation(self):
        return [[1.0]]


class FakeDevice:
    def getMxId(self):
        return "mx-id"

    def getProductName(self):
        return "OAK-D Lite"

    def getDeviceName(self):
        return "OAK-D-LITE"

    def getUsbSpeed(self):
        return dai.UsbSpeed.HIGH

    def getConnectedCameraFeatures(self):
        return [FakeFeature()]

    def getConnectedCameras(self):
        return [dai.CameraBoardSocket.CAM_B, dai.CameraBoardSocket.CAM_C]

    def getConnectedIMU(self):
        return "BMI270"

    def getDdrMemoryUsage(self):
        return FakeMemory()

    def getCmxMemoryUsage(self):
        return FakeMemory()

    def getLeonCssHeapUsage(self):
        return FakeMemory()

    def getLeonMssHeapUsage(self):
        return FakeMemory()

    def getChipTemperature(self):
        return FakeTemperature()

    def readCalibration2(self):
        return FakeCalibration()


class FakeCalibrationUnavailableDevice(FakeDevice):
    def readCalibration2(self):
        raise RuntimeError("calibration acquisition unavailable")


class InventoryCollectionTests(unittest.TestCase):
    def test_collect_host_records_identity_timestamps_and_power_status(self):
        expected_power = {"raspberry_pi_throttled": {"status": "fake"}}
        power_calls = []

        def fake_power_collector():
            power_calls.append(True)
            return expected_power

        result = collect_host(power_collector=fake_power_collector)
        self.assertEqual(set(result["timestamps"]), {"utc", "local"})
        self.assertIs(result["power"], expected_power)
        self.assertEqual(power_calls, [True])
        self.assertTrue(result["hostname"])

    def test_collect_device_keeps_usb_provisional_and_calibration_read_failures_local(
        self,
    ):
        # Fail one distortion read and check USB and other calibration evidence remain
        # available.
        device, calibration = collect_device(FakeDevice(), dai)
        self.assertEqual(device["usb_speed"]["value"], "HIGH")
        self.assertEqual(device["usb_speed"]["markers"], ["*"])
        self.assertEqual(device["usb_speed"]["evidence"], ["HIGH"])
        self.assertEqual(calibration["eeprom_json"]["value"]["boardName"], "OAK-D-LITE")
        self.assertEqual(
            calibration["cameras"]["CAM_C"]["distortion_coefficients"],
            {"status": "unsupported", "error": "distortion unavailable"},
        )
        self.assertEqual(
            calibration["stereo_cam_b_to_cam_c"]["extrinsics"]["markers"],
            [],
        )

    def test_collect_device_preserves_inventory_when_calibration_acquisition_fails(
        self,
    ):
        # Fail calibration acquisition and check device identity and sensor inventory
        # still return.
        device, calibration = collect_device(FakeCalibrationUnavailableDevice(), dai)
        self.assertEqual(device["mxid"]["value"], "mx-id")
        self.assertEqual(device["usb_speed"]["value"], "HIGH")
        self.assertEqual(device["camera_features"][0]["sensor_name"], "IMX214")
        self.assertEqual(
            calibration,
            {"status": "unsupported", "error": "calibration acquisition unavailable"},
        )


class MetricTests(unittest.TestCase):
    def test_make_metric_preserves_evidence_and_markers(self):
        result = make_metric(
            value=480,
            unit="Mbit/s nominal",
            status="observed",
            markers=("*",),
            method="DepthAI getUsbSpeed",
            evidence=["UsbSpeed.HIGH"],
        )
        self.assertEqual(result["value"], 480)
        self.assertEqual(result["markers"], ["*"])
        self.assertEqual(result["evidence"], ["UsbSpeed.HIGH"])

    def test_make_metric_rejects_unknown_marker(self):
        with self.assertRaisesRegex(ValueError, "Unknown result marker"):
            make_metric(
                1,
                "count",
                "observed",
                ("?",),
                "test",
                [],
            )


class FrameStatisticsTests(unittest.TestCase):
    def test_summarize_frames_counts_sequence_gaps_and_payload(self):
        # Three arrivals span two intervals; sequence 102 is missing.
        samples = [
            {"host_s": 10.00, "device_s": 9.99, "sequence": 100, "payload_bytes": 1000},
            {
                "host_s": 10.04,
                "device_s": 10.02,
                "sequence": 101,
                "payload_bytes": 1000,
            },
            {
                "host_s": 10.08,
                "device_s": 10.05,
                "sequence": 103,
                "payload_bytes": 1000,
            },
        ]
        result = summarize_frames(samples, requested_fps=25.0)
        self.assertEqual(result["frames_received"]["value"], 3)
        self.assertEqual(result["sequence_gaps"]["value"], 1)
        self.assertAlmostEqual(result["received_fps"]["value"], 25.0)
        self.assertAlmostEqual(result["payload_bitrate_mbps"]["value"], 0.3)
        self.assertAlmostEqual(result["latency_mean_ms"]["value"], 20.0)
        self.assertAlmostEqual(result["latency_p95_ms"]["value"], 30.0)

    def test_summarize_frames_handles_an_empty_stream(self):
        result = summarize_frames([], requested_fps=30.0)
        self.assertEqual(result["frames_received"]["value"], 0)
        self.assertEqual(result["received_fps"]["status"], "no-data")
        self.assertIsNone(result["latency_mean_ms"]["value"])

    def test_nearest_rank_validates_percentile_before_empty_input(self):
        # Check invalid percentiles raise even when there are no timing samples.
        for percentile in (0, -1, 100.1, float("nan"), float("inf")):
            with self.subTest(percentile=percentile):
                with self.assertRaisesRegex(ValueError, "greater than 0"):
                    nearest_rank([], percentile)

    def test_nearest_rank_handles_boundaries_empty_and_single_value(self):
        self.assertIsNone(nearest_rank([], 95))
        self.assertEqual(nearest_rank([7], 0.001), 7.0)
        self.assertEqual(nearest_rank([7], 100), 7.0)
        self.assertEqual(nearest_rank([4, 1, 3, 2], 25), 1.0)
        self.assertEqual(nearest_rank([4, 1, 3, 2], 100), 4.0)


class ComparisonTests(unittest.TestCase):
    def test_compare_runs_reports_absolute_and_percentage_change(self):
        # Compare 20 and 30 FPS and check the result reports a 10-FPS and 50-percent
        # increase.
        baseline = {
            "tests": {
                "rgb": {
                    "metrics": {
                        "received_fps": make_metric(
                            20.0,
                            "frames/s",
                            "measured",
                            ("*",),
                            "test",
                            [],
                        )
                    }
                }
            }
        }
        candidate = {
            "tests": {
                "rgb": {
                    "metrics": {
                        "received_fps": make_metric(
                            30.0,
                            "frames/s",
                            "measured",
                            ("*",),
                            "test",
                            [],
                        )
                    }
                }
            }
        }
        comparison = compare_runs(baseline, candidate)
        item = comparison["tests.rgb.metrics.received_fps"]
        self.assertEqual(item["absolute_change"], 10.0)
        self.assertEqual(item["percentage_change"], 50.0)
        self.assertEqual(item["markers"], ["*"])

    def test_compare_runs_does_not_calculate_percentage_from_zero(self):
        # Increase the value from zero and check the percentage field remains None.
        baseline = {
            "device": {
                "disconnects": make_metric(
                    0,
                    "count",
                    "observed",
                    ("*",),
                    "test",
                    [],
                )
            }
        }
        candidate = {
            "device": {
                "disconnects": make_metric(
                    1,
                    "count",
                    "observed",
                    ("*",),
                    "test",
                    [],
                )
            }
        }
        comparison = compare_runs(baseline, candidate)
        self.assertIsNone(comparison["device.disconnects"]["percentage_change"])

    def test_compare_runs_marks_high_to_super_as_expected_usb_retest(self):
        # Compare HIGH with SUPER and check the expected retest classification has no
        # warnings.
        baseline = {
            "device": {
                "usb_speed": make_metric(
                    "HIGH",
                    "DepthAI enum",
                    "observed",
                    ("*",),
                    "getUsbSpeed",
                    [],
                )
            }
        }
        candidate = {
            "device": {
                "usb_speed": make_metric(
                    "SUPER",
                    "DepthAI enum",
                    "observed",
                    (),
                    "getUsbSpeed",
                    [],
                )
            }
        }
        comparison = compare_runs(baseline, candidate)
        metadata = comparison["_comparison"]
        self.assertEqual(metadata["baseline_usb_speed"], "HIGH")
        self.assertEqual(metadata["candidate_usb_speed"], "SUPER")
        self.assertEqual(metadata["status"], "expected_usb2_to_usb3_retest")
        self.assertEqual(metadata["warnings"], [])

    def test_compare_runs_warns_for_a_non_expected_usb_transition(self):
        # Reverse the USB transition and check the comparison records its warning.
        baseline = {
            "device": {
                "usb_speed": make_metric(
                    "SUPER",
                    "DepthAI enum",
                    "observed",
                    (),
                    "getUsbSpeed",
                    [],
                )
            }
        }
        candidate = {
            "device": {
                "usb_speed": make_metric(
                    "HIGH",
                    "DepthAI enum",
                    "observed",
                    ("*",),
                    "getUsbSpeed",
                    [],
                )
            }
        }
        comparison = compare_runs(baseline, candidate)
        metadata = comparison["_comparison"]
        self.assertEqual(metadata["baseline_usb_speed"], "SUPER")
        self.assertEqual(metadata["candidate_usb_speed"], "HIGH")
        self.assertEqual(metadata["status"], "not_expected_usb2_to_usb3_retest")
        self.assertEqual(
            metadata["warnings"],
            ["Comparison is not the expected USB 2 (HIGH) to USB 3 (SUPER) retest."],
        )

    def test_compare_runs_warns_but_does_not_reject_provenance_mismatch(self):
        # Supply different benchmark versions and hashes, then check comparison returns
        # a warning.
        baseline = {
            "benchmark_provenance": {
                "version": "1.0.0",
                "source_sha256": {"oak_benchmark.py": "aaa"},
                "profile_definitions_sha256": "profiles-a",
            }
        }
        candidate = {
            "benchmark_provenance": {
                "version": "1.0.1",
                "source_sha256": {"oak_benchmark.py": "bbb"},
                "profile_definitions_sha256": "profiles-b",
            }
        }

        comparison = compare_runs(baseline, candidate)

        metadata = comparison["_comparison"]
        self.assertFalse(metadata["provenance_match"])
        self.assertIn(
            "Benchmark provenance differs; interpret changes with the recorded revisions.",
            metadata["warnings"],
        )


class ProvenanceTests(unittest.TestCase):
    def test_provenance_hashes_exact_files_and_canonical_profile_json(self):
        # Hash known fixture bytes and compare the profile hash with fixed canonical
        # JSON.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contents = {
                "oak_benchmark.py": b"benchmark\n",
                "oak_metrics.py": b"metrics\n",
                "requirements.txt": b"depthai==2.32.0.0\n",
            }

            for filename, content in contents.items():
                (root / filename).write_bytes(content)

            profiles = {"p": {"streams": {"rgb": {"kind": "image"}}}}

            result = benchmark_provenance(root, profiles=profiles)

        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(
            result["source_sha256"],
            {
                filename: hashlib.sha256(content).hexdigest()
                for filename, content in contents.items()
            },
        )
        canonical = '{"p":{"streams":{"rgb":{"kind":"image"}}}}'
        self.assertEqual(
            result["profile_definitions_sha256"],
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        json.dumps(result, allow_nan=False)


class ReportTests(unittest.TestCase):
    def test_no_device_summary_shows_failed_status_and_exact_access_error(self):
        # Create a failed discovery record and check its status and access error appear
        # in Markdown.
        record = create_run_document(
            build_parser().parse_args(["inventory"]),
            dai_module=SimpleNamespace(__version__="2.32.0.0"),
            host_collector=lambda: {"platform": "test-host"},
            device_discovery=lambda: [],
            printer=lambda message: None,
        )
        report = render_markdown(record)
        self.assertIn("Overall status: `failed`", report)
        self.assertIn("Device/access status: `unavailable`", report)
        self.assertIn("RuntimeError: No OAK device discovered", report)

    def test_partial_profile_failure_summary_shows_each_status_and_exact_error(self):
        # Fail one profile and check the summary shows both its error and the later
        # completed profile.
        device_info = FakeDeviceInfo("mxid-selected")
        inventory_device = object()
        events = []

        def collect_inventory(device, dai_module):
            return valid_inventory(), {}

        def run_one(selected, profile_id, duration_s, temperature_stop_c, **options):
            if profile_id == "rgb_preview_640x360_30":
                raise RuntimeError("profile exploded exactly")

            return {"profile_id": profile_id, "status": "complete", "errors": []}

        record = create_run_document(
            orchestration_args(),
            dai_module=SimpleNamespace(__version__="2.32.0.0"),
            host_collector=lambda: {"platform": "test-host"},
            device_collector=collect_inventory,
            profile_runner=run_one,
            device_discovery=lambda: [device_info],
            inventory_device_factory=lambda selected: FakeInventoryContext(
                inventory_device,
                events,
            ),
            printer=lambda message: None,
        )
        report = render_markdown(record)
        self.assertIn("Overall status: `failed`", report)
        self.assertIn("`rgb_preview_640x360_30`: `error`", report)
        self.assertIn("RuntimeError: profile exploded exactly", report)
        self.assertIn("`mono_pair_640x480_30`: `complete`", report)
        self.assertNotIn("‡ ‡", report)

    def test_report_explains_markers_and_usb2_provisional_results(self):
        # Render a HIGH-link run and check it includes the provisional notice and
        # evidence markers.
        run = {
            "run_id": "20260814T120000Z_usb2",
            "timestamps": {
                "utc": "2026-08-14T12:00:00Z",
                "local": "2026-08-14T22:00:00+10:00",
            },
            "device": {
                "usb_speed": make_metric(
                    "HIGH",
                    "DepthAI enum",
                    "observed",
                    ("*",),
                    "getUsbSpeed",
                    [],
                )
            },
            "calibration": {},
            "tests": {},
            "pending_application_tests": [
                "200 mm ArUco pose†",
                "pressure gauge reading†",
                "valve state classification†",
                "web delivery†",
            ],
            "warnings": [],
            "source_ambiguities": ["datasheet USB generation wording conflict"],
        }
        report = render_markdown(run)
        self.assertIn("PROVISIONAL USB 2 BASELINE", report)
        self.assertIn("Fixed constraints", report)
        self.assertIn("Calibrated values", report)
        self.assertIn("Pending application tests", report)
        self.assertIn("`*` link-dependent", report)
        self.assertIn("datasheet USB generation wording conflict ‡", report)

    def test_report_renders_manufacturer_claims_separately_from_measured_device_metrics(
        self,
    ):
        # Put one claim and one measured device metric into their expected report
        # sections.
        run = {
            "run_id": "manufacturer-claims",
            "manufacturer_claims": {
                "rgb_fps": make_metric(
                    60,
                    "frames/s",
                    "claimed",
                    (),
                    "datasheet",
                    ["specification"],
                )
            },
            "device": {
                "mxid": make_metric(
                    "abc123",
                    "",
                    "observed",
                    (),
                    "getMxId",
                    [],
                ),
                "usb_speed": make_metric(
                    "HIGH",
                    "DepthAI enum",
                    "observed",
                    ("*",),
                    "getUsbSpeed",
                    [],
                ),
            },
            "calibration": {},
            "tests": {},
            "pending_application_tests": [],
            "warnings": [],
            "source_ambiguities": [],
        }
        report = render_markdown(run)
        manufacturer_section = report.split("## Manufacturer claims\n", 1)[1].split(
            "## Calibrated values",
            1,
        )[0]
        measured_section = report.split("## Measured baseline\n", 1)[1].split(
            "## Pending application tests",
            1,
        )[0]
        self.assertIn("- `rgb_fps`: 60 frames/s (claimed)", manufacturer_section)
        self.assertNotIn("  (claimed)", manufacturer_section)
        self.assertNotIn("rgb_fps", measured_section)
        self.assertIn(
            "- `device.usb_speed`: HIGH DepthAI enum * (observed)",
            measured_section,
        )

import copy
import json
import tempfile
import unittest
from pathlib import Path

from reports.build_usb_comparison_report import (
    EXPECTED_MXID,
    audit_document,
    build_document,
    build_report_model,
    load_strict_json,
    validate_report_model,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_PROFILE_STREAMS = [
    ("rgb_preview_640x360_30", "RGB"),
    ("rgb_video_1280x720_30", "RGB"),
    ("rgb_video_1920x1080_30", "RGB"),
    ("mono_pair_640x480_30", "Left"),
    ("mono_pair_640x480_30", "Right"),
    ("stereo_depth_640x480_30", "Depth"),
    ("h264_rgb_1920x1080_30", "H.264"),
    ("combined_h264_depth_system_30", "Depth"),
    ("combined_h264_depth_system_30", "H.264"),
    ("combined_h264_depth_system_30_stability", "Depth"),
    ("combined_h264_depth_system_30_stability", "H.264"),
]


class ReportEvidenceModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = build_report_model(PROJECT_ROOT)

    def test_verified_evidence_pair_builds_exact_comparison_rows(self):
        # Check the unit, link modes and stream rows extracted from the archived
        # evidence.
        model = self.model
        self.assertEqual(model["identity"]["mxid"], EXPECTED_MXID)
        self.assertEqual(model["identity"]["product_name"], "OAK-D-LITE")
        self.assertEqual(model["links"], {"usb2": "HIGH", "usb3": "SUPER"})
        self.assertEqual(
            [
                (row["profile_id"], row["stream_label"])
                for row in model["comparison_rows"]
            ],
            EXPECTED_PROFILE_STREAMS,
        )
        raw_1080 = next(
            row
            for row in model["comparison_rows"]
            if row["profile_id"] == "rgb_video_1920x1080_30"
        )
        self.assertEqual(round(raw_1080["usb2"]["fps"], 3), 12.960)
        self.assertEqual(raw_1080["usb2"]["gaps"], 156)
        self.assertEqual(round(raw_1080["usb3"]["fps"], 3), 29.947)
        self.assertEqual(raw_1080["usb3"]["gaps"], 0)
        self.assertEqual(round(raw_1080["usb3"]["payload_mbps"], 3), 747.698)

    def test_model_contains_traceable_specs_constraints_and_references(self):
        model = self.model
        spec_names = {row["parameter"] for row in model["specification_rows"]}
        self.assertTrue(
            {
                "Product",
                "Board",
                "RGB camera",
                "Stereo mono cameras",
                "USB interface wording",
                "Runtime USB link",
                "DepthAI",
            }.issubset(spec_names)
        )
        constraint_ids = {row["id"] for row in model["constraint_rows"]}
        self.assertEqual(
            constraint_ids,
            {
                "CON-USB-01",
                "CON-RAW-02",
                "CON-ENC-03",
                "CON-DEP-04",
                "CON-STB-05",
                "CON-PI-06",
                "CON-MET-07",
                "CON-NET-08",
                "CON-OVR-09",
                "CON-TGT-10",
                "CON-OPS-11",
                "CON-ONB-12",
                "CON-DUR-13",
            },
        )
        self.assertEqual(len(model["evidence_references"]), 4)
        self.assertTrue(
            any("‡" in row["status"] for row in model["specification_rows"])
        )
        self.assertTrue(any("†" in row["status"] for row in model["constraint_rows"]))

    def test_strict_json_rejects_non_standard_constants(self):
        # Write a NaN token to a temporary evidence file and check that loading raises.
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "invalid.json"
            path.write_text('{"invalid": NaN}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "non-standard JSON constant"):
                load_strict_json(path)

    def test_validation_rejects_provenance_mismatch(self):
        # Change only the provenance flag and check that validation rejects the copied
        # model.
        model = copy.deepcopy(self.model)
        model["integrity"]["provenance_match"] = False

        with self.assertRaisesRegex(ValueError, "provenance"):
            validate_report_model(model)

    def test_validation_rejects_incomplete_profile(self):
        # Lower the completion count to seven and check that validation rejects the
        # model.
        model = copy.deepcopy(self.model)
        model["integrity"]["complete_execution_count"] = 7

        with self.assertRaisesRegex(ValueError, "eight complete"):
            validate_report_model(model)

    def test_validation_rejects_wrong_camera(self):
        # Replace the verified camera ID and check that validation rejects the model.
        model = copy.deepcopy(self.model)
        model["identity"]["mxid"] = "wrong-device"

        with self.assertRaisesRegex(ValueError, "MXID"):
            validate_report_model(model)


class ReportDocumentStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = build_document(build_report_model(PROJECT_ROOT))

    def test_document_uses_approved_structure_and_landscape_geometry(self):
        # Check page dimensions, heading order and structural audit results in memory.
        document = self.document
        section = document.sections[0]
        self.assertAlmostEqual(section.page_width.inches, 11.0, places=2)
        self.assertAlmostEqual(section.page_height.inches, 8.5, places=2)
        self.assertAlmostEqual(section.left_margin.inches, 0.65, places=2)
        self.assertAlmostEqual(section.right_margin.inches, 0.65, places=2)
        headings = [
            paragraph.text
            for paragraph in document.paragraphs
            if paragraph.style.name == "Heading 1"
        ]
        self.assertEqual(
            headings,
            [
                "1. Test Context",
                "2. Camera Specifications and Observed Hardware",
                "3. USB 2 and USB 3 Benchmark Comparison",
                "4. Stability and System Observations",
                "5. Constraints Register",
                "6. Interpretation Boundary and Evidence",
            ],
        )
        self.assertGreaterEqual(len(document.tables), 5)
        forbidden = ("T" + "BD", "T" + "ODO")
        self.assertTrue(
            all(
                not any(token in paragraph.text for token in forbidden)
                for paragraph in document.paragraphs
            )
        )
        self.assertEqual(audit_document(document), [])

    def test_document_contains_evidence_boundaries_and_markers(self):
        # Search paragraph and table text for the run IDs, evidence boundary and
        # markers.
        text = "\n".join(paragraph.text for paragraph in self.document.paragraphs)
        table_text = "\n".join(
            cell.text
            for table in self.document.tables
            for row in table.rows
            for cell in row.cells
        )
        combined = text + "\n" + table_text

        for required in (
            "OAK-D Lite USB 2 / USB 3 Benchmark Comparison",
            "20260814T024121316782Z_run_b381fce8",
            "20260814T034134853796Z_run_ff85bad7",
            "complete means evidence collection completed",
            "*",
            "†",
            "‡",
        ):
            self.assertIn(required, combined)

    def test_tables_use_repeating_headers_and_fixed_geometry(self):
        # Check every table uses fixed widths and a repeating header in its Word XML.
        for table in self.document.tables:
            xml = table._tbl.xml
            self.assertIn("w:tblLayout", xml)
            self.assertIn('w:type="fixed"', xml)
            self.assertIn("w:tblHeader", table.rows[0]._tr.xml)
            self.assertNotIn('w:type="pct"', xml)


if __name__ == "__main__":
    unittest.main()

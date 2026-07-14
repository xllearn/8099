from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "8099_regression_cases.json"


class RegressionManifestTests(unittest.TestCase):
    def manifest_module(self):
        self.assertIsNotNone(
            importlib.util.find_spec("app.regression_manifest"),
            "app.regression_manifest is required",
        )
        return importlib.import_module("app.regression_manifest")

    def test_fixed3_is_a_stable_subset_of_fixed10(self) -> None:
        module = self.manifest_module()
        manifest = module.load_manifest(FIXTURE_PATH)

        fixed3 = module.select_cases(manifest, "fixed3")
        fixed10 = module.select_cases(manifest, "fixed10")

        self.assertEqual(manifest["schema_version"], "8099.regression-manifest/v2")
        self.assertEqual(len(fixed3), 3)
        self.assertEqual(len(fixed10), 9)
        self.assertEqual(len(manifest["cases"]), 10)
        self.assertTrue({case["id"] for case in fixed3} <= {case["id"] for case in fixed10})
        self.assertEqual(
            len({(case["menu_code"], case["articleid"]) for case in fixed10}),
            9,
        )

    def test_manifest_requires_role_title_hash_capture_and_replay(self) -> None:
        module = self.manifest_module()
        invalid = {
            "schema_version": "8099.regression-manifest/v2",
            "manifest_version": "test-v1",
            "captured_at": "2026-07-14T09:00:00+08:00",
            "hash_algorithm": "sha256",
            "subsets": {"fixed3": ["case-1"], "fixed10": ["case-1"]},
            "cases": [
                {
                    "id": "case-1",
                    "name": "Case 1",
                    "menu_code": "project_notice",
                    "articleid": "article-1",
                    "title": "Case 1 title",
                    "captured_at": "2026-07-14T09:00:00+08:00",
                    "record_sha256": "a" * 64,
                    "capability_tags": ["ordinary_notice"],
                    "materials": [
                        {
                            "menu_code": "project_notice",
                            "articleid": "article-1",
                            "title": "Case 1 title",
                            "captured_at": "2026-07-14T09:00:00+08:00",
                            "title_sha256": "b" * 64,
                            "body_sha256": "c" * 64,
                            "attachments_sha256": "d" * 64,
                            "record_sha256": "a" * 64,
                            "attachments": [],
                        }
                    ],
                    "replay": {
                        "method": "POST",
                        "path": "/analysis/prepare",
                        "request": {"primary_materials": [], "auxiliary_materials": []},
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "role|title|hash|replay"):
                module.load_manifest(path)

    def test_record_fingerprint_is_stable_when_attachment_order_changes(self) -> None:
        module = self.manifest_module()
        record = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "Notice",
            "audittime": "2026-07-14 08:00:00",
            "updatetime": "2026-07-14 08:10:00",
            "content_text": "Line one\r\nLine two",
            "attachments": [
                {
                    "articleattid": "att-2",
                    "filename": "b.xlsx",
                    "uploadtime": "2026-07-14",
                    "parsed_text": "attachment b",
                },
                {
                    "articleattid": "att-1",
                    "filename": "a.docx",
                    "uploadtime": "2026-07-14",
                    "parsed_text": "attachment a",
                },
            ],
        }

        first = module.build_record_fingerprint(record)
        second = module.build_record_fingerprint(
            {**record, "attachments": list(reversed(record["attachments"]))}
        )

        self.assertEqual(first, second)
        self.assertRegex(first["record_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(first["attachments"]), 2)
        self.assertTrue(all(item["metadata_sha256"] for item in first["attachments"]))

    def test_attachment_locator_change_is_rejected(self) -> None:
        module = self.manifest_module()
        record = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "Notice",
            "content_text": "Original body",
            "attachments": [
                {
                    "articleattid": "att-1",
                    "filename": "notice.pdf",
                    "filesize": "1024",
                    "filepath": "/files/original/notice.pdf",
                    "parsed_text": "attachment source text",
                }
            ],
        }
        expected = module.build_record_fingerprint(record)

        changed = {
            **record,
            "attachments": [
                {
                    **record["attachments"][0],
                    "filepath": "/files/replaced/notice.pdf",
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "attachments_sha256|record_sha256"):
            module.verify_record_fingerprint(changed, expected)

    def test_body_hash_uses_source_preserving_line_normalization(self) -> None:
        module = self.manifest_module()
        record = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "Notice",
            "content_text": "  \uff21  \r\nLine two\t\r",
            "attachments": [],
        }

        fingerprint = module.build_record_fingerprint(record)

        expected = hashlib.sha256("  \uff21\nLine two\n".encode("utf-8")).hexdigest()
        self.assertEqual(fingerprint["body_sha256"], expected)

    def test_attachment_raw_content_change_is_rejected_with_unchanged_metadata(self) -> None:
        module = self.manifest_module()
        record = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "Notice",
            "content_text": "Original body",
            "attachments": [
                {
                    "articleattid": "att-1",
                    "filename": "notice.pdf",
                    "filepath": "/files/notice.pdf",
                    "content_bytes": b"original bytes",
                }
            ],
        }
        expected = module.build_record_fingerprint(record)
        changed = {
            **record,
            "attachments": [{**record["attachments"][0], "content_bytes": b"replacement bytes"}],
        }

        with self.assertRaisesRegex(ValueError, "attachments_sha256|record_sha256"):
            module.verify_record_fingerprint(changed, expected)

    def test_attachment_text_fallback_uses_text_only_prefix(self) -> None:
        module = self.manifest_module()
        record = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "Notice",
            "content_text": "Original body",
            "attachments": [
                {
                    "articleattid": "att-1",
                    "filename": "notice.pdf",
                    "filepath": "/files/notice.pdf",
                    "parsed_text": "Line one  \r\nLine two\t",
                }
            ],
        }

        fingerprint = module.build_record_fingerprint(record)
        attachment = fingerprint["attachments"][0]

        expected = hashlib.sha256(b"TEXT_ONLY\nLine one\nLine two").hexdigest()
        self.assertEqual(attachment["content_sha256"], expected)
        self.assertEqual(attachment["content_hash_source"], "text_only")

    def test_attachment_text_fallback_rejects_missing_or_empty_parsed_text(self) -> None:
        from scripts import run_fixed_regression as runner

        for attachment in ({}, {"parsed_text": "  \r\n\t"}):
            with self.subTest(attachment=attachment):
                with self.assertRaisesRegex(ValueError, "attachment content"):
                    runner._text_only_attachment_hash(attachment)

    def test_offline_material_replay_does_not_trust_captured_content_hash(self) -> None:
        evaluator = importlib.import_module("app.offline_quality_evaluator")
        module = self.manifest_module()
        source_record = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "Notice",
            "content_text": "Original body",
            "attachments": [
                {
                    "articleattid": "att-1",
                    "filename": "notice.txt",
                    "filepath": "/files/notice.txt",
                    "parsed_text": "attachment source text",
                }
            ],
        }
        fingerprint = module.build_record_fingerprint(source_record)
        snapshot_record = {
            **source_record,
            "attachments": [
                {
                    key: value
                    for key, value in source_record["attachments"][0].items()
                    if key != "parsed_text"
                }
            ],
        }

        self.assertFalse(
            evaluator._material_hashes_complete(
                {
                    "materials": [
                        {
                            "expected": fingerprint,
                            "actual": fingerprint,
                            "record": snapshot_record,
                        }
                    ]
                }
            )
        )

    def test_offline_material_replay_accepts_recomputable_raw_bytes(self) -> None:
        evaluator = importlib.import_module("app.offline_quality_evaluator")
        module = self.manifest_module()
        content = b"original attachment bytes"
        source_record = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "Notice",
            "content_text": "Original body",
            "attachments": [
                {
                    "articleattid": "att-1",
                    "filename": "notice.pdf",
                    "filepath": "/files/notice.pdf",
                    "content_bytes": content,
                }
            ],
        }
        fingerprint = module.build_record_fingerprint(source_record)
        snapshot_record = {
            **source_record,
            "attachments": [
                {
                    "articleattid": "att-1",
                    "filename": "notice.pdf",
                    "filepath": "/files/notice.pdf",
                }
            ],
        }
        verification = {
            "identity": ["att-1", "notice.pdf", "/files/notice.pdf"],
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_hash_source": "raw_bytes",
            "content_length": len(content),
            "verifier": "stream_sha256_v1",
        }
        snapshot = {
            "materials": [
                {
                    "expected": fingerprint,
                    "actual": fingerprint,
                    "record": snapshot_record,
                    "attachment_content_verification": [verification],
                }
            ]
        }
        self.assertTrue(evaluator._material_hashes_complete(snapshot))

        verification["content_sha256"] = hashlib.sha256(b"replacement bytes").hexdigest()
        self.assertFalse(evaluator._material_hashes_complete(snapshot))

    def test_record_fingerprint_rejects_caller_supplied_hash_without_content(self) -> None:
        module = self.manifest_module()
        record = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "Notice",
            "content_text": "Original body",
            "attachments": [
                {
                    "articleattid": "att-1",
                    "filename": "notice.pdf",
                    "filepath": "/files/notice.pdf",
                    "_content_sha256": "a" * 64,
                    "_content_hash_source": "raw_bytes",
                    "_content_length": 99,
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "attachment content"):
            module.build_record_fingerprint(record)

    def test_runner_limits_streamed_attachment_size_without_persisting_bytes(self) -> None:
        from scripts import run_fixed_regression as runner

        class CountingStream(httpx.SyncByteStream):
            def __init__(self) -> None:
                self.yielded = 0

            def __iter__(self):
                for chunk in (b"x" * 700, b"y" * 700, b"z" * 700):
                    self.yielded += 1
                    yield chunk

        stream = CountingStream()
        client_type = httpx.Client
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                stream=stream,
                headers={"content-type": "application/pdf"},
                request=request,
            )
        )

        def build_client(**kwargs):
            return client_type(transport=transport, follow_redirects=True)

        with patch.dict(os.environ, {"ATTACHMENT_MAX_DOWNLOAD_MB": "0.001"}), patch.object(
            runner.httpx, "Client", side_effect=build_client
        ):
            with self.assertRaisesRegex(ValueError, "attachment content"):
                runner._attachment_content_hash(
                    {
                        "articleattid": "too-large-att",
                        "filename": "large.pdf",
                        "uploadtime": "2026-07-14",
                    }
                )

        self.assertEqual(stream.yielded, 2)

    def test_runner_rehashes_same_attachment_on_every_attempt(self) -> None:
        from scripts import run_fixed_regression as runner

        payloads = iter((b"first-version", b"second-version"))
        client_type = httpx.Client
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=next(payloads),
                headers={"content-type": "application/pdf"},
                request=request,
            )
        )

        def build_client(**kwargs):
            return client_type(transport=transport, follow_redirects=True)

        attachment = {
            "articleattid": "changing-att",
            "filename": "notice.pdf",
            "filesize": "13",
            "uploadtime": "2026-07-14",
        }
        with patch.object(runner.httpx, "Client", side_effect=build_client):
            first = runner._attachment_content_hash(attachment)
            second = runner._attachment_content_hash(attachment)

        self.assertNotEqual(first["content_sha256"], second["content_sha256"])

    def test_runner_attachment_receipt_contains_no_raw_bytes_or_base64(self) -> None:
        from scripts import run_fixed_regression as runner

        client_type = httpx.Client
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"safe attachment payload",
                headers={"content-type": "application/pdf"},
                request=request,
            )
        )

        def build_client(**kwargs):
            return client_type(transport=transport, follow_redirects=True)

        with patch.object(runner.httpx, "Client", side_effect=build_client):
            receipt = runner._attachment_content_hash(
                {"articleattid": "safe-att", "filename": "safe.pdf"}
            )

        self.assertEqual(
            set(receipt),
            {"content_sha256", "content_hash_source", "content_length", "verifier"},
        )
        self.assertFalse(any(isinstance(value, bytes) for value in receipt.values()))
        self.assertFalse(any("base64" in key.lower() for key in receipt))

    def test_manifest_contract_rejects_sensitive_fields_and_oversize_input(self) -> None:
        module = self.manifest_module()
        manifest = module.load_manifest(FIXTURE_PATH)

        for field in (
            "report_markdown",
            "access_token",
            "private_secret",
            "raw_file_bytes",
            "payload_base64",
            "content_text",
        ):
            with self.subTest(field=field):
                sensitive = json.loads(json.dumps(manifest))
                sensitive["metadata"] = {"nested": {field: "secret body"}}
                with self.assertRaisesRegex(ValueError, "sensitive|forbidden"):
                    module.build_manifest_contract(sensitive)

        oversized = json.loads(json.dumps(manifest))
        oversized["notes"] = "x" * (300 * 1024)
        with self.assertRaisesRegex(ValueError, "size|large"):
            module.build_manifest_contract(oversized)

    def test_manifest_contract_is_minimal_bounded_and_contains_no_raw_payload(self) -> None:
        module = self.manifest_module()
        manifest = module.load_manifest(FIXTURE_PATH)

        contract = module.build_manifest_contract(manifest)
        serialized = module.canonical_json_bytes(contract)

        self.assertLessEqual(len(serialized), module.MANIFEST_CONTRACT_MAX_BYTES)
        self.assertNotIn("source", contract)
        forbidden = {
            "api_key",
            "content_bytes",
            "content_bytes_base64",
            "evidence_pack",
            "formal_body",
            "memory",
            "memory_items",
            "report_ir",
            "report_markdown",
        }

        def keys(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    yield str(key)
                    yield from keys(nested)
            elif isinstance(value, list):
                for nested in value:
                    yield from keys(nested)

        self.assertFalse(forbidden & set(keys(contract)))
        module.validate_manifest_contract(contract)

    def test_runner_atomic_json_write_removes_temporary_file_on_replace_failure(self) -> None:
        from scripts import run_fixed_regression as runner

        self.assertTrue(hasattr(runner, "_write_json_atomically"))
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "summary.json"
            with patch.object(runner.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    runner._write_json_atomically(target, {"safe": True})

            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmpdir).glob("*.tmp")), [])

    def test_record_drift_is_rejected(self) -> None:
        module = self.manifest_module()
        record = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "Notice",
            "content_text": "Original body",
            "attachments": [],
        }
        expected = module.build_record_fingerprint(record)
        changed = {**record, "content_text": "Changed body"}

        with self.assertRaisesRegex(ValueError, "record_sha256"):
            module.verify_record_fingerprint(changed, expected)

    def test_fixed10_manifest_has_complete_identity_hash_and_replay_metadata(self) -> None:
        module = self.manifest_module()
        manifest = module.load_manifest(FIXTURE_PATH)

        for case in module.select_cases(manifest, "fixed10"):
            with self.subTest(case=case["id"]):
                self.assertEqual(case["replay"]["method"], "POST")
                self.assertEqual(case["replay"]["path"], "/analysis/prepare")
                self.assertTrue(case["capability_tags"])
                self.assertEqual(case["materials"][0]["role"], "primary")
                self.assertEqual(case["materials"][0]["record_sha256"], case["record_sha256"])
                self.assertRegex(case["record_sha256"], r"^[0-9a-f]{64}$")
                for material in case["materials"]:
                    for attachment in material["attachments"]:
                        self.assertRegex(attachment["content_sha256"], r"^[0-9a-f]{64}$")
                        self.assertIn(attachment["content_hash_source"], {"raw_bytes", "text_only"})

    def test_expected_attachment_counts_match_frozen_materials(self) -> None:
        module = self.manifest_module()
        manifest = module.load_manifest(FIXTURE_PATH)

        for case in module.select_cases(manifest, "fixed10"):
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    case["expected_attachment_count"],
                    sum(material["attachment_count"] for material in case["materials"]),
                )

    def test_fixed10_identity_set_is_explicitly_frozen(self) -> None:
        module = self.manifest_module()
        manifest = module.load_manifest(FIXTURE_PATH)

        self.assertEqual(
            {
                (case["menu_code"], case["articleid"])
                for case in module.select_cases(manifest, "fixed10")
            },
            {
                ("project_notice", "803492fa-839b-4fff-aa7c-e9c1d9a3028a"),
                ("project_notice", "4695e7d8-9d04-4c5a-b198-00ed022bf413"),
                ("project_notice", "4ca0eae9-bb5f-4301-8d1a-ee4d85cc30b6"),
                ("policy_interpretation", "859bbf46-77b3-4664-9606-e79101c084aa"),
                ("yb_drg", "718b4c38-34b3-4be8-9800-c0c4c24bdb08"),
                ("ylsf", "b10e3e1f-bea5-4d3b-ada6-124be038b8a9"),
                ("project_information", "81a4fe2a-04ad-4591-9613-c717a9e688b0"),
                ("lxzn", "d6fae12f-20e2-4448-a194-c4cc3e8ece54"),
                ("ylsf", "5340b5c6-6faf-4a7c-aa25-ac5445c95e8f"),
            },
        )

    def test_unavailable_source_case_is_explicitly_excluded(self) -> None:
        module = self.manifest_module()
        manifest = module.load_manifest(FIXTURE_PATH)
        excluded = next(
            case
            for case in manifest["cases"]
            if case["id"] == "fixed-4-guizhou-project-analysis"
        )
        excluded_material = excluded["materials"][0]

        self.assertEqual(
            module.excluded_cases(manifest, "fixed10"),
            [
                {
                    "case_id": "fixed-4-guizhou-project-analysis",
                    "menu_code": "project_analysis",
                    "articleid": "4a2e0dbc-1a24-490e-b807-38374f8cd530",
                    "code": "SOURCE_ATTACHMENT_UNAVAILABLE",
                    "observed_at": "2026-07-14T16:00:00+08:00",
                }
            ],
        )
        self.assertEqual(excluded["record_sha256"], "")
        self.assertEqual(excluded_material["record_sha256"], "")
        self.assertEqual(excluded_material["attachments_sha256"], "")
        self.assertNotIn(
            "fixed-4-guizhou-project-analysis",
            {case["id"] for case in module.select_cases(manifest, "fixed10")},
        )

    def test_unavailable_source_case_rejects_stale_aggregate_hashes(self) -> None:
        module = self.manifest_module()
        manifest = module.load_manifest(FIXTURE_PATH)
        excluded = next(
            case
            for case in manifest["cases"]
            if case["id"] == "fixed-4-guizhou-project-analysis"
        )
        excluded["record_sha256"] = "a" * 64
        excluded["materials"][0]["record_sha256"] = "a" * 64
        excluded["materials"][0]["attachments_sha256"] = "b" * 64

        with self.assertRaisesRegex(ValueError, "unavailable.*aggregate|aggregate.*unavailable"):
            module.validate_manifest(manifest)

    def test_replay_identities_must_match_material_roles(self) -> None:
        module = self.manifest_module()
        manifest = module.load_manifest(FIXTURE_PATH)
        manifest["cases"][0]["replay"]["request"]["primary_materials"] = [
            {"menu_code": "project_notice", "articleid": "different-article"}
        ]

        with self.assertRaisesRegex(ValueError, "replay.*material|material.*replay"):
            module.validate_manifest(manifest)

    def test_runner_exposes_manifest_subset_repeat_and_artifact_schema(self) -> None:
        from scripts import run_fixed_regression as runner

        self.assertTrue(hasattr(runner, "build_parser"), "runner parser is not reusable")
        if not hasattr(runner, "build_parser"):
            return
        args = runner.build_parser().parse_args(
            [
                "--manifest",
                str(FIXTURE_PATH),
                "--subset",
                "fixed10",
                "--repeat",
                "2",
                "--stage",
                "S0",
                "--environment",
                "server_test",
                "--report-dir",
                ".",
            ]
        )
        self.assertEqual(args.subset, "fixed10")
        self.assertEqual(args.repeat, 2)
        self.assertEqual(args.stage, "S0")
        self.assertEqual(args.environment, "server_test")
        self.assertEqual(runner.ARTIFACT_SCHEMA_VERSION, "8099.fixed-regression/v2")

        with self.assertRaises(SystemExit):
            runner.build_parser().parse_args(
                ["--environment", "local", "--report-dir", "."]
            )

    def test_s0_runner_rejects_nonbaseline_subset_or_repeat(self) -> None:
        from scripts import run_fixed_regression as runner

        self.assertTrue(
            hasattr(runner, "validate_run_matrix"),
            "runner must validate the S0 execution matrix",
        )
        if not hasattr(runner, "validate_run_matrix"):
            return
        runner.validate_run_matrix("S0", "fixed3", 3)
        runner.validate_run_matrix("S0", "fixed10", 1)
        for subset, repeat in (("fixed3", 1), ("fixed10", 2), ("all", 1)):
            with self.subTest(subset=subset, repeat=repeat):
                with self.assertRaisesRegex(ValueError, "S0.*matrix|matrix.*S0"):
                    runner.validate_run_matrix("S0", subset, repeat)


if __name__ == "__main__":
    unittest.main()

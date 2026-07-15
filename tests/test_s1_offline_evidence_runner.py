from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.compact_pack import stabilize_character_fields
from app.evidence_schema import create_evidence_item, text_source_hash
from app.regression_manifest import build_record_fingerprint


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "8099_regression_cases.json"


class FakeResponse:
    def __init__(self, body: dict[str, object], status_code: int = 200):
        self._body = body
        self.status_code = status_code
        self.text = ""

    def json(self) -> dict[str, object]:
        return self._body


class FakeClient:
    def __init__(self, record: dict[str, object], full_pack: dict[str, object], compact_pack: dict[str, object]):
        self.record = record
        self.full_pack = full_pack
        self.compact_pack = compact_pack
        self.calls: list[tuple[str, str]] = []

    def get(self, path: str, **_: object) -> FakeResponse:
        self.calls.append(("GET", path))
        if path.startswith("/records/"):
            return FakeResponse({"success": True, **self.record})
        if path == "/analysis/packs/pack-offline":
            return FakeResponse(self.compact_pack)
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path: str, **_: object) -> FakeResponse:
        self.calls.append(("POST", path))
        if path == "/analysis/prepare":
            return FakeResponse(
                {
                    "success": True,
                    "pack_id": "pack-offline",
                    "evidence_pack": self.full_pack,
                }
            )
        raise AssertionError(f"unexpected POST {path}")


class S1OfflineEvidenceRunnerTests(unittest.TestCase):
    @staticmethod
    def packs() -> tuple[dict[str, object], dict[str, object]]:
        text = "采购周期为2年。"
        source_ref = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "attachment_id": None,
            "filename": None,
            "page_no": None,
            "sheet_name": None,
            "table_index": None,
            "row": None,
            "column": None,
            "cell_range": None,
            "quote": text,
            "source_hash": text_source_hash(text),
            "region": None,
        }
        direct = create_evidence_item(
            level="A",
            kind="article_text",
            value=text,
            source_ref=source_ref,
            mandatory=True,
        )
        full = {
            "pack_id": "pack-offline",
            "evidence_schema_version": 2,
            "evidence_items": [direct],
        }
        compact = {
            **full,
            "hard_limit_chars": 80_000,
        }
        stabilize_character_fields(compact)
        return full, compact

    @staticmethod
    def record_and_case() -> tuple[dict[str, object], dict[str, object]]:
        record = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "集中带量采购公告",
            "audittime": "2026-07-15 08:00:00",
            "updatetime": "2026-07-15 08:00:00",
            "content_text": "采购周期为2年。",
            "attachments": [],
        }
        expected = {
            **build_record_fingerprint(record),
            "role": "primary",
            "captured_at": "2026-07-15T08:00:00+08:00",
        }
        case = {
            "id": "fixed-test-offline",
            "menu_code": "project_notice",
            "articleid": "article-1",
            "materials": [expected],
            "replay": {
                "method": "POST",
                "path": "/analysis/prepare",
                "request": {
                    "primary_materials": [{"menu_code": "project_notice", "articleid": "article-1"}],
                    "auxiliary_materials": [],
                },
            },
        }
        return record, case

    def test_fixed10_loader_keeps_declared_count_and_manifest_exclusion(self):
        from scripts import run_s1_evidence_regression as runner

        cases, exclusions, declared_count = runner.load_subset(FIXTURE_PATH, "fixed10")

        self.assertEqual(10, declared_count)
        self.assertEqual(9, len(cases))
        self.assertEqual(1, len(exclusions))
        self.assertEqual("SOURCE_ATTACHMENT_UNAVAILABLE", exclusions[0]["code"])

    def test_case_filter_supports_resuming_specific_manifest_cases(self):
        from scripts import run_s1_evidence_regression as runner

        cases = [{"id": "case-a"}, {"id": "case-b"}, {"id": "case-c"}]

        self.assertEqual([{"id": "case-b"}], runner.filter_cases(cases, ["case-b"]))
        with self.assertRaises(ValueError):
            runner.filter_cases(cases, ["missing-case"])

    def test_filtered_case_run_is_not_a_complete_fixed10_gate(self):
        from scripts import run_s1_evidence_regression as runner

        status = runner.execution_scope_status(
            subset="fixed10",
            requested_case_ids=["case-a"],
            declared_count=10,
            selected_count=1,
            attempted_count=1,
            executed_count=1,
            failure_count=0,
        )

        self.assertEqual("case_filter", status["execution_scope"])
        self.assertFalse(status["subset_complete"])
        self.assertFalse(status["gate_passed"])
        self.assertIsNone(status["gate_subset"])
        self.assertTrue(status["execution_passed"])

    def test_full_subset_with_only_runtime_exclusions_does_not_pass(self):
        from scripts import run_s1_evidence_regression as runner

        status = runner.execution_scope_status(
            subset="fixed10",
            requested_case_ids=[],
            declared_count=10,
            selected_count=2,
            attempted_count=2,
            executed_count=0,
            failure_count=0,
        )

        self.assertTrue(status["subset_complete"])
        self.assertFalse(status["execution_passed"])
        self.assertFalse(status["gate_passed"])

    def test_capture_materials_rejects_unlisted_attachment_before_download(self):
        from scripts import run_s1_evidence_regression as runner

        record, case = self.record_and_case()
        record["attachments"] = [
            {
                "articleattid": "unexpected-attachment",
                "filename": "unexpected.pdf",
                "source_url": "https://example.invalid/unexpected.pdf",
            }
        ]
        client = FakeClient(record, {}, {})

        with patch.object(runner, "_attachment_content_verifications") as verifier:
            with self.assertRaises(ValueError):
                runner._capture_materials(client, case)

        verifier.assert_not_called()

    def test_text_only_attachment_fallback_is_classified_as_source_unavailable(self):
        from scripts import run_s1_evidence_regression as runner

        record, case = self.record_and_case()
        attachment = {
            "articleattid": "attachment-1",
            "filename": "source.pdf",
            "filesize": "100",
            "uploadtime": "2026-07-15 08:00:00",
        }
        record["attachments"] = [attachment]
        case["materials"][0]["attachment_count"] = 1
        case["materials"][0]["attachments"] = [attachment]
        identity = ("attachment-1", "source.pdf", "100")
        client = FakeClient(record, {}, {})

        with patch.object(
            runner,
            "_attachment_content_verifications",
            return_value={identity: {"content_hash_source": "text_only", "content_sha256": "0" * 64}},
        ):
            with self.assertRaises(runner.SourceAttachmentUnavailable):
                runner._capture_materials(client, case)

    def test_offline_case_never_calls_analysis_run_and_artifact_replays(self):
        from scripts import run_s1_evidence_regression as runner

        record, case = self.record_and_case()
        full, compact = self.packs()
        client = FakeClient(record, full, compact)

        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_dir = Path(temp_dir) / "case"
            artifact = runner.run_offline_case(client, case, artifact_dir)
            replay = runner.verify_case_replay(artifact_dir)

        self.assertTrue(artifact["evaluation"]["passed"])
        self.assertTrue(replay["passed"])
        self.assertFalse(any(path == "/analysis/run" for _, path in client.calls))
        self.assertEqual(
            [
                ("GET", "/records/project_notice/article-1"),
                ("POST", "/analysis/prepare"),
                ("GET", "/analysis/packs/pack-offline"),
            ],
            client.calls,
        )

    def test_case_replay_detects_snapshot_tampering(self):
        from scripts import run_s1_evidence_regression as runner

        record, case = self.record_and_case()
        full, compact = self.packs()
        client = FakeClient(record, full, compact)

        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_dir = Path(temp_dir) / "case"
            runner.run_offline_case(client, case, artifact_dir)
            (artifact_dir / "compact-pack.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                runner.verify_case_replay(artifact_dir)


if __name__ == "__main__":
    unittest.main()

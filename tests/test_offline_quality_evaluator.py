from __future__ import annotations

import importlib
import importlib.util
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from docx import Document


class OfflineQualityEvaluatorTests(unittest.TestCase):
    def evaluator_module(self):
        self.assertIsNotNone(
            importlib.util.find_spec("app.offline_quality_evaluator"),
            "app.offline_quality_evaluator is required",
        )
        return importlib.import_module("app.offline_quality_evaluator")

    @staticmethod
    def clean_result(case_id: str = "case-1", *, deliverable: bool = False) -> dict[str, object]:
        return {
            "sample_id": f"{case_id}-attempt-1",
            "case_id": case_id,
            "attempt": 1,
            "passed": True,
            "status": "finished" if deliverable else "needs_manual_review",
            "provider": "dify",
            "workflow_run_id": f"workflow-{case_id}",
            "provider_run_id": f"workflow-{case_id}",
            "compact_pack_chars": 78000,
            "input_strategy": "light_compact",
            "evidence_pack_sha256": "b" * 64,
            "timings": {
                "prepare_ms": 100,
                "generation_ms": 1000,
                "local_quality_gate_ms": 50,
                "repair_ms": 0,
                "export_check_ms": 20,
                "word_export_ms": 100,
                "total_ms": 1170,
            },
            "quality_status": "passed" if deliverable else "needs_manual_review",
            "quality_passed": deliverable,
            "unsupported_fact_count": 2,
            "primary_failure_code": "" if deliverable else "UNSUPPORTED_FACT",
            "secondary_failure_codes": [],
            "quality_failure_codes": [] if deliverable else ["UNSUPPORTED_FACT"],
            "generation_failure_codes": [],
            "deliverable": deliverable,
            "needs_manual_review": not deliverable,
            "word_export_available": True,
            "draft_word_export_available": True,
            "final_word_export_available": deliverable,
            "word_download_available": True,
            "word_generated": True,
            "forbidden_phrase_hits": [],
            "word_scan_hits": {},
            "staging_artifacts": [],
            "history_consistency": {"consistent": True, "mismatches": []},
            "identity_hash_complete": True,
            "metrics_complete": True,
            "snapshot_replayable": True,
            "report_chars": 200,
            "report_section_count": 2,
            "elapsed_seconds": 12.5,
            "snapshots": {},
        }

    @staticmethod
    def bind_manifest_contract(artifact: dict[str, object]) -> dict[str, object]:
        declared = [dict(item) for item in artifact["declared_cases"]]
        used_ids = {str(item["case_id"]) for item in declared}
        numeric_case_ids = all(
            str(item["case_id"]).startswith("case-")
            and str(item["case_id"])[5:].isdigit()
            for item in declared
        )
        next_dummy = 1
        while len(declared) < 10:
            case_id = (
                f"case-{next_dummy}" if numeric_case_ids else f"dummy-case-{next_dummy}"
            )
            next_dummy += 1
            if case_id in used_ids:
                continue
            declared.append(
                {
                    "case_id": case_id,
                    "menu_code": f"menu-{case_id}",
                    "articleid": f"article-{case_id}",
                }
            )
            used_ids.add(case_id)
        exclusions = {
            str(item["case_id"]): dict(item)
            for item in artifact.get("excluded_cases", [])
        }
        cases: list[dict[str, object]] = []
        for identity in declared:
            case_id = str(identity["case_id"])
            skipped = exclusions.get(case_id)
            record_hash = "" if skipped else "e" * 64
            attachments: list[dict[str, object]] = []
            attachments_hash = "d" * 64
            if skipped:
                attachments_hash = ""
                attachments = [
                    {
                        "articleattid": f"attachment-{case_id}",
                        "filename": f"{case_id}.docx",
                        "filepath_sha256": "a" * 64,
                        "metadata_sha256": "b" * 64,
                        "content_hash_source": "unavailable",
                        "content_sha256": "",
                        "content_length": "0",
                    }
                ]
            material = {
                "role": "primary",
                "menu_code": identity["menu_code"],
                "articleid": identity["articleid"],
                "title": case_id,
                "captured_at": "2026-07-14T09:00:00+08:00",
                "title_sha256": "a" * 64,
                "body_sha256": "c" * 64,
                "attachments_sha256": attachments_hash,
                "record_sha256": record_hash,
                "attachments": attachments,
            }
            case: dict[str, object] = {
                "id": case_id,
                "name": case_id,
                "menu_code": identity["menu_code"],
                "articleid": identity["articleid"],
                "title": case_id,
                "captured_at": "2026-07-14T09:00:00+08:00",
                "record_sha256": record_hash,
                "expected_attachment_count": len(attachments),
                "capability_tags": ["synthetic_test"],
                "materials": [material],
                "replay": {
                    "method": "POST",
                    "path": "/analysis/prepare",
                    "request": {
                        "primary_materials": [
                            {
                                "menu_code": identity["menu_code"],
                                "articleid": identity["articleid"],
                            }
                        ],
                        "auxiliary_materials": [],
                    },
                },
            }
            if skipped:
                case["skip"] = {
                    "code": skipped["code"],
                    "observed_at": skipped["observed_at"],
                }
            cases.append(case)
        snapshot = {
            "schema_version": "8099.regression-manifest/v2",
            "manifest_version": "synthetic-test-v1",
            "captured_at": "2026-07-14T09:00:00+08:00",
            "hash_algorithm": "sha256",
            "source": "unit-test",
            "cases": cases,
            "subsets": {
                "fixed3": [str(item["case_id"]) for item in declared[:3]],
                "fixed10": [str(item["case_id"]) for item in declared],
            },
        }
        manifest_module = importlib.import_module("app.regression_manifest")
        contract = manifest_module.build_manifest_contract(snapshot)
        payload = manifest_module.canonical_json_bytes(contract)
        artifact["manifest_contract"] = contract
        artifact["manifest_version"] = snapshot["manifest_version"]
        artifact["manifest_sha256"] = hashlib.sha256(payload).hexdigest()
        bindings_by_case = {
            str(case["id"]): [
                {
                    "role": str(material["role"]),
                    "menu_code": str(material["menu_code"]),
                    "articleid": str(material["articleid"]),
                    "record_sha256": str(material["record_sha256"]),
                }
                for material in case["materials"]
            ]
            for case in contract["cases"]
            if not isinstance(case.get("skip"), dict)
        }
        for sample in artifact.get("samples", []):
            if isinstance(sample, dict):
                sample["material_bindings"] = json.loads(
                    json.dumps(bindings_by_case[str(sample["case_id"])])
                )
        return artifact

    def artifact(self, samples: list[dict[str, object]]) -> dict[str, object]:
        case_ids = sorted(
            {str(sample["case_id"]) for sample in samples},
            key=lambda value: (
                0,
                int(value[5:]),
            )
            if value.startswith("case-") and value[5:].isdigit()
            else (1, value),
        )
        selected_cases = [
            {
                "case_id": case_id,
                "menu_code": f"menu-{case_id}",
                "articleid": f"article-{case_id}",
            }
            for case_id in case_ids
        ]
        artifact: dict[str, object] = {
            "schema_version": "8099.fixed-regression/v2",
            "manifest_version": "2026-07-14.1",
            "manifest_sha256": "a" * 64,
            "stage": "S0",
            "environment": "server_test",
            "subset": "fixed10",
            "repeat": 1,
            "declared_case_count": len(selected_cases),
            "selected_case_count": len(selected_cases),
            "declared_cases": selected_cases,
            "selected_cases": selected_cases,
            "samples": samples,
            "failures": [],
        }
        return self.bind_manifest_contract(artifact)

    @staticmethod
    def server_baseline_evaluations(
        *evaluations: dict[str, object],
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for evaluation in evaluations:
            server = json.loads(json.dumps(evaluation))
            server["environment"] = "server_test"
            result.append(server)
        return result

    def test_same_artifact_evaluates_identically_twice(self) -> None:
        module = self.evaluator_module()
        artifact = self.artifact([self.clean_result(), self.clean_result("case-2", deliverable=True)])

        first = module.evaluate_artifact(artifact)
        second = module.evaluate_artifact(json.loads(json.dumps(artifact)))

        self.assertEqual(first, second)
        self.assertRegex(first["evaluator_rules_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(first["evaluator_version"])

    def test_evaluator_rejects_legacy_full_manifest_snapshot(self) -> None:
        module = self.evaluator_module()
        artifact = self.artifact([self.clean_result()])
        artifact["manifest_snapshot"] = artifact.pop("manifest_contract")

        with self.assertRaisesRegex(ValueError, "manifest_contract"):
            module.evaluate_artifact(artifact)

    def test_p95_uses_nearest_rank(self) -> None:
        module = self.evaluator_module()

        self.assertEqual(module.nearest_rank_percentile(list(range(1, 21)), 0.95), 19)
        self.assertEqual(module.nearest_rank_percentile([7], 0.95), 7)

    def test_non_deliverable_requires_one_primary_failure_code(self) -> None:
        module = self.evaluator_module()
        sample = self.clean_result()
        sample["primary_failure_code"] = ""

        with self.assertRaisesRegex(ValueError, "primary_failure_code"):
            module.evaluate_artifact(self.artifact([sample]))

    def test_deliverable_rejects_primary_failure_code(self) -> None:
        module = self.evaluator_module()
        sample = self.clean_result(deliverable=True)
        sample["primary_failure_code"] = "UNSUPPORTED_FACT"

        with self.assertRaisesRegex(ValueError, "deliverable.*primary_failure_code"):
            module.evaluate_artifact(self.artifact([sample]))

    def test_primary_failure_code_cannot_repeat_as_secondary(self) -> None:
        module = self.evaluator_module()
        sample = self.clean_result()
        sample["secondary_failure_codes"] = ["UNSUPPORTED_FACT"]

        with self.assertRaisesRegex(ValueError, "primary_failure_code.*secondary"):
            module.evaluate_artifact(self.artifact([sample]))

    def test_run_and_history_state_must_match(self) -> None:
        module = self.evaluator_module()
        run = {
            "run_id": "run_case0001",
            "pack_id": "pack-case-1",
            "status": "needs_manual_review",
            "provider": "dify",
            "workflow_run_id": "workflow-case-1",
            "compact_pack_chars": 78000,
            "quality_passed": False,
            "primary_failure_code": "UNSUPPORTED_FACT",
            "word_export_available": True,
            "draft_word_export_available": True,
            "final_word_export_available": False,
            "deliverable": False,
            "needs_manual_review": True,
        }
        history = {
            **run,
            "quality_status": "needs_manual_review",
            "word_download_available": True,
            "word_generated": True,
        }

        consistent = module.compare_run_history(run, history, word_downloaded=True)
        contradictory = module.compare_run_history(
            run,
            {**history, "compact_pack_chars": 47000, "final_word_export_available": True},
            word_downloaded=True,
        )

        self.assertEqual(consistent, {"consistent": True, "mismatches": []})
        self.assertFalse(contradictory["consistent"])
        self.assertEqual(
            {item["field"] for item in contradictory["mismatches"]},
            {"compact_pack_chars", "final_word_export_available"},
        )

    def test_history_word_generated_must_match_word_availability(self) -> None:
        module = self.evaluator_module()
        run = {
            "run_id": "run_case0001",
            "pack_id": "pack-case-1",
            "status": "failed",
            "provider": "dify",
            "workflow_run_id": "workflow-case-1",
            "compact_pack_chars": 78000,
            "quality_passed": False,
            "primary_failure_code": "GENERATION_FAILED",
            "word_export_available": False,
            "draft_word_export_available": False,
            "final_word_export_available": False,
            "deliverable": False,
            "needs_manual_review": True,
        }
        history = {
            **run,
            "quality_status": "failed",
            "quality_gate_status": "",
            "word_download_available": False,
            "word_generated": True,
        }

        result = module.compare_run_history(run, history, word_downloaded=False)

        self.assertIn("word_generated", {item["field"] for item in result["mismatches"]})

    def test_history_list_rejects_report_memory_and_evidence_payloads(self) -> None:
        module = self.evaluator_module()
        history_query = {
            "items": [
                {
                    "run_id": "run_case0001",
                    "report_markdown": "full report",
                    "nested": {"memory_items": ["style"], "evidence_pack": {"x": 1}},
                }
            ]
        }

        self.assertEqual(
            set(module._history_query_forbidden_fields(history_query)),
            {"evidence_pack", "memory_items", "report_markdown"},
        )

    def test_evaluator_rules_hash_includes_diagnostics_source(self) -> None:
        module = self.evaluator_module()

        self.assertRegex(module.EVALUATOR_RULES["diagnostics_source_sha256"], r"^[0-9a-f]{64}$")

    def test_snapshot_hashes_are_verified_for_offline_replay(self) -> None:
        module = self.evaluator_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot = root / "cases" / "case-1" / "run.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text('{"run_id":"run_case0001"}\n', encoding="utf-8")
            sample = self.clean_result()
            sample["snapshots"] = {
                "run": {
                    "path": "cases/case-1/run.json",
                    "sha256": module.sha256_file(snapshot),
                }
            }
            artifact = self.artifact([sample])

            verified = module.verify_snapshot_replay(root, artifact)
            snapshot.write_text('{"run_id":"changed"}\n', encoding="utf-8")

            self.assertEqual(verified, {"verified": 1, "total": 1})
            with self.assertRaisesRegex(ValueError, "snapshot hash"):
                module.verify_snapshot_replay(root, artifact)

    def _snapshot_artifact(
        self,
        root: Path,
        *,
        report_markdown: str = "# 公告分析\n\n公告明确执行要求。",
        history_compact_pack_chars: int = 78000,
        word_text: str = "公告明确执行要求。",
    ) -> dict[str, object]:
        module = self.evaluator_module()
        manifest_module = importlib.import_module("app.regression_manifest")
        sample = self.clean_result()
        run = {
            "run_id": "run-case-1",
            "pack_id": "pack-case-1",
            "status": "needs_manual_review",
            "provider": "dify",
            "workflow_run_id": "workflow-case-1",
            "provider_run_id": "workflow-case-1",
            "compact_pack_chars": 78000,
            "input_strategy": "light_compact",
            "quality_passed": False,
            "quality_check": {"passed": False, "issues": []},
            "quality_gate": {
                "deliverable_status": "needs_manual_review",
                "unsupported_fact_count": 0,
            },
            "primary_failure_code": "LOCAL_QUALITY_GATE_FAILED",
            "secondary_failure_codes": [],
            "quality_failure_codes": ["LOCAL_QUALITY_GATE_FAILED"],
            "generation_failure_codes": [],
            "word_export_available": True,
            "draft_word_export_available": True,
            "final_word_export_available": False,
            "deliverable": False,
            "needs_manual_review": True,
            "report_markdown": report_markdown,
            "report_ir": {
                "title": "公告分析",
                "lead_paragraphs": ["公告明确执行要求。"],
                "sections": [],
            },
            "timings": {
                "prepare_ms": 100,
                "generation_ms": 1000,
                "local_quality_gate_ms": 50,
                "repair_ms": 0,
                "export_check_ms": 20,
                "word_export_ms": 100,
                "total_ms": 1170,
            },
        }
        evidence_pack = {
            "pack_id": "pack-case-1",
            "primary_materials": [
                {
                    "title": "公告",
                    "content_text": "公告明确执行要求。",
                    "attachments": [],
                }
            ],
            "auxiliary_materials": [],
        }
        history = {
            **{key: run[key] for key in module.CONSISTENCY_FIELDS},
            "compact_pack_chars": history_compact_pack_chars,
            "quality_status": "needs_manual_review",
            "quality_passed": False,
            "quality_gate_status": "needs_manual_review",
            "word_download_available": True,
            "word_generated": True,
        }
        record = {
            "menu_code": "project_notice",
            "articleid": "article-1",
            "title": "公告",
            "content_text": "公告明确执行要求。",
            "attachments": [],
        }
        fingerprint = manifest_module.build_record_fingerprint(record)
        values: dict[str, object] = {
            "materials": {
                "case_id": "case-1",
                "materials": [
                    {
                        "role": "primary",
                        "expected": {**fingerprint, "role": "primary"},
                        "actual": fingerprint,
                        "record": record,
                    }
                ],
            },
            "prepare": {"pack_id": "pack-case-1", "evidence_pack": evidence_pack},
            "compact_pack": {
                **evidence_pack,
                "compact_pack_chars": 78000,
                "input_strategy": "light_compact",
            },
            "run": run,
            "report": {"report_markdown": report_markdown, "report_ir": run["report_ir"]},
            "diagnostics": {"quality_gate": {"unsupported_fact_count": 0}},
            "history": history,
            "history_query": {"items": [history]},
            "word_contract": {
                "word_export_enabled": True,
                "endpoint_statuses": {
                    "run_download": 200,
                    "report_export": 200,
                    "report_export_checked": 200,
                    "file_download": 200,
                },
                "created_docx": ["run.docx"],
                "staging_artifacts": [],
                "download_locators": {},
                "timings": sample["timings"],
            },
        }
        snapshots: dict[str, dict[str, str]] = {}
        sample_dir = root / "cases" / "case-1" / "attempt-1"
        sample_dir.mkdir(parents=True)
        for name, value in values.items():
            path = sample_dir / f"{name}.json"
            path.write_text(
                json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            snapshots[name] = {
                "path": path.relative_to(root).as_posix(),
                "sha256": module.sha256_file(path),
            }
        for snapshot_name, filename in (
            ("word_run", "run.docx"),
            ("word_export", "export.docx"),
            ("word_checked", "checked.docx"),
        ):
            word_path = sample_dir / filename
            document = Document()
            document.add_paragraph(word_text)
            document.save(word_path)
            snapshots[snapshot_name] = {
                "path": word_path.relative_to(root).as_posix(),
                "sha256": module.sha256_file(word_path),
            }
        sample.update(
            {
                "run_id": "run-case-1",
                "pack_id": "pack-case-1",
                "unsupported_fact_count": 999,
                "forbidden_phrase_hits": [],
                "word_scan_hits": {},
                "history_consistency": {"consistent": True, "mismatches": []},
                "snapshots": snapshots,
            }
        )
        return self.artifact([sample])

    def test_snapshot_evaluator_recomputes_quality_instead_of_trusting_summary(self) -> None:
        module = self.evaluator_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact = self._snapshot_artifact(root)

            evaluation = module.evaluate_artifact_from_snapshots(root, artifact)

        self.assertEqual(evaluation["metrics"]["unsupported_fact_count"], 0)
        self.assertEqual(evaluation["metrics"]["state_contradiction_count"], 0)
        self.assertRegex(evaluation["samples"][0]["evidence_pack_sha256"], r"^[0-9a-f]{64}$")

    def test_snapshot_evaluator_detects_hidden_forbidden_word_and_history_drift(self) -> None:
        module = self.evaluator_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact = self._snapshot_artifact(
                root,
                history_compact_pack_chars=47000,
                word_text="公告明确执行要求，需人工核验。",
            )

            evaluation = module.evaluate_artifact_from_snapshots(root, artifact)

        self.assertGreater(evaluation["metrics"]["forbidden_phrase_hit_count"], 0)
        self.assertEqual(evaluation["metrics"]["state_contradiction_count"], 1)

    def test_evaluator_freezes_baseline_b_with_quality_and_performance_metrics(self) -> None:
        module = self.evaluator_module()
        samples = [self.clean_result(f"case-{index}", deliverable=index % 2 == 0) for index in range(1, 11)]
        evaluation = module.evaluate_artifact(self.artifact(samples))
        fixed3 = {
            **self.artifact(
                [
                    {
                        **self.clean_result(f"case-{index}"),
                        "sample_id": f"case-{index}-attempt-{attempt}",
                        "attempt": attempt,
                    }
                    for attempt in range(1, 4)
                    for index in range(1, 4)
                ]
            ),
            "subset": "fixed3",
            "repeat": 3,
        }
        baseline = module.freeze_baseline(
            baseline_id="B",
            manifest_sha256=str(evaluation["manifest_sha256"]),
            evaluations=self.server_baseline_evaluations(
                module.evaluate_artifact(fixed3), evaluation
            ),
        )

        self.assertEqual(baseline["baseline_id"], "B")
        self.assertEqual(baseline["schema_version"], "8099.regression-baseline/v2")
        self.assertEqual(baseline["metrics"]["sample_count"], 10)
        self.assertEqual(baseline["metrics"]["unsupported_fact_count"], 20)
        self.assertEqual(baseline["metrics"]["identity_hash_complete_count"], 10)
        self.assertEqual(baseline["metrics"]["metrics_complete_count"], 10)

    def test_fixed3_only_baseline_requires_explicit_coverage_mode(self) -> None:
        module = self.evaluator_module()
        fixed3_artifact = {
            **self.artifact(
                [
                    {
                        **self.clean_result(f"case-{index}"),
                        "sample_id": f"case-{index}-attempt-{attempt}",
                        "attempt": attempt,
                    }
                    for attempt in range(1, 4)
                    for index in range(1, 4)
                ]
            ),
            "subset": "fixed3",
            "repeat": 3,
        }
        evaluation = module.evaluate_artifact(fixed3_artifact)

        with self.assertRaisesRegex(ValueError, "fixed3/fixed10"):
            module.freeze_baseline(
                baseline_id="B",
                manifest_sha256=str(evaluation["manifest_sha256"]),
                evaluations=[evaluation],
            )

        baseline = module.freeze_baseline(
            baseline_id="B",
            manifest_sha256=str(evaluation["manifest_sha256"]),
            evaluations=[evaluation],
            coverage_mode="fixed3_only",
        )

        self.assertEqual(baseline["coverage_mode"], "fixed3_only")
        self.assertEqual(baseline["waived_subsets"], ["fixed10"])
        self.assertEqual(baseline["metrics"]["sample_count"], 9)
        self.assertEqual(baseline["quality"][0]["subset"], "fixed3")

    def test_baseline_freeze_rejects_incomplete_fixed10_or_fixed3_matrix(self) -> None:
        module = self.evaluator_module()
        one_fixed10 = module.evaluate_artifact(self.artifact([self.clean_result()]))
        fixed3 = {
            **self.artifact([self.clean_result("case-1")]),
            "subset": "fixed3",
            "repeat": 3,
        }

        with self.assertRaisesRegex(ValueError, "fixed10.*(10|identity)"):
            module.freeze_baseline(
                baseline_id="B",
                manifest_sha256=str(one_fixed10["manifest_sha256"]),
                evaluations=[module.evaluate_artifact(fixed3), one_fixed10],
            )

    def test_baseline_gate_requires_every_snapshot_to_be_replayable(self) -> None:
        module = self.evaluator_module()
        evaluation = module.evaluate_artifact(
            self.artifact([self.clean_result(f"case-{index}") for index in range(1, 11)])
        )
        evaluation["metrics"]["replayable_snapshot_count"] = 9

        with self.assertRaisesRegex(ValueError, "snapshot"):
            module._validate_evaluation_for_freeze(evaluation)

    def test_baseline_gate_requires_s0_stage(self) -> None:
        module = self.evaluator_module()
        evaluation = module.evaluate_artifact(
            self.artifact([self.clean_result(f"case-{index}") for index in range(1, 11)])
        )
        evaluation["stage"] = "manual"

        with self.assertRaisesRegex(ValueError, "S0"):
            module._validate_evaluation_for_freeze(evaluation)

    def test_baseline_gate_binds_each_sample_to_manifest_material_hashes(self) -> None:
        module = self.evaluator_module()
        artifact = self.artifact(
            [self.clean_result(f"case-{index}") for index in range(1, 11)]
        )
        evaluation = module.evaluate_artifact(artifact)
        first_binding = evaluation["samples"][0].get("material_bindings")
        second_binding = evaluation["samples"][1].get("material_bindings")
        self.assertTrue(first_binding)
        self.assertTrue(second_binding)
        evaluation["samples"][0]["material_bindings"] = second_binding
        evaluation["samples"][1]["material_bindings"] = first_binding

        with self.assertRaisesRegex(ValueError, "material.*(binding|identity|hash)"):
            module._validate_evaluation_for_freeze(evaluation)

    def test_fixed10_gate_accepts_one_explicit_source_exclusion(self) -> None:
        module = self.evaluator_module()
        artifact = self.artifact(
            [self.clean_result(f"case-{index}") for index in range(1, 10)]
        )
        artifact["selected_case_count"] = 9
        artifact["excluded_cases"] = [
            {
                "case_id": "fixed-4-guizhou-project-analysis",
                "menu_code": "project_analysis",
                "articleid": "4a2e0dbc-1a24-490e-b807-38374f8cd530",
                "code": "SOURCE_ATTACHMENT_UNAVAILABLE",
                "observed_at": "2026-07-14T16:00:00+08:00",
            }
        ]
        artifact["declared_case_count"] = 10
        artifact["declared_cases"] = [
            *artifact["selected_cases"],
            {
                "case_id": "fixed-4-guizhou-project-analysis",
                "menu_code": "project_analysis",
                "articleid": "4a2e0dbc-1a24-490e-b807-38374f8cd530",
            },
        ]
        self.bind_manifest_contract(artifact)
        evaluation = module.evaluate_artifact(artifact)

        module._validate_evaluation_for_freeze(evaluation)
        evaluation["excluded_cases"] = []
        with self.assertRaisesRegex(ValueError, "excluded|10"):
            module._validate_evaluation_for_freeze(evaluation)

    def test_source_exclusions_reject_duplicate_material_identity(self) -> None:
        module = self.evaluator_module()
        duplicated_identity = [
            {
                "case_id": "case-a",
                "menu_code": "project_analysis",
                "articleid": "article-1",
                "code": "SOURCE_ATTACHMENT_UNAVAILABLE",
                "observed_at": "2026-07-14T16:00:00+08:00",
            },
            {
                "case_id": "case-b",
                "menu_code": "project_analysis",
                "articleid": "article-1",
                "code": "SOURCE_ATTACHMENT_UNAVAILABLE",
                "observed_at": "2026-07-14T16:05:00+08:00",
            },
        ]

        with self.assertRaisesRegex(ValueError, "duplicate.*identity|identity.*duplicate"):
            module._normalize_excluded_cases(duplicated_identity)

    def test_fixed10_gate_rejects_identity_outside_hashed_manifest_contract(self) -> None:
        module = self.evaluator_module()
        artifact = self.artifact(
            [self.clean_result(f"case-{index}") for index in range(1, 10)]
        )
        artifact["selected_case_count"] = 9
        artifact["declared_case_count"] = 10
        artifact["declared_cases"] = [
            *artifact["selected_cases"],
            {
                "case_id": "excluded-case",
                "menu_code": "project_analysis",
                "articleid": "external-article",
            },
        ]
        artifact["excluded_cases"] = [
            {
                "case_id": "excluded-case",
                "menu_code": "project_analysis",
                "articleid": "external-article",
                "code": "SOURCE_ATTACHMENT_UNAVAILABLE",
                "observed_at": "2026-07-14T16:00:00+08:00",
            }
        ]

        evaluation = module.evaluate_artifact(artifact)
        with self.assertRaisesRegex(ValueError, "manifest.*identity|identity.*manifest"):
            module._validate_evaluation_for_freeze(evaluation)

    def test_baseline_freeze_requires_single_server_fixed3_and_fixed10(self) -> None:
        module = self.evaluator_module()
        fixed10 = module.evaluate_artifact(
            self.artifact([self.clean_result(f"case-{index}") for index in range(1, 11)])
        )
        fixed3_artifact = {
            **self.artifact(
                [
                    {
                        **self.clean_result(f"case-{index}"),
                        "sample_id": f"case-{index}-attempt-{attempt}",
                        "attempt": attempt,
                    }
                    for attempt in range(1, 4)
                    for index in range(1, 4)
                ]
            ),
            "subset": "fixed3",
            "repeat": 3,
        }
        fixed3 = module.evaluate_artifact(fixed3_artifact)

        baseline = module.freeze_baseline(
            baseline_id="B",
            manifest_sha256=str(fixed10["manifest_sha256"]),
            evaluations=[fixed3, fixed10],
        )
        self.assertEqual(
            {(item["environment"], item["subset"]) for item in baseline["evaluations"]},
            {("server_test", "fixed3"), ("server_test", "fixed10")},
        )

        local_fixed10 = json.loads(json.dumps(fixed10))
        local_fixed10["environment"] = "local"
        with self.assertRaisesRegex(ValueError, "server_test"):
            module.freeze_baseline(
                baseline_id="B",
                manifest_sha256=str(fixed10["manifest_sha256"]),
                evaluations=[fixed3, local_fixed10],
            )

    def test_baseline_b_uses_fixed10_quality_without_double_counting_fixed3_repeats(self) -> None:
        module = self.evaluator_module()
        fixed10 = self.artifact([self.clean_result(f"case-{index}") for index in range(1, 11)])
        fixed3 = {
            **self.artifact(
                [
                    {
                        **self.clean_result(f"case-{index}"),
                        "sample_id": f"case-{index}-attempt-{attempt}",
                        "attempt": attempt,
                    }
                    for attempt in range(1, 4)
                    for index in range(1, 4)
                ]
            ),
            "subset": "fixed3",
            "repeat": 3,
        }

        baseline = module.freeze_baseline(
            baseline_id="B",
            manifest_sha256=str(fixed10["manifest_sha256"]),
            evaluations=self.server_baseline_evaluations(
                module.evaluate_artifact(fixed3),
                module.evaluate_artifact(fixed10),
            ),
        )

        self.assertEqual(baseline["metrics"]["sample_count"], 10)
        self.assertEqual(baseline["metrics"]["unsupported_fact_count"], 20)
        self.assertEqual(baseline["performance"][0]["metrics"]["sample_count"], 9)

    def test_baseline_comparison_is_deterministic_and_enforces_fixed3_p95_limit(self) -> None:
        module = self.evaluator_module()
        artifact = {
            **self.artifact(
                [
                    {
                        **self.clean_result(f"case-{index}"),
                        "sample_id": f"case-{index}-attempt-{attempt}",
                        "attempt": attempt,
                    }
                    for attempt in range(1, 4)
                    for index in range(1, 4)
                ]
            ),
            "subset": "fixed3",
            "repeat": 3,
        }
        evaluation = module.evaluate_artifact(artifact)
        fixed10 = module.evaluate_artifact(
            self.artifact([self.clean_result(f"case-{index}") for index in range(1, 11)])
        )
        baseline = module.freeze_baseline(
            baseline_id="B",
            manifest_sha256=str(fixed10["manifest_sha256"]),
            evaluations=self.server_baseline_evaluations(evaluation, fixed10),
        )

        same = module.compare_evaluation_to_baseline(evaluation, baseline)
        slower = json.loads(json.dumps(evaluation))
        slower["metrics"]["elapsed_seconds"]["p95"] = 60
        regression = module.compare_evaluation_to_baseline(slower, baseline)

        self.assertTrue(same["passed"])
        self.assertFalse(regression["passed"])
        self.assertEqual(regression["performance"]["reason"], "p95_regression")

    def test_baseline_comparison_rejects_non_server_environment(self) -> None:
        module = self.evaluator_module()
        fixed3_artifact = {
            **self.artifact(
                [
                    {
                        **self.clean_result(f"case-{index}"),
                        "sample_id": f"case-{index}-attempt-{attempt}",
                        "attempt": attempt,
                    }
                    for attempt in range(1, 4)
                    for index in range(1, 4)
                ]
            ),
            "subset": "fixed3",
            "repeat": 3,
        }
        server_fixed3 = module.evaluate_artifact(fixed3_artifact)
        server_fixed10 = module.evaluate_artifact(
            self.artifact([self.clean_result(f"case-{index}") for index in range(1, 11)])
        )
        baseline = module.freeze_baseline(
            baseline_id="B",
            manifest_sha256=str(server_fixed10["manifest_sha256"]),
            evaluations=self.server_baseline_evaluations(server_fixed3, server_fixed10),
        )
        local_fixed3 = json.loads(json.dumps(server_fixed3))
        local_fixed3["environment"] = "local"

        with self.assertRaisesRegex(ValueError, "server_test"):
            module.compare_evaluation_to_baseline(local_fixed3, baseline)

    def test_baseline_comparison_rejects_incomplete_fixed_subsets(self) -> None:
        module = self.evaluator_module()
        fixed3_artifact = {
            **self.artifact(
                [
                    {
                        **self.clean_result(f"case-{index}"),
                        "sample_id": f"case-{index}-attempt-{attempt}",
                        "attempt": attempt,
                    }
                    for attempt in range(1, 4)
                    for index in range(1, 4)
                ]
            ),
            "subset": "fixed3",
            "repeat": 3,
        }
        fixed3 = module.evaluate_artifact(fixed3_artifact)
        fixed10 = module.evaluate_artifact(
            self.artifact([self.clean_result(f"case-{index}") for index in range(1, 11)])
        )
        baseline = module.freeze_baseline(
            baseline_id="B",
            manifest_sha256=str(fixed10["manifest_sha256"]),
            evaluations=self.server_baseline_evaluations(fixed3, fixed10),
        )

        for incomplete in (
            module.evaluate_artifact({**fixed3_artifact, "samples": fixed3_artifact["samples"][:1]}),
            module.evaluate_artifact(
                {
                    **self.artifact(
                        [self.clean_result(f"case-{index}") for index in range(1, 10)]
                    ),
                    "repeat": 2,
                }
            ),
        ):
            with self.subTest(subset=incomplete["subset"], repeat=incomplete["repeat"]):
                with self.assertRaisesRegex(ValueError, "requires"):
                    module.compare_evaluation_to_baseline(incomplete, baseline)

    def test_verify_only_gate_rejects_incomplete_fixed_subset(self) -> None:
        evaluator_cli = importlib.import_module("scripts.evaluate_regression_baseline")
        module = self.evaluator_module()
        incomplete = module.evaluate_artifact(
            {
                **self.artifact([self.clean_result("case-1")]),
                "subset": "fixed3",
                "repeat": 3,
            }
        )

        with self.assertRaisesRegex(ValueError, "requires|identity"):
            evaluator_cli._verify_gate(incomplete)

    def test_word_contract_checks_every_endpoint_and_all_docx_snapshots(self) -> None:
        module = self.evaluator_module()
        run = {
            "draft_word_export_available": True,
            "final_word_export_available": False,
            "word_export_available": True,
            "deliverable": False,
            "needs_manual_review": True,
        }
        expected_endpoints = (
            "run_download",
            "report_export",
            "report_export_checked",
            "file_download",
        )
        clean_statuses = {name: 200 for name in expected_endpoints}
        for endpoint in expected_endpoints:
            with self.subTest(endpoint=endpoint):
                statuses = {**clean_statuses, endpoint: 500}
                mismatches = module._word_contract_mismatches(
                    run,
                    {"endpoint_statuses": statuses, "staging_artifacts": []},
                    ["word_run", "word_export", "word_checked"],
                )
                self.assertIn(f"{endpoint}_status", {item["field"] for item in mismatches})

        missing = module._word_contract_mismatches(
            run,
            {"endpoint_statuses": clean_statuses, "staging_artifacts": []},
            ["word_run"],
        )
        self.assertIn("word_snapshots", {item["field"] for item in missing})

    def test_fixed10_quality_regression_fails_baseline_comparison(self) -> None:
        module = self.evaluator_module()
        fixed10 = module.evaluate_artifact(
            self.artifact([self.clean_result(f"case-{index}") for index in range(1, 11)])
        )
        fixed3_artifact = {
            **self.artifact(
                [
                    {
                        **self.clean_result(f"case-{index}"),
                        "sample_id": f"case-{index}-attempt-{attempt}",
                        "attempt": attempt,
                    }
                    for attempt in range(1, 4)
                    for index in range(1, 4)
                ]
            ),
            "subset": "fixed3",
            "repeat": 3,
        }
        baseline = module.freeze_baseline(
            baseline_id="B",
            manifest_sha256=str(fixed10["manifest_sha256"]),
            evaluations=self.server_baseline_evaluations(
                module.evaluate_artifact(fixed3_artifact),
                fixed10,
            ),
        )
        regressed = json.loads(json.dumps(fixed10))
        regressed["metrics"]["unsupported_fact_count"] += 1

        comparison = module.compare_evaluation_to_baseline(regressed, baseline)

        self.assertFalse(comparison["passed"])
        self.assertFalse(comparison["quality"]["unsupported_fact_count"])

    def test_evaluate_and_freeze_cli_write_versioned_json(self) -> None:
        self.assertIsNotNone(
            importlib.util.find_spec("scripts.evaluate_regression_baseline"),
            "evaluation CLI is required",
        )
        self.assertIsNotNone(
            importlib.util.find_spec("scripts.freeze_regression_baseline"),
            "baseline freeze CLI is required",
        )
        if importlib.util.find_spec("scripts.evaluate_regression_baseline") is None:
            return
        module = self.evaluator_module()
        evaluator_cli = importlib.import_module("scripts.evaluate_regression_baseline")
        freeze_cli = importlib.import_module("scripts.freeze_regression_baseline")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact = self._snapshot_artifact(root)
            artifact_path = root / "artifact.json"
            evaluation_path = root / "evaluation.json"
            fixed3_path = root / "fixed3-evaluation.json"
            fixed10_path = root / "fixed10-evaluation.json"
            manifest_path = root / "manifest.json"
            baseline_path = root / "baseline.json"
            artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
            fixed3_artifact = {
                **self.artifact(
                    [
                        {
                            **self.clean_result(f"case-{index}"),
                            "sample_id": f"case-{index}-attempt-{attempt}",
                            "attempt": attempt,
                        }
                        for attempt in range(1, 4)
                        for index in range(1, 4)
                    ]
                ),
                "subset": "fixed3",
                "repeat": 3,
            }
            fixed3_path.write_text(
                json.dumps(module.evaluate_artifact(fixed3_artifact)),
                encoding="utf-8",
            )
            fixed10_artifact = self.artifact(
                [self.clean_result(f"case-{index}") for index in range(1, 11)]
            )
            fixed10_path.write_text(
                json.dumps(module.evaluate_artifact(fixed10_artifact)),
                encoding="utf-8",
            )
            manifest_path.write_text(
                json.dumps(fixed10_artifact["manifest_contract"]),
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    evaluator_cli.main(
                        ["--artifact", str(artifact_path), "--output", str(evaluation_path)]
                    ),
                    0,
                )
                self.assertEqual(
                    freeze_cli.main(
                        [
                            "--baseline-id",
                            "B",
                            "--manifest",
                            str(manifest_path),
                            "--evaluation",
                            str(fixed3_path),
                            "--evaluation",
                            str(fixed10_path),
                            "--output",
                            str(baseline_path),
                        ]
                    ),
                    0,
                )

            self.assertEqual(
                json.loads(evaluation_path.read_text(encoding="utf-8"))["schema_version"],
                "8099.regression-evaluation/v1",
            )
            self.assertEqual(
                json.loads(baseline_path.read_text(encoding="utf-8"))["baseline_id"],
                "B",
            )

    def test_freeze_cli_supports_explicit_fixed3_only_coverage(self) -> None:
        module = self.evaluator_module()
        freeze_cli = importlib.import_module("scripts.freeze_regression_baseline")
        fixed3_artifact = {
            **self.artifact(
                [
                    {
                        **self.clean_result(f"case-{index}"),
                        "sample_id": f"case-{index}-attempt-{attempt}",
                        "attempt": attempt,
                    }
                    for attempt in range(1, 4)
                    for index in range(1, 4)
                ]
            ),
            "subset": "fixed3",
            "repeat": 3,
        }
        evaluation = module.evaluate_artifact(fixed3_artifact)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            evaluation_path = root / "fixed3-evaluation.json"
            manifest_path = root / "manifest.json"
            baseline_path = root / "baseline.json"
            evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
            manifest_path.write_text(
                json.dumps(fixed3_artifact["manifest_contract"]), encoding="utf-8"
            )

            with redirect_stdout(io.StringIO()):
                result = freeze_cli.main(
                    [
                        "--baseline-id",
                        "B",
                        "--manifest",
                        str(manifest_path),
                        "--evaluation",
                        str(evaluation_path),
                        "--coverage-mode",
                        "fixed3_only",
                        "--output",
                        str(baseline_path),
                    ]
                )

            self.assertEqual(result, 0)
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            self.assertEqual(baseline["coverage_mode"], "fixed3_only")
            self.assertEqual(baseline["waived_subsets"], ["fixed10"])


if __name__ == "__main__":
    unittest.main()

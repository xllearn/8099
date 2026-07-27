from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.deepeval_advisory import datasets as dataset_module
from app.deepeval_advisory.datasets import (
    MAX_DATASET_JSON_BYTES,
    DatasetCase,
    DatasetEntry,
    DatasetError,
    DatasetManifest,
    DatasetValidation,
    GoldenLabel,
    HumanAdjudication,
    HumanReview,
    MetricLabels,
    Prelabel,
    iter_jsonl,
    load_dataset_case,
    load_dataset_manifest,
    validate_dataset_tree,
)
from app.deepeval_advisory.hashing import canonical_json_bytes, canonical_sha256


CASE_REF = "case_" + "1" * 32
SECOND_CASE_REF = "case_" + "2" * 32
SOURCE_GROUP_REF = "source_group_" + "3" * 32
REVIEW_REF = "review_" + "4" * 32
SECOND_REVIEW_REF = "review_" + "5" * 32
ADJUDICATION_REF = "adjudication_" + "6" * 32
REVIEWER_REF = "reviewer_" + "7" * 32
SAMPLE_REF = "sample_" + "8" * 32
FIXED10_SOURCE_SCHEMA_VERSION = "8099.deepeval-fixed10-source/v1"
FIXED10_RUBRIC_VERSION = "advisory-rubric-v1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
FREEZE_SCRIPT = PROJECT_ROOT / "scripts" / "freeze_deepeval_dataset.py"


def _projection_value() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "8099.deepeval-projection/v1",
        "projection_version": "claim-ab-v1",
        "run_ref": "runref_" + "9" * 32,
        "report_version": 1,
        "report_sha256": "a" * 64,
        "units": [
            {
                "unit_id": "unit_" + "b" * 16,
                "kind": "claim",
                "text": "公告明确采购范围。",
                "claim_count": 1,
                "evidence": [
                    {
                        "local_id": "e1",
                        "level": "A",
                        "kind": "article_text",
                        "excerpt": "采购范围包括一次性使用耗材。",
                        "parent_a_ids": [],
                        "locator": {
                            "kind": "article",
                            "ordinal": 1,
                            "table_index": None,
                            "row": None,
                            "column": None,
                        },
                    }
                ],
                "expected_facts": ["采购范围已明确"],
                "attachment_expectation": None,
            }
        ],
    }
    payload["projection_sha256"] = canonical_sha256(payload)
    return payload


def _case_value(
    *,
    case_ref: str = CASE_REF,
    source_group_ref: str = SOURCE_GROUP_REF,
    split: str = "fixed",
    risk_tier: str = "standard",
    risk_tags: list[str] | None = None,
    projection: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "8099.deepeval-case/v1",
        "case_ref": case_ref,
        "source_group_ref": source_group_ref,
        "split": split,
        "risk_tier": risk_tier,
        "risk_tags": risk_tags or [],
        "material_identity_sha256": "c" * 64,
        "source_content_sha256": "d" * 64,
        "projection": projection or _projection_value(),
    }
    payload["case_sha256"] = canonical_sha256(payload)
    return payload


def _rehash_case_value(value: dict[str, object]) -> dict[str, object]:
    projection = value["projection"]
    projection_payload = {
        key: nested
        for key, nested in projection.items()
        if key != "projection_sha256"
    }
    projection["projection_sha256"] = canonical_sha256(projection_payload)
    case_payload = {
        key: nested
        for key, nested in value.items()
        if key != "case_sha256"
    }
    value["case_sha256"] = canonical_sha256(case_payload)
    return value


def _entry_value(
    case_bytes: bytes,
    *,
    case_ref: str = CASE_REF,
    source_group_ref: str = SOURCE_GROUP_REF,
    relative_path: str | None = None,
    split: str = "fixed",
    risk_tier: str = "standard",
) -> dict[str, object]:
    return {
        "case_ref": case_ref,
        "relative_path": (
            relative_path or f"cases/{case_ref}.json"
        ),
        "file_sha256": hashlib.sha256(case_bytes).hexdigest(),
        "source_group_ref": source_group_ref,
        "split": split,
        "risk_tier": risk_tier,
    }


def _manifest_value(
    entries: list[dict[str, object]],
    *,
    dataset_version: str = "fixed10/v1",
    declared_count: int | None = None,
    runnable_count: int | None = None,
    exclusion_count: int = 0,
) -> dict[str, object]:
    runnable = len(entries) if runnable_count is None else runnable_count
    declared = runnable + exclusion_count if declared_count is None else declared_count
    payload: dict[str, object] = {
        "schema_version": "8099.deepeval-dataset-manifest/v1",
        "dataset_id": dataset_version.partition("/")[0],
        "dataset_version": dataset_version,
        "projection_version": "claim-ab-v1",
        "rubric_version": "rubric-v1",
        "entries": entries,
        "subsets": {
            "fixed3": list(
                dict.fromkeys(entry["case_ref"] for entry in entries[:3])
            )
        },
        "declared_count": declared,
        "runnable_count": runnable,
        "exclusion_count": exclusion_count,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _metric_labels() -> dict[str, str]:
    return {
        "claim_faithfulness_v1": "consistent",
        "critical_coverage_v1": "mixed",
        "attachment_state_consistency_v1": "not_applicable",
        "answer_relevancy_v1": "inconsistent",
    }


def _golden_value() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "8099.deepeval-golden/v1",
        "case_ref": CASE_REF,
        "sample_ref": None,
        "human_review_refs": [REVIEW_REF],
        "human_adjudication_refs": [],
        "metric_labels": _metric_labels(),
    }
    payload["golden_sha256"] = canonical_sha256(payload)
    return payload


def _fixed10_source_value(
    index: int,
    *,
    fixed3_rank: int | None = None,
) -> dict[str, object]:
    projection = _projection_value()
    projection["run_ref"] = f"runref_{index:032x}"
    projection["report_sha256"] = hashlib.sha256(
        f"report-{index}".encode("utf-8")
    ).hexdigest()
    projection["units"][0]["unit_id"] = f"unit_{index:016x}"
    projection["units"][0]["text"] = f"合成案例 {index} 的采购范围明确。"
    projection_payload = {
        key: nested
        for key, nested in projection.items()
        if key != "projection_sha256"
    }
    projection["projection_sha256"] = canonical_sha256(projection_payload)
    return {
        "schema_version": FIXED10_SOURCE_SCHEMA_VERSION,
        "case_ref": f"case_{index:032x}",
        "source_group_ref": f"source_group_{index:032x}",
        "risk_tier": "standard",
        "risk_tags": [],
        "material_identity_sha256": hashlib.sha256(
            f"material-{index}".encode("utf-8")
        ).hexdigest(),
        "source_content_sha256": hashlib.sha256(
            f"content-{index}".encode("utf-8")
        ).hexdigest(),
        "projection": projection,
        "fixed3_rank": fixed3_rank,
    }


def _write_fixed10_sources(
    root: Path,
    *,
    count: int = 10,
    ranks: dict[int, int | None] | None = None,
) -> list[dict[str, object]]:
    root.mkdir()
    rank_map = ranks or {1: 1, 2: 2, 3: 3}
    values: list[dict[str, object]] = []
    for index in range(1, count + 1):
        value = _fixed10_source_value(
            index,
            fixed3_rank=rank_map.get(index),
        )
        values.append(value)
        (root / f"export-{index:02d}.json").write_bytes(
            canonical_json_bytes(value)
        )
    return values


def _directory_snapshot(root: Path) -> dict[str, tuple[bytes, int, int]]:
    snapshot: dict[str, tuple[bytes, int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            info = path.stat()
            snapshot[path.relative_to(root).as_posix()] = (
                path.read_bytes(),
                info.st_ino,
                info.st_mtime_ns,
            )
    return snapshot


def _write_tree(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    case_value = _case_value()
    case_bytes = canonical_json_bytes(case_value)
    case_path = root / "cases" / f"{CASE_REF}.json"
    case_path.parent.mkdir(parents=True)
    case_path.write_bytes(case_bytes)
    entry_value = _entry_value(case_bytes)
    manifest_value = _manifest_value([entry_value])
    (root / "manifest.json").write_bytes(canonical_json_bytes(manifest_value))
    return case_value, manifest_value


class DatasetSchemaTests(unittest.TestCase):
    def test_dataset_models_are_frozen_and_reject_unknown_fields(self) -> None:
        value = _case_value()
        value["raw_run_id"] = "run_private_12345678"
        with self.assertRaises(ValidationError):
            DatasetCase.model_validate(value)

        valid = DatasetCase.model_validate(_case_value())
        with self.assertRaises(ValidationError):
            valid.case_ref = SECOND_CASE_REF

    def test_dataset_version_is_an_immutable_supported_version(self) -> None:
        case_bytes = canonical_json_bytes(_case_value())
        entry = _entry_value(case_bytes)
        for version in ("fixed10", "v1", "fixed10/latest", "calibration100/v2"):
            with self.subTest(version=version), self.assertRaises(ValidationError):
                DatasetManifest.model_validate(
                    _manifest_value([entry], dataset_version=version)
                )

        for version in ("fixed10/v1", "calibration100/v1"):
            with self.subTest(version=version):
                value = _manifest_value([entry], dataset_version=version)
                value["manifest_sha256"] = canonical_sha256(
                    {
                        key: nested
                        for key, nested in value.items()
                        if key != "manifest_sha256"
                    }
                )
                self.assertEqual(
                    DatasetManifest.model_validate(value).dataset_version,
                    version,
                )

    def test_reference_and_hash_formats_are_strict(self) -> None:
        invalid_cases = (
            ("case_ref", "case-visible-source-id"),
            ("source_group_ref", "source_group_ABC"),
            ("material_identity_sha256", "A" * 64),
            ("source_content_sha256", "d" * 63),
            ("case_sha256", "not-a-hash"),
        )
        for field, invalid in invalid_cases:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                value = _case_value()
                value[field] = invalid
                DatasetCase.model_validate(value)

        case_bytes = canonical_json_bytes(_case_value())
        entry = _entry_value(case_bytes)
        entry["file_sha256"] = "0" * 63
        with self.assertRaises(ValidationError):
            DatasetEntry.model_validate(entry)

    def test_metric_labels_are_exact_per_metric_observations(self) -> None:
        labels = MetricLabels.model_validate(_metric_labels())
        self.assertEqual(labels.answer_relevancy_v1, "inconsistent")

        invalid_values = (
            {"pass": True},
            {"aggregate": "pass"},
            {"claim_faithfulness_v1": "pass"},
            {"critical_coverage_v1": "failed"},
        )
        for mutation in invalid_values:
            with self.subTest(mutation=mutation), self.assertRaises(ValidationError):
                value = _metric_labels()
                value.update(mutation)
                MetricLabels.model_validate(value)

        missing = _metric_labels()
        del missing["answer_relevancy_v1"]
        with self.assertRaises(ValidationError):
            MetricLabels.model_validate(missing)

    def test_risk_tags_are_exact_and_nonempty_tags_require_high_risk(self) -> None:
        allowed = (
            "date",
            "amount",
            "entity",
            "procurement_scope",
            "attachment_state",
            "evidence_c_boundary",
            "memory_boundary",
            "unsupported_key_conclusion",
        )
        for tag in allowed:
            with self.subTest(tag=tag):
                value = _case_value(risk_tier="high", risk_tags=[tag])
                self.assertEqual(
                    DatasetCase.model_validate(value).risk_tags,
                    (tag,),
                )

        with self.assertRaises(ValidationError):
            DatasetCase.model_validate(
                _case_value(risk_tier="standard", risk_tags=["date"])
            )
        with self.assertRaises(ValidationError):
            DatasetCase.model_validate(
                _case_value(risk_tier="high", risk_tags=["other"])
            )
        self.assertEqual(
            DatasetCase.model_validate(_case_value()).risk_tags,
            (),
        )

    def test_case_rejects_projection_hash_mismatch(self) -> None:
        projection = _projection_value()
        projection["projection_sha256"] = "0" * 64
        value = _case_value(projection=projection)
        value["case_sha256"] = canonical_sha256(
            {key: nested for key, nested in value.items() if key != "case_sha256"}
        )
        with self.assertRaisesRegex(ValidationError, "projection integrity"):
            DatasetCase.model_validate(value)

    def test_case_rejects_raw_or_unsafe_projection_content_before_hash(self) -> None:
        unsafe_values = (
            "run_id=run_private_12345678",
            "pack_id=pack_private_12345678",
            "articleid=28296323-private",
            "menu_code=project_information",
            "原始编号 28296323-9aa5-4fa0-81c0-36f6c3e18adc",
            "原始栏目 project_notice",
            "ｒｕｎ＿ｉｄ＝ｒｕｎ＿ｐｒｉｖａｔｅ＿１２３４５６７８",
            "附件名为采购文件.pdf",
            r"来源在 C:\private\report.json",
            "../private/report.json",
            "来源为 https://example.com/private",
            "完整报告 report_markdown 如下",
            "完整 Evidence Pack evidence_items 如下",
            "逐步推理：第一步分析原文",
        )
        for unsafe in unsafe_values:
            with self.subTest(unsafe=unsafe), self.assertRaisesRegex(
                ValidationError,
                "safety boundary",
            ):
                projection = _projection_value()
                projection["units"][0]["text"] = unsafe
                # Keep both hashes stale: boundary rejection must win.
                DatasetCase.model_validate(_case_value(projection=projection))

    def test_case_rejects_rehashed_uri_locators_after_nfkc(self) -> None:
        unsafe_values = (
            "来源 s3://private-bucket/object",
            "实时通道 ws://example.test/feed",
            "联系人 mailto:reviewer@example.test",
            "本地来源 file:/private/source",
            "内嵌内容 data:text/plain,private",
            "脚本 javascript:alert(1)",
            "远程 ssh:user@private-host",
            "仓库 git:private",
            "自定义 x:foo",
            r"Windows C:\private\source",
            "全角 ｘ：ｆｏｏ",
        )
        for unsafe in unsafe_values:
            with self.subTest(unsafe=unsafe), self.assertRaisesRegex(
                ValidationError,
                "safety boundary",
            ):
                value = _case_value()
                value["projection"]["units"][0]["text"] = unsafe
                DatasetCase.model_validate(_rehash_case_value(value))

        safe_values = (
            "Note: review approved",
            "结论: 通过",
            "Protocol: review approved",
            "时间 08:30；比例 3:2；版本 2:1",
            "全角标签 Note： review approved",
        )
        for safe_text in safe_values:
            with self.subTest(safe=safe_text):
                safe = _case_value()
                safe["projection"]["units"][0]["text"] = safe_text
                model = DatasetCase.model_validate(_rehash_case_value(safe))
                self.assertEqual(model.projection.units[0].text, safe_text)

    def test_case_rejects_evidence_c_and_full_source_payload_fields(self) -> None:
        projection = _projection_value()
        projection["units"][0]["evidence"][0]["level"] = "C"
        with self.assertRaises(ValidationError):
            DatasetCase.model_validate(_case_value(projection=projection))

        for field in ("report_markdown", "report_ir", "evidence_pack"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                value = _case_value()
                value[field] = (
                    {"full": "source"}
                    if field != "report_markdown"
                    else "full"
                )
                DatasetCase.model_validate(value)

    def test_case_hash_is_canonical_and_excludes_its_own_field(self) -> None:
        value = _case_value()
        expected = value["case_sha256"]
        reordered = dict(reversed(list(value.items())))
        model = DatasetCase.model_validate(reordered)
        payload = model.model_dump(mode="json")
        digest = payload.pop("case_sha256")
        self.assertEqual(digest, expected)
        self.assertEqual(digest, canonical_sha256(payload))

        changed = copy.deepcopy(value)
        changed["source_content_sha256"] = "e" * 64
        with self.assertRaisesRegex(ValidationError, "case integrity"):
            DatasetCase.model_validate(changed)

    def test_manifest_hash_is_canonical_and_excludes_its_own_field(self) -> None:
        case_bytes = canonical_json_bytes(_case_value())
        value = _manifest_value([_entry_value(case_bytes)])
        expected = value["manifest_sha256"]
        reordered = dict(reversed(list(value.items())))
        model = DatasetManifest.model_validate(reordered)
        payload = model.model_dump(mode="json")
        digest = payload.pop("manifest_sha256")
        self.assertEqual(digest, expected)
        self.assertEqual(digest, canonical_sha256(payload))

        changed = copy.deepcopy(value)
        changed["rubric_version"] = "rubric-v2"
        with self.assertRaisesRegex(ValidationError, "manifest integrity"):
            DatasetManifest.model_validate(changed)

    def test_self_hashed_models_require_every_raw_defaulted_field(self) -> None:
        contracts = (
            (
                DatasetCase,
                _case_value(),
                ("schema_version", "risk_tags"),
            ),
            (
                DatasetManifest,
                _manifest_value(
                    [_entry_value(canonical_json_bytes(_case_value()))]
                ),
                ("schema_version", "projection_version"),
            ),
            (
                GoldenLabel,
                _golden_value(),
                (
                    "schema_version",
                    "sample_ref",
                    "human_adjudication_refs",
                ),
            ),
        )
        for model_type, full_value, fields in contracts:
            for field in fields:
                with (
                    self.subTest(model=model_type.__name__, field=field),
                    self.assertRaises(ValidationError),
                ):
                    omitted = copy.deepcopy(full_value)
                    del omitted[field]
                    model_type.model_validate(omitted)

        for field in ("schema_version", "projection_version"):
            with self.subTest(projection_field=field), self.assertRaises(
                ValidationError
            ):
                omitted_projection_field = _case_value()
                del omitted_projection_field["projection"][field]
                DatasetCase.model_validate(omitted_projection_field)

    def test_loaders_reject_default_synthesis_before_raw_self_hashing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_value, manifest_value = _write_tree(root)
            manifest_path = root / "manifest.json"
            case_path = root / "cases" / f"{CASE_REF}.json"

            omitted_manifest = copy.deepcopy(manifest_value)
            del omitted_manifest["schema_version"]
            manifest_path.write_bytes(canonical_json_bytes(omitted_manifest))
            with self.assertRaisesRegex(DatasetError, "manifest_invalid"):
                load_dataset_manifest(manifest_path)

            manifest_path.write_bytes(canonical_json_bytes(manifest_value))
            manifest = load_dataset_manifest(manifest_path)
            omitted_case = copy.deepcopy(case_value)
            del omitted_case["projection"]["projection_version"]
            omitted_case_bytes = canonical_json_bytes(omitted_case)
            case_path.write_bytes(omitted_case_bytes)
            entry = manifest.entries[0].model_copy(
                update={
                    "file_sha256": hashlib.sha256(
                        omitted_case_bytes
                    ).hexdigest()
                }
            )
            with self.assertRaisesRegex(DatasetError, "case_invalid"):
                load_dataset_case(root, entry)

    def test_self_hashed_model_json_rejects_duplicate_object_keys(self) -> None:
        contracts = (
            (DatasetCase, _case_value(), "schema_version"),
            (
                DatasetManifest,
                _manifest_value(
                    [_entry_value(canonical_json_bytes(_case_value()))]
                ),
                "projection_version",
            ),
            (GoldenLabel, _golden_value(), "schema_version"),
        )
        for model_type, value, duplicated_field in contracts:
            serialized = canonical_json_bytes(value).decode("utf-8")
            duplicate = json.dumps(
                {duplicated_field: value[duplicated_field]},
                ensure_ascii=False,
                separators=(",", ":"),
            )[1:-1]
            payload = "{" + duplicate + "," + serialized[1:]
            with self.subTest(model=model_type.__name__), self.assertRaises(
                (ValidationError, ValueError)
            ):
                model_type.model_validate_json(payload)

    def test_manifest_rejects_duplicates_paths_and_inconsistent_counts(self) -> None:
        case_bytes = canonical_json_bytes(_case_value())
        entry = _entry_value(case_bytes)
        invalid_paths = (
            "/absolute/case.json",
            r"C:\private\case.json",
            "../case.json",
            "cases/../case.json",
            r"cases\case.json",
            "",
        )
        for relative_path in invalid_paths:
            with self.subTest(relative_path=relative_path), self.assertRaises(
                ValidationError
            ):
                DatasetEntry.model_validate(
                    {**entry, "relative_path": relative_path}
                )

        with self.assertRaisesRegex(ValidationError, "duplicate case_ref"):
            DatasetManifest.model_validate(_manifest_value([entry, entry]))
        duplicate_path = _entry_value(
            case_bytes,
            case_ref=SECOND_CASE_REF,
            relative_path=str(entry["relative_path"]),
        )
        with self.assertRaisesRegex(ValidationError, "duplicate relative_path"):
            DatasetManifest.model_validate(
                _manifest_value([entry, duplicate_path])
            )
        with self.assertRaises(ValidationError):
            DatasetManifest.model_validate(
                _manifest_value(
                    [entry],
                    declared_count=2,
                    runnable_count=1,
                    exclusion_count=0,
                )
            )

    def test_prelabel_reason_is_bounded_conclusion_only_and_safe(self) -> None:
        base = {
            "schema_version": "8099.deepeval-prelabel/v1",
            "case_ref": CASE_REF,
            "sample_ref": SAMPLE_REF,
            "agent_id": "agent-a",
            "input_sha256": "1" * 64,
            "rubric_sha256": "2" * 64,
            "prompt_sha256": "3" * 64,
            "metric_labels": _metric_labels(),
            "bounded_reason": "关键结论与 A 级证据一致。",
        }
        self.assertEqual(
            Prelabel.model_validate(base).bounded_reason,
            "关键结论与 A 级证据一致。",
        )
        for invalid in (
            "x" * 501,
            "第一步逐步推理，然后给出结论",
            "chain of thought: inspect every token",
            "结论见 https://example.com/source",
            r"结论见 C:\private\source.txt",
            "line one\nline two",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                Prelabel.model_validate({**base, "bounded_reason": invalid})

    def test_human_review_requires_explicit_human_provenance_and_canonical_time(
        self,
    ) -> None:
        base = {
            "schema_version": "8099.deepeval-human-review/v1",
            "review_ref": REVIEW_REF,
            "case_ref": CASE_REF,
            "sample_ref": SAMPLE_REF,
            "reviewer_ref": REVIEWER_REF,
            "review_reason": ["disagreement", "high_risk", "validation"],
            "metric_labels": _metric_labels(),
            "bounded_reason": "人工复核后采用混合结论。",
            "reviewed_at": "2026-07-28T01:02:03.004Z",
            "provenance": "human_supplied",
        }
        review = HumanReview.model_validate(base)
        self.assertEqual(review.provenance, "human_supplied")
        self.assertEqual(
            review.review_reason,
            ("disagreement", "high_risk", "validation"),
        )

        for reason in ("agent_disagreement", "validation_split", "other"):
            with self.subTest(reason=reason), self.assertRaises(ValidationError):
                HumanReview.model_validate({**base, "review_reason": [reason]})
        for timestamp in (
            "2026-07-28T01:02:03Z",
            "2026-07-28 01:02:03.004Z",
            "2026-07-28T01:02:03.004+00:00",
            "2026-02-30T01:02:03.004Z",
        ):
            with self.subTest(timestamp=timestamp), self.assertRaises(
                ValidationError
            ):
                HumanReview.model_validate({**base, "reviewed_at": timestamp})
        with self.assertRaises(ValidationError):
            HumanReview.model_validate({**base, "provenance": "agent_generated"})

    def test_human_adjudication_and_golden_reference_only_human_records(
        self,
    ) -> None:
        adjudication_value = {
            "schema_version": "8099.deepeval-human-adjudication/v1",
            "adjudication_ref": ADJUDICATION_REF,
            "case_ref": CASE_REF,
            "sample_ref": SAMPLE_REF,
            "human_review_refs": [REVIEW_REF, SECOND_REVIEW_REF],
            "metric_labels": _metric_labels(),
            "bounded_reason": "人工裁决采用经核对的证据边界。",
            "adjudicated_at": "2026-07-28T01:02:03.004Z",
            "provenance": "human_supplied",
        }
        adjudication = HumanAdjudication.model_validate(adjudication_value)
        self.assertEqual(adjudication.provenance, "human_supplied")

        golden_payload: dict[str, object] = {
            "schema_version": "8099.deepeval-golden/v1",
            "case_ref": CASE_REF,
            "sample_ref": SAMPLE_REF,
            "human_review_refs": [REVIEW_REF, SECOND_REVIEW_REF],
            "human_adjudication_refs": [ADJUDICATION_REF],
            "metric_labels": _metric_labels(),
        }
        golden_payload["golden_sha256"] = canonical_sha256(golden_payload)
        golden = GoldenLabel.model_validate(golden_payload)
        self.assertEqual(golden.human_adjudication_refs, (ADJUDICATION_REF,))

        for forbidden in ("agent_id", "prelabel_ref", "judge_score", "release_pass"):
            with self.subTest(forbidden=forbidden), self.assertRaises(
                ValidationError
            ):
                GoldenLabel.model_validate({**golden_payload, forbidden: "agent-a"})
        with self.assertRaises(ValidationError):
            HumanAdjudication.model_validate(
                {**adjudication_value, "provenance": "model_generated"}
            )

    def test_dataset_validation_has_non_gating_semantics(self) -> None:
        valid = DatasetValidation(
            valid=True,
            error_categories=(),
            checked_entry_count=1,
        )
        self.assertFalse(valid.labeling_ready)
        self.assertIsNone(valid.release_decision)
        with self.assertRaises(ValidationError):
            DatasetValidation(
                valid=True,
                error_categories=(),
                checked_entry_count=1,
                labeling_ready=True,
            )
        with self.assertRaises(ValidationError):
            DatasetValidation(
                valid=True,
                error_categories=(),
                checked_entry_count=1,
                release_decision="pass",
            )

    def test_canonical_manifest_and_case_readers_validate_complete_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_value, manifest_value = _write_tree(root)
            manifest = load_dataset_manifest(root / "manifest.json")
            case = load_dataset_case(root, manifest.entries[0])
            validation = validate_dataset_tree(root)

        self.assertEqual(
            manifest.manifest_sha256,
            manifest_value["manifest_sha256"],
        )
        self.assertEqual(case.case_sha256, case_value["case_sha256"])
        self.assertTrue(validation.valid)
        self.assertEqual(validation.error_categories, ())
        self.assertEqual(validation.checked_entry_count, 1)
        self.assertFalse(validation.labeling_ready)
        self.assertIsNone(validation.release_decision)

    def test_tree_inventory_rejects_every_unexpected_member_kind(self) -> None:
        def add_file(root: Path) -> None:
            (root / "unreferenced.json").write_text("{}", encoding="utf-8")

        def add_directory(root: Path) -> None:
            (root / "unexpected").mkdir()

        def add_symlink(root: Path) -> None:
            (root / "unreferenced-link").symlink_to(root / "manifest.json")

        def add_fifo(root: Path) -> None:
            os.mkfifo(root / "unreferenced-fifo")

        def add_oversize(root: Path) -> None:
            (root / "unreferenced.bin").write_bytes(
                b"x" * (MAX_DATASET_JSON_BYTES + 1)
            )

        mutations = (
            ("file", add_file),
            ("directory", add_directory),
            ("symlink", add_symlink),
            ("fifo", add_fifo),
            ("oversize", add_oversize),
        )
        for name, mutate in mutations:
            with self.subTest(member=name), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                _write_tree(root)
                mutate(root)

                validation = validate_dataset_tree(root)

                self.assertFalse(validation.valid)
                self.assertIn("tree_unexpected", validation.error_categories)

    def test_tree_inventory_rejects_oversize_referenced_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_tree(root)
            case_path = root / "cases" / f"{CASE_REF}.json"
            case_path.write_bytes(b"{" + b" " * MAX_DATASET_JSON_BYTES + b"}")

            validation = validate_dataset_tree(root)

        self.assertFalse(validation.valid)
        self.assertIn("file_size", validation.error_categories)

    def test_deep_unexpected_tree_is_rejected_without_recursion_error(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_tree(root)
            current = root / "unexpected"
            current.mkdir()
            created = [current]
            try:
                for _ in range(1_050):
                    current = current / "d"
                    current.mkdir()
                    created.append(current)

                validation = validate_dataset_tree(root)
            finally:
                for directory in reversed(created):
                    directory.rmdir()

        self.assertFalse(validation.valid)
        self.assertEqual(
            validation.error_categories,
            ("tree_unexpected",),
        )

    def test_wide_tree_stops_at_exported_member_limit(self) -> None:
        member_limit = getattr(
            dataset_module,
            "MAX_DATASET_TREE_MEMBERS",
            128,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_tree(root)
            for index in range(member_limit + 1):
                (root / f"extra-{index:05d}").write_bytes(b"x")

            first = validate_dataset_tree(root)
            second = validate_dataset_tree(root)

        self.assertFalse(first.valid)
        self.assertEqual(first, second)
        self.assertIn("tree_limit", first.error_categories)

    def test_exported_tree_limits_bound_manifest_shape(self) -> None:
        max_depth = getattr(dataset_module, "MAX_DATASET_TREE_DEPTH", 8)
        max_entries = getattr(
            dataset_module,
            "MAX_DATASET_MANIFEST_ENTRIES",
            128,
        )
        max_members = getattr(
            dataset_module,
            "MAX_DATASET_TREE_MEMBERS",
            1 + max_depth * max_entries,
        )
        self.assertGreaterEqual(max_depth, 2)
        self.assertGreaterEqual(max_entries, 100)
        self.assertGreater(max_members, 100)
        self.assertGreaterEqual(
            max_members,
            1 + max_depth * max_entries,
        )

        case_bytes = canonical_json_bytes(_case_value())
        too_deep = _entry_value(
            case_bytes,
            relative_path="/".join(
                ["nested"] * max_depth + [f"{CASE_REF}.json"]
            ),
        )
        with self.assertRaises(ValidationError):
            DatasetEntry.model_validate(too_deep)

        entries = []
        for index in range(1, max_entries + 2):
            case_ref = f"case_{index:032x}"
            entries.append(
                _entry_value(
                    case_bytes,
                    case_ref=case_ref,
                    relative_path=f"cases/{case_ref}.json",
                )
            )
        with self.assertRaises(ValidationError):
            DatasetManifest.model_validate(_manifest_value(entries))

    def test_manifest_reader_rejects_utf8_json_size_and_hash_errors(self) -> None:
        invalid_payloads = (
            b"\xff",
            b"{",
            canonical_json_bytes({"not": "a manifest"}),
            b"{" + b" " * MAX_DATASET_JSON_BYTES + b"}",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.json"
            for payload in invalid_payloads:
                with self.subTest(size=len(payload)):
                    path.write_bytes(payload)
                    with self.assertRaises(DatasetError):
                        load_dataset_manifest(path)

            case_bytes = canonical_json_bytes(_case_value())
            value = _manifest_value([_entry_value(case_bytes)])
            value["manifest_sha256"] = "0" * 64
            path.write_bytes(canonical_json_bytes(value))
            with self.assertRaisesRegex(DatasetError, "manifest_invalid"):
                load_dataset_manifest(path)

    def test_case_reader_rejects_file_hash_case_hash_and_metadata_mismatch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_value, _ = _write_tree(root)
            case_path = root / "cases" / f"{CASE_REF}.json"
            case_bytes = case_path.read_bytes()
            entry = DatasetEntry.model_validate(_entry_value(case_bytes))

            wrong_file = entry.model_copy(update={"file_sha256": "0" * 64})
            with self.assertRaisesRegex(DatasetError, "file_hash_mismatch"):
                load_dataset_case(root, wrong_file)

            changed = copy.deepcopy(case_value)
            changed["source_content_sha256"] = "e" * 64
            changed_bytes = canonical_json_bytes(changed)
            case_path.write_bytes(changed_bytes)
            changed_entry = DatasetEntry.model_validate(
                _entry_value(changed_bytes)
            )
            with self.assertRaisesRegex(DatasetError, "case_invalid"):
                load_dataset_case(root, changed_entry)

            mismatch = _case_value(source_group_ref="source_group_" + "f" * 32)
            mismatch_bytes = canonical_json_bytes(mismatch)
            case_path.write_bytes(mismatch_bytes)
            mismatch_entry = DatasetEntry.model_validate(
                _entry_value(mismatch_bytes)
            )
            with self.assertRaisesRegex(DatasetError, "entry_metadata_mismatch"):
                load_dataset_case(root, mismatch_entry)

    def test_loaders_reject_absolute_escape_symlink_and_nonregular_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_value, _ = _write_tree(root)
            case_bytes = canonical_json_bytes(case_value)
            manifest_path = root / "manifest.json"

            manifest_path.unlink()
            manifest_path.mkdir()
            with self.assertRaisesRegex(DatasetError, "file_type"):
                load_dataset_manifest(manifest_path)

            case_path = root / "cases" / f"{CASE_REF}.json"
            outside = root / "outside.json"
            outside.write_bytes(case_bytes)
            case_path.unlink()
            try:
                case_path.symlink_to(outside)
            except (NotImplementedError, OSError):
                self.skipTest("symlinks are unavailable on this platform")
            entry = DatasetEntry.model_validate(_entry_value(case_bytes))
            with self.assertRaisesRegex(DatasetError, "file_type"):
                load_dataset_case(root, entry)

            linked_dir = root / "linked"
            linked_dir.symlink_to(outside.parent, target_is_directory=True)
            nested_entry = DatasetEntry.model_validate(
                _entry_value(
                    case_bytes,
                    relative_path=f"linked/{outside.name}",
                )
            )
            with self.assertRaisesRegex(DatasetError, "path_invalid"):
                load_dataset_case(root, nested_entry)

    def test_anchored_reader_rejects_symlinked_root_and_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            real_parent = base / "real-parent"
            root = real_parent / "dataset"
            root.mkdir(parents=True)
            _write_tree(root)
            manifest = load_dataset_manifest(root / "manifest.json")

            root_link = base / "root-link"
            root_link.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(DatasetError, "root_invalid"):
                load_dataset_case(root_link, manifest.entries[0])
            with self.assertRaises(DatasetError):
                load_dataset_manifest(root_link / "manifest.json")

            ancestor_link = base / "ancestor-link"
            ancestor_link.symlink_to(real_parent, target_is_directory=True)
            linked_root = ancestor_link / "dataset"
            with self.assertRaisesRegex(DatasetError, "root_invalid"):
                load_dataset_case(linked_root, manifest.entries[0])
            with self.assertRaises(DatasetError):
                load_dataset_manifest(linked_root / "manifest.json")

    def test_reader_fails_closed_without_required_nofollow_primitives(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_tree(root)
            manifest = load_dataset_manifest(root / "manifest.json")
            for primitive in ("O_NOFOLLOW", "O_DIRECTORY"):
                with (
                    self.subTest(primitive=primitive),
                    patch.object(dataset_module.os, primitive, 0),
                    self.assertRaisesRegex(DatasetError, "root_invalid"),
                ):
                    load_dataset_case(root, manifest.entries[0])

    def test_intermediate_directory_swap_never_accepts_outside_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "dataset"
            root.mkdir()
            _write_tree(root)
            manifest = load_dataset_manifest(root / "manifest.json")
            entry = manifest.entries[0]
            case_path = root / entry.relative_path

            outside_cases = base / "outside" / "cases"
            outside_cases.mkdir(parents=True)
            os.link(case_path, outside_cases / case_path.name)
            original_open = os.open
            swapped = False

            def racing_open(
                path: object,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal swapped
                path_text = os.fspath(path)
                final_path_open = (
                    dir_fd is None
                    and Path(path_text) == case_path
                )
                anchored_parent_open = (
                    dir_fd is not None and path_text == "cases"
                )
                if not swapped and (final_path_open or anchored_parent_open):
                    (root / "cases").rename(root / "cases-original")
                    (root / "cases").symlink_to(
                        outside_cases,
                        target_is_directory=True,
                    )
                    swapped = True
                if dir_fd is None:
                    return original_open(path, flags, mode)
                return original_open(
                    path,
                    flags,
                    mode,
                    dir_fd=dir_fd,
                )

            with patch.object(dataset_module.os, "open", side_effect=racing_open):
                with self.assertRaises(DatasetError):
                    load_dataset_case(root, entry)
            self.assertTrue(swapped)

    def test_root_path_swap_cannot_redirect_an_opened_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "dataset"
            root.mkdir()
            _write_tree(root)
            manifest = load_dataset_manifest(root / "manifest.json")
            entry = manifest.entries[0]
            case_path = root / entry.relative_path

            outside_cases = base / "outside" / "cases"
            outside_cases.mkdir(parents=True)
            (outside_cases / case_path.name).write_bytes(b"{}")
            original_open = os.open
            swapped = False

            def racing_open(
                path: object,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal swapped
                path_text = os.fspath(path)
                final_path_open = (
                    dir_fd is None
                    and Path(path_text) == case_path
                )
                anchored_parent_open = (
                    dir_fd is not None and path_text == "cases"
                )
                if not swapped and (final_path_open or anchored_parent_open):
                    root.rename(base / "dataset-original")
                    root.symlink_to(
                        base / "outside",
                        target_is_directory=True,
                    )
                    swapped = True
                if dir_fd is None:
                    return original_open(path, flags, mode)
                return original_open(
                    path,
                    flags,
                    mode,
                    dir_fd=dir_fd,
                )

            with patch.object(dataset_module.os, "open", side_effect=racing_open):
                loaded = load_dataset_case(root, entry)

            self.assertTrue(swapped)
            self.assertEqual(loaded.case_ref, CASE_REF)

    def test_anchored_readers_close_descriptors_on_success_and_failure(
        self,
    ) -> None:
        fd_directory = Path("/proc/self/fd")
        if not fd_directory.is_dir():
            self.skipTest("Linux file descriptor inventory is unavailable")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_tree(root)
            manifest = load_dataset_manifest(root / "manifest.json")
            entry = manifest.entries[0]
            bad_entry = entry.model_copy(update={"file_sha256": "0" * 64})
            baseline = len(tuple(fd_directory.iterdir()))

            for _ in range(20):
                load_dataset_case(root, entry)
                with self.assertRaises(DatasetError):
                    load_dataset_case(root, bad_entry)

            after = len(tuple(fd_directory.iterdir()))
        self.assertEqual(after, baseline)

    def test_jsonl_validates_each_line_and_rejects_bad_lines_and_symlinks(
        self,
    ) -> None:
        first = {
            "schema_version": "8099.deepeval-prelabel/v1",
            "case_ref": CASE_REF,
            "sample_ref": SAMPLE_REF,
            "agent_id": "agent-a",
            "input_sha256": "1" * 64,
            "rubric_sha256": "2" * 64,
            "prompt_sha256": "3" * 64,
            "metric_labels": _metric_labels(),
            "bounded_reason": "结论一。",
        }
        second = {**first, "case_ref": SECOND_CASE_REF}
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "labels.jsonl"
            path.write_bytes(
                canonical_json_bytes(first)
                + b"\n"
                + canonical_json_bytes(second)
                + b"\n"
            )
            labels = tuple(iter_jsonl(path, Prelabel))
            self.assertEqual(
                tuple(label.case_ref for label in labels),
                (CASE_REF, SECOND_CASE_REF),
            )

            path.write_bytes(canonical_json_bytes(first) + b"\n{}\n")
            with self.assertRaisesRegex(DatasetError, "jsonl_model_invalid"):
                tuple(iter_jsonl(path, Prelabel))
            path.write_bytes(canonical_json_bytes(first) + b"\n\n")
            with self.assertRaisesRegex(DatasetError, "jsonl_invalid"):
                tuple(iter_jsonl(path, Prelabel))
            path.write_bytes(b"\xff\n")
            with self.assertRaisesRegex(DatasetError, "json_invalid"):
                tuple(iter_jsonl(path, Prelabel))

            target = root / "target.jsonl"
            target.write_bytes(canonical_json_bytes(first) + b"\n")
            path.unlink()
            try:
                path.symlink_to(target)
            except (NotImplementedError, OSError):
                self.skipTest("symlinks are unavailable on this platform")
            with self.assertRaisesRegex(DatasetError, "file_type"):
                tuple(iter_jsonl(path, Prelabel))

    def test_validate_tree_returns_deterministic_integrity_categories_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_tree(root)
            case_path = root / "cases" / f"{CASE_REF}.json"
            case_path.write_bytes(case_path.read_bytes() + b" ")

            first = validate_dataset_tree(root)
            second = validate_dataset_tree(root)

        self.assertFalse(first.valid)
        self.assertEqual(first, second)
        self.assertEqual(first.error_categories, ("file_hash_mismatch",))
        self.assertFalse(first.labeling_ready)
        self.assertIsNone(first.release_decision)

    def test_case_aba_bytes_are_bound_to_first_inventory_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_value, _ = _write_tree(root)
            case_path = root / "cases" / f"{CASE_REF}.json"
            valid_bytes = case_path.read_bytes()
            invalid_value = copy.deepcopy(case_value)
            invalid_value["case_sha256"] = "0" * 64
            invalid_bytes = canonical_json_bytes(invalid_value)
            self.assertEqual(len(invalid_bytes), len(valid_bytes))
            inode = case_path.stat().st_ino
            fixed_mtime_ns = case_path.stat().st_mtime_ns

            def replace(payload: bytes) -> None:
                case_path.write_bytes(payload)
                os.utime(
                    case_path,
                    ns=(fixed_mtime_ns, fixed_mtime_ns),
                )

            replace(invalid_bytes)
            original_load = dataset_module._load_dataset_case_at

            def load_during_valid_window(*args: object, **kwargs: object) -> object:
                replace(valid_bytes)
                try:
                    return original_load(*args, **kwargs)
                finally:
                    replace(invalid_bytes)

            with patch.object(
                dataset_module,
                "_load_dataset_case_at",
                side_effect=load_during_valid_window,
            ):
                validation = validate_dataset_tree(root)

            final_stat = case_path.stat()
        self.assertEqual(final_stat.st_ino, inode)
        self.assertEqual(final_stat.st_size, len(invalid_bytes))
        self.assertEqual(final_stat.st_mtime_ns, fixed_mtime_ns)
        self.assertFalse(validation.valid)
        self.assertIn("file_changed", validation.error_categories)

    def test_manifest_aba_bytes_are_bound_to_first_inventory_digest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _, manifest_value = _write_tree(root)
            manifest_path = root / "manifest.json"
            valid_bytes = manifest_path.read_bytes()
            invalid_value = copy.deepcopy(manifest_value)
            invalid_value["manifest_sha256"] = "0" * 64
            invalid_bytes = canonical_json_bytes(invalid_value)
            self.assertEqual(len(invalid_bytes), len(valid_bytes))
            inode = manifest_path.stat().st_ino
            fixed_mtime_ns = manifest_path.stat().st_mtime_ns

            def replace(payload: bytes) -> None:
                manifest_path.write_bytes(payload)
                os.utime(
                    manifest_path,
                    ns=(fixed_mtime_ns, fixed_mtime_ns),
                )

            replace(invalid_bytes)
            original_load = dataset_module._load_manifest_at

            def load_during_valid_window(*args: object, **kwargs: object) -> object:
                replace(valid_bytes)
                try:
                    return original_load(*args, **kwargs)
                finally:
                    replace(invalid_bytes)

            with patch.object(
                dataset_module,
                "_load_manifest_at",
                side_effect=load_during_valid_window,
            ):
                validation = validate_dataset_tree(root)

            final_stat = manifest_path.stat()
        self.assertEqual(final_stat.st_ino, inode)
        self.assertEqual(final_stat.st_size, len(invalid_bytes))
        self.assertEqual(final_stat.st_mtime_ns, fixed_mtime_ns)
        self.assertFalse(validation.valid)
        self.assertIn("file_changed", validation.error_categories)


class Fixed10FreezeTests(unittest.TestCase):
    def freeze(self, source_dir: Path, output_dir: Path) -> str:
        freeze = getattr(dataset_module, "freeze_fixed10_dataset")
        return freeze(
            source_dir,
            output_dir,
            lock_timeout_seconds=5.0,
        )

    def assert_freeze_rejected(
        self,
        source_dir: Path,
        output_dir: Path,
    ) -> None:
        output_preexisted = output_dir.exists() or output_dir.is_symlink()
        error_type = getattr(dataset_module, "Fixed10FreezeError")
        with self.assertRaises(error_type):
            self.freeze(source_dir, output_dir)
        if not output_preexisted:
            self.assertFalse(output_dir.exists())

    def test_source_export_schema_is_strict_and_revalidates_projection(
        self,
    ) -> None:
        model_type = getattr(dataset_module, "Fixed10SourceExport")
        value = _fixed10_source_value(1, fixed3_rank=1)
        model = model_type.model_validate(value)
        self.assertEqual(model.fixed3_rank, 1)

        for forbidden in (
            "skip",
            "exclusion",
            "run_id",
            "articleid",
            "url",
        ):
            with self.subTest(forbidden=forbidden), self.assertRaises(
                ValidationError
            ):
                model_type.model_validate({**value, forbidden: "forbidden"})

        bad_hash = copy.deepcopy(value)
        bad_hash["projection"]["projection_sha256"] = "0" * 64
        with self.assertRaises(ValidationError):
            model_type.model_validate(bad_hash)

        evidence_c = copy.deepcopy(value)
        evidence_c["projection"]["units"][0]["evidence"][0]["level"] = "C"
        projection_payload = {
            key: nested
            for key, nested in evidence_c["projection"].items()
            if key != "projection_sha256"
        }
        evidence_c["projection"]["projection_sha256"] = canonical_sha256(
            projection_payload
        )
        with self.assertRaises(ValidationError):
            model_type.model_validate(evidence_c)

        unsafe = copy.deepcopy(value)
        unsafe["projection"]["units"][0]["text"] = "来源 x:private"
        projection_payload = {
            key: nested
            for key, nested in unsafe["projection"].items()
            if key != "projection_sha256"
        }
        unsafe["projection"]["projection_sha256"] = canonical_sha256(
            projection_payload
        )
        with self.assertRaises(ValidationError):
            model_type.model_validate(unsafe)

    def test_freeze_writes_exact_deterministic_fixed10_and_is_noop_on_repeat(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            output_dir = root / "fixed10" / "v1"
            _write_fixed10_sources(source_dir)

            first_status = self.freeze(source_dir, output_dir)
            first_snapshot = _directory_snapshot(output_dir)
            manifest = load_dataset_manifest(output_dir / "manifest.json")
            validation = validate_dataset_tree(output_dir)
            second_status = self.freeze(source_dir, output_dir)
            second_snapshot = _directory_snapshot(output_dir)

        self.assertEqual(first_status, "created")
        self.assertEqual(second_status, "unchanged")
        self.assertEqual(first_snapshot, second_snapshot)
        self.assertEqual(manifest.dataset_id, "fixed10")
        self.assertEqual(manifest.dataset_version, "fixed10/v1")
        self.assertEqual(manifest.rubric_version, FIXED10_RUBRIC_VERSION)
        self.assertEqual(manifest.declared_count, 10)
        self.assertEqual(manifest.runnable_count, 10)
        self.assertEqual(manifest.exclusion_count, 0)
        self.assertEqual(len(manifest.entries), 10)
        ordered_refs = tuple(entry.case_ref for entry in manifest.entries)
        self.assertEqual(ordered_refs, tuple(sorted(ordered_refs)))
        self.assertEqual(manifest.subsets["fixed10"], ordered_refs)
        self.assertEqual(
            manifest.subsets["fixed3"],
            tuple(f"case_{index:032x}" for index in (1, 2, 3)),
        )
        self.assertTrue(validation.valid)
        self.assertEqual(
            set(first_snapshot),
            {
                "manifest.json",
                *{
                    f"cases/case_{index:032x}.json"
                    for index in range(1, 11)
                },
            },
        )
        for payload, _inode, _mtime_ns in first_snapshot.values():
            self.assertEqual(
                payload,
                canonical_json_bytes(json.loads(payload.decode("utf-8"))),
            )

    def test_normal_cli_freezes_synthetic_sources(self) -> None:
        self.assertTrue(FREEZE_SCRIPT.is_file())
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            output_dir = root / "output"
            _write_fixed10_sources(source_dir)
            result = subprocess.run(
                [
                    sys.executable,
                    str(FREEZE_SCRIPT),
                    "fixed10",
                    "--source-dir",
                    str(source_dir),
                    "--output-dir",
                    str(output_dir),
                    "--lock-timeout-seconds",
                    "5",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "dataset_version": "fixed10/v1",
                "status": "created",
            },
        )
        self.assertEqual(result.stderr, "")

    def test_freeze_rejects_wrong_counts_and_duplicate_identities(self) -> None:
        mutations = (
            ("nine", 9, None),
            ("eleven", 11, None),
            ("duplicate_case", 10, "case_ref"),
            ("duplicate_material", 10, "material_identity_sha256"),
            ("duplicate_content", 10, "source_content_sha256"),
        )
        for name, count, duplicate_field in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                source_dir = root / "source"
                output_dir = root / "output"
                values = _write_fixed10_sources(source_dir, count=count)
                if duplicate_field is not None:
                    changed = copy.deepcopy(values[-1])
                    changed[duplicate_field] = values[0][duplicate_field]
                    (source_dir / f"export-{count:02d}.json").write_bytes(
                        canonical_json_bytes(changed)
                    )

                self.assert_freeze_rejected(source_dir, output_dir)

    def test_freeze_requires_exact_fixed3_ranks(self) -> None:
        rank_maps = (
            {1: 1, 2: 2},
            {1: 1, 2: 2, 3: 2},
            {1: 1, 2: 2, 3: 3, 4: 3},
        )
        for rank_map in rank_maps:
            with (
                self.subTest(rank_map=rank_map),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                root = Path(tmpdir)
                source_dir = root / "source"
                output_dir = root / "output"
                _write_fixed10_sources(source_dir, ranks=rank_map)

                self.assert_freeze_rejected(source_dir, output_dir)

    def test_freeze_rejects_unsafe_noncanonical_and_bad_hash_sources(
        self,
    ) -> None:
        mutations = (
            "bad_hash",
            "evidence_c",
            "raw_field",
            "unsafe_url",
            "noncanonical",
        )
        for mutation in mutations:
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                root = Path(tmpdir)
                source_dir = root / "source"
                output_dir = root / "output"
                values = _write_fixed10_sources(source_dir)
                changed = copy.deepcopy(values[-1])
                if mutation == "bad_hash":
                    changed["projection"]["projection_sha256"] = "0" * 64
                elif mutation == "evidence_c":
                    changed["projection"]["units"][0]["evidence"][0]["level"] = "C"
                elif mutation == "raw_field":
                    changed["skip"] = {"code": "SOURCE_ATTACHMENT_UNAVAILABLE"}
                elif mutation == "unsafe_url":
                    changed["projection"]["units"][0]["text"] = (
                        "来源 javascript:private"
                    )
                if mutation in {"evidence_c", "unsafe_url"}:
                    projection_payload = {
                        key: nested
                        for key, nested in changed["projection"].items()
                        if key != "projection_sha256"
                    }
                    changed["projection"]["projection_sha256"] = (
                        canonical_sha256(projection_payload)
                    )
                path = source_dir / "export-10.json"
                if mutation == "noncanonical":
                    path.write_text(
                        json.dumps(changed, ensure_ascii=True, indent=2),
                        encoding="utf-8",
                    )
                else:
                    path.write_bytes(canonical_json_bytes(changed))

                self.assert_freeze_rejected(source_dir, output_dir)

    def test_source_tree_rejects_extras_subdirs_symlinks_and_nonregulars(
        self,
    ) -> None:
        def extra_file(source_dir: Path) -> None:
            (source_dir / "extra.json").write_text("{}", encoding="utf-8")

        def subdirectory(source_dir: Path) -> None:
            (source_dir / "nested").mkdir()

        def symlink(source_dir: Path) -> None:
            (source_dir / "linked.json").symlink_to(
                source_dir / "export-01.json"
            )

        def fifo(source_dir: Path) -> None:
            os.mkfifo(source_dir / "source-fifo")

        for name, mutate in (
            ("extra", extra_file),
            ("subdirectory", subdirectory),
            ("symlink", symlink),
            ("fifo", fifo),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                source_dir = root / "source"
                output_dir = root / "output"
                _write_fixed10_sources(source_dir)
                mutate(source_dir)

                self.assert_freeze_rejected(source_dir, output_dir)

    def test_existing_different_or_unsafe_output_fails_unchanged(self) -> None:
        for mutation in ("different", "extra", "symlink", "nonregular"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                root = Path(tmpdir)
                source_dir = root / "source"
                output_dir = root / "output"
                _write_fixed10_sources(source_dir)
                if mutation in {"different", "extra"}:
                    self.freeze(source_dir, output_dir)
                    if mutation == "different":
                        manifest_path = output_dir / "manifest.json"
                        manifest_path.write_bytes(
                            manifest_path.read_bytes() + b" "
                        )
                    else:
                        (output_dir / "extra").write_bytes(b"x")
                    before = _directory_snapshot(output_dir)
                    error_type = getattr(
                        dataset_module,
                        "Fixed10FreezeError",
                    )
                    with self.assertRaises(error_type):
                        self.freeze(source_dir, output_dir)
                    self.assertEqual(_directory_snapshot(output_dir), before)
                elif mutation == "symlink":
                    target = root / "target"
                    target.mkdir()
                    output_dir.symlink_to(target, target_is_directory=True)
                    self.assert_freeze_rejected(source_dir, output_dir)
                    self.assertTrue(output_dir.is_symlink())
                else:
                    output_dir.write_bytes(b"do-not-replace")
                    self.assert_freeze_rejected(source_dir, output_dir)
                    self.assertEqual(output_dir.read_bytes(), b"do-not-replace")

    def test_existing_hardlinked_manifest_or_case_is_rejected_unchanged(
        self,
    ) -> None:
        for member in ("manifest", "case"):
            with (
                self.subTest(member=member),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                root = Path(tmpdir)
                source_dir = root / "source"
                output_dir = root / "output"
                external = root / "external.json"
                _write_fixed10_sources(source_dir)
                self.freeze(source_dir, output_dir)
                published = (
                    output_dir / "manifest.json"
                    if member == "manifest"
                    else output_dir
                    / "cases"
                    / "case_00000000000000000000000000000001.json"
                )
                external.write_bytes(published.read_bytes())
                published.unlink()
                os.link(external, published)
                before = external.stat()
                before_bytes = external.read_bytes()

                error_type = getattr(dataset_module, "Fixed10FreezeError")
                with self.assertRaises(error_type):
                    self.freeze(source_dir, output_dir)

                after = external.stat()
                self.assertEqual(external.read_bytes(), before_bytes)
                self.assertEqual(after.st_ino, before.st_ino)
                self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(after.st_nlink, 2)
                self.assertTrue(os.path.samefile(external, published))

    def test_staged_hardlinked_manifest_or_case_is_never_published(
        self,
    ) -> None:
        for member in ("manifest", "case"):
            with (
                self.subTest(member=member),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                root = Path(tmpdir)
                source_dir = root / "source"
                output_dir = root / "output"
                external = root / f"external-{member}.json"
                _write_fixed10_sources(source_dir)
                original = getattr(dataset_module, "_write_exclusive_at")
                linked_payload: bytes | None = None

                def hardlink_written_file(
                    parent_descriptor: int,
                    name: str,
                    payload: bytes,
                ) -> None:
                    nonlocal linked_payload
                    original(parent_descriptor, name, payload)
                    matches = (
                        member == "manifest" and name == "manifest.json"
                    ) or (
                        member == "case"
                        and name
                        == "case_00000000000000000000000000000001.json"
                    )
                    if matches:
                        os.link(
                            name,
                            external,
                            src_dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                        linked_payload = payload

                error_type = getattr(dataset_module, "Fixed10FreezeError")
                with (
                    patch.object(
                        dataset_module,
                        "_write_exclusive_at",
                        side_effect=hardlink_written_file,
                    ),
                    self.assertRaises(error_type),
                ):
                    self.freeze(source_dir, output_dir)

                self.assertIsNotNone(linked_payload)
                self.assertFalse(output_dir.exists())
                self.assertEqual(external.read_bytes(), linked_payload)
                self.assertEqual(external.stat().st_nlink, 1)
                self.assertFalse(
                    any("freeze-stage" in path.name for path in root.iterdir())
                )

    def test_concurrent_cli_calls_create_once_without_clobber(self) -> None:
        self.assertTrue(FREEZE_SCRIPT.is_file())
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            output_dir = root / "output"
            _write_fixed10_sources(source_dir)
            command = [
                sys.executable,
                str(FREEZE_SCRIPT),
                "fixed10",
                "--source-dir",
                str(source_dir),
                "--output-dir",
                str(output_dir),
            ]
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import fcntl,os,sys;"
                        "fd=os.open(sys.argv[1],"
                        "os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);"
                        "fcntl.flock(fd,fcntl.LOCK_EX);"
                        "print('locked',flush=True);"
                        "os.read(0,1)"
                    ),
                    str(root),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertIsNotNone(holder.stdout)
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            try:
                blocked_processes = [
                    subprocess.Popen(
                        [
                            *command,
                            "--lock-timeout-seconds",
                            "0.2",
                        ],
                        cwd=PROJECT_ROOT,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    for _ in range(2)
                ]
                blocked = [
                    process.communicate(timeout=30)
                    for process in blocked_processes
                ]
                self.assertEqual(
                    [process.returncode for process in blocked_processes],
                    [2, 2],
                    blocked,
                )
                self.assertFalse(output_dir.exists())
                self.assertEqual(
                    {
                        json.loads(stderr)["category"]
                        for _stdout, stderr in blocked
                    },
                    {"lock_timeout"},
                )
            finally:
                holder.kill()
                holder.communicate(timeout=10)

            processes = [
                subprocess.Popen(
                    [
                        *command,
                        "--lock-timeout-seconds",
                        "5",
                    ],
                    cwd=PROJECT_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(2)
            ]
            completed = [
                process.communicate(timeout=30)
                for process in processes
            ]
            return_codes = [process.returncode for process in processes]
            validation = validate_dataset_tree(output_dir)

        self.assertEqual(return_codes, [0, 0], completed)
        outputs = [json.loads(stdout) for stdout, _stderr in completed]
        self.assertEqual(
            {output["status"] for output in outputs},
            {"created", "unchanged"},
        )
        self.assertTrue(validation.valid)
        self.assertTrue(all(stderr == "" for _stdout, stderr in completed))

    def test_failed_write_cleans_owned_staging_and_leaves_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            output_dir = root / "output"
            _write_fixed10_sources(source_dir)
            original = getattr(dataset_module, "_write_exclusive_at")
            call_count = 0

            def fail_after_first(*args: object, **kwargs: object) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("synthetic write failure")
                original(*args, **kwargs)

            error_type = getattr(dataset_module, "Fixed10FreezeError")
            with (
                patch.object(
                    dataset_module,
                    "_write_exclusive_at",
                    side_effect=fail_after_first,
                ),
                self.assertRaises(error_type),
            ):
                self.freeze(source_dir, output_dir)

            self.assertFalse(output_dir.exists())
            self.assertFalse(
                any(
                    "freeze-stage" in path.name
                    for path in root.iterdir()
                )
            )
            lock_path = root / ".output.freeze-lock"
            self.assertFalse(lock_path.exists())

    def test_crashed_holder_releases_anchored_parent_directory_lock(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            output_dir = root / "output"
            _write_fixed10_sources(source_dir)
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import fcntl,os,sys;"
                        "fd=os.open(sys.argv[1],"
                        "os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);"
                        "fcntl.flock(fd,fcntl.LOCK_EX);"
                        "print('locked',flush=True);"
                        "os.read(0,1)"
                    ),
                    str(root),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertIsNotNone(holder.stdout)
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            freeze = getattr(dataset_module, "freeze_fixed10_dataset")
            error_type = getattr(dataset_module, "Fixed10FreezeError")
            try:
                with self.assertRaises(error_type):
                    freeze(
                        source_dir,
                        output_dir,
                        lock_timeout_seconds=0.2,
                    )
                self.assertFalse(output_dir.exists())
            finally:
                holder.kill()
                holder.communicate(timeout=10)

            status = self.freeze(source_dir, output_dir)

        self.assertEqual(status, "created")

    def test_decoy_lock_path_is_not_used_or_replaced(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            output_dir = root / "output"
            target = root / "target"
            lock_path = root / ".output.freeze-lock"
            _write_fixed10_sources(source_dir)
            target.write_bytes(b"do-not-follow")
            lock_path.symlink_to(target)

            status = self.freeze(source_dir, output_dir)

            self.assertEqual(status, "created")
            self.assertTrue(lock_path.is_symlink())
            self.assertEqual(target.read_bytes(), b"do-not-follow")

    def test_legacy_declared10_selected9_manifest_cannot_be_source(self) -> None:
        from app import regression_manifest

        legacy_path = (
            PROJECT_ROOT / "tests" / "fixtures" / "8099_regression_cases.json"
        )
        legacy = regression_manifest.load_manifest(legacy_path)
        self.assertEqual(len(legacy["subsets"]["fixed10"]), 10)
        self.assertEqual(
            len(regression_manifest.select_cases(legacy, "fixed10")),
            9,
        )
        self.assertEqual(
            regression_manifest.excluded_cases(legacy, "fixed10"),
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

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            output_dir = root / "output"
            _write_fixed10_sources(source_dir)
            (source_dir / "export-10.json").write_bytes(
                canonical_json_bytes(legacy)
            )

            self.assert_freeze_rejected(source_dir, output_dir)


if __name__ == "__main__":
    unittest.main()

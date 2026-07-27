from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.deepeval_advisory import datasets as dataset_module
from app.deepeval_advisory.hashing import (
    canonical_json_bytes,
    canonical_sha256,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FREEZE_SCRIPT = PROJECT_ROOT / "scripts" / "freeze_deepeval_dataset.py"
REAL_CALIBRATION_ROOT = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "deepeval_advisory"
    / "calibration100"
    / "v1"
)
CALIBRATION_SOURCE_SCHEMA_VERSION = (
    "8099.deepeval-calibration100-source/v1"
)
RISK_TAGS = (
    "date",
    "amount",
    "entity",
    "procurement_scope",
    "attachment_state",
    "evidence_c_boundary",
    "memory_boundary",
    "unsupported_key_conclusion",
)


def _expected_sample_ref(
    source_group_ref: str,
    unit_id: str,
    source_content_sha256: str,
) -> str:
    digest = canonical_sha256(
        {
            "source_group_ref": source_group_ref,
            "unit_id": unit_id,
            "source_content_sha256": source_content_sha256,
        }
    )
    return f"sample_{digest[:32]}"


def _projection_value(
    index: int,
    *,
    unit_count: int = 1,
) -> dict[str, object]:
    units = [
        {
            "unit_id": f"unit_{index + offset:016x}",
            "kind": "claim" if offset % 2 == 0 else "section",
            "text": f"Approved advisory unit {index + offset}.",
            "claim_count": 1,
            "evidence": [
                {
                    "local_id": "e1",
                    "level": "A",
                    "kind": "article_text",
                    "excerpt": f"Bounded evidence {index + offset}.",
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
            "expected_facts": [f"Expected fact {index + offset}."],
            "attachment_expectation": None,
        }
        for offset in range(unit_count)
    ]
    payload: dict[str, object] = {
        "schema_version": "8099.deepeval-projection/v1",
        "projection_version": "claim-ab-v1",
        "run_ref": f"runref_{index:032x}",
        "report_version": 1,
        "report_sha256": hashlib.sha256(
            f"report-{index}".encode("utf-8")
        ).hexdigest(),
        "units": units,
    }
    payload["projection_sha256"] = canonical_sha256(payload)
    return payload


def _source_value(
    index: int,
    *,
    split_assignment: str | None = None,
    unit_count: int = 1,
) -> dict[str, object]:
    split = split_assignment or (
        "calibration" if index <= 60 else "validation"
    )
    group_index = 1 if index in {1, 2} else index
    source_group_ref = f"source_group_{group_index:032x}"
    material_index = group_index
    content_index = group_index
    projection = _projection_value(index, unit_count=unit_count)
    unit_id = (
        projection["units"][0]["unit_id"]
        if projection["units"]
        else f"unit_{index:016x}"
    )
    source_content_sha256 = hashlib.sha256(
        f"content-{content_index}".encode("utf-8")
    ).hexdigest()
    risk_tags: list[str] = []
    if 61 <= index <= 68:
        risk_tags = [RISK_TAGS[index - 61]]
    return {
        "schema_version": CALIBRATION_SOURCE_SCHEMA_VERSION,
        "case_ref": f"case_{index:032x}",
        "sample_ref": _expected_sample_ref(
            source_group_ref,
            unit_id,
            source_content_sha256,
        ),
        "source_group_ref": source_group_ref,
        "split_assignment": split,
        "risk_tier": "high" if risk_tags else "standard",
        "risk_tags": risk_tags,
        "material_identity_sha256": hashlib.sha256(
            f"material-{material_index}".encode("utf-8")
        ).hexdigest(),
        "source_content_sha256": source_content_sha256,
        "projection": projection,
    }


def _refresh_projection_hash(value: dict[str, object]) -> None:
    projection = value["projection"]
    projection["projection_sha256"] = canonical_sha256(
        {
            key: nested
            for key, nested in projection.items()
            if key != "projection_sha256"
        }
    )


def _refresh_sample_ref(value: dict[str, object]) -> None:
    unit_id = value["projection"]["units"][0]["unit_id"]
    value["sample_ref"] = _expected_sample_ref(
        value["source_group_ref"],
        unit_id,
        value["source_content_sha256"],
    )


def _write_sources(
    root: Path,
    *,
    count: int = 100,
    values: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    root.mkdir()
    source_values = values or [
        _source_value(index)
        for index in range(1, count + 1)
    ]
    for index, value in enumerate(source_values, start=1):
        (root / f"export-{index:03d}.json").write_bytes(
            canonical_json_bytes(value)
        )
    return source_values


def _freeze(
    source_dir: Path,
    output_dir: Path,
) -> str:
    freeze = getattr(
        dataset_module,
        "freeze_calibration100_dataset",
        None,
    )
    if freeze is None:
        raise AssertionError("freeze_calibration100_dataset is not implemented")
    return freeze(source_dir, output_dir)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class CalibrationManifestTests(unittest.TestCase):
    def assert_freeze_rejected(
        self,
        values: list[dict[str, object]],
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            output_dir = root / "output"
            _write_sources(source_dir, values=values)

            error_type = getattr(
                dataset_module,
                "Fixed10FreezeError",
            )
            with self.assertRaises(error_type):
                _freeze(source_dir, output_dir)

            self.assertFalse(output_dir.exists())

    def test_calibration_freeze_api_and_strict_source_schema_exist(
        self,
    ) -> None:
        self.assertTrue(
            hasattr(dataset_module, "freeze_calibration100_dataset")
        )
        self.assertTrue(
            hasattr(dataset_module, "Calibration100SourceExport")
        )

    def test_requires_exactly_one_hundred_units(self) -> None:
        for count in (99, 101):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                source_dir = root / "source"
                output_dir = root / "output"
                _write_sources(source_dir, count=count)
                with self.assertRaises(
                    getattr(dataset_module, "Fixed10FreezeError")
                ):
                    _freeze(source_dir, output_dir)
                self.assertFalse(output_dir.exists())

    def test_requires_exactly_one_projection_unit_per_sample(self) -> None:
        for unit_count in (0, 2):
            with self.subTest(unit_count=unit_count):
                values = [
                    _source_value(index)
                    for index in range(1, 101)
                ]
                values[9] = _source_value(10, unit_count=unit_count)
                self.assert_freeze_rejected(values)

    def test_rejects_duplicate_case_sample_and_unit_identities(self) -> None:
        duplicate_case = [
            _source_value(index)
            for index in range(1, 101)
        ]
        duplicate_case[1]["case_ref"] = duplicate_case[0]["case_ref"]
        self.assert_freeze_rejected(duplicate_case)

        duplicate_sample = [
            _source_value(index)
            for index in range(1, 101)
        ]
        duplicate_sample[1]["source_group_ref"] = duplicate_sample[0][
            "source_group_ref"
        ]
        duplicate_sample[1]["source_content_sha256"] = duplicate_sample[0][
            "source_content_sha256"
        ]
        duplicate_sample[1]["projection"]["units"][0]["unit_id"] = (
            duplicate_sample[0]["projection"]["units"][0]["unit_id"]
        )
        _refresh_projection_hash(duplicate_sample[1])
        _refresh_sample_ref(duplicate_sample[1])
        self.assertEqual(
            duplicate_sample[1]["sample_ref"],
            duplicate_sample[0]["sample_ref"],
        )
        self.assert_freeze_rejected(duplicate_sample)

        duplicate_unit = [
            _source_value(index)
            for index in range(1, 101)
        ]
        duplicate_unit[2]["projection"]["units"][0]["unit_id"] = (
            duplicate_unit[0]["projection"]["units"][0]["unit_id"]
        )
        _refresh_projection_hash(duplicate_unit[2])
        _refresh_sample_ref(duplicate_unit[2])
        self.assertNotEqual(
            duplicate_unit[2]["sample_ref"],
            duplicate_unit[0]["sample_ref"],
        )
        self.assert_freeze_rejected(duplicate_unit)

    def test_sample_ref_binds_group_unit_and_source_content(self) -> None:
        for field in (
            "source_group_ref",
            "source_content_sha256",
        ):
            with self.subTest(field=field):
                values = [
                    _source_value(index)
                    for index in range(1, 101)
                ]
                values[4][field] = (
                    "source_group_" + "f" * 32
                    if field == "source_group_ref"
                    else "f" * 64
                )
                self.assert_freeze_rejected(values)

        values = [
            _source_value(index)
            for index in range(1, 101)
        ]
        values[4]["projection"]["units"][0]["unit_id"] = (
            "unit_" + "f" * 16
        )
        _refresh_projection_hash(values[4])
        self.assert_freeze_rejected(values)

    def test_requires_exact_sixty_forty_split(self) -> None:
        for changed_index, assignment, expected_counts in (
            (60, "validation", (59, 41)),
            (61, "calibration", (61, 39)),
        ):
            with self.subTest(expected_counts=expected_counts):
                values = [
                    _source_value(index)
                    for index in range(1, 101)
                ]
                values[changed_index - 1]["split_assignment"] = assignment
                self.assert_freeze_rejected(values)

    def test_rejects_source_group_leakage_across_splits(self) -> None:
        values = [
            _source_value(index)
            for index in range(1, 101)
        ]
        values[60]["source_group_ref"] = values[0]["source_group_ref"]
        _refresh_sample_ref(values[60])
        self.assert_freeze_rejected(values)

    def test_requires_known_explicit_split_assignment(self) -> None:
        for mutation in ("missing", "unknown"):
            with self.subTest(mutation=mutation):
                values = [
                    _source_value(index)
                    for index in range(1, 101)
                ]
                if mutation == "missing":
                    del values[49]["split_assignment"]
                else:
                    values[49]["split_assignment"] = "holdout"
                self.assert_freeze_rejected(values)

    def test_source_schema_rejects_unknown_raw_and_unsafe_fields(self) -> None:
        values = [
            _source_value(index)
            for index in range(1, 101)
        ]
        values[0]["articleid"] = "raw-source-identity"
        self.assert_freeze_rejected(values)

        values = [
            _source_value(index)
            for index in range(1, 101)
        ]
        values[0]["projection"]["units"][0]["evidence"][0]["level"] = "C"
        _refresh_projection_hash(values[0])
        self.assert_freeze_rejected(values)

    def test_freeze_builds_complete_partition_and_labeling_incomplete(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            output_dir = root / "output"
            _write_sources(source_dir)

            self.assertEqual(_freeze(source_dir, output_dir), "created")
            manifest = dataset_module.load_dataset_manifest(
                output_dir / "manifest.json"
            )
            validation = dataset_module.validate_dataset_tree(output_dir)
            cases = tuple(
                dataset_module.load_dataset_case(output_dir, entry)
                for entry in manifest.entries
            )

        self.assertEqual(manifest.dataset_version, "calibration100/v1")
        self.assertEqual(manifest.declared_count, 100)
        self.assertEqual(manifest.runnable_count, 100)
        self.assertEqual(manifest.exclusion_count, 0)
        self.assertEqual(
            getattr(manifest, "labeling_status", None),
            "labeling_incomplete",
        )
        self.assertEqual(set(manifest.subsets), {"calibration", "validation"})
        calibration = set(manifest.subsets["calibration"])
        independent_validation = set(manifest.subsets["validation"])
        self.assertEqual(len(calibration), 60)
        self.assertEqual(len(independent_validation), 40)
        self.assertFalse(calibration & independent_validation)
        self.assertEqual(
            calibration | independent_validation,
            {entry.case_ref for entry in manifest.entries},
        )
        self.assertEqual(
            len({entry.sample_ref for entry in manifest.entries}),
            100,
        )
        self.assertTrue(
            all(len(case.projection.units) == 1 for case in cases)
        )
        self.assertEqual(
            {
                risk_tag
                for case in cases
                for risk_tag in case.risk_tags
            },
            set(RISK_TAGS),
        )
        validation_cases = tuple(
            case for case in cases if case.split == "validation"
        )
        self.assertEqual(len(validation_cases), 40)
        self.assertTrue(
            all(
                case.risk_tier in {"high", "standard"}
                and case.risk_tags is not None
                for case in validation_cases
            )
        )
        self.assertTrue(validation.valid, validation.error_categories)
        self.assertEqual(validation.checked_entry_count, 100)
        self.assertFalse(validation.labeling_ready)

    def test_manifest_loader_rejects_calibration_contract_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            output_dir = root / "output"
            _write_sources(source_dir)
            self.assertEqual(_freeze(source_dir, output_dir), "created")
            manifest_path = output_dir / "manifest.json"
            payload = json.loads(manifest_path.read_bytes())
            del payload["labeling_status"]
            payload["manifest_sha256"] = canonical_sha256(
                {
                    key: nested
                    for key, nested in payload.items()
                    if key != "manifest_sha256"
                }
            )
            manifest_path.write_bytes(canonical_json_bytes(payload))

            with self.assertRaises(
                getattr(dataset_module, "DatasetError")
            ):
                dataset_module.load_dataset_manifest(manifest_path)

    def test_same_announcement_may_supply_multiple_distinct_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            output_dir = root / "output"
            values = _write_sources(source_dir)

            self.assertEqual(
                values[0]["source_group_ref"],
                values[1]["source_group_ref"],
            )
            self.assertEqual(
                values[0]["material_identity_sha256"],
                values[1]["material_identity_sha256"],
            )
            self.assertEqual(
                values[0]["source_content_sha256"],
                values[1]["source_content_sha256"],
            )
            self.assertNotEqual(
                values[0]["projection"]["units"][0]["unit_id"],
                values[1]["projection"]["units"][0]["unit_id"],
            )
            self.assertEqual(_freeze(source_dir, output_dir), "created")

    def test_same_input_is_byte_identical_and_v1_is_not_rewritten(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            first_output = root / "first"
            second_output = root / "second"
            values = _write_sources(source_dir)

            self.assertEqual(_freeze(source_dir, first_output), "created")
            first_snapshot = _snapshot(first_output)
            first_manifest_stat = (first_output / "manifest.json").stat()
            self.assertEqual(_freeze(source_dir, first_output), "unchanged")
            unchanged_manifest_stat = (first_output / "manifest.json").stat()
            self.assertEqual(
                (
                    first_manifest_stat.st_ino,
                    first_manifest_stat.st_mtime_ns,
                ),
                (
                    unchanged_manifest_stat.st_ino,
                    unchanged_manifest_stat.st_mtime_ns,
                ),
            )
            self.assertEqual(_freeze(source_dir, second_output), "created")
            self.assertEqual(_snapshot(second_output), first_snapshot)

            changed = copy.deepcopy(values[99])
            changed["case_ref"] = "case_" + "f" * 32
            (source_dir / "export-100.json").write_bytes(
                canonical_json_bytes(changed)
            )
            with self.assertRaises(
                getattr(dataset_module, "Fixed10FreezeError")
            ):
                _freeze(source_dir, first_output)

            self.assertEqual(_snapshot(first_output), first_snapshot)

    def test_cli_supports_calibration100_without_network_or_fixture(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "source"
            output_dir = root / "output"
            _write_sources(source_dir)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(FREEZE_SCRIPT),
                    "calibration100",
                    "--source-dir",
                    str(source_dir),
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "dataset_version": "calibration100/v1",
                "status": "created",
            },
        )
        self.assertEqual(completed.stderr, "")

    def test_real_calibration_fixture_is_not_claimed_ready(self) -> None:
        self.assertFalse(REAL_CALIBRATION_ROOT.exists())


if __name__ == "__main__":
    unittest.main()

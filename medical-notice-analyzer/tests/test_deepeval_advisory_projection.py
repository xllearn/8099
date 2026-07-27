from __future__ import annotations

import copy
import hashlib
import hmac
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.deepeval_advisory.hashing import (
    BoundaryViolation,
    advisory_input_sha256,
    assert_safe_outbound_text,
    canonical_json_bytes,
    canonical_sha256,
    hmac_reference,
    text_sha256,
)
from app.deepeval_advisory.projection import (
    ProjectionError,
    build_projection,
    build_projection_from_values,
    build_run_snapshot_from_value,
    load_run_snapshot,
)
from app.evidence_schema import create_evidence_item, text_source_hash


ADVISORY_INPUT = {
    "report_sha256": "a" * 64,
    "evidence_projection_sha256": "b" * 64,
    "metric_set_sha256": "c" * 64,
    "deepeval_version": "4.1.3",
    "adapter_version": "adapter-v1",
    "judge_provider": "fake",
    "immutable_judge_model_or_profile_version": "fake-v1",
    "prompt_version": "prompt-v1",
    "response_schema_version": "result-v1",
    "safety_preamble_version": "safety-v1",
    "locale": "zh-CN",
}

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "deepeval_advisory" / "unit"
HMAC_KEY = b"h" * 32


def _fixture_value(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _run_value(**changes: object) -> dict[str, object]:
    value = _fixture_value("run_finished.json")
    value.update(changes)
    return value


def _pack_value() -> dict[str, object]:
    return _fixture_value("evidence_pack_v2.json")


def _empty_pack(pack_id: str = "pack_projection_fixture") -> dict[str, object]:
    return {
        "pack_id": pack_id,
        "evidence_schema_version": 2,
        "evidence_items": [],
    }


class AdvisoryHashingTests(unittest.TestCase):
    def test_canonical_hash_ignores_dictionary_order(self) -> None:
        self.assertEqual(
            canonical_sha256({"b": 2, "a": 1}),
            canonical_sha256({"a": 1, "b": 2}),
        )

    def test_canonical_json_preserves_unicode_and_does_not_mutate(self) -> None:
        value = {
            "报告": ["采购", {"结论": "合格"}],
            "a": 1,
        }
        before = copy.deepcopy(value)

        payload = canonical_json_bytes(value)

        self.assertEqual(
            payload,
            '{"a":1,"报告":["采购",{"结论":"合格"}]}'.encode("utf-8"),
        )
        self.assertEqual(value, before)
        self.assertEqual(
            canonical_sha256(value),
            hashlib.sha256(payload).hexdigest(),
        )

    def test_canonical_json_rejects_nan(self) -> None:
        with self.assertRaises(ValueError):
            canonical_json_bytes({"score": float("nan")})
        with self.assertRaises(ValueError):
            canonical_sha256({"score": float("nan")})

    def test_canonical_json_rejects_non_string_keys_recursively(self) -> None:
        invalid_values = (
            {1: "integer key"},
            {"outer": [{"valid": "value"}, {1: "nested integer key"}]},
        )
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(TypeError):
                canonical_json_bytes(value)

        self.assertEqual(
            canonical_json_bytes({"1": "string key"}),
            b'{"1":"string key"}',
        )

    def test_canonical_json_rejects_non_json_container_values(self) -> None:
        invalid_values = (
            {"payload": b"not-json"},
            {"payload": {"not", "json"}},
        )
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(TypeError):
                canonical_json_bytes(value)

    def test_canonical_json_keeps_tuples_as_json_arrays(self) -> None:
        self.assertEqual(
            canonical_json_bytes({"items": ("a", 1, {"ok": True})}),
            b'{"items":["a",1,{"ok":true}]}',
        )

    def test_text_hash_normalizes_crlf_and_cr_to_lf(self) -> None:
        expected = hashlib.sha256(
            "第一行\n第二行\n第三行\n".encode("utf-8")
        ).hexdigest()

        self.assertEqual(
            text_sha256("第一行\r\n第二行\r第三行\n"),
            expected,
        )
        self.assertEqual(
            text_sha256("第一行\n第二行\n第三行\n"),
            expected,
        )

    def test_text_hash_treats_none_as_empty_text(self) -> None:
        self.assertEqual(
            text_sha256(None),
            hashlib.sha256(b"").hexdigest(),
        )

    def test_hmac_reference_does_not_expose_source_identifier(self) -> None:
        source_id = "run_internal_123"
        ref = hmac_reference(b"k" * 32, "run", source_id)

        self.assertRegex(ref, r"^runref_[0-9a-f]{32}$")
        self.assertNotIn(source_id, ref)
        self.assertNotIn("internal", ref)

    def test_hmac_reference_uses_expected_digest_and_prefixes(self) -> None:
        key = b"k" * 32
        source_id = "source_123"
        expected_digest = hmac.new(
            key,
            f"run\0{source_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]

        self.assertEqual(
            hmac_reference(key, "run", source_id),
            f"runref_{expected_digest}",
        )
        self.assertRegex(
            hmac_reference(key, "pack", source_id),
            r"^packref_[0-9a-f]{32}$",
        )
        self.assertRegex(
            hmac_reference(key, "evidence", source_id),
            r"^ref_[0-9a-f]{32}$",
        )

    def test_hmac_reference_rejects_short_key(self) -> None:
        with self.assertRaises(ValueError):
            hmac_reference(b"k" * 31, "run", "run_internal_123")

    def test_cache_key_signature_is_exactly_keyword_only(self) -> None:
        signature = inspect.signature(advisory_input_sha256)

        self.assertEqual(
            tuple(signature.parameters),
            tuple(ADVISORY_INPUT),
        )
        for parameter in signature.parameters.values():
            with self.subTest(parameter=parameter.name):
                self.assertEqual(
                    parameter.kind,
                    inspect.Parameter.KEYWORD_ONLY,
                )
                self.assertIs(parameter.default, inspect.Parameter.empty)
        with self.assertRaises(TypeError):
            advisory_input_sha256(*ADVISORY_INPUT.values())

    def test_cache_key_changes_for_each_versioned_input(self) -> None:
        baseline = advisory_input_sha256(**ADVISORY_INPUT)

        self.assertEqual(baseline, canonical_sha256(ADVISORY_INPUT))
        for name, value in ADVISORY_INPUT.items():
            with self.subTest(parameter=name):
                changed = {
                    **ADVISORY_INPUT,
                    name: f"{value}-changed",
                }
                self.assertNotEqual(
                    advisory_input_sha256(**changed),
                    baseline,
                )

    def test_guard_rejects_generic_credential_indicators(self) -> None:
        blocked = (
            "Authorization: redacted",
            "COOKIE=session",
            "api key = redacted",
            "api_key=redacted",
            "api-key: redacted",
            "apikey:redacted",
            "password=redacted",
            "dsn=postgresql://db.example.com/reporting",
        )
        for value in blocked:
            with self.subTest(value=value), self.assertRaises(
                BoundaryViolation
            ):
                assert_safe_outbound_text(value)

    def test_guard_rejects_named_secret_and_token_indicators(self) -> None:
        blocked = (
            "access_token=syntheticAccessToken123",
            "refresh-token: syntheticRefreshToken123",
            "client secret = syntheticClientSecret123",
            "secret_key=syntheticSecretKey123",
            "private-key: syntheticPrivateKey123",
            "token=syntheticTokenValue123",
            '"service_token": "syntheticServiceToken123"',
            "credential: syntheticCredential123",
        )
        for value in blocked:
            with self.subTest(value=value), self.assertRaises(
                BoundaryViolation
            ):
                assert_safe_outbound_text(value)

    def test_guard_rejects_pem_jwt_and_provider_token_forms(self) -> None:
        blocked = (
            "-----BEGIN PRIVATE KEY-----",
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----BEGIN PGP PRIVATE KEY BLOCK-----",
            (
                "eyJhbGciOiJIUzI1NiJ9."
                "eyJzdWIiOiJzeW50aGV0aWMifQ."
                "c3ludGhldGljX3NpZ25hdHVyZQ"
            ),
            "sk-synthetic1234567890",
            "ghp_synthetic1234567890",
            "github_pat_synthetic1234567890",
            "xoxb-synthetic-1234567890",
            "xoxp-synthetic-1234567890",
            "glpat-synthetic1234567890",
            "AKIA" + "A" * 16,
            "AIza" + "A" * 35,
        )
        for value in blocked:
            with self.subTest(value=value), self.assertRaises(
                BoundaryViolation
            ):
                assert_safe_outbound_text(value)

    def test_guard_rejects_bearer_tokens(self) -> None:
        with self.assertRaises(BoundaryViolation):
            assert_safe_outbound_text("Bearer abc.DEF-123_+/~")

    def test_guard_rejects_windows_drive_paths(self) -> None:
        for value in (
            r"C:\private\report.json",
            "d:/private/report.json",
        ):
            with self.subTest(value=value), self.assertRaises(
                BoundaryViolation
            ):
                assert_safe_outbound_text(value)

    def test_guard_rejects_windows_unc_device_and_extended_paths(self) -> None:
        blocked = (
            r"\\server\share\report.json",
            r"//server/share/report.json",
            r"//server\share\report.json",
            r"\\server/share/report.json",
            r"\\?\C:\private\report.json",
            r"\\.\PIPE\advisory",
        )
        for value in blocked:
            with self.subTest(value=value), self.assertRaises(
                BoundaryViolation
            ):
                assert_safe_outbound_text(value)

    def test_guard_rejects_any_unix_absolute_path_token(self) -> None:
        blocked = (
            "/workspace/private/report.json",
            "/Users/alice/report.json",
            "/private/tmp/report.json",
            "source=/custom/location/report.json",
        )
        for value in blocked:
            with self.subTest(value=value), self.assertRaises(
                BoundaryViolation
            ):
                assert_safe_outbound_text(value)

    def test_guard_rejects_sensitive_absolute_unix_paths(self) -> None:
        for root in (
            "app",
            "opt",
            "var",
            "home",
            "root",
            "data",
            "etc",
            "usr",
            "srv",
            "mnt",
            "tmp",
            "run",
            "proc",
            "sys",
            "dev",
        ):
            value = f"source=/{root}/private/report.json"
            with self.subTest(root=root), self.assertRaises(
                BoundaryViolation
            ):
                assert_safe_outbound_text(value)

    def test_guard_rejects_private_and_internal_http_urls(self) -> None:
        blocked = (
            "http://localhost/admin",
            "https://LOCALHOST:8443/admin",
            "http://api.localhost/admin",
            "http://intranet/report",
            "https://judge.internal/evaluate",
            "http://127.42.0.1/admin",
            "http://10.255.255.254/admin",
            "http://192.168.34.87/internal",
            "http://172.16.0.1/internal",
            "https://172.31.255.254:9443/internal",
        )
        for value in blocked:
            with self.subTest(value=value), self.assertRaises(
                BoundaryViolation
            ):
                assert_safe_outbound_text(value)

    def test_guard_rejects_legacy_non_global_numeric_ipv4_hosts(self) -> None:
        blocked = (
            "http://127.1/admin",
            "http://0177.0.0.1/admin",
            "http://0x7f.0.0.1/admin",
            "http://2130706433/admin",
        )
        for value in blocked:
            with self.subTest(value=value), self.assertRaises(
                BoundaryViolation
            ):
                assert_safe_outbound_text(value)

    def test_guard_rejects_non_global_ipv6_and_dotted_localhost(self) -> None:
        blocked = (
            "http://localhost./admin",
            "http://[::1]/admin",
            "http://[::]/admin",
            "http://[fc00::1]/admin",
            "http://[fe80::1]/admin",
        )
        for value in blocked:
            with self.subTest(value=value), self.assertRaises(
                BoundaryViolation
            ):
                assert_safe_outbound_text(value)

    def test_guard_allows_benign_chinese_text_and_public_urls(self) -> None:
        allowed = (
            "本报告依据采购公告及附件形成，服务期为两年。",
            "A层证据显示项目预算为人民币一百万元，B层证据补充了交付安排。",
            "公开政策来源：https://www.gov.cn/zhengce/content/2026/report.html",
            "Public report: https://example.com/app/report?section=data",
            "Public path: https://example.com/workspace/private/report.json",
            "Public path: https://example.com/Users/alice/report.json",
            "Public boundary: http://172.15.255.254/report",
            "Public boundary: http://172.32.0.1/report",
            "Public address: http://11.0.0.1/report",
            "Public address: http://192.167.1.1/report",
            "Public address: http://128.0.0.1/report",
            "Public resolver: https://8.8.8.8/dns-query",
        )
        for value in allowed:
            with self.subTest(value=value):
                self.assertIsNone(assert_safe_outbound_text(value))

    def test_boundary_exception_is_generic_and_does_not_echo_text(self) -> None:
        secret = "UNIQUE_SECRET_VALUE_7f1d"
        offending = f"Authorization: Bearer {secret}"

        with self.assertRaises(BoundaryViolation) as raised:
            assert_safe_outbound_text(offending)

        self.assertEqual(
            str(raised.exception),
            "outbound text violates the safety boundary",
        )
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(offending, str(raised.exception))

    def test_standalone_provider_token_is_not_echoed_in_exception(self) -> None:
        token = "glpat-UNIQUE_SYNTHETIC_TOKEN_9f3d"

        with self.assertRaises(BoundaryViolation) as raised:
            assert_safe_outbound_text(token)

        self.assertEqual(
            str(raised.exception),
            "outbound text violates the safety boundary",
        )
        self.assertNotIn(token, str(raised.exception))

    def test_new_secret_classes_are_not_echoed_in_exceptions(self) -> None:
        secret = "UNIQUE_ACCESS_TOKEN_VALUE_8e2c"
        offending = f"access_token={secret}"

        with self.assertRaises(BoundaryViolation) as raised:
            assert_safe_outbound_text(offending)

        self.assertEqual(
            str(raised.exception),
            "outbound text violates the safety boundary",
        )
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(offending, str(raised.exception))


class AdvisoryProjectionTests(unittest.TestCase):
    def create_symlink_or_skip(
        self,
        link: Path,
        target: Path,
    ) -> None:
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(
                f"symlink creation is unavailable: {exc.__class__.__name__}"
            )

    def build_identity_projection(
        self,
        *,
        menu_code: str,
        visible_identity: str,
    ):
        text = f"采购服务期限为两年，内部代码 {visible_identity}"
        item = create_evidence_item(
            level="A",
            kind="article_text",
            value=text,
            source_ref={
                "menu_code": menu_code,
                "articleid": "article-identity-source",
                "quote": text,
                "source_hash": text_source_hash(text),
            },
        )
        return build_projection_from_values(
            run_value=_run_value(report_markdown=text),
            pack_value={
                "pack_id": "pack_projection_fixture",
                "evidence_schema_version": 2,
                "evidence_items": [item],
            },
            hmac_key=HMAC_KEY,
        )

    def test_finished_and_manual_review_fixtures_with_body_are_eligible(self) -> None:
        for name, expected_version in (
            ("run_finished.json", 1),
            ("run_manual_review.json", 2),
        ):
            with self.subTest(name=name):
                snapshot = load_run_snapshot(FIXTURES / name, HMAC_KEY)
                self.assertTrue(snapshot.eligible)
                self.assertIsNone(snapshot.ineligible_reason)
                self.assertEqual(snapshot.report_version, expected_version)

    def test_body_presence_is_derived_instead_of_trusting_persisted_boolean(self) -> None:
        with_body = build_run_snapshot_from_value(
            _run_value(has_formal_body=False),
            HMAC_KEY,
        )
        without_body = build_run_snapshot_from_value(
            _run_value(report_markdown="", report_ir=None, has_formal_body=True),
            HMAC_KEY,
        )

        self.assertTrue(with_body.eligible)
        self.assertFalse(without_body.eligible)
        self.assertEqual(without_body.ineligible_reason, "empty_body")

    def test_nonterminal_failed_interrupted_empty_and_technical_bodies_are_excluded(
        self,
    ) -> None:
        for status in (
            "created",
            "preparing",
            "running",
            "generating",
            "generated",
            "local_quality_checking",
            "repairing",
            "fallback_generating",
            "export_checking",
        ):
            with self.subTest(status=status):
                snapshot = build_run_snapshot_from_value(
                    _run_value(status=status, run_status=status),
                    HMAC_KEY,
                )
                self.assertFalse(snapshot.eligible)
                self.assertEqual(snapshot.ineligible_reason, "not_ready")

        for status in ("failed", "interrupted"):
            with self.subTest(status=status):
                snapshot = build_run_snapshot_from_value(
                    _run_value(status=status, run_status=status),
                    HMAC_KEY,
                )
                self.assertFalse(snapshot.eligible)

        technical = build_run_snapshot_from_value(
            _run_value(
                report_markdown=(
                    "由于本次自动生成结果未形成完整正文，仅供人工复核。"
                    "Dify evidence_pack OCR"
                )
            ),
            HMAC_KEY,
        )
        self.assertFalse(technical.eligible)
        self.assertEqual(technical.ineligible_reason, "technical_body")

    def test_a_substantive_report_that_mentions_ocr_is_not_technical_dominated(
        self,
    ) -> None:
        snapshot = build_run_snapshot_from_value(
            _run_value(
                report_markdown=(
                    "## 采购影响\n\n"
                    "项目服务期限为两年，企业应在规定时间内提交采购材料。"
                    "附件中的OCR文字已经人工核验，采购范围、申报条件和执行周期"
                    "均以公告原文为准，企业还应持续核对后续通知。"
                )
            ),
            HMAC_KEY,
        )

        self.assertTrue(snapshot.eligible)

    def test_status_disagreement_future_schema_and_nonpositive_version_fail_closed(
        self,
    ) -> None:
        invalid_values = (
            _run_value(run_status="needs_manual_review"),
            _run_value(schema_version=2),
            _run_value(version=0),
            _run_value(version=True),
        )
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(ProjectionError):
                build_run_snapshot_from_value(value, HMAC_KEY)

    def test_future_nested_report_ir_schema_fails_closed(self) -> None:
        secret = "REPORT_IR_FUTURE_SECRET"

        with self.assertRaises(ProjectionError) as raised:
            build_run_snapshot_from_value(
                _run_value(
                    report_markdown="",
                    report_ir={
                        "schema_version": 2,
                        "lead_paragraphs": [secret],
                        "sections": [],
                    },
                ),
                HMAC_KEY,
            )

        self.assertEqual(
            str(raised.exception),
            "report_ir schema is invalid or unsupported",
        )
        self.assertNotIn(secret, str(raised.exception))

    def test_report_hash_depends_only_on_normalized_markdown_and_report_ir(
        self,
    ) -> None:
        report_ir_one = {
            "title": "采购报告",
            "sections": [
                {
                    "heading": "影响",
                    "paragraphs": ["企业应核对申报条件。\r\n服务期限为两年。"],
                    "tables": [],
                    "highlights": [],
                }
            ],
        }
        report_ir_two = {
            "sections": [
                {
                    "tables": [],
                    "highlights": [],
                    "paragraphs": ["企业应核对申报条件。\n服务期限为两年。"],
                    "heading": "影响",
                }
            ],
            "title": "采购报告",
        }
        first = build_run_snapshot_from_value(
            _run_value(
                run_id="run_hash_first",
                pack_id="pack_hash_first",
                version=1,
                report_markdown="## 报告\r\n\r\n项目服务期限为两年。\r\n",
                report_ir=report_ir_one,
                updated_at="2026-07-27T00:00:00.000Z",
            ),
            HMAC_KEY,
        )
        second = build_run_snapshot_from_value(
            _run_value(
                run_id="run_hash_second",
                pack_id="pack_hash_second",
                status="needs_manual_review",
                run_status="needs_manual_review",
                version=9,
                report_markdown="## 报告\n\n项目服务期限为两年。\n",
                report_ir=report_ir_two,
                updated_at="2027-01-01T00:00:00.000Z",
            ),
            HMAC_KEY,
        )
        changed = build_run_snapshot_from_value(
            _run_value(
                run_id="run_hash_changed",
                pack_id="pack_hash_changed",
                report_markdown="## 报告\n\n项目服务期限为三年。\n",
                report_ir=report_ir_two,
            ),
            HMAC_KEY,
        )

        self.assertEqual(first.report_sha256, second.report_sha256)
        self.assertNotEqual(first.report_sha256, changed.report_sha256)
        self.assertNotEqual(first.run_ref, second.run_ref)
        self.assertNotEqual(first.report_version, second.report_version)

    def test_run_file_loader_rejects_bad_files_and_filename_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = (
                (b"", "empty"),
                (b"\xff\xfe", "invalid UTF-8"),
                (b"{", "invalid JSON"),
                (b"[]", "non-object JSON"),
            )
            for payload, label in cases:
                path = root / "run_finished.json"
                path.write_bytes(payload)
                with self.subTest(label=label), self.assertRaises(ProjectionError):
                    load_run_snapshot(path, HMAC_KEY)

            mismatch = root / "run_wrong_name.json"
            mismatch.write_text(
                json.dumps(_run_value(), ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(ProjectionError):
                load_run_snapshot(mismatch, HMAC_KEY)

            oversized = root / "run_finished.json"
            oversized.write_bytes(b"{}")
            with self.assertRaises(ProjectionError):
                load_run_snapshot(oversized, HMAC_KEY, max_bytes=1)

            with self.assertRaises(ProjectionError):
                load_run_snapshot(root, HMAC_KEY)

    def test_run_file_loader_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text(
                json.dumps(_run_value(), ensure_ascii=False),
                encoding="utf-8",
            )
            link = root / "run_finished.json"
            self.create_symlink_or_skip(link, target)

            with self.assertRaises(ProjectionError):
                load_run_snapshot(link, HMAC_KEY)

    @unittest.skipUnless(
        hasattr(os, "O_NOFOLLOW"),
        "O_NOFOLLOW is unavailable on this platform",
    )
    def test_file_loader_requests_o_nofollow_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run_finished.json"
            path.write_text(
                json.dumps(_run_value(), ensure_ascii=False),
                encoding="utf-8",
            )

            with patch(
                "app.deepeval_advisory.projection.os.open",
                wraps=os.open,
            ) as guarded_open:
                load_run_snapshot(path, HMAC_KEY)

            self.assertTrue(guarded_open.called)
            flags = guarded_open.call_args.args[1]
            self.assertEqual(flags & os.O_NOFOLLOW, os.O_NOFOLLOW)

    def test_projection_contains_only_matched_a_b_with_local_parent_ids(self) -> None:
        snapshot = load_run_snapshot(FIXTURES / "run_finished.json", HMAC_KEY)
        pack = _pack_value()
        projection = build_projection(
            snapshot,
            FIXTURES / "evidence_pack_v2.json",
        )
        serialized = projection.model_dump_json()
        unit = projection.units[0]

        self.assertEqual(len(projection.units), 1)
        self.assertEqual(unit.kind, "claim")
        self.assertEqual(unit.claim_count, 1)
        self.assertEqual([item.level for item in unit.evidence], ["A", "B"])
        self.assertEqual([item.local_id for item in unit.evidence], ["e1", "e2"])
        self.assertEqual(unit.evidence[1].parent_a_ids, ("e1",))
        for forbidden in (
            "C_ONLY_SECRET_MARKER",
            snapshot.run_id,
            snapshot.pack_id,
            "menu-private-001",
            "article-private-001",
            "menu_code",
            "articleid",
            "filename",
            "source_hash",
            *(str(item["evidence_id"]) for item in pack["evidence_items"]),
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)

    def test_projection_locator_contains_only_safe_enum_and_positive_positions(
        self,
    ) -> None:
        text = "采购表格要求企业提交申报材料"
        source_ref = {
            "menu_code": "private-menu",
            "articleid": "private-article",
            "attachment_id": "private-attachment",
            "filename": "private-filename.xlsx",
            "page_no": 3,
            "sheet_name": "private-sheet",
            "table_index": 2,
            "row": 4,
            "column": 5,
            "quote": text,
            "source_hash": "a" * 64,
        }
        item = create_evidence_item(
            level="A",
            kind="table_cell",
            value=text,
            source_ref=source_ref,
        )
        projection = build_projection_from_values(
            run_value=_run_value(report_markdown=text),
            pack_value={
                "pack_id": "pack_projection_fixture",
                "evidence_schema_version": 2,
                "evidence_items": [item],
            },
            hmac_key=HMAC_KEY,
        )
        locator = projection.units[0].evidence[0].locator

        self.assertIsNotNone(locator)
        self.assertEqual(
            locator.model_dump(exclude_none=True),
            {
                "kind": "table_cell",
                "table_index": 2,
                "row": 4,
                "column": 5,
            },
        )
        serialized = projection.model_dump_json()
        for forbidden in (
            "private-menu",
            "private-article",
            "private-attachment",
            "private-filename",
            "private-sheet",
            "a" * 64,
        ):
            self.assertNotIn(forbidden, serialized)

    def test_attachment_text_with_table_position_is_not_labeled_table_cell(
        self,
    ) -> None:
        text = "附件说明采购服务期限为两年"
        item = create_evidence_item(
            level="A",
            kind="attachment_text",
            value=text,
            source_ref={
                "menu_code": "attachment-menu",
                "articleid": "attachment-article",
                "attachment_id": "attachment-private-id",
                "filename": "attachment-private.txt",
                "table_index": 2,
                "quote": text,
                "source_hash": "b" * 64,
            },
        )

        projection = build_projection_from_values(
            run_value=_run_value(report_markdown=text),
            pack_value={
                "pack_id": "pack_projection_fixture",
                "evidence_schema_version": 2,
                "evidence_items": [item],
            },
            hmac_key=HMAC_KEY,
        )

        locator = projection.units[0].evidence[0].locator
        self.assertIsNotNone(locator)
        self.assertEqual(locator.kind, "article")
        self.assertIsNone(locator.table_index)
        self.assertIsNone(locator.row)
        self.assertIsNone(locator.column)

    def test_projection_is_deterministic_across_dict_order_item_order_and_newlines(
        self,
    ) -> None:
        run_one = _run_value()
        pack_one = _pack_value()
        run_two = dict(reversed(list(copy.deepcopy(run_one).items())))
        run_two["report_markdown"] = str(run_two["report_markdown"]).replace(
            "\n",
            "\r\n",
        )
        pack_two = dict(reversed(list(copy.deepcopy(pack_one).items())))
        pack_two["evidence_items"] = [
            dict(reversed(list(item.items())))
            for item in reversed(pack_two["evidence_items"])
        ]

        first = build_projection_from_values(
            run_value=run_one,
            pack_value=pack_one,
            hmac_key=HMAC_KEY,
        )
        second = build_projection_from_values(
            run_value=run_two,
            pack_value=pack_two,
            hmac_key=HMAC_KEY,
        )
        payload = first.model_dump(mode="json")
        digest = payload.pop("projection_sha256")

        self.assertEqual(first, second)
        self.assertEqual(digest, canonical_sha256(payload))

    def test_build_projection_does_not_modify_source_fixtures(self) -> None:
        run_path = FIXTURES / "run_finished.json"
        pack_path = FIXTURES / "evidence_pack_v2.json"
        before = {
            run_path: hashlib.sha256(run_path.read_bytes()).hexdigest(),
            pack_path: hashlib.sha256(pack_path.read_bytes()).hexdigest(),
        }

        snapshot = load_run_snapshot(run_path, HMAC_KEY)
        build_projection(snapshot, pack_path)

        after = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in before
        }
        self.assertEqual(before, after)

    def test_pack_identity_future_schema_and_ineligible_run_fail_closed(self) -> None:
        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=_run_value(),
                pack_value=_empty_pack("pack_different_identity"),
                hmac_key=HMAC_KEY,
            )
        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=_run_value(),
                pack_value={
                    "pack_id": "pack_projection_fixture",
                    "evidence_schema_version": 999,
                    "evidence_items": [],
                },
                hmac_key=HMAC_KEY,
            )
        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=_run_value(status="running", run_status="running"),
                pack_value=_empty_pack(),
                hmac_key=HMAC_KEY,
            )

    def test_pack_identity_is_required(self) -> None:
        pack = _pack_value()
        pack.pop("pack_id")

        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=_run_value(),
                pack_value=pack,
                hmac_key=HMAC_KEY,
            )

    def test_malformed_evidence_dependency_is_wrapped_without_echo(self) -> None:
        secret = "MALFORMED_DEPENDENCY_SECRET"
        pack = _pack_value()
        pack["evidence_items"][1]["derived_from"] = 20260727
        pack["private_diagnostic"] = secret

        with self.assertRaises(ProjectionError) as raised:
            build_projection_from_values(
                run_value=_run_value(),
                pack_value=pack,
                hmac_key=HMAC_KEY,
            )

        self.assertEqual(
            str(raised.exception),
            "evidence pack schema is invalid or unsupported",
        )
        self.assertNotIn(secret, str(raised.exception))

    def test_nested_nonfinite_pack_value_fails_closed_for_values_and_files(
        self,
    ) -> None:
        snapshot = load_run_snapshot(FIXTURES / "run_finished.json", HMAC_KEY)
        for label, value in (
            ("NaN", float("nan")),
            ("Infinity", float("inf")),
            ("negative Infinity", float("-inf")),
        ):
            pack = _pack_value()
            pack["private_diagnostic"] = {"score": value}

            with self.subTest(source="value", constant=label):
                with self.assertRaises(ProjectionError) as direct:
                    build_projection_from_values(
                        run_value=_run_value(),
                        pack_value=pack,
                        hmac_key=HMAC_KEY,
                    )
                self.assertNotIn(label.lower(), str(direct.exception).lower())

            with self.subTest(source="file", constant=label):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "pack.json"
                    path.write_text(
                        json.dumps(pack, ensure_ascii=False, allow_nan=True),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ProjectionError) as file_error:
                        build_projection(snapshot, path)
                self.assertNotIn(
                    label.lower(),
                    str(file_error.exception).lower(),
                )

    def test_index_key_error_is_wrapped_without_echo(self) -> None:
        secret = "INDEX_KEY_ERROR_SECRET"

        with patch(
            "app.deepeval_advisory.projection.build_claim_evidence_index",
            side_effect=KeyError(secret),
        ):
            with self.assertRaises(ProjectionError) as raised:
                build_projection_from_values(
                    run_value=_run_value(),
                    pack_value=_pack_value(),
                    hmac_key=HMAC_KEY,
                )

        self.assertEqual(
            str(raised.exception),
            "claim evidence indexing failed",
        )
        self.assertNotIn(secret, str(raised.exception))

    def test_evidence_file_loader_rejects_bad_nonregular_and_oversize_files(
        self,
    ) -> None:
        snapshot = load_run_snapshot(FIXTURES / "run_finished.json", HMAC_KEY)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "pack.json"
            for payload, label in (
                (b"", "empty"),
                (b"\xff\xfe", "invalid UTF-8"),
                (b"{", "invalid JSON"),
                (b"[]", "non-object JSON"),
            ):
                path.write_bytes(payload)
                with self.subTest(label=label), self.assertRaises(ProjectionError):
                    build_projection(snapshot, path)

            path.write_bytes(b"{}")
            with self.assertRaises(ProjectionError):
                build_projection(snapshot, path, max_bytes=1)

            with self.assertRaises(ProjectionError):
                build_projection(snapshot, root)

    def test_evidence_file_loader_rejects_symlinks(self) -> None:
        snapshot = load_run_snapshot(FIXTURES / "run_finished.json", HMAC_KEY)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text(
                json.dumps(_pack_value(), ensure_ascii=False),
                encoding="utf-8",
            )
            link = root / "pack-link.json"
            self.create_symlink_or_skip(link, target)
            with self.assertRaises(ProjectionError):
                build_projection(snapshot, link)

    def test_quote_is_preferred_and_non_scalar_fallback_is_rejected(self) -> None:
        text = "采购服务期限为两年"
        ref = {
            "menu_code": "menu-one",
            "articleid": "article-one",
            "quote": "",
            "source_hash": text_source_hash(text),
        }
        item = create_evidence_item(
            level="A",
            kind="article_field",
            value={"name": "期限", "value": text},
            source_ref=ref,
        )

        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=_run_value(report_markdown=text),
                pack_value={
                    "pack_id": "pack_projection_fixture",
                    "evidence_schema_version": 2,
                    "evidence_items": [item],
                },
                hmac_key=HMAC_KEY,
            )

    def test_per_evidence_excerpt_limit_rejects_without_truncation(self) -> None:
        text = "采购" + ("期" * 1499)
        item = create_evidence_item(
            level="A",
            kind="article_text",
            value=text,
            source_ref={
                "menu_code": "menu-long",
                "articleid": "article-long",
                "quote": "",
                "source_hash": text_source_hash(text),
            },
        )

        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=_run_value(report_markdown=text),
                pack_value={
                    "pack_id": "pack_projection_fixture",
                    "evidence_schema_version": 2,
                    "evidence_items": [item],
                },
                hmac_key=HMAC_KEY,
            )

    def test_total_claim_text_budget_is_hard(self) -> None:
        report = ("甲" * 3001) + "\n" + ("乙" * 3001)
        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=_run_value(report_markdown=report),
                pack_value=_empty_pack(),
                hmac_key=HMAC_KEY,
            )

    def test_total_unique_evidence_budget_is_hard(self) -> None:
        text = "采购服务期限为两年"
        items = [
            create_evidence_item(
                level="A",
                kind="article_text",
                value=text,
                source_ref={
                    "menu_code": f"menu-{index}",
                    "articleid": f"article-{index}",
                    "quote": text,
                    "source_hash": text_source_hash(text),
                },
            )
            for index in range(9)
        ]

        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=_run_value(report_markdown=text),
                pack_value={
                    "pack_id": "pack_projection_fixture",
                    "evidence_schema_version": 2,
                    "evidence_items": items,
                },
                hmac_key=HMAC_KEY,
            )

    def test_total_evidence_excerpt_budget_is_hard(self) -> None:
        text = "采购" + ("期" * 1398)
        items = [
            create_evidence_item(
                level="A",
                kind="article_text",
                value=text,
                source_ref={
                    "menu_code": f"menu-{index}",
                    "articleid": f"article-{index}",
                    "quote": "",
                    "source_hash": text_source_hash(text),
                },
            )
            for index in range(6)
        ]

        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=_run_value(report_markdown=text),
                pack_value={
                    "pack_id": "pack_projection_fixture",
                    "evidence_schema_version": 2,
                    "evidence_items": items,
                },
                hmac_key=HMAC_KEY,
            )

    def test_complete_projection_json_budget_is_hard(self) -> None:
        report = "\n".join(f"采购规则{index:04d}" for index in range(700))

        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=_run_value(report_markdown=report),
                pack_value=_empty_pack(),
                hmac_key=HMAC_KEY,
            )

    def test_outbound_boundary_violation_fails_projection(self) -> None:
        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=_run_value(
                    report_markdown=(
                        "采购服务期限为两年，内部详情见 "
                        "http://192.168.34.87/internal"
                    )
                ),
                pack_value=_empty_pack(),
                hmac_key=HMAC_KEY,
            )

    def test_raw_run_pack_and_evidence_id_in_claim_fail_closed(self) -> None:
        pack = _pack_value()
        raw_evidence_id = str(pack["evidence_items"][0]["evidence_id"])
        report = (
            "项目服务期限为两年，企业应在规定时间内提交采购材料。"
            f"内部记录 run_finished pack_projection_fixture {raw_evidence_id}"
        )

        with self.assertRaises(ProjectionError):
            build_projection_from_values(
                run_value=_run_value(report_markdown=report),
                pack_value=pack,
                hmac_key=HMAC_KEY,
            )

    def test_short_and_nfkc_equivalent_identity_leaks_fail_closed(self) -> None:
        cases = (
            ("m123456", "ｍ１２３４５６"),
            ("m", "m"),
        )
        for menu_code, visible_identity in cases:
            with self.subTest(menu_code=menu_code), self.assertRaises(
                ProjectionError
            ):
                self.build_identity_projection(
                    menu_code=menu_code,
                    visible_identity=visible_identity,
                )

    def test_identity_substring_inside_larger_ascii_token_is_allowed(self) -> None:
        cases = (
            ("m123456", "xm123456y"),
            ("m", "minimum"),
        )
        for menu_code, visible_identity in cases:
            with self.subTest(menu_code=menu_code):
                projection = self.build_identity_projection(
                    menu_code=menu_code,
                    visible_identity=visible_identity,
                )
                self.assertEqual(len(projection.units), 1)


if __name__ == "__main__":
    unittest.main()

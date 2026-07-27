from __future__ import annotations

import copy
import hashlib
import hmac
import inspect
import unittest

from app.deepeval_advisory.hashing import (
    BoundaryViolation,
    advisory_input_sha256,
    assert_safe_outbound_text,
    canonical_json_bytes,
    canonical_sha256,
    hmac_reference,
    text_sha256,
)


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

    def test_guard_rejects_sensitive_absolute_unix_paths(self) -> None:
        for root in ("app", "opt", "var", "home", "root", "data"):
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

    def test_guard_allows_benign_chinese_text_and_public_urls(self) -> None:
        allowed = (
            "本报告依据采购公告及附件形成，服务期为两年。",
            "A层证据显示项目预算为人民币一百万元，B层证据补充了交付安排。",
            "公开政策来源：https://www.gov.cn/zhengce/content/2026/report.html",
            "Public report: https://example.com/app/report?section=data",
            "Public boundary: http://172.15.255.254/report",
            "Public boundary: http://172.32.0.1/report",
            "Public address: http://11.0.0.1/report",
            "Public address: http://192.167.1.1/report",
            "Public address: http://128.0.0.1/report",
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


if __name__ == "__main__":
    unittest.main()

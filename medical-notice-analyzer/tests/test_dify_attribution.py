from __future__ import annotations

import importlib
import json
import unittest


class DifyAttributionTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module("app.dify_attribution")
        except ModuleNotFoundError:
            self.fail("provider attribution capability is missing")

    @staticmethod
    def response(*, workflow_id="wf-1", status="succeeded", outputs=None, **data_overrides):
        data = {"status": status, "outputs": outputs if outputs is not None else {"report_markdown": "# 完整报告"}}
        data.update(data_overrides)
        return {"workflow_run_id": workflow_id, "data": data}

    def assert_failure(self, response, expected_code):
        module = self.module()
        with self.assertRaises(module.ProviderResponseError) as caught:
            module.extract_provider_output(response)
        self.assertEqual(expected_code, caught.exception.code)
        self.assertEqual("provider", caught.exception.stage["layer"])
        self.assertEqual("blocked", caught.exception.stage["status"])
        self.assertEqual(expected_code, caught.exception.stage["code"])
        return caught.exception.stage

    def test_provider_failure_code_matrix(self):
        module = self.module()
        self.assertEqual(
            {"HTTP_ERROR", "WORKFLOW_ID_MISSING", "WORKFLOW_FAILED", "OUTPUT_EMPTY", "OUTPUT_TRUNCATED", "OUTPUT_SCHEMA_INVALID", "TIMEOUT"},
            module.PROVIDER_FAILURE_CODES,
        )
        self.assert_failure(self.response(workflow_id=""), "WORKFLOW_ID_MISSING")
        self.assert_failure(self.response(status="failed", error="workflow failed"), "WORKFLOW_FAILED")
        self.assert_failure(self.response(outputs={}), "OUTPUT_EMPTY")
        self.assert_failure(self.response(outputs={"report_markdown": "正文", "truncated": True}), "OUTPUT_TRUNCATED")
        self.assert_failure(self.response(outputs={"report_markdown": "正文"}, finish_reason="length"), "OUTPUT_TRUNCATED")
        self.assert_failure(self.response(outputs=["not", "an", "object"]), "OUTPUT_SCHEMA_INVALID")

    def test_success_returns_raw_output_separately_from_safe_stage_metadata(self):
        module = self.module()
        response = self.response(outputs={"report_markdown": "# 完整报告", "quality_check": {"passed": True}})
        workflow_id, output, stage = module.extract_provider_output(response)
        self.assertEqual("wf-1", workflow_id)
        self.assertEqual("# 完整报告", output["report_markdown"])
        self.assertEqual("ok", stage["status"])
        self.assertEqual("", stage["code"])
        serialized = json.dumps(stage, ensure_ascii=False)
        self.assertNotIn("完整报告", serialized)
        self.assertRegex(stage["output_artifact"]["sha256"], r"^[0-9a-f]{64}$")

    def test_nested_json_output_is_supported_but_malformed_json_is_schema_invalid(self):
        module = self.module()
        workflow_id, output, _ = module.extract_provider_output(
            self.response(outputs={"result": '{"report_markdown":"嵌套正文"}'})
        )
        self.assertEqual("wf-1", workflow_id)
        self.assertEqual("嵌套正文", output["report_markdown"])
        self.assert_failure(self.response(outputs={"result": "{bad-json"}), "OUTPUT_SCHEMA_INVALID")

    def test_empty_report_markdown_is_output_empty(self):
        self.assert_failure(self.response(outputs={"report_markdown": "  "}), "OUTPUT_EMPTY")

    def test_timeout_and_http_exceptions_use_distinct_canonical_codes(self):
        module = self.module()
        timeout_stage = module.provider_stage_from_exception(TimeoutError("secret timeout detail"))
        http_stage = module.provider_stage_from_exception(RuntimeError("secret HTTP body"))
        self.assertEqual("TIMEOUT", timeout_stage["code"])
        self.assertEqual("HTTP_ERROR", http_stage["code"])
        self.assertNotIn("secret", json.dumps(timeout_stage))
        self.assertNotIn("secret", json.dumps(http_stage))

    def test_legacy_provider_codes_are_read_only_mapped_to_canonical_codes(self):
        module = self.module()
        cases = {
            "DIFY_TIMEOUT": "TIMEOUT",
            "DIFY_WORKFLOW_FAILED": "WORKFLOW_FAILED",
            "DIFY_INVALID_RESPONSE": "OUTPUT_SCHEMA_INVALID",
            "GENERATION_JSON_PARSE_FAILED": "OUTPUT_SCHEMA_INVALID",
            "DIFY_OUTPUT_EMPTY": "OUTPUT_EMPTY",
            "DIFY_FRAGMENTARY_REPORT": "OUTPUT_TRUNCATED",
            "DIFY_HTTP_ERROR": "HTTP_ERROR",
            "something-new": "UNCLASSIFIED",
        }
        for old, expected in cases.items():
            with self.subTest(code=old):
                self.assertEqual(expected, module.canonical_provider_failure_code(old))

    def test_provider_attribution_is_order_stable(self):
        module = self.module()
        first = {"workflow_run_id": "wf-1", "data": {"status": "succeeded", "outputs": {"b": 2, "report_markdown": "正文", "a": 1}}}
        second = {"data": {"outputs": {"a": 1, "report_markdown": "正文", "b": 2}, "status": "succeeded"}, "workflow_run_id": "wf-1"}
        _, _, first_stage = module.extract_provider_output(first)
        _, _, second_stage = module.extract_provider_output(second)
        self.assertEqual(first_stage, second_stage)


if __name__ == "__main__":
    unittest.main()

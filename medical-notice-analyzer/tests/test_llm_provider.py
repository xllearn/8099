from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


class LLMProviderTests(unittest.TestCase):
    def test_prompt_ref_loads_versioned_prompt_and_sha256(self) -> None:
        from app.core.llm import load_prompt_ref

        prompt_ref = load_prompt_ref("local_report_generation", version="v1")
        prompt_path = Path(prompt_ref.path)
        expected_hash = hashlib.sha256(prompt_path.read_bytes()).hexdigest()

        self.assertEqual(prompt_ref.name, "local_report_generation")
        self.assertEqual(prompt_ref.version, "v1")
        self.assertEqual(prompt_ref.sha256, expected_hash)
        self.assertEqual(len(prompt_ref.sha256), 64)
        self.assertTrue(prompt_path.as_posix().endswith("prompts/local_report_generation/v1.md"))

    def test_mock_provider_returns_json_with_provider_model_and_prompt_metadata(self) -> None:
        from app.core.llm import LLMRequest, MockLLMProvider, load_prompt_ref, parse_llm_json

        prompt_ref = load_prompt_ref("local_report_generation", version="v1")
        provider = MockLLMProvider(
            response=json.dumps(
                {
                    "report_title": "Mock generated report",
                    "report_markdown": "# Mock generated report\n\nBody from synthetic provider.",
                }
            )
        )
        result = provider.generate(LLMRequest(prompt_ref=prompt_ref, variables={"title": "ignored"}))
        parsed = parse_llm_json(result)

        self.assertEqual(result.provider, "mock")
        self.assertEqual(result.model, "mock-local-report-v1")
        self.assertEqual(result.prompt_ref.sha256, prompt_ref.sha256)
        self.assertEqual(parsed["report_title"], "Mock generated report")
        self.assertIn("synthetic provider", parsed["report_markdown"])

    def test_parse_llm_json_raises_consistent_error_for_invalid_json(self) -> None:
        from app.core.llm import LLMProviderError, LLMRequest, MockLLMProvider, load_prompt_ref, parse_llm_json

        result = MockLLMProvider(response="{not valid json").generate(
            LLMRequest(prompt_ref=load_prompt_ref("local_report_generation", version="v1"), variables={})
        )

        with self.assertRaises(LLMProviderError) as ctx:
            parse_llm_json(result)

        self.assertEqual(ctx.exception.code, "LLM_JSON_PARSE_ERROR")
        self.assertIn("invalid JSON", ctx.exception.message)
        self.assertIn("{not valid json", ctx.exception.raw_output)


if __name__ == "__main__":
    unittest.main()

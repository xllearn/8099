import inspect
import unittest

import deepeval
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, GEval
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams


class DeepEvalAdvisoryDependencyContractTests(unittest.TestCase):
    def test_deepeval_version_is_exactly_4_1_3(self) -> None:
        self.assertEqual(deepeval.__version__, "4.1.3")

    def test_base_llm_exposes_schema_aware_generation_hooks(self) -> None:
        for method_name in (
            "generate_with_schema",
            "a_generate_with_schema",
        ):
            with self.subTest(method=method_name):
                helper = getattr(DeepEvalBaseLLM, method_name)
                self.assertTrue(callable(helper))
                parameters = inspect.signature(helper).parameters
                self.assertIn("schema", parameters)

    def test_advisory_metric_constructors_keep_required_parameters(self) -> None:
        expected_parameters = {
            AnswerRelevancyMetric: {
                "threshold",
                "model",
                "include_reason",
                "async_mode",
                "strict_mode",
                "verbose_mode",
            },
            FaithfulnessMetric: {
                "threshold",
                "model",
                "include_reason",
                "async_mode",
                "strict_mode",
                "verbose_mode",
            },
            GEval: {
                "name",
                "evaluation_params",
                "criteria",
                "evaluation_steps",
                "rubric",
                "threshold",
                "model",
                "async_mode",
                "strict_mode",
                "verbose_mode",
            },
        }

        for metric_class, required_parameters in expected_parameters.items():
            with self.subTest(metric=metric_class.__name__):
                actual_parameters = set(
                    inspect.signature(metric_class).parameters
                )
                self.assertTrue(
                    required_parameters <= actual_parameters,
                    required_parameters - actual_parameters,
                )

    def test_llm_test_case_supports_advisory_fields(self) -> None:
        test_case = LLMTestCase(
            input="Which findings are supported by the notice?",
            actual_output="Finding A is supported.",
            expected_output="Finding A is supported.",
            context=["The notice states finding A."],
            retrieval_context=["The notice states finding A."],
            metadata={"analysis_run_id": "run-contract-001"},
        )

        self.assertEqual(
            test_case.input,
            "Which findings are supported by the notice?",
        )
        self.assertEqual(test_case.actual_output, "Finding A is supported.")
        self.assertEqual(test_case.expected_output, "Finding A is supported.")
        self.assertEqual(test_case.context, ["The notice states finding A."])
        self.assertEqual(
            test_case.retrieval_context,
            ["The notice states finding A."],
        )
        self.assertEqual(
            test_case.metadata,
            {"analysis_run_id": "run-contract-001"},
        )
        self.assertEqual(SingleTurnParams.INPUT.value, "input")
        self.assertEqual(SingleTurnParams.ACTUAL_OUTPUT.value, "actual_output")
        self.assertEqual(
            SingleTurnParams.EXPECTED_OUTPUT.value,
            "expected_output",
        )
        self.assertEqual(SingleTurnParams.CONTEXT.value, "context")
        self.assertEqual(
            SingleTurnParams.RETRIEVAL_CONTEXT.value,
            "retrieval_context",
        )


if __name__ == "__main__":
    unittest.main()

import inspect
import os
import unittest

CONTRACT_REQUIRED_ENV = "DEEPEVAL_ADVISORY_CONTRACT_REQUIRED"

try:
    import deepeval
    from deepeval.metrics import (
        AnswerRelevancyMetric,
        FaithfulnessMetric,
        GEval,
    )
    from deepeval.models import DeepEvalBaseLLM
    from deepeval.test_case import LLMTestCase, SingleTurnParams
except ModuleNotFoundError as error:
    if error.name != "deepeval":
        raise
    if os.environ.get(CONTRACT_REQUIRED_ENV) == "1":
        raise RuntimeError(
            "DeepEval advisory Worker contract requires deepeval==4.1.3"
        ) from error
    raise unittest.SkipTest(
        "DeepEval is intentionally absent from the main service; "
        f"set {CONTRACT_REQUIRED_ENV}=1 for the advisory Worker contract"
    )


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
                schema_parameter = parameters["schema"]
                self.assertEqual(
                    schema_parameter.kind,
                    inspect.Parameter.KEYWORD_ONLY,
                )
                self.assertIsNone(schema_parameter.default)

    def test_advisory_metric_constructors_keep_required_parameters(self) -> None:
        expected_parameters_and_defaults = {
            AnswerRelevancyMetric: {
                "threshold": 0.5,
                "model": None,
                "include_reason": True,
                "async_mode": True,
                "strict_mode": False,
                "verbose_mode": False,
            },
            FaithfulnessMetric: {
                "threshold": 0.5,
                "model": None,
                "include_reason": True,
                "async_mode": True,
                "strict_mode": False,
                "verbose_mode": False,
            },
            GEval: {
                "name": inspect.Parameter.empty,
                "evaluation_params": None,
                "criteria": None,
                "evaluation_steps": None,
                "rubric": None,
                "threshold": 0.5,
                "model": None,
                "async_mode": True,
                "strict_mode": False,
                "verbose_mode": False,
            },
        }

        for (
            metric_class,
            expected_parameters,
        ) in expected_parameters_and_defaults.items():
            with self.subTest(metric=metric_class.__name__):
                actual_parameters = inspect.signature(
                    metric_class
                ).parameters
                for parameter_name, expected_default in (
                    expected_parameters.items()
                ):
                    with self.subTest(parameter=parameter_name):
                        self.assertIn(parameter_name, actual_parameters)
                        parameter = actual_parameters[parameter_name]
                        self.assertEqual(
                            parameter.kind,
                            inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        )
                        self.assertEqual(
                            parameter.default,
                            expected_default,
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

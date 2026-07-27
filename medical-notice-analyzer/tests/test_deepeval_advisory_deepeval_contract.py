import inspect
import os
from pathlib import Path
import subprocess
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATTERN = "test_deepeval_advisory_deepeval_contract.py"
CONTRACT_REQUIRED_ENV = "DEEPEVAL_ADVISORY_CONTRACT_REQUIRED"
DOCKERFILE_PATH = PROJECT_ROOT / "Dockerfile.deepeval"
DOCKERIGNORE_PATH = PROJECT_ROOT / ".dockerignore"
MAIN_REQUIREMENTS_PATH = PROJECT_ROOT / "requirements.txt"
WORKER_REQUIREMENTS_INPUT_PATH = (
    PROJECT_ROOT / "requirements-deepeval.in"
)
MAIN_SERVICE_SKIP_MESSAGE = (
    "DeepEval is intentionally absent from the main service"
)
WORKER_REQUIRED_MESSAGE = (
    "DeepEval advisory Worker contract requires deepeval==4.1.3"
)
PYTHON_BASE_DIGEST = (
    "sha256:"
    "a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0"
)

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
    def _run_contract_without_site_packages(
        self,
        *,
        worker_contract_required: bool,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.pop(CONTRACT_REQUIRED_ENV, None)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if worker_contract_required:
            environment[CONTRACT_REQUIRED_ENV] = "1"

        return subprocess.run(
            [
                sys.executable,
                "-S",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                CONTRACT_PATTERN,
                "-v",
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_main_service_discovery_skips_worker_contract(self) -> None:
        result = self._run_contract_without_site_packages(
            worker_contract_required=False,
        )
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("OK (skipped=1)", output)
        self.assertIn(MAIN_SERVICE_SKIP_MESSAGE, output)

    def test_worker_required_mode_fails_when_deepeval_is_absent(self) -> None:
        result = self._run_contract_without_site_packages(
            worker_contract_required=True,
        )
        output = result.stdout + result.stderr

        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(WORKER_REQUIRED_MESSAGE, output)
        self.assertNotIn("OK (skipped=1)", output)

    @unittest.skipUnless(
        DOCKERFILE_PATH.is_file(),
        "source-only Worker Dockerfile is not copied into the final image",
    )
    def test_worker_base_image_is_digest_pinned_with_provenance(self) -> None:
        dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

        self.assertIn(
            f"FROM python:3.11-slim@{PYTHON_BASE_DIGEST}",
            dockerfile,
        )
        self.assertIn(
            "docker.io/library/python:3.11-slim resolved 2026-07-27",
            dockerfile,
        )
        self.assertIn(
            f"{CONTRACT_REQUIRED_ENV}=1",
            dockerfile,
        )

    @unittest.skipUnless(
        MAIN_REQUIREMENTS_PATH.is_file()
        and WORKER_REQUIREMENTS_INPUT_PATH.is_file(),
        "source-only requirement inputs are not copied into the final image",
    )
    def test_main_requirements_do_not_contain_deepeval(self) -> None:
        main = MAIN_REQUIREMENTS_PATH.read_text(
            encoding="utf-8"
        ).lower()
        worker_lines = WORKER_REQUIREMENTS_INPUT_PATH.read_text(
            encoding="utf-8"
        ).lower().splitlines()

        self.assertNotIn("deepeval", main)
        self.assertEqual(
            tuple(
                line.strip()
                for line in worker_lines
                if line.strip().startswith("deepeval")
            ),
            ("deepeval==4.1.3",),
        )

    @unittest.skipUnless(
        DOCKERFILE_PATH.is_file(),
        "source-only Worker Dockerfile is not copied into the final image",
    )
    def test_worker_dockerfile_is_final_isolated_test_runner(self) -> None:
        dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "COPY requirements-deepeval.lock "
            "/opt/advisory/requirements-deepeval.lock",
            dockerfile,
        )
        self.assertIn("--require-hashes", dockerfile)
        self.assertNotIn("COPY requirements.txt", dockerfile)
        self.assertIn("HOME=/state", dockerfile)
        self.assertIn(
            "--home-dir /state --shell /usr/sbin/nologin advisory",
            dockerfile,
        )
        self.assertIn(
            "install -d -m 0700 -o 10001 -g 10001 /state",
            dockerfile,
        )
        self.assertIn("WORKDIR /app", dockerfile)
        for required_copy in (
            "COPY app/__init__.py ./app/__init__.py",
            "COPY app/evidence_schema.py ./app/evidence_schema.py",
            "COPY app/evidence_index.py ./app/evidence_index.py",
            "COPY app/formal_body.py ./app/formal_body.py",
            "COPY app/diagnostics.py ./app/diagnostics.py",
            "COPY app/schema_migrations.py ./app/schema_migrations.py",
            "COPY app/report_rules ./app/report_rules",
            "COPY app/deepeval_advisory ./app/deepeval_advisory",
            "COPY tests ./tests",
        ):
            with self.subTest(copy=required_copy):
                self.assertIn(required_copy, dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn('ENTRYPOINT ["python"]', dockerfile)
        self.assertIn(
            'CMD ["-m", "unittest", '
            '"tests.test_deepeval_advisory_deepeval_contract", '
            '"tests.test_deepeval_advisory_judge", '
            '"tests.test_deepeval_advisory_evaluator", "-v"]',
            dockerfile,
        )
        for forbidden in ("8099", "uvicorn", "app.main"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, dockerfile)

    @unittest.skipUnless(
        DOCKERIGNORE_PATH.is_file(),
        "source-only .dockerignore is not copied into the final image",
    )
    def test_dockerignore_excludes_all_env_files_except_safe_example(
        self,
    ) -> None:
        ignore_lines = DOCKERIGNORE_PATH.read_text(
            encoding="utf-8"
        ).splitlines()

        self.assertIn(".env*", ignore_lines)
        self.assertIn("!.env.example", ignore_lines)
        self.assertLess(
            ignore_lines.index(".env*"),
            ignore_lines.index("!.env.example"),
        )

    @unittest.skipUnless(
        DOCKERIGNORE_PATH.is_file(),
        "source-only .dockerignore is not copied into the final image",
    )
    def test_dockerignore_excludes_foundation_sensitive_paths(self) -> None:
        ignore_lines = set(
            DOCKERIGNORE_PATH.read_text(
                encoding="utf-8"
            ).splitlines()
        )

        self.assertTrue(
            {
                "data",
                "data/**",
                "reports",
                "reports/**",
                "site-cache",
                "deepeval-advisory-data",
                "artifacts",
                "artifacts/**",
                "secrets",
                "secrets/**",
                "*.docx",
            }
            <= ignore_lines
        )

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

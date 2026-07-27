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


class DeepEvalAdvisoryFoundationIsolationTests(unittest.TestCase):
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
        "source-only Worker Dockerfile is not copied into the main image",
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
        DOCKERIGNORE_PATH.is_file(),
        "source-only .dockerignore is not copied into the main image",
    )
    def test_dockerignore_excludes_all_env_files_except_safe_example(
        self,
    ) -> None:
        ignore_lines = DOCKERIGNORE_PATH.read_text(encoding="utf-8").splitlines()

        self.assertIn(".env*", ignore_lines)
        self.assertIn("!.env.example", ignore_lines)
        self.assertLess(
            ignore_lines.index(".env*"),
            ignore_lines.index("!.env.example"),
        )


if __name__ == "__main__":
    unittest.main()

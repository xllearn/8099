from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import sys
import unittest

import deepeval


APP_ROOT = Path("/app")
STATE_ROOT = Path("/state")
EXPECTED_TEST_FILES = {
    "__init__.py",
    "test_deepeval_advisory_evaluator.py",
    "test_deepeval_advisory_image_contract.py",
    "test_deepeval_advisory_judge.py",
}
SIDE_EFFECT_ENVIRONMENT = {
    "DEEPEVAL_TELEMETRY_OPT_OUT": "1",
    "DEEPEVAL_DISABLE_DOTENV": "1",
    "DEEPEVAL_NO_INSPECT_PROMPT": "1",
    "ENABLE_DEEPEVAL_CACHE": "0",
    "DEEPEVAL_ADVISORY_CONTRACT_REQUIRED": "1",
}


class DeepEvalAdvisoryImageContractTests(unittest.TestCase):
    def test_runtime_versions_are_exact(self) -> None:
        self.assertEqual(sys.version_info[:2], (3, 11))
        self.assertEqual(deepeval.__version__, "4.1.3")

    def test_runtime_identity_and_state_are_nonroot_and_writable(self) -> None:
        self.assertEqual(os.getuid(), 10001)
        self.assertEqual(os.getgid(), 10001)
        self.assertEqual(os.environ.get("HOME"), "/state")

        state_stat = STATE_ROOT.stat()
        self.assertEqual(stat.S_IMODE(state_stat.st_mode), 0o700)
        self.assertEqual(state_stat.st_uid, 10001)
        self.assertEqual(state_stat.st_gid, 10001)

        probe = STATE_ROOT / f".image-contract-{os.getpid()}"
        try:
            probe.write_text("ok", encoding="utf-8")
            self.assertEqual(probe.read_text(encoding="utf-8"), "ok")
        finally:
            probe.unlink(missing_ok=True)

    def test_deepeval_side_effect_environment_is_fixed(self) -> None:
        self.assertEqual(
            {
                name: os.environ.get(name)
                for name in SIDE_EFFECT_ENVIRONMENT
            },
            SIDE_EFFECT_ENVIRONMENT,
        )

    def test_main_service_and_source_only_files_are_absent(self) -> None:
        self.assertIsNone(importlib.util.find_spec("app.main"))
        for relative_path in (
            ".dockerignore",
            "Dockerfile",
            "Dockerfile.deepeval",
            "docker-compose.yml",
            "requirements.txt",
            "requirements-deepeval.in",
            "requirements-deepeval.lock",
            "tests/test_deepeval_advisory_deepeval_contract.py",
            "tests/fixtures/8099_regression_cases.json",
        ):
            with self.subTest(path=relative_path):
                self.assertFalse((APP_ROOT / relative_path).exists())

    def test_only_allowlisted_test_modules_are_baked(self) -> None:
        tests_root = APP_ROOT / "tests"
        actual_files = {
            path.relative_to(tests_root).as_posix()
            for path in tests_root.rglob("*")
            if path.is_file()
        }

        self.assertEqual(actual_files, EXPECTED_TEST_FILES)

    def test_no_python_cache_files_are_baked(self) -> None:
        cache_files = tuple(
            sorted(
                path.relative_to(APP_ROOT).as_posix()
                for path in APP_ROOT.rglob("*")
                if path.is_file()
                and (
                    "__pycache__" in path.parts
                    or path.suffix == ".pyc"
                )
            )
        )

        self.assertEqual(cache_files, ())


if __name__ == "__main__":
    unittest.main()

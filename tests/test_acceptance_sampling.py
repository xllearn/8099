from __future__ import annotations

import tempfile
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_chatflow_acceptance.py"
HENAN_URL = "https://ypnew.hnsggzyjy.henan.gov.cn/cms/detail.html?infoId=1330&CatalogId=2"


def load_acceptance_module():
    spec = spec_from_file_location("run_chatflow_acceptance", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load acceptance script")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AcceptanceSamplingTests(unittest.TestCase):
    def test_extract_urls_from_mixed_title_url_text(self) -> None:
        module = load_acceptance_module()
        text = f"""
关于调整医用耗材申报挂网操作流程的通知
{HENAN_URL}

深圳公共资源交易中心关于开展广东省麻醉管路等三类医用耗材带量联动采购的公告
https://www.szggzy.com/jygg/details.html?contentId=20426283
"""

        urls = module.extract_urls(text)

        self.assertEqual(urls[0], HENAN_URL)
        self.assertEqual(urls[1], "https://www.szggzy.com/jygg/details.html?contentId=20426283")

    def test_select_acceptance_urls_always_includes_henan_and_two_more(self) -> None:
        module = load_acceptance_module()
        urls = [
            HENAN_URL,
            "https://example.test/one",
            "https://example.test/two",
            "https://example.test/three",
        ]

        selected = module.select_acceptance_urls(urls, count=3, seed=7)

        self.assertEqual(selected[0], HENAN_URL)
        self.assertEqual(len(selected), 3)
        self.assertEqual(len(set(selected)), 3)

    def test_create_output_directory_uses_desktop_prefix_without_overwriting(self) -> None:
        module = load_acceptance_module()
        with tempfile.TemporaryDirectory() as tmp:
            first = module.create_output_dir(Path(tmp), timestamp="20260608-101010")
            second = module.create_output_dir(Path(tmp), timestamp="20260608-101010")

        self.assertEqual(first.name, "新版系统测试报告_20260608-101010")
        self.assertEqual(second.name, "新版系统测试报告_20260608-101010_2")

    def test_safe_download_filename_decodes_and_truncates_chinese_url_name(self) -> None:
        module = load_acceptance_module()
        long_name = "%E3%80%90%E6%B2%B3%E5%8D%97%E3%80%91" + "a" * 180 + ".docx"

        filename = module.safe_download_filename(f"http://localhost:8099/download/{long_name}")

        self.assertTrue(filename.startswith("【河南】"))
        self.assertTrue(filename.endswith(".docx"))
        self.assertLessEqual(len(filename), 120)

    def test_reference_tokens_prefer_url_region_before_title_city(self) -> None:
        module = load_acceptance_module()

        tokens = module.reference_tokens(
            "深圳公共资源交易中心关于开展广东省麻醉管路等三类医用耗材带量联动采购的公告",
            "https://www.szggzy.com/jygg/details.html?contentId=20426283",
        )

        self.assertEqual(tokens[0], "广东")
        self.assertIn("深圳", tokens)


if __name__ == "__main__":
    unittest.main()

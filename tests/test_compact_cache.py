from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import app.main as main_module
from app.compact_cache import CompactCache, compact_cache_key


class CompactCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pack = {
            "pack_id": "pack-cache-1",
            "created_at": "2026-07-16T10:00:00",
            "pack_version": "2.0",
            "source": "database_selection",
            "primary_materials": [
                {
                    "menu_code": "project_notice",
                    "articleid": "article-1",
                    "title": "Project notice",
                    "content_text": "Project rules and execution details. " * 80,
                    "content_summary": "Project rules summary.",
                    "key_facts": [{"name": "region", "value": "Shandong"}],
                    "attachments": [
                        {
                            "articleattid": "attachment-1",
                            "filename": "rules.pdf",
                            "content_sha256": "a" * 64,
                            "parse_status": "parsed_summary",
                            "download_status": "stream_parsed",
                            "summary": "Attachment rules and dates.",
                            "key_facts": ["deadline: 2026-07-31"],
                            "table_summaries": [],
                        }
                    ],
                }
            ],
            "auxiliary_materials": [],
            "combined_key_facts": [{"name": "region", "value": "Shandong"}],
            "report_focus": ["rules", "execution"],
            "warnings": [],
            "generation_guidance": {},
        }
        self.versions = {
            "compact_rule_version": "s3c-v1",
            "evidence_schema_version": 1,
            "pdf_table_rule_version": "pdf-v1",
        }

    def test_key_changes_for_body_attachment_schema_rule_and_limit(self) -> None:
        baseline = compact_cache_key(self.pack, max_chars=80000, version_context=self.versions)
        variants = []

        body_changed = copy.deepcopy(self.pack)
        body_changed["primary_materials"][0]["content_text"] += "changed"
        variants.append((body_changed, self.versions, 80000))

        attachment_changed = copy.deepcopy(self.pack)
        attachment_changed["primary_materials"][0]["attachments"][0]["content_sha256"] = "b" * 64
        variants.append((attachment_changed, self.versions, 80000))

        schema_changed = copy.deepcopy(self.pack)
        schema_changed["evidence_schema_version"] = 2
        variants.append((schema_changed, self.versions, 80000))

        rule_changed = dict(self.versions, compact_rule_version="s3c-v2")
        variants.append((self.pack, rule_changed, 80000))
        variants.append((self.pack, self.versions, 79000))

        keys = {
            compact_cache_key(pack, max_chars=limit, version_context=versions)
            for pack, versions, limit in variants
        }
        self.assertNotIn(baseline, keys)
        self.assertEqual(len(keys), len(variants))

    def test_miss_then_hit_returns_byte_identical_value_and_computes_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = CompactCache(Path(directory), ttl_seconds=60, clock=lambda: 1000.0)
            calls = 0

            def compute():
                nonlocal calls
                calls += 1
                return {"compact_pack_chars": 42, "payload": ["stable", 1]}

            cold, cold_status, key = cache.get_or_compute(
                self.pack,
                max_chars=80000,
                version_context=self.versions,
                compute=compute,
            )
            hit, hit_status, hit_key = cache.get_or_compute(
                self.pack,
                max_chars=80000,
                version_context=self.versions,
                compute=compute,
            )

            self.assertEqual(cold_status, "miss")
            self.assertEqual(hit_status, "hit")
            self.assertEqual(key, hit_key)
            self.assertEqual(calls, 1)
            self.assertEqual(
                json.dumps(cold, ensure_ascii=False, sort_keys=True),
                json.dumps(hit, ensure_ascii=False, sort_keys=True),
            )

    def test_expired_and_corrupt_entries_are_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            now = [1000.0]
            cache = CompactCache(Path(directory), ttl_seconds=10, clock=lambda: now[0])
            calls = 0

            def compute():
                nonlocal calls
                calls += 1
                return {"call": calls}

            _, _, key = cache.get_or_compute(
                self.pack,
                max_chars=80000,
                version_context=self.versions,
                compute=compute,
            )
            now[0] = 1011.0
            expired, expired_status, _ = cache.get_or_compute(
                self.pack,
                max_chars=80000,
                version_context=self.versions,
                compute=compute,
            )
            self.assertEqual(expired_status, "expired_rebuilt")
            self.assertEqual(expired, {"call": 2})

            cache.entry_path(key).write_text("{not-json", encoding="utf-8")
            rebuilt, corrupt_status, _ = cache.get_or_compute(
                self.pack,
                max_chars=80000,
                version_context=self.versions,
                compute=compute,
            )
            self.assertEqual(corrupt_status, "corrupt_rebuilt")
            self.assertEqual(rebuilt, {"call": 3})

    def test_atomic_write_failure_returns_computed_value_without_temp_residue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = CompactCache(Path(directory), ttl_seconds=60)
            with patch("app.compact_cache.os.replace", side_effect=OSError("fixture write failure")):
                value, status, key = cache.get_or_compute(
                    self.pack,
                    max_chars=80000,
                    version_context=self.versions,
                    compute=lambda: {"safe": True},
                )

            self.assertEqual(value, {"safe": True})
            self.assertEqual(status, "write_failed")
            self.assertFalse(cache.entry_path(key).exists())
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_concurrent_callers_share_one_atomic_cache_fill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = CompactCache(Path(directory), ttl_seconds=60)
            calls = 0
            lock = threading.Lock()

            def compute():
                nonlocal calls
                with lock:
                    calls += 1
                time.sleep(0.03)
                return {"compact_pack_chars": 100, "value": "same"}

            def call_cache(_index: int):
                return cache.get_or_compute(
                    self.pack,
                    max_chars=80000,
                    version_context=self.versions,
                    compute=compute,
                )

            with ThreadPoolExecutor(max_workers=16) as executor:
                results = list(executor.map(call_cache, range(32)))

            self.assertEqual(calls, 1)
            self.assertEqual(sum(status == "miss" for _, status, _ in results), 1)
            self.assertEqual(sum(status == "hit" for _, status, _ in results), 31)
            self.assertEqual({json.dumps(value, sort_keys=True) for value, _, _ in results}, {'{"compact_pack_chars": 100, "value": "same"}'})
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 1)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_main_cache_preserves_under_limit_compact_output_exactly(self) -> None:
        pack = copy.deepcopy(self.pack)
        pack["database_password"] = "SECRET_DATABASE_PASSWORD"
        pack["report_markdown"] = "SECRET_REPORT_BODY"
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "ENABLE_COMPACT_CACHE": "false",
                "COMPACT_CACHE_DIR": directory,
                "COMPACT_RULE_VERSION": "s3c-v1",
            }
            with patch.dict(os.environ, env, clear=False):
                uncached = main_module._compact_evidence_pack_for_dify(pack)
            with patch.dict(os.environ, dict(env, ENABLE_COMPACT_CACHE="true"), clear=False):
                cold = main_module._compact_evidence_pack_for_dify(pack)
                hit = main_module._compact_evidence_pack_for_dify(pack)

            serialized = json.dumps(uncached, ensure_ascii=False, sort_keys=True)
            self.assertEqual(json.dumps(cold, ensure_ascii=False, sort_keys=True), serialized)
            self.assertEqual(json.dumps(hit, ensure_ascii=False, sort_keys=True), serialized)
            self.assertLessEqual(uncached["compact_pack_chars"], 80000)
            cache_text = "\n".join(path.read_text(encoding="utf-8") for path in Path(directory).glob("*.json"))
            self.assertNotIn("SECRET_DATABASE_PASSWORD", cache_text)
            self.assertNotIn("SECRET_REPORT_BODY", cache_text)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import io
import os
import re
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import jsonschema

from app.analyze import AnalysisConfigurationError, OpenAIAnalyzer
from app.collect import parse_usgs_candidates, validate_public_http_url
from app.common import load_json, load_yaml
from app.archive import archive_target
from app.pipeline import make_event
from app.run_state import AnalysisCache, BudgetLedger, evidence_cache_key
from app.structured_analysis import analyze_usgs
from app.verify import validate_claim_evidence, validate_event_times


ROOT = Path(__file__).resolve().parents[1]


def usgs_cluster() -> dict:
    payload = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "id": "us-test",
            "properties": {
                "mag": 5.3,
                "magType": "mww",
                "place": "83 km E of Lospalos, Timor Leste",
                "time": 1789065880243,
                "updated": 1789067060040,
                "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us-test",
                "felt": None,
                "cdi": None,
                "mmi": None,
                "alert": None,
                "status": "reviewed",
                "tsunami": 0,
                "sig": 432,
                "type": "earthquake",
            },
            "geometry": {"type": "Point", "coordinates": [127.7505, -8.5914, 10]},
        }],
    }
    item = parse_usgs_candidates(json.dumps(payload).encode(), 5)[0]
    item.update({
        "source_id": "usgs", "source": "USGS 地震数据", "source_role": "primary_data",
        "rights_review": "approved", "allow_public_summary": True,
        "evidence_text": item["feed_excerpt"], "access_level": "authorized_feed",
        "upstream_origin": "usgs", "independence_group": "usgs",
        "regions": [], "topics": [],
    })
    return {"items": [item], "lead_title": item["title"], "independence_groups": ["usgs"]}


class NextBuildTests(unittest.TestCase):
    def test_structured_public_data_produces_real_chinese_event(self):
        cluster = usgs_cluster()
        analysis, sources = analyze_usgs(cluster, "2026-09-10T23:20:00Z")
        gate = {"independent_source_count": 1, "substantive_source_count": 1}
        event = make_event(cluster, analysis, sources, "2026-09-10T23:20:00Z", "deterministic_public_data", gate)
        schema = load_json(ROOT / "schemas" / "event.schema.json")
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(event)
        self.assertRegex(event["title_zh"], r"[\u4e00-\u9fff]")
        self.assertIn("东帝汶", event["title_zh"])
        self.assertEqual(event["region_ids"], ["southeast_asia"])
        self.assertEqual(event["topic_ids"], ["disasters", "science"])
        self.assertEqual(event["analysis_method"], "deterministic_public_data")
        self.assertGreaterEqual(len(event["summary_zh"]), 80)
        self.assertNotIn("伤亡", event["material_update"])
        self.assertTrue(event["sources"][0]["evidence_excerpt"])

    def test_future_noncalendar_event_is_blocked(self):
        cluster = usgs_cluster()
        analysis, sources = analyze_usgs(cluster, "2026-09-10T20:00:00Z")
        gate = {"independent_source_count": 1, "substantive_source_count": 1}
        event = make_event(cluster, analysis, sources, "2026-09-10T20:00:00Z", "deterministic_public_data", gate)
        event["event_time"]["value"] = "2026-09-10T20:06:00Z"
        self.assertTrue(validate_event_times(event))

    def test_claim_requires_evidence_excerpt_for_real_news(self):
        cluster = usgs_cluster()
        analysis, sources = analyze_usgs(cluster, "2026-09-10T23:20:00Z")
        event = make_event(cluster, analysis, sources, "2026-09-10T23:20:00Z", "deterministic_public_data", {"independent_source_count": 1, "substantive_source_count": 1})
        event["sources"][0]["evidence_excerpt"] = None
        self.assertTrue(validate_claim_evidence(event))

    def test_private_and_loopback_fetch_targets_are_rejected(self):
        for url in ("http://127.0.0.1/feed", "http://10.0.0.1/feed", "http://[::1]/feed"):
            with self.assertRaises(ValueError):
                validate_public_http_url(url)

    def test_analysis_cache_key_ignores_unlicensed_metadata_but_changes_with_evidence(self):
        cluster = usgs_cluster()
        original = evidence_cache_key(cluster, "test-model")
        cluster["items"].append({"source_id": "pending", "rights_review": "pending", "evidence_text": "headline only"})
        self.assertEqual(original, evidence_cache_key(cluster, "test-model"))
        cluster["items"][0]["evidence_text"] += " revised"
        self.assertNotEqual(original, evidence_cache_key(cluster, "test-model"))

    def test_analysis_cache_and_budget_ledger_survive_new_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_path = root / "cache.json"
            cache = AnalysisCache(cache_path)
            cache.put("key", "model", "2026-09-10T23:30:00Z", {"title_zh": "真实标题"}, [{"id": "src"}])
            self.assertEqual(AnalysisCache(cache_path).get("key")["analysis"]["title_zh"], "真实标题")

            ledger_path = root / "ledger.json"
            now = datetime(2026, 9, 10, 23, 30, tzinfo=timezone.utc)
            limits = {"events": 1, "input_tokens": 1000, "output_tokens": 1000, "usd": 1.0}
            ledger = BudgetLedger(ledger_path, now, "Asia/Shanghai", "model")
            ledger.reserve("event", 100, 100, 1.0, 1.0, limits)
            restored = BudgetLedger(ledger_path, now, "Asia/Shanghai", "model")
            self.assertEqual(restored.day, "2026-09-11")
            self.assertEqual(restored.report()["pending_reservations"], 1)
            with self.assertRaises(RuntimeError):
                restored.reserve("another", 100, 100, 1.0, 1.0, limits)
            restored.commit("event", 80, 40, 1.0, 1.0)
            self.assertEqual(restored.report()["completed_events"], 1)

    def test_archive_directory_uses_beijing_edition_date(self):
        instant = datetime(2026, 9, 10, 23, 37, tzinfo=timezone.utc)
        target = archive_target(Path("history"), instant, "Asia/Shanghai")
        self.assertEqual(target.as_posix(), "history/2026/09/11/2026-09-10T233700Z.json")

    def test_site_uses_relative_project_paths_and_no_duplicate_timezone_label(self):
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('href="/', page)
        self.assertNotIn('src="/', page)
        self.assertNotIn("北京时间 北京时间", page)
        self.assertIsNone(re.search(r"<script>.*(?:cookie|api[_-]?key|password).*?</script>", page, re.I | re.S))

    def test_mobile_filters_and_keyboard_focus_are_present(self):
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('class="filter-toggle"', page)
        self.assertIn('aria-controls="filter-body"', page)
        self.assertIn("lastEvidenceTrigger?.focus()", script)
        self.assertIn("button:focus-visible", styles)
        self.assertRegex(styles, r"@media \(max-width: 520px\)[\s\S]*\.chip-row \{ flex-wrap: wrap; overflow-x: visible; \}")

    def test_enabled_ai_requires_a_secret_before_any_request(self):
        config = load_yaml(ROOT / "config" / "pipeline.yaml")["ai"]
        with patch.dict(os.environ, {"ENABLE_AI_ANALYSIS": "true"}, clear=True):
            with self.assertRaisesRegex(AnalysisConfigurationError, "missing required secret"):
                OpenAIAnalyzer(config)

    def test_openai_429_retry_is_limited_and_returns_no_result(self):
        analyzer = object.__new__(OpenAIAnalyzer)
        analyzer.api_key = "test-only"
        analyzer.config = {"endpoint": "https://api.openai.com/v1/responses", "retries": 1, "request_timeout_seconds": 1}
        errors = [
            urllib.error.HTTPError(
                analyzer.config["endpoint"], 429, "rate limited", {"Retry-After": "0"}, io.BytesIO(b"rate limited")
            )
            for _ in range(2)
        ]
        with patch("urllib.request.urlopen", side_effect=errors) as request, patch("time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "OpenAI API HTTP 429"):
                analyzer._request({"model": "test"})
        self.assertEqual(request.call_count, 2)

    def test_openai_timeout_retry_is_limited_and_returns_no_result(self):
        analyzer = object.__new__(OpenAIAnalyzer)
        analyzer.api_key = "test-only"
        analyzer.config = {"endpoint": "https://api.openai.com/v1/responses", "retries": 1, "request_timeout_seconds": 1}
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")) as request, patch("time.sleep"):
            with self.assertRaises(TimeoutError):
                analyzer._request({"model": "test"})
        self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()

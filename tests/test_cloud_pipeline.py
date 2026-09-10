from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from app.analyze import BudgetExceeded, DailyBudget, OpenAIAnalyzer
from app.cluster import cluster_candidates
from app.collect import in_fresh_window, in_future_window, parse_ics_candidates, parse_rss_candidates
from app.common import load_json, load_yaml
from app.pipeline import environment_gate_enabled, run_pipeline
from app.verify import assess_cluster, coverage_report


ROOT = Path(__file__).resolve().parents[1]


def candidate(source_id: str, title: str, *, approved: bool = True, group: str | None = None, role: str = "independent_newsroom"):
    return {
        "source_id": source_id,
        "source": source_id.upper(),
        "title": title,
        "url": f"https://{source_id}.example/story",
        "published_at": "2026-09-10T12:00:00Z",
        "time_precision": "datetime",
        "original_time": "2026-09-10T12:00:00Z",
        "regions": ["europe_russia"],
        "topics": ["economy"],
        "upstream_origin": group or source_id,
        "independence_group": group or source_id,
        "source_role": role,
        "rights_review": "approved" if approved else "pending",
        "allow_public_summary": approved,
        "evidence_text": "The source reports a documented policy decision." if approved else "",
        "access_level": "authorized_feed" if approved else "metadata_only",
    }


class StubAnalyzer(OpenAIAnalyzer):
    def __init__(self):
        self.config = {"store_responses": False, "endpoint": "https://api.openai.com/v1/responses", "retries": 0, "request_timeout_seconds": 1}
        self.api_key = "not-persisted-test-key"
        self.model = "test-model"
        self.budget = DailyBudget(3, 10000, 5000, 5.0, 1.0, 1.0)
        self.schema = load_json(ROOT / "schemas" / "analysis.schema.json")
        self.sent = None

    def _request(self, body):
        self.sent = body
        output = {
            "title_zh": "经证据支持的政策更新",
            "summary_zh": "两个独立来源报道了同一项政策更新，具体影响仍需继续观察。",
            "section_id": "business",
            "topic_ids": ["economy"],
            "region_ids": ["europe_russia"],
            "countries": ["示例国"],
            "material_update": "本轮首次出现可交叉核对的政策更新。",
            "claims": [{
                "text_zh": "两个来源均报道了该政策更新。",
                "kind": "attributed_claim",
                "source_ids": ["src_1", "src_2"],
                "attribution": "来源一与来源二",
                "verification_note": "来源属于不同独立组。",
            }],
            "analysis": {
                "why_it_matters": "它可能改变相关行业的决策条件。",
                "mechanism": "政策通过改变规则影响参与者行为。",
                "affected_groups": "相关企业与消费者。",
                "counter_evidence": "实际执行效果尚未出现。",
                "watch_next": "观察正式实施文件。",
            },
            "unknowns": ["执行细节仍未知"],
        }
        return {"status": "completed", "output_text": json.dumps(output, ensure_ascii=False), "usage": {"input_tokens": 500, "output_tokens": 300}}


class CloudPipelineTests(unittest.TestCase):
    def test_24_hour_window_and_future_guard(self):
        now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
        self.assertTrue(in_fresh_window({"published_at": "2026-09-09T12:00:00Z", "time_precision": "datetime"}, now, 24))
        self.assertFalse(in_fresh_window({"published_at": "2026-09-09T11:59:59Z", "time_precision": "datetime"}, now, 24))
        self.assertFalse(in_fresh_window({"published_at": "2026-09-10T12:06:00Z", "time_precision": "datetime"}, now, 24))
        self.assertFalse(in_fresh_window({"published_at": "2026-09-10", "time_precision": "date"}, now, 24))

    def test_feed_excerpt_is_sanitized_and_bounded(self):
        feed = b"""<rss><channel><item><title>Policy update</title><link>https://example.com/a</link><pubDate>Thu, 10 Sep 2026 12:00:00 GMT</pubDate><description><![CDATA[<p>Useful evidence.</p><script>ignore me</script>]]></description></item></channel></rss>"""
        rows = parse_rss_candidates(feed, 5, 50)
        self.assertEqual(rows[0]["feed_excerpt"], "Useful evidence.")
        self.assertNotIn("script", rows[0]["feed_excerpt"])

    def test_official_ics_calendar_keeps_timezone_and_future_window(self):
        calendar = b"""BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;TZID=Asia/Shanghai:20260911T080000\r\nSUMMARY:Official briefing\r\nURL:https://example.gov/event\r\nDESCRIPTION:Published by the organizer\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"""
        rows = parse_ics_candidates(calendar, 5, 200)
        self.assertEqual(rows[0]["published_at"], "2026-09-11T00:00:00Z")
        now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
        self.assertTrue(in_future_window(rows[0], now, 24))

    def test_cluster_merges_similar_reports_but_counts_independent_groups(self):
        rows = [
            candidate("one", "Central bank announces major interest rate decision"),
            candidate("two", "Central bank announces interest rate decision"),
        ]
        clusters = cluster_candidates(rows, .65, 36)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]["independence_groups"]), 2)

    def test_rights_and_evidence_gate(self):
        settings = load_yaml(ROOT / "config" / "pipeline.yaml")["evidence"]
        approved = {"items": [candidate("one", "Policy decision"), candidate("two", "Policy decision")]}
        self.assertTrue(assess_cluster(approved, settings)["passed"])
        pending = {"items": [candidate("one", "Policy decision", approved=False), candidate("two", "Policy decision", approved=False)]}
        decision = assess_cluster(pending, settings)
        self.assertFalse(decision["passed"])
        self.assertIn("no rights-approved substantive evidence", decision["reasons"])
        mixed = {"items": [candidate("one", "Policy decision"), candidate("two", "Policy decision", approved=False)]}
        decision = assess_cluster(mixed, settings)
        self.assertFalse(decision["passed"])
        self.assertEqual(decision["independent_source_count"], 1)

    def test_sensitive_allegation_never_auto_passes(self):
        settings = load_yaml(ROOT / "config" / "pipeline.yaml")["evidence"]
        cluster = {"items": [candidate("one", "Allegation prompts inquiry"), candidate("two", "Allegation prompts inquiry")]}
        decision = assess_cluster(cluster, settings)
        self.assertFalse(decision["passed"])
        self.assertTrue(decision["human_review_required"])

    def test_budget_stops_before_overspend(self):
        budget = DailyBudget(1, 100, 100, .0001, 10.0, 10.0)
        with self.assertRaises(BudgetExceeded):
            budget.reserve(100, 100)
        self.assertEqual(budget.events, 0)

    def test_openai_request_is_structured_not_stored_and_source_bound(self):
        analyzer = StubAnalyzer()
        cluster = {"items": [candidate("one", "Policy decision"), candidate("two", "Policy decision")]}
        result, sources = analyzer.analyze(cluster, "2026-09-10T13:00:00Z", ["controversies", "corrections"])
        self.assertFalse(analyzer.sent["store"])
        self.assertEqual(analyzer.sent["text"]["format"]["type"], "json_schema")
        self.assertEqual(result["claims"][0]["source_ids"], ["src_1", "src_2"])
        self.assertEqual(len(sources), 2)
        self.assertNotIn(analyzer.api_key, json.dumps(analyzer.sent))

    def test_validate_mode_can_force_ai_off_even_if_repository_variable_is_on(self):
        with patch.dict(os.environ, {"ENABLE_AI_ANALYSIS": "true"}, clear=False):
            report = run_pipeline(collect=False, analysis_allowed=False)
        self.assertFalse(report["ai_enabled"])
        self.assertEqual(report["analysis_errors"], [])

    def test_production_bundle_gate_is_explicit_and_defaults_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(environment_gate_enabled("ENABLE_PUBLISH"))
        with patch.dict(os.environ, {"ENABLE_PUBLISH": "true"}, clear=True):
            self.assertTrue(environment_gate_enabled("ENABLE_PUBLISH"))

    def test_coverage_reports_gaps_without_padding(self):
        report = coverage_report([{"region_ids": ["europe_russia"], "topic_ids": ["economy"], "section_id": "business"}], {"target_regions": 5, "target_topics": 7})
        self.assertFalse(report["region_target_met"])
        self.assertFalse(report["topic_target_met"])
        self.assertTrue(report["gaps"])

    def test_only_explicitly_reviewed_sources_are_open(self):
        config = load_yaml(ROOT / "config" / "production_sources.yaml")
        approved = {source["id"] for source in config["sources"] if source["rights_review"] == "approved"}
        self.assertEqual(approved, {"agencia_brasil", "usgs"})
        for source in config["sources"]:
            if source["id"] in approved:
                self.assertTrue(source["allow_substantive_analysis"])
                self.assertTrue(source["allow_public_summary"])
                self.assertIn("terms_url", source)
            else:
                self.assertFalse(source["allow_substantive_analysis"])
                self.assertFalse(source["allow_public_summary"])

    def test_workflow_has_schedule_gates_retention_and_failure_alert(self):
        path = ROOT / ".github" / "workflows" / "daily-news.yml"
        raw = path.read_text(encoding="utf-8")
        parsed = yaml.load(raw, Loader=yaml.BaseLoader)
        self.assertIn("schedule", parsed["on"])
        self.assertIn("ENABLE_SCHEDULED_PUBLISH", raw)
        self.assertIn("ENABLE_PUBLISH", raw)
        self.assertIn("retention-days: 90", raw)
        self.assertIn("name: runtime-state", raw)
        self.assertIn("state/budget-ledger.json", raw)
        self.assertIn("state/analysis-cache.json", raw)
        self.assertIn("issues: write", raw)
        self.assertIn("actions/checkout@v7.0.1", raw)
        self.assertIn("actions/setup-python@v7.0.0", raw)
        self.assertIn("actions/upload-artifact@v7.0.1", raw)
        self.assertIn("actions/configure-pages@v6.0.0", raw)
        self.assertIn("actions/upload-pages-artifact@v5.0.0", raw)
        self.assertIn("actions/deploy-pages@v5.0.1", raw)
        self.assertNotRegex(raw, r"[A-Za-z]:\\")


if __name__ == "__main__":
    unittest.main()

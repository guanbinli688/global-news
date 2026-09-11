from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from app.analyze import AnalysisRequestRejected, BudgetExceeded, DailyBudget, OpenAIAnalyzer
from app.cluster import cluster_candidates
from app.collect import article_text, in_fresh_window, in_future_window, parse_ics_candidates, parse_rss_candidates
from app.common import load_json, load_yaml
from app.normalize import normalize_analysis_geography
from app.pipeline import event_reuse_freshness_errors, environment_gate_enabled, run_pipeline, select_analysis_candidates
from app.verify import assess_cluster, balance_events, coverage_report, publication_quality_gate


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

    def test_reuse_freshness_uses_event_time_not_page_modified_time(self):
        now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
        event = {
            "section_id": "headlines",
            "event_time": {"value": "2026-09-09T11:59:59Z", "precision": "datetime"},
            "publication_time": {"value": "2026-09-10T12:00:00Z", "precision": "datetime"},
        }
        self.assertEqual(event_reuse_freshness_errors(event, now, 24), ["reused event is older than 24 hours"])

    def test_feed_excerpt_is_sanitized_and_bounded(self):
        feed = b"""<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item><title>Policy update</title><link>https://example.com/a</link><dc:creator>Jane Reporter</dc:creator><pubDate>Thu, 10 Sep 2026 12:00:00 GMT</pubDate><description><![CDATA[<p>Useful evidence.</p><script>ignore me</script>]]></description></item></channel></rss>"""
        rows = parse_rss_candidates(feed, 5, 50)
        self.assertEqual(rows[0]["feed_excerpt"], "Useful evidence.")
        self.assertEqual(rows[0]["author"], "Jane Reporter")
        self.assertNotIn("script", rows[0]["feed_excerpt"])

    def test_article_extraction_prefers_main_over_navigation(self):
        markup = "<nav>Menu noise</nav><main><h1>Evidence title</h1><p>Substantive evidence.</p><script>ignore</script></main><footer>Footer noise</footer>"
        self.assertEqual(article_text(markup, 200), "Evidence title Substantive evidence.")

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
        complaint = {"items": [candidate("doj", "Department files complaint against company", group="doj", role="primary_document")]}
        decision = assess_cluster(complaint, settings)
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
        self.assertNotIn("uniqueItems", json.dumps(analyzer.sent["text"]["format"]["schema"]))
        self.assertTrue(analyzer.schema["properties"]["topic_ids"]["uniqueItems"])
        self.assertEqual(result["claims"][0]["source_ids"], ["src_1", "src_2"])
        self.assertEqual(len(sources), 2)
        self.assertNotIn(analyzer.api_key, json.dumps(analyzer.sent))

    def test_rejected_request_releases_nonbillable_reservation(self):
        class Ledger:
            pending = False

            def reserve(self, *args, **kwargs):
                self.pending = True

            def release(self, key):
                self.pending = False

        analyzer = StubAnalyzer()
        analyzer.ledger = Ledger()

        def reject(body):
            raise AnalysisRequestRejected("OpenAI API HTTP 400: invalid schema")

        analyzer._request = reject
        cluster = {"items": [candidate("one", "Policy decision"), candidate("two", "Policy decision")]}
        with self.assertRaises(AnalysisRequestRejected):
            analyzer.analyze(cluster, "2026-09-10T13:00:00Z", ["controversies", "corrections"])
        self.assertEqual(analyzer.budget.events, 0)
        self.assertFalse(analyzer.ledger.pending)

    def test_returned_response_usage_is_recorded_before_editorial_checks(self):
        class Ledger:
            def __init__(self):
                self.committed = None

            def reserve(self, *args, **kwargs):
                return None

            def commit(self, key, actual_input, actual_output, input_price, output_price, response_id=None):
                self.committed = (actual_input, actual_output, response_id)

            def mark_usage_missing(self, *args, **kwargs):
                raise AssertionError("usage was present")

        analyzer = StubAnalyzer()
        analyzer.ledger = Ledger()
        analyzer._request = lambda body: {
            "id": "resp_accounted_before_review",
            "status": "failed",
            "usage": {"input_tokens": 321, "output_tokens": 123},
        }
        cluster = {"items": [candidate("one", "Policy decision"), candidate("two", "Policy decision")]}
        with self.assertRaisesRegex(RuntimeError, "not completed"):
            analyzer.analyze(cluster, "2026-09-10T13:00:00Z", ["controversies", "corrections"])
        self.assertEqual(analyzer.ledger.committed, (321, 123, "resp_accounted_before_review"))
        self.assertEqual(analyzer.budget.report()["actual_usage"]["responses"], 1)

    def test_incomplete_analysis_gets_one_repair_and_does_not_block_other_items(self):
        class NoCache:
            def get(self, key):
                return None

            def put(self, *args, **kwargs):
                return None

        class RepairAnalyzer:
            model = "repair-orchestration-test-model"

            def __init__(self):
                self.calls = []

            def analyze(self, cluster, as_of, blocked_sections, reservation_key=None, repair_draft=None):
                self.calls.append(repair_draft)
                analysis = {
                    "title_zh": "Evidence-bound policy update",
                    "summary_zh": "The approved primary source describes a policy update and its stated mechanism.",
                    "section_id": "business",
                    "topic_ids": ["economy"],
                    "region_ids": ["europe_russia"],
                    "countries": [],
                    "material_update": "A primary institution published a new policy update.",
                    "claims": [{
                        "text_zh": "The institution published the update.",
                        "kind": "fact",
                        "source_ids": ["src_1"],
                        "attribution": "Primary institution",
                        "verification_note": "Verified against the approved primary source.",
                    }],
                    "analysis": {
                        "why_it_matters": "It changes the stated policy conditions.",
                        "mechanism": "The policy changes the applicable rule." if repair_draft is not None else None,
                        "affected_groups": "Regulated participants.",
                        "counter_evidence": None,
                        "watch_next": "Watch implementation notices.",
                    },
                    "unknowns": ["Implementation outcomes are not yet known."],
                }
                source = {
                    "id": "src_1", "publisher": "Primary institution", "url": cluster["items"][0]["url"],
                    "title": cluster["items"][0]["title"],
                    "source_time": {"value": cluster["items"][0]["published_at"], "precision": "datetime", "original_text": cluster["items"][0]["original_time"], "timezone": "UTC"},
                    "access_level": "authorized_feed", "upstream_origin": "primary", "independence_group": "primary",
                    "retrieved_at": as_of, "evidence_method": "approved primary feed excerpt",
                    "evidence_excerpt": cluster["items"][0]["evidence_text"][:1200], "attribution": "Primary institution", "license_url": None,
                }
                return analysis, [source]

            def budget_report(self):
                return {"actual_usage": {"responses": len(self.calls)}, "events": len(self.calls)}

        row = candidate("primary", "Substantive policy decision", role="primary_document")
        row["evidence_text"] = "Documented policy evidence. " * 20
        analyzer = RepairAnalyzer()
        with (
            patch("app.pipeline.collect_sources", return_value=([row], [])),
            patch("app.pipeline.collect_calendar_sources", return_value=([], [])),
            patch("app.pipeline.AnalysisCache", return_value=NoCache()),
            patch.dict(os.environ, {"ENABLE_PUBLISH": "false", "ENABLE_AI_ANALYSIS": "false"}, clear=False),
        ):
            report = run_pipeline(now=datetime(2026, 9, 10, 13, tzinfo=timezone.utc), analyzer=analyzer)
        self.assertEqual(len(analyzer.calls), 2)
        self.assertIsNone(analyzer.calls[0])
        self.assertIsNotNone(analyzer.calls[1])
        self.assertEqual(report["analysis_repair"], {"attempts": 1, "repaired": 1, "skipped_after_repair": 0})
        self.assertEqual(report["event_count"], 1)

    def test_controversy_draft_is_quarantined_without_blocking_the_edition(self):
        class NoCache:
            def get(self, key):
                return None

            def put(self, *args, **kwargs):
                return None

        class ControversyAnalyzer:
            model = "controversy-quarantine-test-model"

            def analyze(self, cluster, as_of, blocked_sections, reservation_key=None, repair_draft=None):
                analysis = {
                    "title_zh": "Review-required public controversy",
                    "summary_zh": "This draft remains outside automatic publication pending human review.",
                    "section_id": "controversies", "topic_ids": ["society"],
                    "region_ids": ["north_america"], "countries": [],
                    "material_update": "A review-required draft was generated.",
                    "claims": [{"text_zh": "An institution issued a statement.", "kind": "attributed_claim", "source_ids": ["src_1"], "attribution": "Institution", "verification_note": "Human review is still required."}],
                    "analysis": {"why_it_matters": "Public interest.", "mechanism": "A public statement.", "affected_groups": "Readers.", "counter_evidence": None, "watch_next": "Human verification."},
                    "unknowns": ["Independent verification is pending."],
                }
                source = {
                    "id": "src_1", "publisher": "Institution", "url": cluster["items"][0]["url"], "title": cluster["items"][0]["title"],
                    "source_time": {"value": cluster["items"][0]["published_at"], "precision": "datetime", "original_text": cluster["items"][0]["original_time"], "timezone": "UTC"},
                    "access_level": "authorized_feed", "upstream_origin": "institution", "independence_group": "institution", "retrieved_at": as_of,
                    "evidence_method": "approved primary feed excerpt", "evidence_excerpt": cluster["items"][0]["evidence_text"][:1200], "attribution": "Institution", "license_url": None,
                }
                return analysis, [source]

            def budget_report(self):
                return {"actual_usage": {"responses": 1}, "events": 1}

        row = candidate("institution", "Public statement requiring review", role="primary_document")
        row["evidence_text"] = "Documented statement evidence. " * 20
        with (
            patch("app.pipeline.collect_sources", return_value=([row], [])),
            patch("app.pipeline.collect_calendar_sources", return_value=([], [])),
            patch("app.pipeline.AnalysisCache", return_value=NoCache()),
            patch.dict(os.environ, {"ENABLE_PUBLISH": "false", "ENABLE_AI_ANALYSIS": "false"}, clear=False),
        ):
            report = run_pipeline(now=datetime(2026, 9, 10, 13, tzinfo=timezone.utc), analyzer=ControversyAnalyzer())
        self.assertEqual(report["review_quarantine"]["count"], 1)
        self.assertEqual(report["event_count"], 0)
        quarantine = load_json(ROOT / report["review_quarantine"]["file"])
        self.assertEqual(quarantine["items"][0]["section_id"], "controversies")

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

    def test_known_countries_override_unsupported_model_regions(self):
        analysis = {
            "countries": ["吉布提", "美国"],
            "region_ids": ["east_asia", "north_america"],
        }
        normalized = normalize_analysis_geography(analysis)
        self.assertEqual(normalized["region_ids"], ["north_america", "sub_saharan_africa"])
        self.assertEqual(analysis["region_ids"], ["east_asia", "north_america"])

    def test_unknown_country_does_not_force_partial_region_mapping(self):
        analysis = {"countries": ["美国", "未收录国家"], "region_ids": ["global"]}
        self.assertIs(normalize_analysis_geography(analysis), analysis)

    def test_only_explicitly_reviewed_sources_are_open(self):
        config = load_yaml(ROOT / "config" / "production_sources.yaml")
        approved = {source["id"] for source in config["sources"] if source["rights_review"] == "approved"}
        self.assertEqual(approved, {
            "agencia_brasil", "doj", "european_commission", "fda", "global_voices",
            "gov_uk", "horizon_magazine", "nasa", "nih", "nist", "usgs",
        })
        for source in config["sources"]:
            if source["id"] in approved:
                self.assertTrue(source["allow_substantive_analysis"])
                self.assertTrue(source["allow_public_summary"])
                self.assertIn("terms_url", source)
            else:
                self.assertFalse(source["allow_substantive_analysis"])
                self.assertFalse(source["allow_public_summary"])

    def test_source_group_cap_is_applied_before_paid_analysis(self):
        rows = [
            {"cluster": {"items": [candidate(f"same-{index}", f"Story {index}", group="wire")]}}
            for index in range(6)
        ]
        rows.extend([
            {"cluster": {"items": [candidate("official-a", "Official A", group="agency-a", role="primary_document")]}},
            {"cluster": {"items": [candidate("official-b", "Official B", group="agency-b", role="primary_document")]}},
        ])
        selected = select_analysis_candidates(rows, maximum_events=10, max_per_source_group=2)
        groups = [row["cluster"]["items"][0]["independence_group"] for row in selected]
        self.assertEqual(groups.count("wire"), 2)
        self.assertIn("agency-a", groups)
        self.assertIn("agency-b", groups)

        mixed = {"cluster": {"items": [
            candidate("wire-mixed", "Mixed wire", group="wire"),
            candidate("agency-c", "Mixed official", group="agency-c", role="primary_document"),
        ]}}
        selected_with_mixed = select_analysis_candidates(rows[:3] + [mixed], maximum_events=10, max_per_source_group=2)
        self.assertEqual(len(selected_with_mixed), 2)

        short = {"cluster": {"items": [candidate("short", "Short evidence")]}}
        short["cluster"]["items"][0]["evidence_text"] = "too short"
        self.assertEqual(
            select_analysis_candidates([short], 10, 3, minimum_model_evidence_chars=160),
            [],
        )

    def test_publication_gate_allows_compact_edition_but_keeps_coverage_hard(self):
        def event(index, group, topic="economy", section="headlines", region="north_america"):
            return {
                "event_id": f"event-{index}", "topic_ids": [topic], "section_id": section,
                "region_ids": [region], "sources": [{"independence_group": group}],
            }

        events = [event(index, f"source-{index}") for index in range(5)]
        coverage = coverage_report(events, {"target_regions": 5, "target_topics": 7})
        gate = publication_quality_gate(events, coverage, load_yaml(ROOT / "config" / "pipeline.yaml")["publication"])
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["edition_format"], "compact")
        self.assertIn("publishable events 5/10", gate["warnings"][0])
        self.assertNotIn("publishable events 5/10", gate["reasons"])

        sections = ["headlines", "business", "science_technology", "society_world"]
        regions = ["north_america", "europe_russia", "east_asia", "southeast_asia"]
        topics = ["economy", "science", "society", "law", "health", "energy"]
        compact_events = [
            event(index, f"source-{index}", topics[index % len(topics)], sections[index % len(sections)], regions[index % len(regions)])
            for index in range(9)
        ]
        compact_coverage = coverage_report(compact_events, {"target_regions": 5, "target_topics": 7})
        compact_gate = publication_quality_gate(compact_events, compact_coverage, load_yaml(ROOT / "config" / "pipeline.yaml")["publication"])
        self.assertTrue(compact_gate["passed"])
        self.assertEqual(compact_gate["edition_format"], "compact")
        self.assertTrue(compact_gate["warnings"])

        disasters = [event(index, f"source-{index}", "disasters", "science_technology") for index in range(4)]
        selected = balance_events(
            disasters,
            {"max_events_per_source_group": 2, "max_events_by_topic": {"disasters": 2}},
            [{"id": "science_technology", "target_max": 5}],
            5,
        )
        self.assertEqual(len(selected), 2)

    def test_workflow_has_schedule_gates_retention_and_failure_alert(self):
        path = ROOT / ".github" / "workflows" / "daily-news.yml"
        raw = path.read_text(encoding="utf-8")
        parsed = yaml.load(raw, Loader=yaml.BaseLoader)
        self.assertIn("schedule", parsed["on"])
        self.assertIn("ENABLE_SCHEDULED_PUBLISH", raw)
        self.assertIn("ENABLE_PUBLISH", raw)
        self.assertEqual(parsed["jobs"]["pipeline"]["env"]["ENABLE_PUBLISH"], "${{ vars.ENABLE_PUBLISH }}")
        self.assertIn("retention-days: 90", raw)
        self.assertIn("name: runtime-state", raw)
        self.assertIn("state/candidate-edition.json", raw)
        self.assertIn("state/budget-ledger.json", raw)
        self.assertIn("state/analysis-cache.json", raw)
        self.assertIn("reuse_run_id", raw)
        self.assertIn("--reuse-candidate", raw)
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

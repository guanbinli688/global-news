from __future__ import annotations

import json
import os
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.analyze import preview_analysis
from app.build import build, choose_items, e, make_event
from app.deduplicate import deduplicate
from app.source_diagnostics import diagnose, parse_time
from app.verify import (
    conflicts_are_attributed,
    future_calendar_safe,
    preview_payload_safe,
    validate_claim_sources,
)


ROOT = Path(__file__).resolve().parents[1]


def item(**updates):
    base = {
        "source_id": "bbc",
        "source": "BBC News",
        "title": "A sample headline",
        "url": "https://example.com/story?utm_source=test",
        "published_at": "2026-09-10T12:00:00Z",
        "time_precision": "datetime",
        "original_time": "Thu, 10 Sep 2026 12:00:00 GMT",
        "regions": ["global"],
        "topics": ["politics"],
        "upstream_origin": "bbc",
        "independence_group": "bbc",
    }
    base.update(updates)
    return base


def diagnostic_entry():
    return {
        "id": "bbc", "url": "https://example.com/feed", "interface_type": "rss",
        "license_status": "review_required", "documentation_status": "reviewed",
        "documentation_url": "https://example.com/docs", "allow_local_preview": True,
    }


CATALOG = {"bbc": {"name": "BBC News", "suggested_regions": ["global"], "suggested_topics": ["politics"], "publisher_group_hint": "bbc"}}
DEFAULTS = {"timeout_seconds": 1, "max_response_bytes": 10000, "max_items": 5}


class PipelineAcceptanceTests(unittest.TestCase):
    def test_a_old_duplicate_title_keeps_freshest(self):
        rows = [
            item(url="https://old.example/a", published_at="2026-09-01T12:00:00Z"),
            item(url="https://new.example/b", published_at="2026-09-10T12:00:00Z"),
        ]
        result = deduplicate(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["published_at"], "2026-09-10T12:00:00Z")

    def test_b_republisher_does_not_count_as_independent_event(self):
        rows = [item(), item(source_id="republisher", source="Republisher", url="https://another.example/story", independence_group="same-wire")]
        result = deduplicate(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["duplicate_sources"]), 1)

    def test_c_date_only_is_not_given_an_invented_time(self):
        self.assertEqual(parse_time("2026-09-10"), ("2026-09-10", "date"))

    def test_d_headlines_are_not_padded_to_five(self):
        rows = [item(title="One", url="https://example.com/1"), item(title="Two", url="https://example.com/2", source_id="dw")]
        selected = choose_items(rows)
        self.assertEqual(sum(section == "headlines" for _, section in selected), 2)

    def test_e_conflicting_claims_require_attribution(self):
        event = {"evidence_status": "disputed", "claims": [{"attribution": "Party A"}, {"attribution": "Party B"}]}
        self.assertTrue(conflicts_are_attributed(event))
        event["claims"][1]["attribution"] = None
        self.assertFalse(conflicts_are_attributed(event))

    def test_f_no_model_means_no_invented_analysis(self):
        analysis, label = preview_analysis()
        self.assertTrue(all(value is None for value in analysis.values()))
        self.assertEqual(label, "尚未生成深度分析")
        event = make_event(item(), "headlines", "2026-09-10T13:00:00Z")
        self.assertIn("未生成新闻摘要", event["summary_zh"])

    def test_g_404_429_and_empty_are_distinct(self):
        def http_error(code):
            return urllib.error.HTTPError("https://example.com/feed", code, "error", None, None)
        with patch("app.source_diagnostics.request_bytes", side_effect=http_error(404)):
            status, _ = diagnose(diagnostic_entry(), CATALOG, DEFAULTS)
            self.assertEqual(status["status"], "failed")
        with patch("app.source_diagnostics.request_bytes", side_effect=http_error(429)):
            status, _ = diagnose(diagnostic_entry(), CATALOG, DEFAULTS)
            self.assertEqual(status["status"], "partial")
        with patch("app.source_diagnostics.request_bytes", return_value=(b"<rss><channel></channel></rss>", 200, "application/rss+xml")):
            status, _ = diagnose(diagnostic_entry(), CATALOG, DEFAULTS)
            self.assertEqual(status["status"], "empty")

    def test_h_source_health_keeps_every_attempt_and_valid_status(self):
        report = json.loads((ROOT / "source-health.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["total"], len(report["sources"]))
        self.assertEqual(len({row["source_id"] for row in report["sources"]}), len(report["sources"]))
        allowed = {"ok", "empty", "partial", "not_configured", "permission_required", "stale", "failed"}
        self.assertTrue(all(row["status"] in allowed for row in report["sources"]))
        self.assertFalse(report["policy"]["browser_cookies_used"])

    def test_i_future24_requires_official_calendar(self):
        self.assertFalse(future_calendar_safe({"section_id": "next24h"}))
        self.assertTrue(future_calendar_safe({"section_id": "next24h", "official_calendar_source": "https://example.gov/calendar"}))

    def test_j_corrections_are_explicit_records(self):
        event = make_event(item(), "headlines", "2026-09-10T13:00:00Z")
        self.assertEqual(event["corrections"], [])
        self.assertIn("旧主张", (ROOT / "site" / "corrections.html").read_text(encoding="utf-8"))

    def test_k_preview_excludes_secrets_full_text_and_escapes_markup(self):
        event = make_event(item(title="<script>alert(1)</script>"), "headlines", "2026-09-10T13:00:00Z")
        self.assertTrue(preview_payload_safe([event]))
        self.assertNotIn("<script>", e(event["title_zh"]))
        os.environ["GLOBAL_NEWS_TEST_SECRET"] = "must-not-appear"
        try:
            output = (ROOT / "site" / "data.json").read_text(encoding="utf-8")
            self.assertNotIn(os.environ["GLOBAL_NEWS_TEST_SECRET"], output)
        finally:
            del os.environ["GLOBAL_NEWS_TEST_SECRET"]

    def test_l_every_claim_references_an_existing_source(self):
        event = make_event(item(), "headlines", "2026-09-10T13:00:00Z")
        self.assertEqual(validate_claim_sources(event), [])

    def test_m_no_unmeasured_heat_or_growth_claims(self):
        payload = (ROOT / "site" / "data.json").read_text(encoding="utf-8").casefold()
        for forbidden in ("engagement_count", "heat_score", "growth_percent"):
            self.assertNotIn(forbidden, payload)

    def test_static_build_smoke(self):
        result = build()
        self.assertGreater(result["events"], 0)
        for name in ("index.html", "source-health.html", "coverage.html", "history.html", "corrections.html", "data.json"):
            self.assertTrue((ROOT / "site" / name).is_file())


if __name__ == "__main__":
    unittest.main()

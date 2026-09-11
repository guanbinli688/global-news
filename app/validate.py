"""Validate the supplied configuration before building the preview."""

from __future__ import annotations

import json
from typing import Any

from .common import CONFIG_DIR, ROOT, load_json, load_yaml, write_json


def validate_configuration() -> dict[str, Any]:
    site = load_yaml(CONFIG_DIR / "site.yaml")
    sources = load_yaml(CONFIG_DIR / "sources.yaml")
    diagnostic = load_yaml(CONFIG_DIR / "diagnostic_sources.yaml")
    pipeline = load_yaml(CONFIG_DIR / "pipeline.yaml")
    production = load_yaml(CONFIG_DIR / "production_sources.yaml")
    calendars = load_yaml(CONFIG_DIR / "calendar_sources.yaml")
    schema = load_json(ROOT / "schemas" / "event.schema.json")
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    topics = site.get("coverage", {}).get("topics", [])
    regions = site.get("coverage", {}).get("regions", [])
    sections = site.get("sections", [])
    source_rows = sources.get("sources", [])
    source_ids = [row.get("id") for row in source_rows]
    diagnostic_rows = diagnostic.get("sources", [])
    diagnostic_ids = [row.get("id") for row in diagnostic_rows]
    production_rows = production.get("sources", [])
    production_ids = [row.get("id") for row in production_rows]
    calendar_rows = calendars.get("sources", [])
    calendar_ids = [row.get("id") for row in calendar_rows]

    check("site schema version", site.get("schema_version") == "1.0", str(site.get("schema_version")))
    check("15 topic filters", len(topics) == 15 and len({row.get('id') for row in topics}) == 15, f"count={len(topics)}")
    check("10 region filters", len(regions) == 10 and len({row.get('id') for row in regions}) == 10, f"count={len(regions)}")
    check("8 editorial sections", len(sections) == 8 and len({row.get('id') for row in sections}) == 8, f"count={len(sections)}")
    check("declared source count", sources.get("source_entry_count") == len(source_rows), f"declared={sources.get('source_entry_count')}, actual={len(source_rows)}")
    check("unique source IDs", len(source_ids) == len(set(source_ids)), f"count={len(source_ids)}")
    check("all candidate ingestion disabled", all(row.get("ingestion_enabled") is False for row in source_rows), "no registry source auto-enabled")
    check("all public republication disabled", all(row.get("public_republication_enabled") is False for row in source_rows), "no registry source cleared for republication")
    check("diagnostic IDs are registered", set(diagnostic_ids).issubset(set(source_ids)), f"diagnostic_count={len(diagnostic_ids)}")
    check("diagnostic IDs unique", len(diagnostic_ids) == len(set(diagnostic_ids)), f"diagnostic_count={len(diagnostic_ids)}")
    check("production IDs are registered", set(production_ids).issubset(set(source_ids)), f"production_count={len(production_ids)}")
    check("production IDs unique", len(production_ids) == len(set(production_ids)), f"production_count={len(production_ids)}")
    check("calendar IDs are registered", set(calendar_ids).issubset(set(source_ids)), f"calendar_count={len(calendar_ids)}")
    check("calendar sources require approved rights", all(row.get("rights_review") == "approved" and row.get("allow_public_summary") is True for row in calendar_rows), "unapproved calendar entries are rejected")
    check("article fetch requires approved rights", all(not row.get("article_fetch") or (row.get("rights_review") == "approved" and row.get("terms_url") and row.get("allowed_article_hosts")) for row in production_rows), "no pending or unscoped source permits article fetch")
    check("analysis text requires approved public-summary rights", all(not row.get("allow_substantive_analysis") or (row.get("rights_review") == "approved" and row.get("allow_public_summary") is True) for row in production_rows), "only explicitly approved sources may provide substantive inputs")
    check("24-hour collection window", pipeline.get("collection", {}).get("fresh_hours") == 24, str(pipeline.get("collection", {}).get("fresh_hours")))
    check("publication has an editorial event target", int(pipeline.get("publication", {}).get("target_publishable_events", 0)) > 0, str(pipeline.get("publication", {}).get("target_publishable_events")))
    publication = pipeline.get("publication", {})
    check("publication has source diversity floor", int(publication.get("minimum_cited_source_groups", 0)) >= 3, str(publication.get("minimum_cited_source_groups")))
    check("publication has section diversity floor", int(publication.get("minimum_populated_sections", 0)) >= 3, str(publication.get("minimum_populated_sections")))
    check("publication has region and topic floors", int(publication.get("minimum_regions", 0)) >= 3 and int(publication.get("minimum_topics", 0)) >= 5, f"regions={publication.get('minimum_regions')}, topics={publication.get('minimum_topics')}")
    check("disaster topic has a front-page cap", int(pipeline.get("coverage", {}).get("max_events_by_topic", {}).get("disasters", 0)) > 0, str(pipeline.get("coverage", {}).get("max_events_by_topic", {}).get("disasters")))
    check("AI response storage disabled", pipeline.get("ai", {}).get("store_responses") is False, "store=false")
    check("publication requires explicit gate", bool(pipeline.get("publication", {}).get("publication_gate_env")), str(pipeline.get("publication", {}).get("publication_gate_env")))
    check("event schema is closed", schema.get("additionalProperties") is False, "additionalProperties=false")
    check("model remains unconfigured", site.get("security_costs", {}).get("model") is None, "metadata-only fallback active")
    check("deployment remains disabled", site.get("site", {}).get("deployment_state") == "not_deployed", str(site.get("site", {}).get("deployment_state")))

    report = {
        "schema_version": "1.0",
        "passed": all(row["passed"] for row in checks),
        "checks": checks,
    }
    write_json(ROOT / "validation-report.json", report)
    if not report["passed"]:
        failed = ", ".join(row["name"] for row in checks if not row["passed"])
        raise ValueError(f"configuration validation failed: {failed}")
    return report


if __name__ == "__main__":
    print(json.dumps(validate_configuration(), ensure_ascii=False, indent=2))

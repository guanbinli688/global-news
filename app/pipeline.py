"""Daily evidence-gated collection, analysis, archive, and build pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import jsonschema

from .analyze import AnalysisConfigurationError, BudgetExceeded, OpenAIAnalyzer, enabled_from_environment
from .archive import archive_success
from .build import build
from .cluster import cluster_candidates
from .collect import collect_calendar_sources, collect_sources
from .common import CONFIG_DIR, ROOT, load_json, load_yaml, write_json
from .normalize import event_id, sortable_time
from .run_state import AnalysisCache, evidence_cache_key
from .source_diagnostics import markdown_report
from .structured_analysis import analyze_usgs, can_analyze_structured
from .validate import validate_configuration
from .verify import assess_cluster, balance_events, coverage_report, editorial_event_safe, preview_payload_safe, validate_claim_evidence, validate_claim_sources, validate_event_times


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_event(
    cluster: dict[str, Any],
    analysis: dict[str, Any],
    sources: list[dict[str, Any]],
    as_of: str,
    analysis_method: str,
    gate: dict[str, Any],
) -> dict[str, Any]:
    lead = sorted(cluster["items"], key=sortable_time, reverse=True)[0]
    claims = []
    for index, claim in enumerate(analysis["claims"], start=1):
        claims.append({"id": f"claim_{index}", **claim})
    event_time = {
        "value": lead.get("published_at"), "precision": lead.get("time_precision") or "unknown",
        "original_text": lead.get("original_time"), "timezone": "UTC" if lead.get("time_precision") == "datetime" else None,
    }
    published_value = lead.get("source_updated_at") or lead.get("published_at")
    publication_time = {
        "value": published_value, "precision": "datetime" if published_value else "unknown",
        "original_text": lead.get("original_time"), "timezone": "UTC" if published_value else None,
    }
    return {
        "event_id": event_id(lead),
        "title_zh": analysis["title_zh"],
        "original_title": str(lead.get("title") or "") or None,
        "topic_ids": analysis["topic_ids"],
        "region_ids": analysis["region_ids"],
        "countries": list(dict.fromkeys(analysis.get("countries") or [])),
        "section_id": analysis["section_id"],
        "event_time": event_time,
        "publication_time": publication_time,
        "fetched_at": as_of,
        "as_of": as_of,
        "edition_cutoff": as_of,
        "generated_at": as_of,
        "published_success_at": None,
        "material_update": analysis["material_update"],
        "summary_zh": analysis["summary_zh"],
        "claims": claims,
        "sources": sources,
        "evidence_status": "attributed_report",
        "analysis": analysis["analysis"],
        "unknowns": analysis["unknowns"],
        "corrections": [],
        "human_review_required": False,
        "analysis_method": analysis_method,
        "version": 1,
        "verification": {
            "status": "passed",
            "reasons": [],
            "evidence_chain_count": int(gate.get("independent_source_count", 0)),
            "substantive_source_count": int(gate.get("substantive_source_count", 0)),
        },
    }


def substantive_analysis_present(event: dict[str, Any]) -> bool:
    analysis = event.get("analysis", {})
    return bool(analysis.get("why_it_matters") and analysis.get("mechanism"))


def environment_gate_enabled(name: str) -> bool:
    return os.environ.get(name, "").casefold() == "true"


def source_run_stats(
    source_health: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    eligible_ids = {
        item.get("source_id")
        for row in eligible
        for item in row["cluster"].get("items", [])
        if item.get("source_id")
    }
    used_publishers = {
        source.get("publisher")
        for event in events
        for source in event.get("sources", [])
        if source.get("publisher")
    }
    return {
        "planned_sources": len(source_health),
        "attempted_sources": sum(1 for row in source_health if row.get("status") != "not_configured"),
        "successful_sources": sum(1 for row in source_health if row.get("status") in {"ok", "empty"}),
        "time_eligible_sources": len({item.get("source_id") for item in candidates if item.get("source_id")}),
        "evidence_eligible_sources": len(eligible_ids),
        "cited_sources": len(used_publishers),
        "fetched_candidates": len(candidates),
        "evidence_eligible_events": len(eligible),
        "analyzed_events": len(events),
    }


def write_source_health(source_health: list[dict[str, Any]], as_of: str, paid_ai_used: bool) -> None:
    normalized = []
    status_map = {"rate_limited": "partial"}
    for row in source_health:
        normalized.append({
            "source_id": row["source_id"], "source": row["source"], "url": row["url"],
            "interface_type": row["interface_type"], "tested_at": as_of,
            "status": status_map.get(row["status"], row["status"]), "http_status": row.get("http_status"),
            "content_type": row.get("content_type"), "item_count": row.get("items_seen", 0),
            "parsed_date_count": row.get("parsed_date_count", 0), "latest_item_time": row.get("latest_item_time"),
            "license_status": f"rights_review={row.get('rights_review')}; public_summary={row.get('allow_public_summary')}",
            "documentation_status": "production_policy_overlay", "documentation_url": None,
            "failure_reason": row.get("reason"),
            "preview_eligible": row["status"] == "ok" and row.get("allow_public_summary") is True,
            "regions": [], "topics": [], "attempts": row.get("attempts", 0), "fresh_items": row.get("fresh_items", 0),
        })
    statuses = ("ok", "empty", "partial", "not_configured", "permission_required", "stale", "failed")
    counts = {status: sum(1 for row in normalized if row["status"] == status) for status in statuses}
    report = {
        "schema_version": "1.0", "tested_at": as_of,
        "policy": {"network_test_only": not any(row["preview_eligible"] for row in normalized), "metadata_only": not any(row["preview_eligible"] for row in normalized), "browser_cookies_used": False, "paid_services_used": paid_ai_used, "publication_performed": False},
        "summary": {"total": len(normalized), "preview_eligible": sum(1 for row in normalized if row["preview_eligible"]), **counts},
        "sources": normalized,
    }
    write_json(ROOT / "source-health.json", report)
    (ROOT / "source-health.md").write_text(markdown_report(report), encoding="utf-8", newline="\n")


def run_pipeline(
    now: datetime | None = None,
    analyzer: Any | None = None,
    collect: bool = True,
    analysis_allowed: bool = True,
    build_candidate: bool = False,
) -> dict[str, Any]:
    validate_configuration()
    pipeline_config = load_yaml(CONFIG_DIR / "pipeline.yaml")
    production_sources = load_yaml(CONFIG_DIR / "production_sources.yaml")
    calendar_sources = load_yaml(CONFIG_DIR / "calendar_sources.yaml")
    source_registry = load_yaml(CONFIG_DIR / "sources.yaml")
    catalog = {row["id"]: row for row in source_registry["sources"]}
    as_of_dt = (now or utc_now()).astimezone(timezone.utc)
    as_of = iso_utc(as_of_dt)

    if collect:
        candidates, source_health = collect_sources(production_sources, catalog, pipeline_config, as_of_dt)
        calendar_candidates, calendar_health = collect_calendar_sources(calendar_sources, catalog, pipeline_config, as_of_dt)
        candidates.extend(calendar_candidates)
        source_health.extend(calendar_health)
    else:
        candidates, source_health = [], []
    settings = pipeline_config["evidence"]
    clusters = cluster_candidates(candidates, float(settings["title_similarity_threshold"]), int(settings["cluster_window_hours"]))
    assessments = [{"cluster": cluster, "gate": assess_cluster(cluster, settings)} for cluster in clusters]
    eligible = [row for row in assessments if row["gate"]["passed"]]

    ai_config = pipeline_config["ai"]
    state_config = pipeline_config.get("state", {})
    cache_path = ROOT / str(state_config.get("analysis_cache_file", "state/analysis-cache.json"))
    analysis_cache = AnalysisCache(cache_path)
    ai_enabled = analysis_allowed and (analyzer is not None or enabled_from_environment(ai_config))
    analysis_errors: list[str] = []
    events: list[dict[str, Any]] = []
    event_schema = load_json(ROOT / "schemas" / "event.schema.json")
    active_analyzer = analyzer if analysis_allowed else None
    if ai_enabled and active_analyzer is None:
        try:
            active_analyzer = OpenAIAnalyzer(
                ai_config,
                state_config=state_config,
                as_of=as_of_dt,
                edition_timezone=str(pipeline_config.get("automation", {}).get("timezone", "Asia/Shanghai")),
            )
        except AnalysisConfigurationError as exc:
            analysis_errors.append(str(exc))
    skipped_for_model = 0
    cache_hits = 0
    cache_misses = 0
    for row in eligible[: int(pipeline_config["publication"]["maximum_events"])]:
        try:
            cache_write: tuple[str, str] | None = None
            if can_analyze_structured(row["cluster"]):
                analysis, sources = analyze_usgs(row["cluster"], as_of)
                analysis_method = "deterministic_public_data"
            elif active_analyzer is not None:
                model = str(getattr(active_analyzer, "model", "unknown-model"))
                cache_key = evidence_cache_key(row["cluster"], model)
                cached = analysis_cache.get(cache_key)
                if cached is not None:
                    analysis = cached["analysis"]
                    sources = cached["sources"]
                    analysis_method = "cached_openai"
                    cache_hits += 1
                else:
                    analysis, sources = active_analyzer.analyze(
                        row["cluster"], as_of, list(settings.get("blocked_auto_sections", [])), reservation_key=cache_key,
                    )
                    analysis_method = "openai"
                    cache_misses += 1
                    cache_write = (cache_key, model)
            else:
                skipped_for_model += 1
                continue
            if analysis.get("section_id") == "next24h" and not any(item.get("source_role") == "official_calendar" for item in row["cluster"].get("items", [])):
                raise ValueError("next24h item lacks an approved official calendar source")
            event = make_event(row["cluster"], analysis, sources, as_of, analysis_method, row["gate"])
            if not substantive_analysis_present(event):
                raise ValueError("deep analysis fields are incomplete")
            if validate_claim_sources(event):
                raise ValueError("claim references a missing source")
            evidence_errors = validate_claim_evidence(event)
            if evidence_errors:
                raise ValueError(evidence_errors[0])
            time_errors = validate_event_times(event)
            if time_errors:
                raise ValueError(time_errors[0])
            safe, reason = editorial_event_safe(event)
            if not safe:
                raise ValueError(reason or "event failed editorial gate")
            jsonschema.Draft202012Validator(event_schema, format_checker=jsonschema.FormatChecker()).validate(event)
            events.append(event)
            if cache_write is not None:
                analysis_cache.put(cache_write[0], cache_write[1], as_of, analysis, sources)
        except BudgetExceeded as exc:
            analysis_errors.append(str(exc))
            break
        except Exception as exc:
            analysis_errors.append(f"{type(exc).__name__}: {exc}")
    if skipped_for_model:
        analysis_errors.append(f"{skipped_for_model} evidence-eligible event(s) require a configured model")

    valid_events: list[dict[str, Any]] = []
    for event in events:
        try:
            jsonschema.Draft202012Validator(event_schema, format_checker=jsonschema.FormatChecker()).validate(event)
            if not preview_payload_safe([event]):
                raise ValueError("event payload contains disallowed data")
            valid_events.append(event)
        except (jsonschema.ValidationError, ValueError) as exc:
            analysis_errors.append(f"event rejected: {exc}")

    site_config = load_yaml(CONFIG_DIR / "site.yaml")
    valid_events = balance_events(
        valid_events, pipeline_config["coverage"], site_config["sections"],
        int(pipeline_config["publication"]["maximum_events"]),
    )
    coverage = coverage_report(valid_events, pipeline_config["coverage"])
    minimum = int(pipeline_config["publication"]["minimum_publishable_events"])
    allowed_methods = set(pipeline_config["publication"].get("allowed_analysis_methods", []))
    publish_ready = (
        len(valid_events) >= minimum
        and all(event.get("analysis_method") in allowed_methods for event in valid_events)
        and not any(event["human_review_required"] for event in valid_events)
    )
    publication_gate_name = str(pipeline_config["publication"]["publication_gate_env"])
    publish_enabled = environment_gate_enabled(publication_gate_name)
    production_bundle_built = publish_ready and publish_enabled
    budget_report = active_analyzer.budget_report() if active_analyzer is not None and hasattr(active_analyzer, "budget_report") else active_analyzer.budget.report() if active_analyzer is not None and hasattr(active_analyzer, "budget") else None
    source_stats = source_run_stats(source_health, candidates, eligible, valid_events)
    report = {
        "schema_version": "1.0", "as_of": as_of,
        "status": "production_built" if production_bundle_built else "publish_ready" if publish_ready else "blocked",
        "publish_ready": publish_ready, "candidate_count": len(candidates), "cluster_count": len(clusters),
        "evidence_eligible_count": len(eligible), "event_count": len(valid_events), "minimum_event_count": minimum,
        "ai_enabled": ai_enabled, "analysis_errors": analysis_errors[:30], "coverage": coverage,
        "source_stats": source_stats,
        "analysis_cache": {"hits": cache_hits, "misses": cache_misses},
        "source_health": source_health,
        "blocked_clusters": [
            {"title": row["cluster"].get("lead_title"), "reasons": row["gate"]["reasons"]}
            for row in assessments if not row["gate"]["passed"]
        ][:50],
        "budget": budget_report,
        "publication_gate": {"environment_variable": publication_gate_name, "enabled": publish_enabled},
        "production_bundle_built": production_bundle_built,
        "security": {"browser_cookies_used": False, "secrets_persisted": False, "full_text_published": False, "public_deployment_performed": False},
    }
    write_json(ROOT / "state" / "run-report.json", report)
    write_json(ROOT / "state" / "run-source-health.json", {"as_of": as_of, "sources": source_health})
    if source_health:
        write_source_health(source_health, as_of, bool(budget_report and budget_report.get("events")))

    candidate_path = ROOT / str(pipeline_config.get("state", {}).get("candidate_edition_file", "state/candidate-edition.json"))
    if valid_events:
        candidate_bundle = {
            "schema_version": "1.0", "publication_mode": "candidate", "as_of": as_of,
            "coverage": coverage, "source_stats": source_stats, "events": valid_events,
        }
        write_json(candidate_path, candidate_bundle)
        if build_candidate:
            build(candidate=True)
    elif candidate_path.exists():
        candidate_path.unlink()

    if production_bundle_built:
        bundle = {
            "schema_version": "1.0", "publication_mode": "production", "as_of": as_of,
            "coverage": coverage, "source_stats": source_stats, "events": valid_events,
        }
        write_json(ROOT / "state" / "publishable-events.json", bundle)
        archive_config = pipeline_config["archive"]
        archive_success(
            bundle,
            str(archive_config["directory"]),
            int(archive_config["retention_days"]),
            str(pipeline_config.get("automation", {}).get("timezone", "Asia/Shanghai")),
        )
        build(production=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the evidence-gated daily news pipeline.")
    parser.add_argument("--mode", choices=("validate", "production"), default="validate")
    parser.add_argument("--no-collect", action="store_true")
    parser.add_argument("--build-candidate", action="store_true", help="Build an explicitly unpublished local site from real eligible events.")
    args = parser.parse_args()
    report = run_pipeline(
        collect=not args.no_collect,
        analysis_allowed=args.mode == "production",
        build_candidate=args.build_candidate,
    )
    print(json.dumps({key: report[key] for key in ("as_of", "status", "candidate_count", "cluster_count", "evidence_eligible_count", "event_count", "publish_ready")}, ensure_ascii=False, indent=2))
    if args.mode == "production" and not report["publish_ready"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

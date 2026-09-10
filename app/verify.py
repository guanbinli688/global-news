"""Structural evidence checks. This does not claim editorial fact verification."""

from __future__ import annotations

from typing import Any


def validate_claim_sources(event: dict[str, Any]) -> list[str]:
    source_ids = {source.get("id") for source in event.get("sources", [])}
    errors: list[str] = []
    for claim in event.get("claims", []):
        for source_id in claim.get("source_ids", []):
            if source_id not in source_ids:
                errors.append(f"{event.get('event_id')}: unknown source id {source_id}")
    return errors


def independent_source_count(event: dict[str, Any]) -> int:
    groups = {
        source.get("independence_group") or source.get("upstream_origin")
        for source in event.get("sources", [])
        if source.get("independence_group") or source.get("upstream_origin")
    }
    return len(groups)


def publication_safe(event: dict[str, Any]) -> bool:
    return not event.get("human_review_required", True) and event.get("evidence_status") != "unverified_lead"


def conflicts_are_attributed(event: dict[str, Any]) -> bool:
    if event.get("evidence_status") != "disputed":
        return True
    claims = event.get("claims", [])
    return len(claims) >= 2 and all(claim.get("attribution") for claim in claims)


def editorial_event_safe(event: dict[str, Any]) -> tuple[bool, str | None]:
    for claim in event.get("claims", []):
        if not claim.get("source_ids"):
            return False, "claim has no evidence source"
        if claim.get("kind") == "attributed_claim" and not claim.get("attribution"):
            return False, "attributed claim has no attribution"
    if event.get("section_id") == "under_currents":
        groups = {
            source.get("independence_group") or source.get("upstream_origin")
            for source in event.get("sources", [])
        }
        if len(event.get("claims", [])) < 3 or len(groups) < 2 or not event.get("analysis", {}).get("counter_evidence"):
            return False, "under-current item lacks three claims, two evidence chains, or counter-evidence"
    if event.get("section_id") in {"controversies", "corrections"}:
        return False, "section requires a dedicated human verification path"
    return True, None


def future_calendar_safe(candidate: dict[str, Any]) -> bool:
    if candidate.get("section_id") != "next24h":
        return True
    return bool(candidate.get("official_calendar_source"))


def preview_payload_safe(events: list[dict[str, Any]]) -> bool:
    sensitive_keys = {"cookie", "cookies", "token", "api_key", "apikey", "password", "secret", "environment"}
    for event in events:
        if any(source.get("access_level") == "full_text" for source in event.get("sources", [])):
            return False
        stack: list[Any] = [event]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if {str(key).casefold() for key in value}.intersection(sensitive_keys):
                    return False
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
    return True


def assess_cluster(cluster: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    items = cluster.get("items", [])
    independent = {
        item.get("independence_group") or item.get("source_id")
        for item in items
        if item.get("independence_group") or item.get("source_id")
    }
    substantive = [
        item for item in items
        if item.get("evidence_text")
        and item.get("rights_review") == "approved"
        and item.get("allow_public_summary") is True
    ]
    primary = [
        item for item in substantive
        if item.get("source_role") in {"primary_document", "primary_data", "primary_institution", "official_calendar"}
    ]
    title = " ".join(str(item.get("title") or "") for item in items).casefold()
    sensitive = [pattern for pattern in settings.get("auto_exclude_patterns", []) if pattern.casefold() in title]
    enough_independent = len(independent) >= int(settings.get("min_independent_sources", 2))
    enough_primary = bool(primary) and bool(settings.get("allow_single_primary_document", True))
    reasons: list[str] = []
    if settings.get("require_substantive_text_for_analysis", True) and not substantive:
        reasons.append("no rights-approved substantive evidence")
    if not (enough_independent or enough_primary):
        reasons.append("insufficient independent evidence")
    if sensitive:
        reasons.append("sensitive claim requires human review: " + ", ".join(sensitive))
    return {
        "passed": not reasons,
        "reasons": reasons,
        "independent_source_count": len(independent),
        "substantive_source_count": len(substantive),
        "primary_source_count": len(primary),
        "human_review_required": bool(sensitive),
    }


def coverage_report(events: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    regions = {region for event in events for region in event.get("region_ids", []) if region != "global"}
    topics = {topic for event in events for topic in event.get("topic_ids", [])}
    sections = {event.get("section_id") for event in events}
    target_regions = int(config.get("target_regions", 5))
    target_topics = int(config.get("target_topics", 7))
    focused = sum(1 for event in events if event.get("section_id") in {"science_technology", "business"})
    focused_share = focused / len(events) if events else 0.0
    focused_target = float(config.get("max_frontpage_tech_business_share", 1))
    return {
        "region_count": len(regions),
        "topic_count": len(topics),
        "section_count": len(sections),
        "regions": sorted(regions),
        "topics": sorted(topics),
        "sections": sorted(value for value in sections if value),
        "region_target_met": len(regions) >= target_regions,
        "topic_target_met": len(topics) >= target_topics,
        "tech_business_share": round(focused_share, 3),
        "tech_business_share_target_met": focused_share <= focused_target,
        "gaps": [
            *( [f"地区覆盖 {len(regions)}/{target_regions}"] if len(regions) < target_regions else [] ),
            *( [f"领域覆盖 {len(topics)}/{target_topics}"] if len(topics) < target_topics else [] ),
            *( [f"科技与商业占比 {focused_share:.0%}，软上限 {focused_target:.0%}"] if focused_share > focused_target else [] ),
        ],
    }


def balance_events(events: list[dict[str, Any]], config: dict[str, Any], sections: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    remaining = list(events)
    selected: list[dict[str, Any]] = []
    regions: set[str] = set()
    topics: set[str] = set()
    section_ids: set[str] = set()
    group_counts: dict[str, int] = {}
    section_counts: dict[str, int] = {}
    caps = {row["id"]: int(row["target_max"]) for row in sections}
    max_per_group = int(config.get("max_events_per_source_group", 3))
    while remaining and len(selected) < maximum:
        choices: list[tuple[int, int, dict[str, Any]]] = []
        for index, event in enumerate(remaining):
            section = str(event.get("section_id"))
            if section_counts.get(section, 0) >= caps.get(section, maximum):
                continue
            groups = {
                str(source.get("independence_group") or source.get("upstream_origin") or source.get("publisher"))
                for source in event.get("sources", [])
            }
            if groups and all(group_counts.get(group, 0) >= max_per_group for group in groups):
                continue
            new_regions = {value for value in event.get("region_ids", []) if value != "global"} - regions
            new_topics = set(event.get("topic_ids", [])) - topics
            new_section = section not in section_ids
            score = len(new_regions) * 4 + len(new_topics) * 2 + int(new_section) * 3
            choices.append((score, -index, event))
        if not choices:
            break
        _, _, chosen = max(choices, key=lambda row: (row[0], row[1]))
        remaining.remove(chosen)
        selected.append(chosen)
        section = str(chosen.get("section_id"))
        section_counts[section] = section_counts.get(section, 0) + 1
        section_ids.add(section)
        regions.update(value for value in chosen.get("region_ids", []) if value != "global")
        topics.update(chosen.get("topic_ids", []))
        for source in chosen.get("sources", []):
            group = str(source.get("independence_group") or source.get("upstream_origin") or source.get("publisher"))
            group_counts[group] = group_counts.get(group, 0) + 1

    return selected

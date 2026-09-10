"""Persist successful editions and prune expired local history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .common import ROOT, write_json


def archive_success(bundle: dict[str, Any], directory: str, retention_days: int) -> Path:
    as_of = datetime.fromisoformat(str(bundle["as_of"]).replace("Z", "+00:00")).astimezone(timezone.utc)
    root = ROOT / directory
    target = root / as_of.strftime("%Y") / as_of.strftime("%m") / f"{as_of.strftime('%Y-%m-%dT%H%M%SZ')}.json"
    write_json(target, bundle)
    write_json(ROOT / "state" / "last-success.json", {
        "schema_version": "1.0", "as_of": bundle["as_of"], "archive_path": target.relative_to(ROOT).as_posix(),
        "event_count": len(bundle.get("events", [])),
    })
    cutoff = as_of - timedelta(days=retention_days)
    for path in root.rglob("*.json"):
        try:
            stamp = datetime.strptime(path.stem, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if stamp < cutoff:
            path.unlink()
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return target


def history_index(directory: str) -> list[dict[str, Any]]:
    root = ROOT / directory
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*.json"), reverse=True):
        try:
            from .common import load_json
            bundle = load_json(path)
        except (OSError, ValueError):
            continue
        rows.append({
            "as_of": bundle.get("as_of"), "event_count": len(bundle.get("events", [])),
            "coverage": bundle.get("coverage", {}), "path": path.relative_to(ROOT).as_posix(),
        })
    return rows

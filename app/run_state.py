"""Persistent analysis cache and conservative daily API budget ledger."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .common import load_json, write_json


def evidence_cache_key(cluster: dict[str, Any], model: str, schema_version: str = "1.0") -> str:
    evidence = []
    for item in cluster.get("items", []):
        if item.get("rights_review") != "approved" or not item.get("evidence_text"):
            continue
        evidence.append({
            "source_id": item.get("source_id"),
            "url": item.get("url"),
            "published_at": item.get("published_at"),
            "source_updated_at": item.get("source_updated_at"),
            "independence_group": item.get("independence_group"),
            "evidence_text": item.get("evidence_text"),
        })
    canonical = json.dumps(
        {"schema_version": schema_version, "model": model, "evidence": evidence},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AnalysisCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            payload = load_json(path)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            payload = {"schema_version": "1.0", "entries": {}}
        self.payload = payload if isinstance(payload.get("entries"), dict) else {"schema_version": "1.0", "entries": {}}

    def get(self, key: str) -> dict[str, Any] | None:
        value = self.payload["entries"].get(key)
        return value if isinstance(value, dict) else None

    def put(self, key: str, model: str, as_of: str, analysis: dict[str, Any], sources: list[dict[str, Any]]) -> None:
        self.payload["entries"][key] = {
            "model": model,
            "created_at": as_of,
            "analysis": analysis,
            "sources": sources,
        }
        # Keep the file bounded without using access time or deleting the newest entries.
        ordered = sorted(
            self.payload["entries"].items(),
            key=lambda row: str(row[1].get("created_at") or ""),
            reverse=True,
        )[:500]
        self.payload["entries"] = dict(ordered)
        write_json(self.path, self.payload)


class BudgetLedger:
    def __init__(self, path: Path, as_of: datetime, edition_timezone: str, model: str) -> None:
        self.path = path
        self.day = as_of.astimezone(ZoneInfo(edition_timezone)).date().isoformat()
        self.model = model
        try:
            payload = load_json(path)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            payload = {}
        if payload.get("day") != self.day or payload.get("model") != model:
            payload = {
                "schema_version": "1.1", "day": self.day, "model": model,
                "actual_input_tokens": 0, "actual_output_tokens": 0,
                "estimated_cost_usd": 0.0, "completed_events": 0,
                "pending_reservations": {}, "unreconciled_usage": {},
                "actual_usage_records": [],
            }
        if not isinstance(payload.get("pending_reservations"), dict):
            payload["pending_reservations"] = {}
        if not isinstance(payload.get("unreconciled_usage"), dict):
            payload["unreconciled_usage"] = {}
        if not isinstance(payload.get("actual_usage_records"), list):
            payload["actual_usage_records"] = []
        # A reservation restored by a new process cannot still represent an
        # in-flight request. Preserve it conservatively as unreconciled usage
        # instead of silently releasing it or mislabelling it as actual usage.
        recovered = payload["pending_reservations"]
        if recovered:
            for key, row in recovered.items():
                payload["unreconciled_usage"].setdefault(key, {
                    **row,
                    "reason": "recovered reservation; provider usage was not recorded",
                    "response_id": None,
                })
            payload["pending_reservations"] = {}
            payload["schema_version"] = "1.1"
            write_json(path, payload)
        self.payload = payload

    @staticmethod
    def _reservation_totals(reservations: Any) -> tuple[int, int, float]:
        return (
            sum(int(row.get("input_tokens", 0)) for row in reservations),
            sum(int(row.get("output_tokens", 0)) for row in reservations),
            sum(float(row.get("estimated_cost_usd", 0)) for row in reservations),
        )

    def _pending_totals(self) -> tuple[int, int, float]:
        return self._reservation_totals(self.payload["pending_reservations"].values())

    def _unreconciled_totals(self) -> tuple[int, int, float]:
        return self._reservation_totals(self.payload["unreconciled_usage"].values())

    def reserve(
        self,
        key: str,
        input_tokens: int,
        output_tokens: int,
        input_price: float,
        output_price: float,
        limits: dict[str, Any],
    ) -> None:
        if key in self.payload["pending_reservations"] or key in self.payload["unreconciled_usage"]:
            raise RuntimeError("an unresolved budget reservation already exists for this event")
        pending_input, pending_output, pending_cost = self._pending_totals()
        unresolved_input, unresolved_output, unresolved_cost = self._unreconciled_totals()
        cost = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
        total_events = (
            int(self.payload.get("completed_events", 0))
            + len(self.payload["pending_reservations"])
            + len(self.payload["unreconciled_usage"])
            + 1
        )
        total_input = int(self.payload.get("actual_input_tokens", 0)) + pending_input + unresolved_input + input_tokens
        total_output = int(self.payload.get("actual_output_tokens", 0)) + pending_output + unresolved_output + output_tokens
        total_cost = float(self.payload.get("estimated_cost_usd", 0)) + pending_cost + unresolved_cost + cost
        if total_events > int(limits["events"]):
            raise RuntimeError("persistent daily event limit reached")
        if total_input > int(limits["input_tokens"]):
            raise RuntimeError("persistent daily input-token limit reached")
        if total_output > int(limits["output_tokens"]):
            raise RuntimeError("persistent daily output-token limit reached")
        if total_cost > float(limits["usd"]):
            raise RuntimeError("persistent daily USD limit would be exceeded")
        self.payload["pending_reservations"][key] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": round(cost, 8),
        }
        write_json(self.path, self.payload)

    def commit(
        self,
        key: str,
        actual_input: int,
        actual_output: int,
        input_price: float,
        output_price: float,
        response_id: str | None = None,
    ) -> None:
        if key not in self.payload["pending_reservations"]:
            raise RuntimeError("budget reservation is missing")
        self.payload["pending_reservations"].pop(key)
        self.payload["actual_input_tokens"] = int(self.payload.get("actual_input_tokens", 0)) + actual_input
        self.payload["actual_output_tokens"] = int(self.payload.get("actual_output_tokens", 0)) + actual_output
        call_cost = (actual_input * input_price + actual_output * output_price) / 1_000_000
        self.payload["estimated_cost_usd"] = round(
            float(self.payload.get("estimated_cost_usd", 0))
            + call_cost,
            8,
        )
        self.payload["completed_events"] = int(self.payload.get("completed_events", 0)) + 1
        records = self.payload.setdefault("actual_usage_records", [])
        records.append({
            "reservation_key": key,
            "response_id": response_id,
            "input_tokens": actual_input,
            "output_tokens": actual_output,
            "estimated_cost_usd": round(call_cost, 8),
        })
        self.payload["actual_usage_records"] = records[-500:]
        write_json(self.path, self.payload)

    def mark_usage_missing(self, key: str, response_id: str | None, reason: str) -> None:
        """Keep the worst-case reservation when a response omits token usage."""
        reservation = self.payload["pending_reservations"].pop(key, None)
        if reservation is None:
            raise RuntimeError("budget reservation is missing")
        self.payload["unreconciled_usage"][key] = {
            **reservation,
            "response_id": response_id,
            "reason": reason,
        }
        write_json(self.path, self.payload)

    def release(self, key: str) -> None:
        """Release a reservation only when the provider rejected before generation."""
        if self.payload["pending_reservations"].pop(key, None) is not None:
            write_json(self.path, self.payload)

    def report(self) -> dict[str, Any]:
        pending_input, pending_output, pending_cost = self._pending_totals()
        unresolved_input, unresolved_output, unresolved_cost = self._unreconciled_totals()
        actual = {
            "responses": int(self.payload.get("completed_events", 0)),
            "input_tokens": int(self.payload.get("actual_input_tokens", 0)),
            "output_tokens": int(self.payload.get("actual_output_tokens", 0)),
            "estimated_cost_usd": float(self.payload.get("estimated_cost_usd", 0)),
            "cost_basis": "actual token usage multiplied by configured token prices",
        }
        reservations = {
            "active": len(self.payload["pending_reservations"]),
            "input_tokens": pending_input,
            "output_tokens": pending_output,
            "estimated_cost_usd": round(pending_cost, 8),
        }
        unreconciled = {
            "responses": len(self.payload["unreconciled_usage"]),
            "reserved_input_tokens": unresolved_input,
            "reserved_output_tokens": unresolved_output,
            "worst_case_estimated_cost_usd": round(unresolved_cost, 8),
            "cost_basis": "pre-call reservation; not actual provider usage",
        }
        return {
            "day": self.day,
            "model": self.model,
            "actual_usage": actual,
            "pre_call_reservations": reservations,
            "unreconciled_usage": unreconciled,
            # Backward-compatible summary fields for existing diagnostics.
            "completed_events": actual["responses"],
            "actual_input_tokens": actual["input_tokens"],
            "actual_output_tokens": actual["output_tokens"],
            "estimated_cost_usd": actual["estimated_cost_usd"],
            "pending_reservations": len(self.payload["pending_reservations"]),
            "pending_input_tokens": pending_input,
            "pending_output_tokens": pending_output,
            "pending_estimated_cost_usd": round(pending_cost, 8),
        }

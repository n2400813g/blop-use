"""Aggregate run data into structured RunResult report."""
from __future__ import annotations

from blop.schemas import FailureCase


async def build_report(run: dict, cases: list[FailureCase]) -> dict:
    severity_counts: dict[str, int] = {
        "blocker": 0, "high": 0, "medium": 0, "low": 0, "none": 0, "pass": 0, "error": 0
    }
    for c in cases:
        if c.status == "pass":
            severity_counts["pass"] = severity_counts.get("pass", 0) + 1
        elif c.status in ("error", "blocked"):
            severity_counts["error"] = severity_counts.get("error", 0) + 1
        else:
            severity_counts[c.severity] = severity_counts.get(c.severity, 0) + 1

    failed = [c for c in cases if c.status in ("fail", "error", "blocked")]

    # Enrich case dicts with replay metadata
    cases_out = []
    for c in cases:
        d = c.model_dump()
        d["artifact_paths"] = c.screenshots
        cases_out.append(d)

    failed_out = []
    for c in failed:
        d = c.model_dump()
        d["artifact_paths"] = c.screenshots
        failed_out.append(d)

    return {
        "run_id": run.get("run_id", ""),
        "status": run.get("status", "unknown"),
        "started_at": run.get("started_at", ""),
        "completed_at": run.get("completed_at"),
        "cases": cases_out,
        "severity_counts": severity_counts,
        "failed_cases": failed_out,
        "artifacts_dir": run.get("artifacts_dir", ""),
        "run_mode": run.get("run_mode", "hybrid"),
        "next_actions": [],
    }

from __future__ import annotations

from vibeqa_mcp.reporting import results as reporting
from vibeqa_mcp.schemas import FailureCase
from vibeqa_mcp.storage import sqlite


async def get_test_results(run_id: str) -> dict:
    run = await sqlite.get_run(run_id)
    if not run:
        return {"error": f"Run {run_id} not found"}

    # Try run_cases table first, fall back to cases_json in runs
    cases = await sqlite.list_cases_for_run(run_id)
    if not cases and run.get("cases"):
        cases = [FailureCase(**c) for c in run["cases"]]

    return await reporting.build_report(run, cases)

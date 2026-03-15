from __future__ import annotations

from blop.engine import auth as auth_engine
from blop.engine import classifier, regression as regression_engine
from blop.schemas import DebugResult, FailureCase
from blop.storage import sqlite


async def debug_test_case(run_id: str, case_id: str) -> dict:
    run = await sqlite.get_run(run_id)
    if not run:
        return {"error": f"Run {run_id} not found"}

    # Find case
    cases = await sqlite.list_cases_for_run(run_id)
    if not cases and run.get("cases"):
        cases = [FailureCase(**c) for c in run["cases"]]

    case = next((c for c in cases if c.case_id == case_id), None)
    if not case:
        return {"error": f"Case {case_id} not found in run {run_id}"}

    flow = await sqlite.get_flow(case.flow_id)
    if not flow:
        return {"error": f"Flow {case.flow_id} not found"}

    profile_name = run.get("profile_name")
    storage_state = None
    if profile_name:
        profile = await sqlite.get_auth_profile(profile_name)
        if profile:
            storage_state = await auth_engine.resolve_storage_state(profile)

    new_case = await regression_engine.execute_flow(
        flow=flow,
        app_url=run["app_url"],
        run_id=run_id,
        case_id=case_id,
        storage_state=storage_state,
        headless=False,
        verbose=True,
    )
    new_case = await classifier.classify_case(new_case, run["app_url"])

    from blop.storage.files import console_log_path
    import os
    log_path = console_log_path(run_id, case_id)
    console_log = ""
    if os.path.exists(log_path):
        with open(log_path) as f:
            console_log = f.read()

    return DebugResult(
        case_id=new_case.case_id,
        run_id=run_id,
        status=new_case.status,
        screenshots=new_case.screenshots,
        console_log=console_log or "\n".join(new_case.console_errors),
        repro_steps=new_case.repro_steps,
    ).model_dump()

from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from vibeqa_mcp.engine import auth as auth_engine
from vibeqa_mcp.engine import classifier, regression as regression_engine
from vibeqa_mcp.schemas import RunStartedResult
from vibeqa_mcp.storage import sqlite, files as file_store


async def run_regression_test(
    app_url: str,
    flow_ids: list[str],
    profile_name: Optional[str] = None,
    headless: bool = True,
) -> dict:
    run_id = uuid.uuid4().hex

    profile = None
    if profile_name:
        profile = await sqlite.get_auth_profile(profile_name)

    storage_state: Optional[str] = None
    if profile:
        storage_state = await auth_engine.resolve_storage_state(profile)

    artifacts_dir = file_store.artifacts_dir(run_id)
    await sqlite.create_run(run_id, app_url, profile_name, flow_ids, headless, artifacts_dir)

    # Fire-and-forget; caller polls get_test_results
    asyncio.create_task(_run_and_persist(run_id, flow_ids, app_url, storage_state, headless))

    return RunStartedResult(
        run_id=run_id,
        status="running",
        flow_count=len(flow_ids),
        artifacts_dir=artifacts_dir,
    ).model_dump()


async def _run_and_persist(
    run_id: str,
    flow_ids: list[str],
    app_url: str,
    storage_state: Optional[str],
    headless: bool,
) -> None:
    from datetime import datetime, timezone

    try:
        flows = []
        for fid in flow_ids:
            flow = await sqlite.get_flow(fid)
            if flow:
                flows.append(flow)

        cases = await regression_engine.run_flows(
            flows=flows,
            app_url=app_url,
            run_id=run_id,
            storage_state=storage_state,
            headless=headless,
        )

        # Classify each case
        classified = []
        for case in cases:
            classified.append(await classifier.classify_case(case, app_url))
            await sqlite.save_case(case)

        completed_at = datetime.now(timezone.utc).isoformat()
        await sqlite.update_run(run_id, "completed", classified, completed_at)
    except Exception as e:
        await sqlite.update_run(run_id, "failed", [], None)

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from vibeqa_mcp.engine import auth as auth_engine
from vibeqa_mcp.engine import recording
from vibeqa_mcp.schemas import RecordedFlow, RecordedFlowResult
from vibeqa_mcp.storage import sqlite, files as file_store


async def record_test_flow(
    app_url: str,
    flow_name: str,
    goal: str,
    profile_name: Optional[str] = None,
) -> dict:
    profile = None
    if profile_name:
        profile = await sqlite.get_auth_profile(profile_name)

    storage_state: Optional[str] = None
    if profile:
        storage_state = await auth_engine.resolve_storage_state(profile)

    steps = await recording.record_flow(
        app_url=app_url,
        goal=goal,
        storage_state=storage_state,
        headless=False,
    )

    flow = RecordedFlow(
        flow_name=flow_name,
        app_url=app_url,
        goal=goal,
        steps=steps,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await sqlite.save_flow(flow)

    artifacts_dir = file_store.artifacts_dir(flow.flow_id)

    return RecordedFlowResult(
        flow_id=flow.flow_id,
        flow_name=flow_name,
        step_count=len(steps),
        status="recorded",
        artifacts_dir=artifacts_dir,
    ).model_dump()

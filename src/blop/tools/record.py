from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from blop.engine import auth as auth_engine
from blop.engine import recording
from blop.schemas import RecordedFlow, RecordedFlowResult
from blop.storage import sqlite, files as file_store


async def record_test_flow(
    app_url: str,
    flow_name: str,
    goal: str,
    profile_name: Optional[str] = None,
    command: Optional[str] = None,
    business_criticality: str = "other",
) -> dict:
    # If command provided, parse for additional intent context
    if command:
        from blop.engine.planner import parse_command
        intent = await parse_command(command, app_url, profile_name=profile_name)
        if intent.profile_name and not profile_name:
            profile_name = intent.profile_name

    profile = None
    if profile_name:
        profile = await sqlite.get_auth_profile(profile_name)

    storage_state: Optional[str] = None
    if profile:
        storage_state = await auth_engine.resolve_storage_state(profile)

    import uuid
    run_id = uuid.uuid4().hex

    steps = await recording.record_flow(
        app_url=app_url,
        goal=goal,
        storage_state=storage_state,
        headless=False,
        run_id=run_id,
    )

    # Collect assertion texts from assert steps
    assertions_json = [
        s.value or s.description
        for s in steps
        if s.action == "assert" and (s.value or s.description)
    ]

    valid_criticalities = {"revenue", "activation", "retention", "support", "other"}
    bc = business_criticality if business_criticality in valid_criticalities else "other"

    flow = RecordedFlow(
        flow_name=flow_name,
        app_url=app_url,
        goal=goal,
        steps=steps,
        created_at=datetime.now(timezone.utc).isoformat(),
        assertions_json=assertions_json,
        entry_url=app_url,
        business_criticality=bc,
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

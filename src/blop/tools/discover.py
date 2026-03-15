from __future__ import annotations

from typing import Optional

from blop.engine import discovery


async def discover_test_flows(
    app_url: str,
    repo_path: Optional[str] = None,
    profile_name: Optional[str] = None,
    business_goal: Optional[str] = None,
    command: Optional[str] = None,
    max_depth: int = 2,
) -> dict:
    # If command is provided, parse it for intent/priorities
    if command:
        from blop.engine.planner import parse_command
        intent = await parse_command(command, app_url, repo_path=repo_path, profile_name=profile_name)
        if intent.business_goal and not business_goal:
            business_goal = intent.business_goal
        if intent.max_depth != 2:
            max_depth = intent.max_depth
        if intent.profile_name and not profile_name:
            profile_name = intent.profile_name

    result = await discovery.discover_flows(
        app_url=app_url,
        repo_path=repo_path,
        profile_name=profile_name,
        business_goal=business_goal,
        max_depth=max_depth,
    )
    return result

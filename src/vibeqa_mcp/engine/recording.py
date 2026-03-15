"""Guided Browser-Use run that captures steps into RecordedFlow."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from vibeqa_mcp.schemas import FlowStep


async def record_flow(
    app_url: str,
    goal: str,
    storage_state: Optional[str],
    headless: bool = False,
) -> list[FlowStep]:
    """Run a Browser-Use agent for `goal` and capture each action as a FlowStep."""
    from browser_use import Agent, BrowserSession
    from browser_use.llm import ChatGoogle
    from vibeqa_mcp.engine.browser import make_browser_profile

    google_api_key = os.getenv("GOOGLE_API_KEY", "")
    llm = ChatGoogle(model="gemini-2.0-flash-exp", temperature=0.7, api_key=google_api_key)
    browser_profile = make_browser_profile(headless=headless, storage_state=storage_state)
    browser_session = BrowserSession(browser_profile=browser_profile)

    steps: list[FlowStep] = []
    step_counter = 0

    # Navigate first
    steps.append(FlowStep(
        step_id=step_counter,
        action="navigate",
        value=app_url,
        description=f"Navigate to {app_url}",
    ))
    step_counter += 1

    task = f"Navigate to {app_url} then: {goal}"

    try:
        agent = Agent(task=task, llm=llm, browser_session=browser_session, use_vision=True)
        history = await agent.run(max_steps=50)

        # Extract actions from history if available
        if hasattr(history, "model_actions"):
            for action in history.model_actions():
                action_name = type(action).__name__.lower() if action else "click"
                mapped = _map_action(action_name)
                if mapped:
                    steps.append(FlowStep(
                        step_id=step_counter,
                        action=mapped,
                        description=str(action)[:200] if action else "",
                    ))
                    step_counter += 1
    finally:
        try:
            await browser_session.aclose()
        except Exception:
            pass

    # Ensure at least the navigation step is there
    if len(steps) == 1:
        steps.append(FlowStep(
            step_id=step_counter,
            action="assert",
            description=goal,
        ))

    return steps


def _map_action(action_name: str) -> str | None:
    mapping = {
        "clickelement": "click",
        "inputtext": "fill",
        "navigate": "navigate",
        "selectoption": "select",
        "uploadfile": "upload",
        "dragdrop": "drag",
        "wait": "wait",
    }
    for key, val in mapping.items():
        if key in action_name:
            return val
    return "click"

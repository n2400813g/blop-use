"""Guided Browser-Use run that captures steps with selectors, screenshots, and assertions."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from blop.schemas import FlowStep


async def record_flow(
    app_url: str,
    goal: str,
    storage_state: Optional[str],
    headless: bool = False,
    run_id: Optional[str] = None,
) -> list[FlowStep]:
    """Run a Browser-Use agent for `goal`; capture each action with selector, target_text,
    dom_fingerprint, per-step screenshot, and final assertion steps."""
    from browser_use import Agent, BrowserSession
    from browser_use.llm import ChatGoogle
    from blop.engine.browser import make_browser_profile
    from blop.storage import files as file_store

    google_api_key = os.getenv("GOOGLE_API_KEY", "")
    llm = ChatGoogle(model="gemini-2.5-flash", temperature=0.7, api_key=google_api_key)
    browser_profile = make_browser_profile(headless=headless, storage_state=storage_state)
    browser_session = BrowserSession(browser_profile=browser_profile)

    recording_id = run_id or uuid.uuid4().hex
    steps: list[FlowStep] = []
    step_counter = 0

    # Initial navigation step
    steps.append(FlowStep(
        step_id=step_counter,
        action="navigate",
        value=app_url,
        description=f"Navigate to {app_url}",
        url_after=app_url,
    ))
    step_counter += 1

    task = f"Navigate to {app_url} then: {goal}"
    step_screenshots: list[str] = []
    screenshot_task: Optional[asyncio.Task] = None
    step_idx_counter = [0]

    async def _poll_screenshots():
        while True:
            try:
                await asyncio.sleep(3)
                ctx = getattr(browser_session, "context", None)
                if ctx and ctx.pages:
                    shot_path = file_store.screenshot_path(recording_id, "record", step_idx_counter[0])
                    await ctx.pages[0].screenshot(path=shot_path)
                    step_screenshots.append(shot_path)
                    step_idx_counter[0] += 1
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    try:
        agent = Agent(task=task, llm=llm, browser_session=browser_session, use_vision=True)
        screenshot_task = asyncio.create_task(_poll_screenshots())

        try:
            history = await agent.run(max_steps=50)
        finally:
            screenshot_task.cancel()
            try:
                await screenshot_task
            except asyncio.CancelledError:
                pass

        # Extract actions from history
        if hasattr(history, "model_actions"):
            for i, action in enumerate(history.model_actions()):
                action_name = type(action).__name__.lower() if action else "click"
                mapped = _map_action(action_name)
                if not mapped:
                    continue

                selector: Optional[str] = None
                value: Optional[str] = None
                target_text: Optional[str] = None
                url_before: Optional[str] = None
                url_after: Optional[str] = None

                # Extract structured fields from Browser-Use action objects
                if hasattr(action, "index") and action.index is not None:
                    selector = f"[data-browser-use-index='{action.index}']"
                if hasattr(action, "text") and action.text:
                    value = str(action.text)
                if hasattr(action, "url") and action.url:
                    value = str(action.url)
                    mapped = "navigate"
                    url_after = value

                desc = str(action)[:200] if action else ""
                target_text = _extract_target_text(desc)
                screenshot_path = step_screenshots[i] if i < len(step_screenshots) else None

                steps.append(FlowStep(
                    step_id=step_counter,
                    action=mapped,
                    selector=selector,
                    value=value,
                    description=desc,
                    target_text=target_text,
                    dom_fingerprint=_compute_fingerprint(mapped, selector, target_text, i),
                    url_before=url_before,
                    url_after=url_after,
                    screenshot_path=screenshot_path,
                ))
                step_counter += 1

        # Take final screenshot and generate assertion steps
        try:
            ctx = getattr(browser_session, "context", None)
            if ctx and ctx.pages:
                final_path = file_store.screenshot_path(recording_id, "record", 999)
                await ctx.pages[0].screenshot(path=final_path)

                assertions = await _generate_assertions_from_screenshot(ctx.pages[0], goal, google_api_key)
                for assertion_text in assertions:
                    steps.append(FlowStep(
                        step_id=step_counter,
                        action="assert",
                        description=assertion_text,
                        value=assertion_text,
                        screenshot_path=final_path,
                    ))
                    step_counter += 1
        except Exception:
            pass

    finally:
        try:
            await browser_session.aclose()
        except Exception:
            pass

    # Guarantee at least a navigation + assertion
    if len(steps) == 1:
        steps.append(FlowStep(
            step_id=step_counter,
            action="assert",
            description=goal,
            value=goal,
        ))

    return steps


async def _generate_assertions_from_screenshot(page, goal: str, google_api_key: str) -> list[str]:
    """Ask Gemini to generate 1-3 concrete assertions based on the final page screenshot."""
    if not google_api_key:
        return [f"Page shows expected content for: {goal}"]

    try:
        from browser_use.llm import ChatGoogle
        from browser_use.llm.messages import UserMessage

        img_bytes = await page.screenshot()
        b64 = base64.b64encode(img_bytes).decode()

        llm = ChatGoogle(model="gemini-2.5-flash", api_key=google_api_key, temperature=0.1)
        prompt = f"""Look at this screenshot of a web page after completing: "{goal}"

Generate 1-3 specific, verifiable assertions about what should be visible.
Return ONLY a JSON array of assertion strings:
["assertion 1", "assertion 2"]

Good examples:
- "User dashboard is visible with a welcome message"
- "Success confirmation message is displayed"
- "The form shows a thank-you page after submission"
- "Navigation menu shows authenticated user options"
"""
        response = await llm.ainvoke([UserMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ])])
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r"\[.*?\]", text, re.DOTALL)
        if m:
            result = json.loads(m.group())
            if isinstance(result, list) and result:
                return [str(a) for a in result[:3]]
    except Exception:
        pass

    return [f"Page shows expected content for: {goal}"]


def _extract_target_text(description: str) -> Optional[str]:
    """Pull the most likely visible label from an action description string."""
    m = re.search(r"['\"](.+?)['\"]", description)
    if m:
        return m.group(1)[:100]
    words = description.split()[:6]
    text = " ".join(words)
    return text[:100] if text else None


def _compute_fingerprint(action: str, selector: Optional[str], target_text: Optional[str], index: int) -> str:
    content = f"{action}|{selector or ''}|{target_text or ''}|{index}"
    return hashlib.md5(content.encode()).hexdigest()[:12]


def _map_action(action_name: str) -> Optional[str]:
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

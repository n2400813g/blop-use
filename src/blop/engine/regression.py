"""Flow replay engine — ported and extended from vibetest/runner.py."""
from __future__ import annotations

import asyncio
import os
from typing import Optional

from blop.schemas import FailureCase, RecordedFlow
from blop.storage import files as file_store


async def execute_flow(
    flow: RecordedFlow,
    app_url: str,
    run_id: str,
    case_id: str,
    storage_state: Optional[str],
    headless: bool = True,
    verbose: bool = False,
    max_steps: int = 50,
) -> FailureCase:
    """Replay a RecordedFlow, capturing screenshots/console/network evidence."""
    from browser_use import Agent, BrowserSession
    from browser_use.llm import ChatGoogle
    from blop.engine.browser import make_browser_profile

    google_api_key = os.getenv("GOOGLE_API_KEY", "dummy_key")
    run_headless = False if verbose else headless

    browser_profile = make_browser_profile(headless=run_headless, storage_state=storage_state)
    browser_session = BrowserSession(browser_profile=browser_profile)

    console_errors: list[str] = []
    network_errors: list[str] = []
    screenshots: list[str] = []
    raw_result = ""
    status = "error"

    try:
        llm = ChatGoogle(model="gemini-2.0-flash-exp", temperature=0.7, api_key=google_api_key)
        task = f"Navigate to {app_url} then: {flow.goal}"
        agent = Agent(task=task, llm=llm, browser_session=browser_session, use_vision=True)

        screenshot_task: Optional[asyncio.Task] = None
        step_idx = 0

        async def _poll_screenshots():
            nonlocal step_idx
            while True:
                try:
                    await asyncio.sleep(2)
                    ctx = getattr(browser_session, "context", None)
                    if ctx and ctx.pages:
                        shot_path = file_store.screenshot_path(run_id, case_id, step_idx)
                        await ctx.pages[0].screenshot(path=shot_path)
                        screenshots.append(shot_path)
                        step_idx += 1
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass

        if verbose:
            screenshot_task = asyncio.create_task(_poll_screenshots())

        try:
            history = await agent.run(max_steps=max_steps)
            raw_result = (
                str(history.final_result()) if hasattr(history, "final_result") else str(history)
            )
            lower = raw_result.lower()
            if any(w in lower for w in ("error", "fail", "broken", "exception", "crash", "404", "500")):
                status = "fail"
            else:
                status = "pass"
        finally:
            if screenshot_task:
                screenshot_task.cancel()
                try:
                    await screenshot_task
                except asyncio.CancelledError:
                    pass

        # Final screenshot
        try:
            ctx = getattr(browser_session, "context", None)
            if ctx and ctx.pages:
                final_path = file_store.screenshot_path(run_id, case_id, step_idx)
                await ctx.pages[0].screenshot(path=final_path)
                screenshots.append(final_path)
        except Exception:
            pass

        # Write console log
        if console_errors:
            log_path = file_store.console_log_path(run_id, case_id)
            with open(log_path, "w") as f:
                f.write("\n".join(console_errors))

    except Exception as e:
        raw_result = str(e)
        status = "error"
    finally:
        try:
            await browser_session.aclose()
        except Exception:
            pass

    return FailureCase(
        case_id=case_id,
        run_id=run_id,
        flow_id=flow.flow_id,
        flow_name=flow.flow_name,
        status=status,
        severity="none",
        repro_steps=[],
        console_errors=console_errors,
        network_errors=network_errors,
        screenshots=screenshots,
        raw_result=raw_result,
    )


async def run_flows(
    flows: list[RecordedFlow],
    app_url: str,
    run_id: str,
    storage_state: Optional[str],
    headless: bool,
    max_steps: int = 50,
) -> list[FailureCase]:
    """Execute all flows in parallel (semaphore=5)."""
    import uuid

    semaphore = asyncio.Semaphore(5)

    async def run_one(flow: RecordedFlow) -> FailureCase:
        async with semaphore:
            case_id = uuid.uuid4().hex
            return await execute_flow(
                flow=flow,
                app_url=app_url,
                run_id=run_id,
                case_id=case_id,
                storage_state=storage_state,
                headless=headless,
                max_steps=max_steps,
            )

    results = await asyncio.gather(*[run_one(f) for f in flows], return_exceptions=True)

    cases: list[FailureCase] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            cases.append(FailureCase(
                run_id=run_id,
                flow_id=flows[i].flow_id if i < len(flows) else "unknown",
                flow_name=flows[i].flow_name if i < len(flows) else "unknown",
                status="error",
                raw_result=str(result),
            ))
        else:
            cases.append(result)

    return cases

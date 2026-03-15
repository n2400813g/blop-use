"""Flow replay engine — step-by-step hybrid replay with agent repair fallback."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from typing import Optional, TYPE_CHECKING

from blop.schemas import FailureCase, RecordedFlow, ReplayStepResult, ReplayTrace
from blop.storage import files as file_store

if TYPE_CHECKING:
    from playwright.async_api import Page


# ---------------------------------------------------------------------------
# Hybrid step-by-step executor
# ---------------------------------------------------------------------------

async def execute_recorded_flow(
    flow: RecordedFlow,
    run_id: str,
    case_id: str,
    storage_state: Optional[str],
    headless: bool = True,
    run_mode: str = "hybrid",
) -> FailureCase:
    """Replay a RecordedFlow step-by-step; repair broken selectors before falling back to agent."""
    from playwright.async_api import async_playwright
    from blop.engine.browser import make_browser_profile

    trace = ReplayTrace(
        flow_id=flow.flow_id,
        flow_name=flow.flow_name,
        run_mode="strict_steps",
    )

    browser_profile = make_browser_profile(headless=headless, storage_state=storage_state)

    async with async_playwright() as p:
        launch_kwargs = {
            "headless": browser_profile.headless,
            "args": browser_profile.browser_args if hasattr(browser_profile, "browser_args") else [],
        }
        browser = await p.chromium.launch(**{k: v for k, v in launch_kwargs.items() if v or k == "headless"})

        ctx_kwargs: dict = {}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        context = await browser.new_context(**ctx_kwargs)

        # Capture console errors
        page = await context.new_page()
        page.on("console", lambda msg: trace.console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("response", lambda resp: trace.network_errors.append(f"{resp.status} {resp.url}")
                 if resp.status >= 500 else None)

        try:
            unrecoverable = False

            for step_idx, step in enumerate(flow.steps):
                if step.action == "assert":
                    # Evaluate assertion via vision
                    from blop.engine.vision import assert_by_vision
                    passed = await assert_by_vision(page, step.value or step.description)
                    trace.assertion_results.append({
                        "assertion": step.value or step.description,
                        "passed": passed,
                    })
                    if not passed:
                        trace.assertion_results[-1]["failed"] = True

                    step_result = ReplayStepResult(
                        step_id=step.step_id,
                        action="assert",
                        status="pass" if passed else "fail",
                        replay_mode="selector",
                    )
                    trace.step_results.append(step_result)
                    continue

                step_result = await _execute_single_step(
                    page=page,
                    step=step,
                    step_idx=step_idx,
                    run_id=run_id,
                    case_id=case_id,
                    run_mode=run_mode,
                    trace=trace,
                )
                trace.step_results.append(step_result)

                if step_result.status == "fail":
                    if trace.step_failure_index is None:
                        trace.step_failure_index = step_idx
                    if run_mode == "strict_steps":
                        unrecoverable = True
                        break

                if step_result.screenshot_path:
                    trace.screenshots.append(step_result.screenshot_path)

            # Take final screenshot
            try:
                final_path = file_store.screenshot_path(run_id, case_id, 999)
                await page.screenshot(path=final_path)
                trace.screenshots.append(final_path)
            except Exception:
                pass

        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    return _trace_to_failure_case(trace, flow, run_id, case_id)


async def _execute_single_step(
    page: "Page",
    step,
    step_idx: int,
    run_id: str,
    case_id: str,
    run_mode: str,
    trace: ReplayTrace,
) -> ReplayStepResult:
    """Try selector → text lookup → vision repair → agent repair for one step."""
    action = step.action
    selector = step.selector
    value = step.value
    target_text = step.target_text

    # 1. Navigate steps
    if action == "navigate":
        try:
            nav_url = value or step.description
            await page.goto(nav_url, wait_until="networkidle", timeout=15000)
            shot = _take_step_screenshot(page, run_id, case_id, step_idx)
            return ReplayStepResult(
                step_id=step.step_id, action=action, status="pass",
                replay_mode="selector", screenshot_path=await shot,
            )
        except Exception as e:
            return ReplayStepResult(
                step_id=step.step_id, action=action, status="fail",
                replay_mode="selector", error=str(e),
            )

    # 2. Try CSS selector
    if selector:
        try:
            el = await page.wait_for_selector(selector, timeout=5000)
            if el:
                if action == "click":
                    await el.click()
                elif action == "fill" and value:
                    await el.fill(value)
                elif action == "select" and value:
                    await el.select_option(value)
                shot = await _take_step_screenshot(page, run_id, case_id, step_idx)
                return ReplayStepResult(
                    step_id=step.step_id, action=action, status="pass",
                    replay_mode="selector", screenshot_path=shot,
                )
        except Exception:
            pass

    # 3. Text-based lookup
    if target_text:
        try:
            el = page.get_by_text(target_text, exact=False)
            count = await el.count()
            if count > 0:
                first = el.first
                if action == "click":
                    await first.click()
                elif action == "fill" and value:
                    await first.fill(value)
                shot = await _take_step_screenshot(page, run_id, case_id, step_idx)
                return ReplayStepResult(
                    step_id=step.step_id, action=action, status="pass",
                    replay_mode="text_lookup", screenshot_path=shot,
                )
        except Exception:
            pass

    # 4. Hybrid repair via agent
    if run_mode in ("hybrid", "explore"):
        trace.run_mode = "hybrid_repair"
        repair_result = await repair_step_with_agent(step, page)
        if repair_result:
            repaired_selector = repair_result.get("repaired_selector")
            repaired_value = repair_result.get("repaired_value", value)
            repaired_action = repair_result.get("repaired_action", action)
            try:
                if repaired_selector and repaired_action == "click":
                    el = await page.wait_for_selector(repaired_selector, timeout=5000)
                    if el:
                        await el.click()
                elif repaired_selector and repaired_action == "fill" and repaired_value:
                    el = await page.wait_for_selector(repaired_selector, timeout=5000)
                    if el:
                        await el.fill(repaired_value)
                else:
                    # Fall back to vision click using target_text or description
                    from blop.engine.vision import click_by_vision
                    desc = target_text or step.description
                    await click_by_vision(page, desc)

                shot = await _take_step_screenshot(page, run_id, case_id, step_idx)
                return ReplayStepResult(
                    step_id=step.step_id, action=action, status="repaired",
                    replay_mode="agent_repair", screenshot_path=shot,
                )
            except Exception as e:
                return ReplayStepResult(
                    step_id=step.step_id, action=action, status="fail",
                    replay_mode="agent_repair", error=str(e),
                )

    return ReplayStepResult(
        step_id=step.step_id, action=action, status="fail",
        replay_mode="selector", error="No selector, text, or repair succeeded",
    )


async def repair_step_with_agent(step, page: "Page") -> Optional[dict]:
    """Send REPAIR_STEP_PROMPT + screenshot to Gemini; return repaired action dict."""
    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        return None

    from blop.prompts import REPAIR_STEP_PROMPT

    try:
        from browser_use.llm import ChatGoogle
        from browser_use.llm.messages import UserMessage

        img_bytes = await page.screenshot()
        b64 = base64.b64encode(img_bytes).decode()
        current_url = page.url

        prompt = REPAIR_STEP_PROMPT.format(
            action=step.action,
            selector=step.selector or "none",
            target_text=step.target_text or "none",
            description=step.description,
            current_url=current_url,
        )

        llm = ChatGoogle(model="gemini-2.5-flash", api_key=google_api_key, temperature=0.2)
        response = await llm.ainvoke([UserMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ])])
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass

    return None


async def _take_step_screenshot(page: "Page", run_id: str, case_id: str, step_idx: int) -> Optional[str]:
    try:
        path = file_store.screenshot_path(run_id, case_id, step_idx)
        await page.screenshot(path=path)
        return path
    except Exception:
        return None


def _trace_to_failure_case(
    trace: ReplayTrace,
    flow: RecordedFlow,
    run_id: str,
    case_id: str,
) -> FailureCase:
    """Convert a ReplayTrace to a FailureCase."""
    assertion_failures = [
        r["assertion"] for r in trace.assertion_results if not r.get("passed", True)
    ]
    failed_steps = [r for r in trace.step_results if r.status == "fail"]

    if trace.network_errors:
        status: str = "fail"
    elif assertion_failures or failed_steps:
        status = "fail"
    else:
        status = "pass"

    # Detect auth-blocked scenarios
    auth_kws = ("401", "403", "unauthorized", "forbidden", "login required")
    if any(kw in trace.raw_result.lower() or any(kw in e.lower() for e in trace.console_errors)
           for kw in auth_kws):
        status = "blocked"

    repro: list[str] = []
    for r in trace.step_results:
        if r.status in ("fail",):
            repro.append(f"Step {r.step_id} ({r.action}) failed via {r.replay_mode}: {r.error or 'unknown'}")

    return FailureCase(
        case_id=case_id,
        run_id=run_id,
        flow_id=flow.flow_id,
        flow_name=flow.flow_name,
        status=status,
        severity="none",
        repro_steps=repro,
        console_errors=trace.console_errors[:20],
        network_errors=trace.network_errors[:20],
        screenshots=trace.screenshots,
        raw_result=trace.raw_result,
        replay_mode=trace.run_mode,
        step_failure_index=trace.step_failure_index,
        assertion_failures=assertion_failures,
        assertion_results=trace.assertion_results,
    )


# ---------------------------------------------------------------------------
# Goal-replay fallback (original behaviour, kept for goal_fallback mode)
# ---------------------------------------------------------------------------

async def execute_flow(
    flow: RecordedFlow,
    app_url: str,
    run_id: str,
    case_id: str,
    storage_state: Optional[str],
    headless: bool = True,
    verbose: bool = False,
    max_steps: int = 50,
    run_mode: str = "hybrid",
) -> FailureCase:
    """Replay a RecordedFlow.

    Uses hybrid step-by-step replay by default (run_mode='hybrid').
    Falls back to full goal-replay agent when run_mode='goal_fallback'.
    """
    if run_mode != "goal_fallback" and flow.steps:
        return await execute_recorded_flow(
            flow=flow,
            run_id=run_id,
            case_id=case_id,
            storage_state=storage_state,
            headless=False if verbose else headless,
            run_mode=run_mode,
        )

    return await _goal_fallback(
        flow=flow,
        app_url=app_url,
        run_id=run_id,
        case_id=case_id,
        storage_state=storage_state,
        headless=headless,
        verbose=verbose,
        max_steps=max_steps,
    )


async def _goal_fallback(
    flow: RecordedFlow,
    app_url: str,
    run_id: str,
    case_id: str,
    storage_state: Optional[str],
    headless: bool,
    verbose: bool,
    max_steps: int,
) -> FailureCase:
    """Original goal-based agent replay."""
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
        llm = ChatGoogle(model="gemini-2.5-flash", temperature=0.7, api_key=google_api_key)
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

        try:
            ctx = getattr(browser_session, "context", None)
            if ctx and ctx.pages:
                final_path = file_store.screenshot_path(run_id, case_id, step_idx)
                await ctx.pages[0].screenshot(path=final_path)
                screenshots.append(final_path)
        except Exception:
            pass

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
        replay_mode="goal_fallback",
    )


async def run_flows(
    flows: list[RecordedFlow],
    app_url: str,
    run_id: str,
    storage_state: Optional[str],
    headless: bool,
    max_steps: int = 50,
    run_mode: str = "hybrid",
) -> list[FailureCase]:
    """Execute all flows in parallel (semaphore=5)."""
    import uuid as _uuid

    semaphore = asyncio.Semaphore(5)

    async def run_one(flow: RecordedFlow) -> FailureCase:
        async with semaphore:
            cid = _uuid.uuid4().hex
            return await execute_flow(
                flow=flow,
                app_url=app_url,
                run_id=run_id,
                case_id=cid,
                storage_state=storage_state,
                headless=headless,
                max_steps=max_steps,
                run_mode=run_mode,
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
                replay_mode="goal_fallback",
            ))
        else:
            cases.append(result)

    return cases

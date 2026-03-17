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

        # Start Playwright tracing for debugging artifacts
        trace_zip = file_store.trace_path(run_id, case_id)
        tracing_enabled = False
        try:
            await context.tracing.start(screenshots=True, snapshots=True, sources=False)
            tracing_enabled = True
        except Exception:
            pass

        # Capture console errors
        page = await context.new_page()
        page.on("console", lambda msg: trace.console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("response", lambda resp: trace.network_errors.append(f"{resp.status} {resp.url}")
                 if resp.status >= 500 else None)

        try:
            unrecoverable = False
            deferred_asserts: list[tuple[int, object]] = []  # (step_idx, step) for assert steps

            for step_idx, step in enumerate(flow.steps):
                if step.action == "assert":
                    deferred_asserts.append((step_idx, step))
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

            # Tiered assertion evaluation: deterministic first, vision batch for semantic only
            if deferred_asserts:
                assertion_eval_results = await _evaluate_assertions(page, deferred_asserts)
                for result in assertion_eval_results:
                    step_obj = result["step"]
                    passed = result["passed"]
                    eval_type = result["eval_type"]
                    trace.assertion_results.append({
                        "assertion": step_obj.value or step_obj.description,
                        "passed": passed,
                        "eval_type": eval_type,
                        **({"failed": True} if not passed else {}),
                    })
                    trace.step_results.append(ReplayStepResult(
                        step_id=step_obj.step_id,
                        action="assert",
                        status="pass" if passed else "fail",
                        replay_mode=eval_type,
                    ))

            # Take final screenshot
            try:
                final_path = file_store.screenshot_path(run_id, case_id, 999)
                await page.screenshot(path=final_path)
                trace.screenshots.append(final_path)
            except Exception:
                pass

        finally:
            if tracing_enabled:
                try:
                    await context.tracing.stop(path=trace_zip)
                    trace.trace_path = trace_zip
                except Exception:
                    pass
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
    """6-tier fallback: testid → aria_role → by_label → CSS → text → agent repair."""
    action = step.action
    selector = step.selector
    value = step.value
    target_text = step.target_text

    # Tier 0: Navigate steps
    if action == "navigate":
        try:
            nav_url = value or step.description
            await page.goto(nav_url, wait_until="networkidle", timeout=15000)
            shot = await _take_step_screenshot(page, run_id, case_id, step_idx)
            return ReplayStepResult(
                step_id=step.step_id, action=action, status="pass",
                replay_mode="selector", screenshot_path=shot,
            )
        except Exception as e:
            return ReplayStepResult(
                step_id=step.step_id, action=action, status="fail",
                replay_mode="selector", error=str(e),
            )

    # Tier 1: data-testid selector (most stable)
    testid_sel = getattr(step, "testid_selector", None)
    if testid_sel:
        try:
            el = await page.wait_for_selector(testid_sel, timeout=4000)
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
                    replay_mode="testid", screenshot_path=shot,
                )
        except Exception:
            pass

    # Tier 2: ARIA role + name
    aria_role = getattr(step, "aria_role", None)
    aria_name = getattr(step, "aria_name", None)
    if aria_role and aria_name:
        try:
            loc = page.get_by_role(aria_role, name=aria_name)
            if await loc.count() > 0:
                first = loc.first
                if action == "click":
                    await first.click()
                elif action == "fill" and value:
                    await first.fill(value)
                elif action == "select" and value:
                    await first.select_option(value)
                shot = await _take_step_screenshot(page, run_id, case_id, step_idx)
                return ReplayStepResult(
                    step_id=step.step_id, action=action, status="pass",
                    replay_mode="aria_role", screenshot_path=shot,
                )
        except Exception:
            pass

    # Tier 3: by-label (fill actions only)
    label_text = getattr(step, "label_text", None)
    if action == "fill" and label_text and value:
        try:
            loc = page.get_by_label(label_text, exact=False)
            if await loc.count() > 0:
                await loc.first.fill(value)
                shot = await _take_step_screenshot(page, run_id, case_id, step_idx)
                return ReplayStepResult(
                    step_id=step.step_id, action=action, status="pass",
                    replay_mode="by_label", screenshot_path=shot,
                )
        except Exception:
            pass

    # Tier 4: CSS selector
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

    # Tier 5: Text-based lookup
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

    # Tier 6: Hybrid repair via agent (ARIA-enhanced)
    if run_mode in ("hybrid", "explore"):
        trace.run_mode = "hybrid_repair"
        repair_result = await repair_step_with_agent(step, page)
        if repair_result:
            locator_type = repair_result.get("repaired_locator_type", "css")
            repaired_selector = repair_result.get("repaired_selector")
            repaired_role = repair_result.get("repaired_role")
            repaired_name = repair_result.get("repaired_name")
            repaired_value = repair_result.get("repaired_value", value)
            repaired_action = repair_result.get("repaired_action", action)
            try:
                el = None
                if locator_type == "role" and repaired_role and repaired_name:
                    loc = page.get_by_role(repaired_role, name=repaired_name)
                    if await loc.count() > 0:
                        el = loc.first
                elif locator_type == "label" and repaired_name:
                    loc = page.get_by_label(repaired_name, exact=False)
                    if await loc.count() > 0:
                        el = loc.first
                elif locator_type == "text" and repaired_name:
                    loc = page.get_by_text(repaired_name, exact=False)
                    if await loc.count() > 0:
                        el = loc.first
                elif repaired_selector:
                    el = await page.wait_for_selector(repaired_selector, timeout=5000)

                if el:
                    if repaired_action == "click":
                        await el.click()
                    elif repaired_action == "fill" and repaired_value:
                        await el.fill(repaired_value)
                else:
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
    """Send REPAIR_STEP_PROMPT + ARIA context + screenshot to Gemini; return repaired action dict."""
    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        return None

    from blop.prompts import REPAIR_STEP_PROMPT

    try:
        from browser_use.llm import ChatGoogle
        from browser_use.llm.messages import UserMessage

        # Capture ARIA tree for context-efficient repair
        aria_tree = ""
        try:
            snapshot = await page.accessibility.snapshot(interesting_only=True)
            if snapshot:
                nodes = _extract_interactive_nodes_flat(snapshot, max_nodes=30)
                if nodes:
                    aria_tree = json.dumps(nodes, separators=(",", ":"))
        except Exception:
            pass

        # Use cheaper model when ARIA context is available (selection task, not vision)
        model = "gemini-1.5-flash" if aria_tree else "gemini-2.5-flash"

        img_bytes = await page.screenshot(type="jpeg", quality=85)
        b64 = base64.b64encode(img_bytes).decode()
        current_url = page.url

        aria_section = f"\nAvailable interactive elements (ARIA):\n{aria_tree}\n" if aria_tree else ""

        prompt = REPAIR_STEP_PROMPT.format(
            action=step.action,
            selector=step.selector or "none",
            target_text=step.target_text or "none",
            description=step.description,
            current_url=current_url,
            aria_section=aria_section,
        )

        llm = ChatGoogle(model=model, api_key=google_api_key, temperature=0.2, max_output_tokens=300)
        response = await llm.ainvoke([UserMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ])])
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass

    return None


def _extract_interactive_nodes_flat(node: dict, max_nodes: int = 30, _count: Optional[list] = None) -> list[dict]:
    """Flatten an ARIA snapshot into a compact list of interactive nodes for repair context."""
    if _count is None:
        _count = [0]
    interactive_roles = {
        "button", "link", "textbox", "checkbox", "radio", "combobox",
        "listbox", "menuitem", "tab", "switch", "searchbox", "spinbutton",
    }
    results = []
    role = node.get("role", "")
    name = node.get("name", "")
    if role in interactive_roles and name:
        if _count[0] < max_nodes:
            entry: dict = {"role": role, "name": name}
            if node.get("disabled"):
                entry["disabled"] = True
            results.append(entry)
            _count[0] += 1
    for child in node.get("children", []):
        if _count[0] >= max_nodes:
            break
        results.extend(_extract_interactive_nodes_flat(child, max_nodes, _count))
    return results


async def _take_step_screenshot(page: "Page", run_id: str, case_id: str, step_idx: int) -> Optional[str]:
    try:
        path = file_store.screenshot_path(run_id, case_id, step_idx)
        await page.screenshot(path=path)
        return path
    except Exception:
        return None


async def _evaluate_assertions(page: "Page", deferred_asserts: list) -> list[dict]:
    """Evaluate assertions using deterministic checks where possible, vision batch for semantic."""
    results = []
    semantic_batch: list[tuple] = []  # (step_obj, text)

    for _step_idx, step_obj in deferred_asserts:
        sa = getattr(step_obj, "structured_assertion", None)
        text = step_obj.value or step_obj.description

        if sa is None or sa.assertion_type == "semantic":
            semantic_batch.append((step_obj, text))
            continue

        try:
            passed = await _eval_deterministic(page, sa)
        except Exception:
            # Deterministic eval failed — fall back to vision
            semantic_batch.append((step_obj, text))
            continue

        results.append({"step": step_obj, "passed": passed, "eval_type": sa.assertion_type})

    # Batch evaluate remaining semantic assertions in a single vision call
    if semantic_batch:
        from blop.engine.vision import assert_all_by_vision
        texts = [t for _, t in semantic_batch]
        try:
            vision_results = await assert_all_by_vision(page, texts)
        except Exception:
            vision_results = [False] * len(texts)
        for (step_obj, _), passed in zip(semantic_batch, vision_results):
            results.append({"step": step_obj, "passed": passed, "eval_type": "vision_batch"})

    return results


async def _eval_deterministic(page: "Page", sa) -> bool:
    """Evaluate a non-semantic StructuredAssertion without an LLM call."""
    t = sa.assertion_type
    target = sa.target
    expected = sa.expected

    if t == "text_present":
        if target:
            try:
                el_text = await page.locator(target).first.text_content(timeout=3000)
                result = (expected or "") in (el_text or "")
            except Exception:
                # Target not found — check full body text
                body = await page.evaluate("() => document.body.innerText")
                result = (expected or "") in (body or "")
        else:
            body = await page.evaluate("() => document.body.innerText")
            result = (expected or "") in (body or "")

    elif t == "element_visible":
        if target:
            result = await page.locator(target).first.is_visible(timeout=3000)
        else:
            result = False

    elif t == "url_contains":
        result = (expected or "") in page.url

    elif t == "page_title":
        title = await page.title()
        result = (expected or "") in title

    elif t == "count":
        if target and expected:
            count = await page.locator(target).count()
            try:
                result = count == int(expected)
            except ValueError:
                result = False
        else:
            result = False

    else:
        raise ValueError(f"Unknown deterministic type: {t}")

    return (not result) if sa.negated else result


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
        trace_path=trace.trace_path,
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
        # Append login hint so agent can authenticate if session cookies are stale
        _username = os.getenv("TEST_USERNAME", "")
        _password = os.getenv("TEST_PASSWORD", "")
        _login_url = os.getenv("LOGIN_URL", "") or os.getenv("TEST_AUTH_URL", "")
        if _username and _password and _login_url:
            task += (
                f"\n\nIf you encounter a login page, use these credentials: "
                f"email={_username} password={_password} (login URL: {_login_url}). "
                f"Do NOT create a new account — log in with the provided credentials."
            )
        agent = Agent(task=task, llm=llm, browser_session=browser_session, use_vision=True)

        screenshot_task: Optional[asyncio.Task] = None
        step_idx = 0

        async def _poll_screenshots():
            nonlocal step_idx
            while True:
                try:
                    await asyncio.sleep(3)
                    shot_path = file_store.screenshot_path(run_id, case_id, step_idx)
                    await browser_session.take_screenshot(path=shot_path)
                    screenshots.append(shot_path)
                    step_idx += 1
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass

        # Always capture screenshots — provides evidence the agent is doing real work
        screenshot_task = asyncio.create_task(_poll_screenshots())

        goal_trace_path: Optional[str] = None
        try:
            history = await agent.run(max_steps=max_steps)

            # Guaranteed final screenshot after agent completes
            try:
                final_path = file_store.screenshot_path(run_id, case_id, 999)
                await browser_session.take_screenshot(path=final_path)
                if final_path not in screenshots:
                    screenshots.append(final_path)
            except Exception:
                pass

            # Prefer done-action text over final_result() which only returns ExtractAction content
            # model_dump() includes ALL action type keys (most None) — find the non-None one
            raw_result = ""
            done_success = True
            if hasattr(history, "model_actions"):
                for action in reversed(history.model_actions()):
                    done_val = action.get("done")
                    if done_val is not None:
                        if isinstance(done_val, dict):
                            raw_result = str(done_val.get("text") or done_val)
                            done_success = bool(done_val.get("success", True))
                        else:
                            raw_result = str(done_val)
                        break
            if not raw_result:
                raw_result = str(history.final_result()) if hasattr(history, "final_result") else str(history)

            # Trust the agent's done_success boolean as the primary signal.
            # Additionally catch hard browser-level failures (404, error pages)
            # that the agent may not detect — e.g. logout redirecting to a 404.
            final_url = ""
            final_page_text = ""
            try:
                final_url = await browser_session.get_current_page_url() or ""
                page = await browser_session.get_current_page()
                if page:
                    final_page_text = (await page.inner_text("body") or "").lower()[:500]
            except Exception:
                pass

            _hard_fail = (
                "404" in final_page_text
                or "page not found" in final_page_text
                or "did you forget to add the page" in final_page_text
                or "500" in final_page_text
                or "internal server error" in final_page_text
            )

            if not done_success or _hard_fail:
                if _hard_fail and done_success:
                    raw_result += f" [browser ended on error page: {final_url}]"
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
        trace_path=goal_trace_path,
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

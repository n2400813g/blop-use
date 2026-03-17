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

from blop.schemas import FlowStep, StructuredAssertion


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

        # Get the active page reference for ARIA/testid extraction
        page_ref = None
        try:
            ctx = getattr(browser_session, "context", None)
            if ctx and ctx.pages:
                page_ref = ctx.pages[0]
        except Exception:
            pass

        # Extract actions from history
        if hasattr(history, "model_actions"):
            for i, action in enumerate(history.model_actions()):
                selector: Optional[str] = None
                value: Optional[str] = None
                target_text: Optional[str] = None
                url_before: Optional[str] = None
                url_after: Optional[str] = None

                # model_actions() returns list[dict] with ALL action keys (most None).
                # Find the non-None key to get the actual action type.
                if isinstance(action, dict):
                    action_name = next(
                        (k for k, v in action.items() if k != "interacted_element" and v is not None),
                        "click"
                    )
                    params = action.get(action_name) or {}
                    interacted = action.get("interacted_element")

                    idx = params.get("index") if isinstance(params, dict) else None
                    if idx is not None:
                        selector = f"[data-browser-use-index='{idx}']"
                    if isinstance(params, dict) and "text" in params:
                        value = str(params["text"])
                    if isinstance(params, dict) and "url" in params:
                        value = str(params["url"])
                        url_after = value

                    # Prefer xpath from interacted element as selector
                    interacted_xpath: Optional[str] = None
                    if interacted is not None:
                        try:
                            interacted_xpath = interacted.xpath if hasattr(interacted, "xpath") else None
                            if interacted_xpath:
                                selector = interacted_xpath
                            elem_text = (
                                interacted.get_meaningful_text_for_llm()
                                if hasattr(interacted, "get_meaningful_text_for_llm")
                                else None
                            )
                            if elem_text:
                                target_text = elem_text[:100]
                        except Exception:
                            pass

                    desc = str(action)[:200]
                    if not target_text:
                        target_text = _extract_target_text(desc)
                else:
                    # Fallback for typed action objects (older browser-use versions)
                    action_name = type(action).__name__.lower() if action else "click"
                    interacted_xpath = None
                    if hasattr(action, "index") and action.index is not None:
                        selector = f"[data-browser-use-index='{action.index}']"
                    if hasattr(action, "text") and action.text:
                        value = str(action.text)
                    if hasattr(action, "url") and action.url:
                        value = str(action.url)
                        url_after = value
                    desc = str(action)[:200] if action else ""
                    target_text = _extract_target_text(desc)

                mapped = _map_action(action_name)
                if not mapped:
                    continue
                screenshot_path = step_screenshots[i] if i < len(step_screenshots) else None

                # Capture semantic locators (ARIA role/name, testid, label)
                aria_role: Optional[str] = None
                aria_name: Optional[str] = None
                aria_snapshot: Optional[str] = None
                testid_selector: Optional[str] = None
                label_text: Optional[str] = None

                if page_ref is not None and mapped != "navigate":
                    aria_role, aria_name, aria_snapshot = await _capture_aria_for_element(
                        page_ref, target_text
                    )
                    if interacted_xpath:
                        testid_selector, label_text = await _capture_locator_attrs(
                            page_ref, interacted_xpath, mapped
                        )

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
                    aria_role=aria_role,
                    aria_name=aria_name,
                    aria_snapshot=aria_snapshot,
                    testid_selector=testid_selector,
                    label_text=label_text,
                ))
                step_counter += 1

        # Take final screenshot and generate assertion steps
        try:
            ctx = getattr(browser_session, "context", None)
            if ctx and ctx.pages:
                final_page = ctx.pages[0]
                final_path = file_store.screenshot_path(recording_id, "record", 999)
                await final_page.screenshot(path=final_path)

                # Capture ARIA context for richer assertion generation
                aria_context = await _get_page_aria_context(final_page)

                assertion_steps = await _generate_assertions_from_screenshot(
                    final_page, goal, google_api_key, aria_context=aria_context
                )
                for assertion_text, structured in assertion_steps:
                    steps.append(FlowStep(
                        step_id=step_counter,
                        action="assert",
                        description=assertion_text,
                        value=assertion_text,
                        screenshot_path=final_path,
                        structured_assertion=structured,
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


async def _generate_assertions_from_screenshot(
    page,
    goal: str,
    google_api_key: str,
    aria_context: str = "",
) -> list[tuple[str, Optional[StructuredAssertion]]]:
    """Ask Gemini to generate 1-3 structured assertions based on the final page screenshot.

    Returns a list of (assertion_text, StructuredAssertion | None) tuples.
    Falls back to plain-string assertions if structured parsing fails.
    """
    if not google_api_key:
        return [(f"Page shows expected content for: {goal}", None)]

    try:
        from browser_use.llm import ChatGoogle
        from browser_use.llm.messages import UserMessage

        img_bytes = await page.screenshot(type="jpeg", quality=85)
        b64 = base64.b64encode(img_bytes).decode()

        aria_section = f"\nARIA tree of final page state:\n{aria_context}\n" if aria_context else ""

        llm = ChatGoogle(model="gemini-2.5-flash", api_key=google_api_key, temperature=0.1, max_output_tokens=600)
        prompt = f"""Look at this screenshot of a web page after completing: "{goal}"
{aria_section}
Generate 1-3 specific, verifiable assertions about what should be visible.

Return ONLY a JSON array where each item has these fields:
- type: one of "text_present" | "element_visible" | "url_contains" | "page_title" | "semantic"
- target: CSS selector, ARIA label, or URL substring (null for semantic)
- expected: expected text/value (null if not applicable)
- description: plain English assertion string

Prefer deterministic types (text_present, element_visible, url_contains) over "semantic" when possible.
Use "semantic" only for assertions requiring visual judgment.

Example:
[
  {{"type": "text_present", "target": "h1", "expected": "Dashboard", "description": "Dashboard heading is visible"}},
  {{"type": "url_contains", "target": null, "expected": "/dashboard", "description": "URL contains /dashboard"}}
]
"""
        response = await llm.ainvoke([UserMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ])])
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r"\[.*?\]", text, re.DOTALL)
        if m:
            raw_list = json.loads(m.group())
            if isinstance(raw_list, list) and raw_list:
                results = []
                for item in raw_list[:3]:
                    if isinstance(item, str):
                        results.append((item, None))
                    elif isinstance(item, dict):
                        desc = item.get("description") or item.get("expected") or str(item)
                        try:
                            sa = StructuredAssertion(
                                assertion_type=item.get("type", "semantic"),
                                target=item.get("target"),
                                expected=item.get("expected"),
                                description=desc,
                            )
                        except Exception:
                            sa = None
                        results.append((desc, sa))
                return results
    except Exception:
        pass

    return [(f"Page shows expected content for: {goal}", None)]


async def _get_page_aria_context(page) -> str:
    """Return a compact ARIA tree string of the page's interactive elements (max 40 nodes)."""
    try:
        snapshot = await page.accessibility.snapshot(interesting_only=True)
        if not snapshot:
            return ""
        nodes = _extract_interactive_nodes(snapshot, max_nodes=40)
        return json.dumps(nodes, separators=(",", ":"))
    except Exception:
        return ""


async def _capture_aria_for_element(
    page, target_text: Optional[str]
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (aria_role, aria_name, aria_snapshot_json) for the element matching target_text."""
    if not target_text:
        return None, None, None
    try:
        snapshot = await page.accessibility.snapshot(interesting_only=True)
        if not snapshot:
            return None, None, None
        node = _find_aria_node(snapshot, target_text)
        if node:
            role = node.get("role")
            name = node.get("name")
            # Compact subtree at depth 2
            sub = _serialize_aria_node(node, depth=0, max_depth=2)
            return role, name, json.dumps(sub, separators=(",", ":"))
    except Exception:
        pass
    return None, None, None


async def _capture_locator_attrs(
    page, xpath: str, action: str
) -> tuple[Optional[str], Optional[str]]:
    """Extract data-testid and label text via JavaScript for stable locators."""
    testid_selector: Optional[str] = None
    label_text: Optional[str] = None
    try:
        result = await page.evaluate(
            """(xpath) => {
                try {
                    const el = document.evaluate(
                        xpath, document, null,
                        XPathResult.FIRST_ORDERED_NODE_TYPE, null
                    ).singleNodeValue;
                    if (!el) return null;
                    const testid = el.getAttribute('data-testid') ||
                                   el.getAttribute('data-cy') ||
                                   el.getAttribute('data-test');
                    let label = null;
                    if (el.getAttribute('aria-label')) {
                        label = el.getAttribute('aria-label');
                    } else if (el.getAttribute('placeholder')) {
                        label = el.getAttribute('placeholder');
                    } else if (el.id) {
                        const lbl = document.querySelector('label[for="' + el.id + '"]');
                        if (lbl) label = lbl.textContent.trim();
                    }
                    return {testid: testid, label: label};
                } catch(e) { return null; }
            }""",
            xpath,
        )
        if result:
            if result.get("testid"):
                testid_val = str(result["testid"]).replace("'", "\\'")
                testid_selector = f"[data-testid='{testid_val}']"
            if action == "fill" and result.get("label"):
                label_text = str(result["label"])[:100]
    except Exception:
        pass
    return testid_selector, label_text


def _find_aria_node(node: dict, target_text: str) -> Optional[dict]:
    """DFS search for an ARIA node whose name contains target_text (case-insensitive)."""
    name = (node.get("name") or "").lower()
    if target_text.lower() in name:
        return node
    for child in node.get("children", []):
        found = _find_aria_node(child, target_text)
        if found:
            return found
    return None


def _serialize_aria_node(node: dict, depth: int, max_depth: int) -> dict:
    """Serialize an ARIA node tree to a compact dict (bounded depth)."""
    out: dict = {}
    for key in ("role", "name", "value", "checked", "level", "disabled"):
        if node.get(key) is not None:
            out[key] = node[key]
    if depth < max_depth:
        children = [
            _serialize_aria_node(c, depth + 1, max_depth)
            for c in node.get("children", [])
            if c.get("role") not in ("generic", "none", "presentation")
        ]
        if children:
            out["children"] = children
    return out


def _extract_interactive_nodes(node: dict, max_nodes: int = 40, _count: Optional[list] = None) -> list[dict]:
    """Flatten the ARIA tree into a list of interactive leaf nodes."""
    if _count is None:
        _count = [0]
    interactive_roles = {
        "button", "link", "textbox", "checkbox", "radio", "combobox",
        "listbox", "menuitem", "tab", "switch", "searchbox", "spinbutton",
    }
    results = []
    role = node.get("role", "")
    if role in interactive_roles and node.get("name"):
        if _count[0] < max_nodes:
            results.append({"role": role, "name": node["name"]})
            _count[0] += 1
    for child in node.get("children", []):
        if _count[0] >= max_nodes:
            break
        results.extend(_extract_interactive_nodes(child, max_nodes, _count))
    return results


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
    name = action_name.lower().replace("_", "")
    # Actions to skip (no browser interaction to replay)
    skip = {"done", "extractpagecontent", "extract", "screenshot", "saveaspdf", "searchpage", "findelements"}
    if name in skip:
        return None
    mapping = {
        "clickelement": "click",
        "click": "click",
        "inputtext": "fill",
        "input": "fill",
        "sendkeys": "fill",
        "navigate": "navigate",
        "gotourl": "navigate",
        "goback": "navigate",
        "searchgoogle": "navigate",
        "selectdropdownoption": "select",
        "selectoption": "select",
        "uploadfile": "upload",
        "dragdrop": "drag",
        "wait": "wait",
        "scroll": "scroll",
        "switchtab": "navigate",
    }
    for key, val in mapping.items():
        if key in name:
            return val
    return "click"

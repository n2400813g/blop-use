"""Page scan + Gemini → candidate test flows. Ported from vibetest/planner.py."""
from __future__ import annotations

import json
import os
import re
from typing import Optional


async def discover_flows(app_url: str, repo_path: Optional[str] = None) -> list[dict]:
    """Return 3-8 flow dicts with {flow_name, goal, likely_assertions}."""
    if repo_path:
        raw_flows = await _flows_from_repo(repo_path, app_url)
    else:
        inventory = await _scan_page(app_url)
        raw_flows = await _flows_from_inventory(inventory, app_url)

    # Clamp to 3-8
    raw_flows = raw_flows[:8]
    if len(raw_flows) < 3:
        raw_flows += _fallback_flows(app_url)[: 3 - len(raw_flows)]

    return raw_flows


async def _scan_page(app_url: str) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(app_url, wait_until="networkidle", timeout=15000)
        except Exception:
            await page.goto(app_url, timeout=15000)

        buttons = await page.evaluate(
            """() => Array.from(document.querySelectorAll('button, [role="button"]'))
                .map(el => ({text: el.textContent.trim().slice(0,120), id: el.id}))
                .filter(el => el.text).slice(0,20)"""
        )
        links = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                .map(el => ({text: el.textContent.trim().slice(0,120), href: el.href}))
                .filter(el => el.text).slice(0,20)"""
        )
        forms = await page.evaluate(
            """() => Array.from(document.querySelectorAll('form'))
                .map(form => ({
                    action: form.action,
                    inputs: Array.from(form.querySelectorAll('input, textarea, select'))
                        .map(el => ({type: el.type, name: el.name, placeholder: el.placeholder}))
                })).slice(0,5)"""
        )
        routes = await page.evaluate(
            """() => [...new Set(Array.from(document.querySelectorAll('a[href]'))
                .map(el => { try { return new URL(el.href).pathname; } catch(e) { return null; } })
                .filter(p => p && p !== '/'))].slice(0,20)"""
        )
        await browser.close()

    return {"buttons": buttons, "links": links, "forms": forms, "routes": routes}


async def _flows_from_inventory(inventory: dict, app_url: str) -> list[dict]:
    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        return _fallback_flows(app_url)

    from browser_use.llm import ChatGoogle
    from browser_use.llm.messages import UserMessage

    llm = ChatGoogle(model="gemini-1.5-flash", api_key=google_api_key, temperature=0.7)
    inventory_text = json.dumps(
        {k: inventory.get(k, []) for k in ("buttons", "links", "forms", "routes")},
        indent=2,
    )

    prompt = f"""Given this page element inventory from {app_url}:

{inventory_text}

Generate 5-8 browser test flows. For each flow return:
- flow_name: short name (snake_case)
- goal: one-sentence plain-English goal
- likely_assertions: list of 1-3 things to verify

Return only a JSON array, no other text:
[{{"flow_name": "...", "goal": "...", "likely_assertions": ["...", "..."]}}]"""

    response = await llm.ainvoke([UserMessage(content=prompt)])
    text = str(response.content) if hasattr(response, "content") else str(response)
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass

    return _fallback_flows(app_url)


async def _flows_from_repo(repo_path: str, app_url: str) -> list[dict]:
    import glob as glob_module

    patterns = [
        os.path.join(repo_path, "pages/**/*.tsx"),
        os.path.join(repo_path, "app/**/page.tsx"),
        os.path.join(repo_path, "src/**/*.tsx"),
    ]
    files: list[str] = []
    for pat in patterns:
        files.extend(glob_module.glob(pat, recursive=True))
    if not files:
        files = glob_module.glob(os.path.join(repo_path, "**/*.{ts,tsx,js,jsx}"), recursive=True)[:30]

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not files or not google_api_key:
        return _fallback_flows(app_url)

    from browser_use.llm import ChatGoogle
    from browser_use.llm.messages import UserMessage

    llm = ChatGoogle(model="gemini-1.5-flash", api_key=google_api_key, temperature=0.7)
    file_list = "\n".join(files[:50])

    prompt = f"""Based on these source files for {app_url}:
{file_list}

Generate 5-8 browser test flows as JSON:
[{{"flow_name": "...", "goal": "...", "likely_assertions": ["..."]}}]"""

    response = await llm.ainvoke([UserMessage(content=prompt)])
    text = str(response.content) if hasattr(response, "content") else str(response)
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass

    return _fallback_flows(app_url)


def _fallback_flows(app_url: str) -> list[dict]:
    return [
        {"flow_name": "page_loads", "goal": f"Navigate to {app_url} and verify the page loads", "likely_assertions": ["page title visible", "no 404 error"]},
        {"flow_name": "nav_links", "goal": f"Check all navigation links on {app_url} are functional", "likely_assertions": ["links respond", "no broken pages"]},
        {"flow_name": "forms_work", "goal": f"Test any forms or input fields on {app_url}", "likely_assertions": ["form submits", "validation works"]},
    ]

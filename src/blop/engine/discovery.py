"""Inventory-first discovery: BFS crawl → Gemini planning → quality gate."""
from __future__ import annotations

import json
import os
import re
from typing import Optional
from urllib.parse import urlparse

from blop.schemas import SiteInventory


async def inventory_site(
    app_url: str,
    max_depth: int = 2,
    same_origin_only: bool = True,
    profile_name: Optional[str] = None,
) -> SiteInventory:
    """BFS crawl up to depth max_depth; extract buttons, links, forms, headings, and signals."""
    from playwright.async_api import async_playwright

    base_origin = urlparse(app_url).netloc
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(app_url, 0)]

    all_buttons: list[dict] = []
    all_links: list[dict] = []
    all_forms: list[dict] = []
    all_headings: list[str] = []
    all_routes: set[str] = set()
    auth_signals: list[str] = []
    business_signals: list[str] = []
    crawled_pages = 0

    storage_state: Optional[str] = None
    if profile_name:
        try:
            from blop.storage.sqlite import get_auth_profile
            from blop.engine.auth import resolve_storage_state
            profile = await get_auth_profile(profile_name)
            if profile:
                storage_state = await resolve_storage_state(profile)
        except Exception:
            pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx_kwargs: dict = {}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        context = await browser.new_context(**ctx_kwargs)

        while queue and crawled_pages < 10:
            url, depth = queue.pop(0)
            if url in visited:
                continue
            if same_origin_only and urlparse(url).netloc != base_origin:
                continue
            visited.add(url)

            try:
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="networkidle", timeout=15000)
                except Exception:
                    try:
                        await page.goto(url, timeout=15000)
                    except Exception:
                        await page.close()
                        continue

                crawled_pages += 1

                page_buttons = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('button, [role="button"], a.btn, .cta, [class*="btn"]'))
                        .map(el => ({text: el.textContent.trim().slice(0,120), id: el.id, href: el.getAttribute('href') || null}))
                        .filter(el => el.text).slice(0,25)"""
                )
                page_links = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('a[href]'))
                        .map(el => ({text: el.textContent.trim().slice(0,120), href: el.href}))
                        .filter(el => el.text && !el.href.startsWith('mailto:') && !el.href.startsWith('tel:'))
                        .slice(0,35)"""
                )
                page_forms = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('form'))
                        .map(form => ({
                            action: form.action,
                            inputs: Array.from(form.querySelectorAll('input, textarea, select'))
                                .map(el => ({type: el.type, name: el.name, placeholder: el.placeholder, label: el.getAttribute('aria-label') || ''}))
                        })).slice(0,6)"""
                )
                page_headings = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('h1, h2, h3'))
                        .map(el => el.textContent.trim().slice(0,100))
                        .filter(t => t).slice(0,10)"""
                )
                page_routes = await page.evaluate(
                    """() => [...new Set(Array.from(document.querySelectorAll('a[href]'))
                        .map(el => { try { return new URL(el.href).pathname; } catch(e) { return null; } })
                        .filter(p => p && p !== '/'))].slice(0,30)"""
                )

                all_buttons.extend(page_buttons)
                all_links.extend(page_links)
                all_forms.extend(page_forms)
                all_headings.extend(page_headings)
                for route in page_routes:
                    all_routes.add(route)

                # Detect auth and business signals from text
                page_text_lower = " ".join(
                    [b.get("text", "") for b in page_buttons]
                    + [l.get("text", "") for l in page_links]
                    + page_headings
                ).lower()

                for signal in ("sign in", "login", "log in", "sign up", "register", "logout",
                               "dashboard", "/auth", "/login", "/signup", "get started", "create account"):
                    if signal in page_text_lower and signal not in auth_signals:
                        auth_signals.append(signal)

                for signal in ("pricing", "contact", "integration", "oauth", "checkout",
                               "payment", "subscribe", "onboarding", "demo", "trial", "plans"):
                    if signal in page_text_lower and signal not in business_signals:
                        business_signals.append(signal)

                # Also check routes for signals
                routes_text = " ".join(page_routes).lower()
                for signal in ("/pricing", "/contact", "/login", "/signup", "/auth", "/checkout", "/demo"):
                    if signal in routes_text and signal not in business_signals + auth_signals:
                        if signal in ("/login", "/signup", "/auth"):
                            if signal not in auth_signals:
                                auth_signals.append(signal)
                        else:
                            if signal not in business_signals:
                                business_signals.append(signal)

                # Queue child links for deeper crawl
                if depth < max_depth:
                    for link in page_links:
                        href = link.get("href", "")
                        if href and href.startswith("http"):
                            if urlparse(href).netloc == base_origin and href not in visited:
                                queue.append((href, depth + 1))

                await page.close()
            except Exception:
                pass

        await browser.close()

    return SiteInventory(
        app_url=app_url,
        routes=sorted(list(all_routes))[:30],
        buttons=all_buttons[:30],
        links=all_links[:40],
        forms=all_forms[:10],
        headings=list(dict.fromkeys(all_headings))[:20],
        auth_signals=auth_signals,
        business_signals=business_signals,
        crawled_pages=crawled_pages,
    )


async def plan_flows_from_inventory(
    inventory: SiteInventory,
    repo_context: Optional[str] = None,
    business_goal: Optional[str] = None,
) -> list[dict]:
    """Send inventory to Gemini with DISCOVER_PROMPT and return typed flows."""
    from blop.prompts import DISCOVER_PROMPT

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        return _fallback_flows(inventory.app_url)

    from browser_use.llm import ChatGoogle
    from browser_use.llm.messages import UserMessage

    llm = ChatGoogle(model="gemini-2.5-flash", api_key=google_api_key, temperature=0.7, max_output_tokens=2000)
    inventory_text = json.dumps(inventory.to_dict(), separators=(",", ":"))

    extra_context = ""
    if business_goal:
        extra_context += f"\nBusiness goal to prioritize: {business_goal}"
    if repo_context:
        extra_context += f"\nRepo context: {repo_context[:500]}"

    prompt = DISCOVER_PROMPT.format(
        app_url=inventory.app_url,
        inventory_text=inventory_text,
        extra_context=extra_context,
    )

    try:
        response = await llm.ainvoke([UserMessage(content=prompt)])
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            flows = json.loads(m.group())
            required_keys = {"flow_name", "goal"}
            valid_flows = []
            for f in flows:
                if isinstance(f, dict) and required_keys.issubset(f.keys()):
                    f.setdefault("starting_url", inventory.app_url)
                    f.setdefault("preconditions", [])
                    f.setdefault("likely_assertions", [])
                    f.setdefault("severity_if_broken", "medium")
                    f.setdefault("confidence", 0.7)
                    f.setdefault("business_criticality", "other")
                    valid_flows.append(f)
            if valid_flows:
                return valid_flows
    except Exception:
        pass

    return _fallback_flows(inventory.app_url)


def quality_gate_flows(inventory: SiteInventory, flows: list[dict]) -> tuple[bool, list[str]]:
    """Check that flows are specific and cover key signals. Returns (passed, warnings)."""
    warnings: list[str] = []

    generic_names = {"page_loads", "nav_links", "forms_work"}
    flow_names = {f.get("flow_name", "") for f in flows}

    if flow_names.issubset(generic_names):
        warnings.append("All flows are generic fallbacks; inventory scan may have returned no rich signals")
        return False, warnings

    # Auth signals must produce an auth flow
    if inventory.auth_signals:
        auth_kws = {"login", "auth", "signin", "signup", "register", "sign_in", "sign_up"}
        has_auth_flow = any(
            any(kw in f.get("flow_name", "").lower() or kw in f.get("goal", "").lower()
                for kw in auth_kws)
            for f in flows
        )
        if not has_auth_flow:
            warnings.append(
                f"Auth signals detected ({inventory.auth_signals[:3]}) but no auth flow proposed"
            )

    # Confidence gate
    confidences = [f.get("confidence", 0.5) for f in flows]
    if all(c < 0.4 for c in confidences):
        warnings.append("All flows have low confidence (< 0.4)")
        return False, warnings

    return True, warnings


async def discover_flows(
    app_url: str,
    repo_path: Optional[str] = None,
    profile_name: Optional[str] = None,
    business_goal: Optional[str] = None,
    max_depth: int = 2,
) -> dict:
    """Crawl site, plan flows, quality-gate, return rich discovery result."""
    inventory = await inventory_site(
        app_url,
        max_depth=max_depth,
        profile_name=profile_name,
    )

    if repo_path:
        flows = await _flows_from_repo(repo_path, app_url, inventory, business_goal)
    else:
        flows = await plan_flows_from_inventory(inventory, business_goal=business_goal)

    # Clamp to 3-8 flows
    flows = flows[:8]
    if len(flows) < 3:
        flows += _fallback_flows(app_url)[: 3 - len(flows)]

    passed, warnings = quality_gate_flows(inventory, flows)

    return {
        "app_url": app_url,
        "inventory_summary": {
            "routes_found": len(inventory.routes),
            "auth_signals": inventory.auth_signals,
            "business_signals": inventory.business_signals,
            "crawled_pages": inventory.crawled_pages,
        },
        "flows": flows,
        "flow_count": len(flows),
        "quality": {"passed": passed, "warnings": warnings},
    }


async def _flows_from_repo(
    repo_path: str,
    app_url: str,
    inventory: SiteInventory,
    business_goal: Optional[str] = None,
) -> list[dict]:
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
        return await plan_flows_from_inventory(inventory, business_goal=business_goal)

    from browser_use.llm import ChatGoogle
    from browser_use.llm.messages import UserMessage

    llm = ChatGoogle(model="gemini-2.5-flash", api_key=google_api_key, temperature=0.7, max_output_tokens=2000)
    file_list = "\n".join(files[:50])
    extra = f"\nBusiness goal: {business_goal}" if business_goal else ""

    prompt = f"""Based on these source files for {app_url}:{extra}
{file_list}

Generate 5-8 browser test flows as JSON with keys:
flow_name, goal, starting_url, preconditions, likely_assertions, severity_if_broken, confidence, business_criticality

business_criticality must be one of: revenue, activation, retention, support, other

Return only a JSON array:
[{{"flow_name": "...", "goal": "...", "starting_url": "...", "preconditions": [], "likely_assertions": ["..."], "severity_if_broken": "high", "confidence": 0.8, "business_criticality": "revenue"}}]"""

    try:
        response = await llm.ainvoke([UserMessage(content=prompt)])
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            flows = json.loads(m.group())
            valid = []
            for f in flows:
                if isinstance(f, dict) and {"flow_name", "goal"}.issubset(f.keys()):
                    f.setdefault("starting_url", app_url)
                    f.setdefault("preconditions", [])
                    f.setdefault("likely_assertions", [])
                    f.setdefault("severity_if_broken", "medium")
                    f.setdefault("confidence", 0.7)
                    f.setdefault("business_criticality", "other")
                    valid.append(f)
            if valid:
                return valid
    except Exception:
        pass

    return await plan_flows_from_inventory(inventory, business_goal=business_goal)


def _fallback_flows(app_url: str) -> list[dict]:
    return [
        {
            "flow_name": "page_loads",
            "goal": f"Navigate to {app_url} and verify the page loads",
            "starting_url": app_url,
            "preconditions": [],
            "likely_assertions": ["page title visible", "no 404 error"],
            "severity_if_broken": "blocker",
            "confidence": 0.3,
        },
        {
            "flow_name": "nav_links",
            "goal": f"Check all navigation links on {app_url} are functional",
            "starting_url": app_url,
            "preconditions": [],
            "likely_assertions": ["links respond", "no broken pages"],
            "severity_if_broken": "medium",
            "confidence": 0.3,
        },
        {
            "flow_name": "forms_work",
            "goal": f"Test any forms or input fields on {app_url}",
            "starting_url": app_url,
            "preconditions": [],
            "likely_assertions": ["form submits", "validation works"],
            "severity_if_broken": "medium",
            "confidence": 0.3,
        },
    ]

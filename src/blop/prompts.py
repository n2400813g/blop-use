"""Centralised prompt templates for all LLM calls."""
from __future__ import annotations

DISCOVER_PROMPT = """You are a senior QA engineer generating browser test flows for a web application.

Application URL: {app_url}

Page inventory (from a depth-2 crawl):
{inventory_text}
{extra_context}

Generate 5-8 meaningful browser test flows. Each flow must test a real user journey, not a generic check.

Rules:
- Use named routes, CTAs, auth links, pricing, contact, onboarding, and integrations as signals
- If auth signals exist (sign in, login, dashboard), include at least one auth flow
- If pricing or contact routes exist, include flows for those
- REJECT generic flows like "page_loads", "nav_links", or "forms_work" unless no richer signal exists
- Each flow must have a concrete, observable outcome

For each flow return:
- flow_name: short snake_case identifier (e.g. "user_login", "checkout_flow")
- goal: one-sentence plain-English user goal
- starting_url: the URL where this flow begins
- preconditions: list of setup requirements (e.g. ["user is logged in"])
- likely_assertions: list of 1-3 specific, verifiable assertions
- severity_if_broken: "blocker" | "high" | "medium" | "low"
- confidence: float 0.0-1.0 representing how confident you are this flow exists

Return ONLY a JSON array, no other text:
[{{"flow_name": "...", "goal": "...", "starting_url": "...", "preconditions": [], "likely_assertions": ["..."], "severity_if_broken": "high", "confidence": 0.85}}]"""


REPAIR_STEP_PROMPT = """You are a browser automation expert repairing a broken test step.

The following test step failed to execute:
- Action: {action}
- Original selector: {selector}
- Target text: {target_text}
- Step description: {description}
- Current URL: {current_url}

The current page screenshot is attached.

Please provide:
1. A repaired action that will accomplish the same goal using what you can see on the page
2. A verification assertion to confirm the step succeeded

Return ONLY a JSON object:
{{
  "repaired_selector": "...",
  "repaired_action": "click|fill|navigate",
  "repaired_value": "...",
  "verification_assertion": "..."
}}

If the element is not visible on screen, set repaired_selector to null."""


NEXT_ACTIONS_PROMPT = """You are a QA engineer explaining a test failure in plain English.

Test flow: {flow_name}
Goal: {goal}
Step that failed: Step {step_index} — {step_description}
Failure mode: {replay_mode}
Assertion failures: {assertion_failures}
Console errors: {console_errors}

Explain in 2-3 sentences:
1. What went wrong
2. Why this matters to the user
3. The most likely fix

Then provide 3 concrete, actionable fix suggestions.

Return ONLY a JSON object:
{{
  "why_failed": "...",
  "next_actions": ["Fix 1", "Fix 2", "Fix 3"]
}}"""

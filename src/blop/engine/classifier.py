"""Severity labelling and next-actions — ported from vibetest/classifier.py."""
from __future__ import annotations

import json
import os
import re

from blop.schemas import FailureCase


async def classify_case(case: FailureCase, url: str) -> FailureCase:
    """Assign severity and repro_steps via Gemini. Returns updated case."""
    if case.status == "pass":
        case.severity = "none"
        return case

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        case.severity = "medium" if case.status == "fail" else "high"
        return case

    from browser_use.llm import ChatGoogle
    from browser_use.llm.messages import UserMessage

    llm = ChatGoogle(model="gemini-1.5-flash", api_key=google_api_key, temperature=0.1)
    console_text = "\n".join(case.console_errors[:10]) or "none"
    network_text = "\n".join(case.network_errors[:10]) or "none"

    prompt = f"""You are a QA analyst reviewing a browser test result for {url}.

Test flow: "{case.flow_name}"
Goal: {case.flow_name}
Status: {case.status}
Result: {case.raw_result[:2000]}
Console errors: {console_text}
Network errors: {network_text}

Severity levels:
- blocker: Complete feature failure, prevents core user workflow
- high: Major functionality broken, significant user impact
- medium: Partial functionality issue, workaround exists
- low: Minor issue, cosmetic or edge case
- none: No real issue found

Return JSON only:
{{
  "severity": "blocker|high|medium|low|none",
  "repro_steps": ["step 1", "step 2", ...],
  "summary": "one-line description"
}}"""

    try:
        response = await llm.ainvoke([UserMessage(content=prompt)])
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            result = json.loads(m.group())
            case.severity = result.get("severity", "medium")
            case.repro_steps = result.get("repro_steps", [])
    except Exception:
        case.severity = "medium" if case.status == "fail" else "high"

    return case


async def classify_run(cases: list[FailureCase], url: str) -> dict:
    """Aggregate classified cases and generate next_actions."""
    failed = [c for c in cases if c.status in ("fail", "error")]
    next_actions: list[str] = []

    if failed and os.getenv("GOOGLE_API_KEY"):
        next_actions = await _generate_next_actions(failed, url)

    severity_counts: dict[str, int] = {"blocker": 0, "high": 0, "medium": 0, "low": 0, "none": 0, "pass": 0, "error": 0}
    for c in cases:
        if c.status == "pass":
            severity_counts["pass"] = severity_counts.get("pass", 0) + 1
        elif c.status == "error":
            severity_counts["error"] = severity_counts.get("error", 0) + 1
        else:
            severity_counts[c.severity] = severity_counts.get(c.severity, 0) + 1

    return {
        "severity_counts": severity_counts,
        "next_actions": next_actions,
        "failed_cases": [c.model_dump() for c in failed],
    }


async def _generate_next_actions(failed_cases: list[FailureCase], url: str) -> list[str]:
    from browser_use.llm import ChatGoogle
    from browser_use.llm.messages import UserMessage

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        return []

    llm = ChatGoogle(model="gemini-1.5-flash", api_key=google_api_key, temperature=0.3)
    failures_text = "\n".join(
        f"- Flow: {c.flow_name}\n  Severity: {c.severity}\n  Result: {c.raw_result[:300]}"
        for c in failed_cases[:5]
    )

    prompt = f"""Given these test failures for {url}:

{failures_text}

List 3 concrete fix actions. Return only a JSON array:
["Fix action 1", "Fix action 2", "Fix action 3"]"""

    try:
        response = await llm.ainvoke([UserMessage(content=prompt)])
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass

    return [
        "Review browser console errors for JavaScript failures",
        "Check network requests for failing API calls",
        "Verify authentication and session handling",
    ]

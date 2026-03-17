"""Severity labelling: deterministic rules first, Gemini LLM fallback."""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from blop.schemas import FailureCase


# ---------------------------------------------------------------------------
# Deterministic severity scoring
# ---------------------------------------------------------------------------

_REVENUE_ACTIVATION = {"revenue", "activation"}


def classify_failure_deterministic(case: FailureCase) -> Optional[str]:
    """Return severity string without calling LLM, or None to fall through to Gemini.

    Priority order (assertion-first):
    1. Explicit assertion failure on revenue/activation flow → blocker
    2. Explicit assertion failure on any flow → high
    3. Auth block (401/403/auth keywords) → blocker
    4. 5xx HTTP → blocker
    5. Uncaught JS crash → blocker
    6. Step failure on revenue/activation flow → high
    7. Any step failure → medium
    """
    if case.status == "pass":
        return "none"

    if case.status == "blocked":
        return "blocker"

    bc = getattr(case, "business_criticality", "other") or "other"
    assertion_failures = case.assertion_failures or []

    # 1. Explicit assertion failure on revenue/activation flow
    if assertion_failures and bc in _REVENUE_ACTIVATION:
        return "blocker"

    # 2. Explicit assertion failure on any flow
    if assertion_failures:
        return "high"

    # 3. Auth failure (401/403 in network errors or console)
    auth_failure = any(
        any(kw in err for kw in ("401", "403", "unauthorized", "forbidden"))
        for err in case.network_errors + case.console_errors
    )
    if auth_failure:
        return "blocker"

    # 4. HTTP 5xx
    network_5xx = any(
        err.startswith(("5", "500", "502", "503", "504"))
        for err in case.network_errors
    )
    if network_5xx:
        return "blocker"

    # 5. Uncaught JS crash
    js_crash = any(
        any(kw in err.lower() for kw in ("uncaught", "typeerror", "referenceerror", "syntaxerror", "crash"))
        for err in case.console_errors
    )
    if js_crash:
        return "blocker"

    # 6. Step failure on revenue/activation flow
    if case.status in ("fail", "error") and bc in _REVENUE_ACTIVATION:
        return "high"

    # 404 on critical routes
    network_404 = any(
        err.startswith("404") and any(kw in err for kw in ("/pricing", "/contact", "/checkout", "/payment"))
        for err in case.network_errors
    )
    if network_404:
        return "high"

    # 7. Any step failure
    if case.status in ("fail", "error"):
        return "medium"

    return None  # Let Gemini decide


# ---------------------------------------------------------------------------
# LLM-assisted classification
# ---------------------------------------------------------------------------

async def classify_case(case: FailureCase, url: str) -> FailureCase:
    """Assign severity and repro_steps. Uses deterministic rules first; Gemini fallback."""
    # Deterministic pass
    det_severity = classify_failure_deterministic(case)
    if det_severity is not None:
        case.severity = det_severity
        return case

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        case.severity = "medium" if case.status == "fail" else "high"
        return case

    from browser_use.llm import ChatGoogle
    from browser_use.llm.messages import UserMessage

    llm = ChatGoogle(model="gemini-1.5-flash", api_key=google_api_key, temperature=0.1, max_output_tokens=400)
    console_text = "\n".join(case.console_errors[:3]) or "none"
    network_text = "\n".join(case.network_errors[:3]) or "none"
    assertion_text = "\n".join(case.assertion_failures[:3]) or "none"

    prompt = f"""You are a QA analyst reviewing a browser test result for {url}.

Test flow: "{case.flow_name}"
Status: {case.status}
Replay mode: {case.replay_mode}
Result: {case.raw_result[:2000]}
Console errors: {console_text}
Network errors: {network_text}
Assertion failures: {assertion_text}

Severity levels:
- blocker: Complete feature failure, prevents core user workflow
- high: Major functionality broken, significant user impact
- medium: Partial functionality issue, workaround exists
- low: Minor issue, cosmetic or edge case
- none: No real issue found

Return JSON only:
{{
  "severity": "blocker|high|medium|low|none",
  "repro_steps": ["step 1", "step 2"],
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
    failed = [c for c in cases if c.status in ("fail", "error", "blocked")]
    next_actions: list[str] = []

    if failed and os.getenv("GOOGLE_API_KEY"):
        next_actions = await _generate_next_actions(failed, url)

    severity_counts: dict[str, int] = {
        "blocker": 0, "high": 0, "medium": 0, "low": 0, "none": 0, "pass": 0, "error": 0
    }
    for c in cases:
        if c.status == "pass":
            severity_counts["pass"] = severity_counts.get("pass", 0) + 1
        elif c.status in ("error", "blocked"):
            severity_counts["error"] = severity_counts.get("error", 0) + 1
        else:
            severity_counts[c.severity] = severity_counts.get(c.severity, 0) + 1

    return {
        "severity_counts": severity_counts,
        "next_actions": next_actions,
        "failed_cases": [c.model_dump() for c in failed],
    }


async def _generate_next_actions(failed_cases: list[FailureCase], url: str) -> list[str]:
    from blop.prompts import NEXT_ACTIONS_PROMPT

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        return []

    from browser_use.llm import ChatGoogle
    from browser_use.llm.messages import UserMessage

    llm = ChatGoogle(model="gemini-1.5-flash", api_key=google_api_key, temperature=0.3, max_output_tokens=300)

    # Build a summary of failed cases
    failures_text = "\n".join(
        f"- Flow: {c.flow_name}\n  Severity: {c.severity}\n  Replay: {c.replay_mode}\n  Result: {c.raw_result[:300]}"
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

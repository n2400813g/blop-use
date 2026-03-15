"""Parse natural language commands into structured ExecutionIntent."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass
class ExecutionIntent:
    intent: Literal["discover", "record", "regress", "debug"]
    scope: Literal["public", "authed", "both"]
    app_url: str
    repo_path: str | None = None
    profile_name: str | None = None
    business_goal: str | None = None
    priorities: list[str] = field(default_factory=list)
    max_depth: int = 2
    run_mode: Literal["explore", "hybrid", "strict"] = "hybrid"


async def parse_command(
    command: str,
    app_url: str,
    repo_path: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> ExecutionIntent:
    """Parse a natural language command string into a structured ExecutionIntent."""
    cmd_lower = (command or "").lower()

    # Determine intent
    if any(w in cmd_lower for w in ("discover", "find flows", "scan", "explore flows")):
        intent: Literal["discover", "record", "regress", "debug"] = "discover"
    elif any(w in cmd_lower for w in ("record", "capture", "save flow")):
        intent = "record"
    elif any(w in cmd_lower for w in ("debug", "diagnose", "investigate failure")):
        intent = "debug"
    else:
        intent = "regress"

    # Determine scope
    if any(w in cmd_lower for w in ("login", "auth", "dashboard", "account", "signed in", "logged in", "authed")):
        scope: Literal["public", "authed", "both"] = "authed"
    elif any(w in cmd_lower for w in ("public", "visitor", "anonymous", "unauthenticated")):
        scope = "public"
    else:
        scope = "both"

    # Determine run_mode
    if "strict" in cmd_lower:
        run_mode: Literal["explore", "hybrid", "strict"] = "strict"
    elif any(w in cmd_lower for w in ("explore", "discover")):
        run_mode = "explore"
    else:
        run_mode = "hybrid"

    # Extract priorities
    priority_keywords = ("payment", "checkout", "signup", "registration", "onboarding", "pricing", "contact", "search")
    priorities = [kw for kw in priority_keywords if kw in cmd_lower]

    # Extract max_depth
    depth_match = re.search(r"depth[=:\s]+(\d+)", cmd_lower)
    max_depth = int(depth_match.group(1)) if depth_match else 2

    # Extract business goal from patterns like "goal: ..." or "focus on ..."
    business_goal: str | None = None
    for pat in (r"goal[:\s]+(.+?)(?:\.|$)", r"focus on[:\s]+(.+?)(?:\.|$)"):
        m = re.search(pat, cmd_lower)
        if m:
            business_goal = m.group(1).strip()
            break

    return ExecutionIntent(
        intent=intent,
        scope=scope,
        app_url=app_url,
        repo_path=repo_path,
        profile_name=profile_name,
        business_goal=business_goal,
        priorities=priorities,
        max_depth=max_depth,
        run_mode=run_mode,
    )

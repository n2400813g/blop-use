import logging
import os

# Must happen before any other imports to prevent JSON-RPC interference
logging.disable(logging.CRITICAL)
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "CRITICAL")

from typing import Optional

from mcp.server.fastmcp import FastMCP

from blop.storage.sqlite import init_db
from blop.tools import discover, auth, record, regression, results, debug, validate

mcp = FastMCP("blop")


@mcp.tool()
async def discover_test_flows(
    app_url: str,
    repo_path: Optional[str] = None,
    profile_name: Optional[str] = None,
    business_goal: Optional[str] = None,
    command: Optional[str] = None,
    max_depth: int = 2,
) -> dict:
    """Discover test flows for an application by scanning its pages or source code.

    Uses a depth-2 BFS crawl to extract page signals (CTAs, auth routes, forms, headings),
    then sends them to Gemini to generate 5-8 meaningful test flows with severity hints.

    Args:
        app_url: The website URL to scan
        repo_path: Optional path to local source directory for code-based flow generation
        profile_name: Optional auth profile name to use during crawl (for auth-gated pages)
        business_goal: Optional plain-English business goal to prioritize in flow planning
        command: Optional natural language command (parsed for intent/scope/priorities)
        max_depth: BFS crawl depth (default 2)

    Returns:
        dict with app_url, inventory_summary, flows, flow_count, quality
    """
    try:
        return await discover.discover_test_flows(
            app_url=app_url,
            repo_path=repo_path,
            profile_name=profile_name,
            business_goal=business_goal,
            command=command,
            max_depth=max_depth,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def save_auth_profile(
    profile_name: str,
    auth_type: str,
    login_url: Optional[str] = None,
    username_env: Optional[str] = "TEST_USERNAME",
    password_env: Optional[str] = "TEST_PASSWORD",
    storage_state_path: Optional[str] = None,
    cookie_json_path: Optional[str] = None,
) -> dict:
    """Save an authentication profile for use in test runs.

    Args:
        profile_name: Unique name for this profile
        auth_type: One of "env_login", "storage_state", or "cookie_json"
        login_url: Login page URL (required for env_login)
        username_env: Name of env var holding the username (default: TEST_USERNAME)
        password_env: Name of env var holding the password (default: TEST_PASSWORD)
        storage_state_path: Path to a Playwright storage_state.json file
        cookie_json_path: Path to a JSON file containing cookie objects

    Returns:
        dict with profile_name, auth_type, status, note
    """
    try:
        return await auth.save_auth_profile(
            profile_name=profile_name,
            auth_type=auth_type,
            login_url=login_url,
            username_env=username_env,
            password_env=password_env,
            storage_state_path=storage_state_path,
            cookie_json_path=cookie_json_path,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def record_test_flow(
    app_url: str,
    flow_name: str,
    goal: str,
    profile_name: Optional[str] = None,
    command: Optional[str] = None,
    business_criticality: Optional[str] = "other",
) -> dict:
    """Record a test flow by running a Browser-Use agent to accomplish a goal.

    Captures each action with selector, target_text, dom_fingerprint, per-step
    screenshots, and generates final assertion steps from a Gemini screenshot analysis.

    Args:
        app_url: The website URL to test
        flow_name: Short name for this flow (used as identifier)
        goal: Plain-English description of what to accomplish
        profile_name: Optional auth profile name (from save_auth_profile)
        command: Optional natural language command for additional context
        business_criticality: "revenue" | "activation" | "retention" | "support" | "other"

    Returns:
        dict with flow_id, flow_name, step_count, status, artifacts_dir
    """
    try:
        return await record.record_test_flow(
            app_url=app_url,
            flow_name=flow_name,
            goal=goal,
            profile_name=profile_name,
            command=command,
            business_criticality=business_criticality or "other",
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def run_regression_test(
    app_url: str,
    flow_ids: list,
    profile_name: Optional[str] = None,
    headless: bool = True,
    run_mode: str = "hybrid",
    command: Optional[str] = None,
) -> dict:
    """Run regression tests against recorded flows. Returns immediately; poll get_test_results for status.

    Uses hybrid step-by-step replay by default: tries saved selectors first, falls back
    to text-based lookup, then repairs individual broken steps via Gemini vision.

    Args:
        app_url: The website URL to test against
        flow_ids: List of flow_id strings from record_test_flow
        profile_name: Optional auth profile name
        headless: Run browsers headlessly (default: True)
        run_mode: "hybrid" (default), "strict_steps", or "goal_fallback"
        command: Optional natural language command for additional context

    Returns:
        dict with run_id, status ("running"), flow_count, artifacts_dir
    """
    try:
        return await regression.run_regression_test(
            app_url=app_url,
            flow_ids=flow_ids,
            profile_name=profile_name,
            headless=headless,
            run_mode=run_mode,
            command=command,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_test_results(run_id: str) -> dict:
    """Get structured results for a test run.

    Args:
        run_id: The run_id returned from run_regression_test

    Returns:
        dict with run_id, status, cases (with assertion_results, replay_mode_used,
        step_failure_index, artifact_paths), severity_counts, failed_cases, next_actions
    """
    try:
        return await results.get_test_results(run_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def list_recorded_tests() -> dict:
    """List all recorded test flows.

    Returns:
        dict with flows (list of {flow_id, flow_name, app_url, goal, created_at}), total
    """
    try:
        from blop.storage.sqlite import list_flows
        from blop.schemas import RecordedTestsResult
        flows = await list_flows()
        return RecordedTestsResult(flows=flows, total=len(flows)).model_dump()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def debug_test_case(run_id: str, case_id: str) -> dict:
    """Re-run a failed test case in headed mode with verbose evidence capture.

    Shows the exact step that failed, repair attempt results, per-step screenshots,
    and a plain-English "why this failed" explanation with concrete next actions.

    Args:
        run_id: The run_id containing the failure
        case_id: The case_id of the specific failure to debug

    Returns:
        dict with case_id, run_id, status, screenshots, console_log, repro_steps,
        step_failure_index, replay_mode, assertion_failures, why_failed
    """
    try:
        return await debug.debug_test_case(run_id, case_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def validate_setup(
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> dict:
    """Check all preconditions before running tests.

    Verifies: GOOGLE_API_KEY, Chromium installation, SQLite DB access,
    optional app_url reachability, and optional auth profile validity.

    Args:
        app_url: Optional URL to check reachability
        profile_name: Optional auth profile name to validate

    Returns:
        dict with status ("ready" | "warnings" | "blocked"), checks, blockers, warnings
    """
    try:
        return await validate.validate_setup(
            app_url=app_url,
            profile_name=profile_name,
        )
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# MCP Prompts — surface workflow starting points in Claude Code / Cursor
# ---------------------------------------------------------------------------

@mcp.prompt()
def discover_critical_flows() -> str:
    return """First run validate_setup to confirm your environment is ready:
  validate_setup(app_url="https://your-app.com")

If status is "ready", discover the most important test flows:
  discover_test_flows(
    app_url="https://your-app.com",
    business_goal="Find the 5 most revenue-critical flows including signup, onboarding, and billing."
  )

The response will include flows with a business_criticality field (revenue, activation, retention, support, other).
Start by recording flows tagged "revenue" or "activation" — those are the ones that will hurt most if broken."""


@mcp.prompt()
def setup_auth() -> str:
    return """To test authenticated flows, save an auth profile first.

Choose the auth_type that matches your app:

1. env_login — agent logs in with credentials from environment variables:
   save_auth_profile(
     profile_name="staging",
     auth_type="env_login",
     login_url="https://your-app.com/login",
     username_env="TEST_USERNAME",
     password_env="TEST_PASSWORD"
   )
   Then set: export TEST_USERNAME=user@example.com && export TEST_PASSWORD=secret

2. storage_state — replay a Playwright session file:
   save_auth_profile(
     profile_name="staging",
     auth_type="storage_state",
     storage_state_path="/path/to/storage_state.json"
   )

3. cookie_json — inject raw cookies:
   save_auth_profile(
     profile_name="staging",
     auth_type="cookie_json",
     cookie_json_path="/path/to/cookies.json"
   )

After saving, pass profile_name to record_test_flow and run_regression_test."""


@mcp.prompt()
def run_smoke_regression() -> str:
    return """To run a quick smoke regression against all recorded flows:

1. List available flows:
   list_recorded_tests()

2. Run regression (returns immediately — poll for results):
   run_regression_test(
     app_url="https://your-app.com",
     flow_ids=["<flow_id_1>", "<flow_id_2>"],
     profile_name="staging"  # optional
   )
   The status will be "queued" → "running" → "completed"

3. Poll for results (repeat until status is "completed" or "failed"):
   get_test_results(run_id="<run_id>")

The report includes severity_counts with revenue/activation flows labeled as
"BLOCKER in revenue flow: checkout" so you can triage at a glance."""


@mcp.prompt()
def debug_failed_case() -> str:
    return """To investigate a specific test failure:

1. Get the run results to find the failed case:
   get_test_results(run_id="<run_id>")

   Look for cases with status "fail" or "error". Note the case_id.

2. Re-run in headed mode with full evidence capture:
   debug_test_case(run_id="<run_id>", case_id="<case_id>")

   This replays the flow with a visible browser, captures per-step screenshots,
   console logs, and a plain-English "why this failed" explanation with 3 fix suggestions.

3. If the failure is an auth issue (status "waiting_auth"):
   - Check your auth profile: validate_setup(profile_name="<profile_name>")
   - Re-save with correct credentials: save_auth_profile(...)
   - Then retry: run_regression_test(...)"""


def run() -> int:
    """Entry point for the MCP server."""
    import asyncio
    asyncio.get_event_loop().run_until_complete(init_db())
    try:
        mcp.run()
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    run()

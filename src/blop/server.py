import logging
import os

# Must happen before any other imports to prevent JSON-RPC interference
logging.disable(logging.CRITICAL)
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "CRITICAL")

from typing import Optional

from mcp.server.fastmcp import FastMCP

from blop.storage.sqlite import init_db
from blop.tools import discover, auth, record, regression, results, debug

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

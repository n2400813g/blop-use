"""Tests for engine/regression.py."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibeqa_mcp.schemas import FlowStep, RecordedFlow


def make_flow(flow_id: str = "flow1", goal: str = "Test the page") -> RecordedFlow:
    return RecordedFlow(
        flow_id=flow_id,
        flow_name="test_flow",
        app_url="https://example.com",
        goal=goal,
        steps=[
            FlowStep(step_id=0, action="navigate", value="https://example.com"),
            FlowStep(step_id=1, action="assert", description="page loads"),
        ],
        created_at=datetime.now(timezone.utc).isoformat(),
    )


@pytest.mark.asyncio
async def test_execute_flow_pass():
    """Flow returns pass status when result has no error keywords."""
    from vibeqa_mcp.engine.regression import execute_flow

    mock_history = MagicMock()
    mock_history.final_result.return_value = "All steps completed successfully"

    mock_agent = AsyncMock()
    mock_agent.run.return_value = mock_history

    mock_session = AsyncMock()
    mock_session.context = None
    mock_session.aclose = AsyncMock()

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("browser_use.Agent", return_value=mock_agent):
            with patch("browser_use.BrowserSession", return_value=mock_session):
                with patch("vibeqa_mcp.engine.browser.make_browser_profile"):
                    with patch("vibeqa_mcp.storage.files.screenshot_path", return_value="/tmp/shot.png"):
                        flow = make_flow()
                        case = await execute_flow(
                            flow=flow,
                            app_url="https://example.com",
                            run_id="run1",
                            case_id="case1",
                            storage_state=None,
                            headless=True,
                        )

    assert case.status == "pass"
    assert case.flow_id == "flow1"


@pytest.mark.asyncio
async def test_execute_flow_fail_on_error_keyword():
    """Flow returns fail status when result contains error keywords."""
    from vibeqa_mcp.engine.regression import execute_flow

    mock_history = MagicMock()
    mock_history.final_result.return_value = "Page returned 404 error"

    mock_agent = AsyncMock()
    mock_agent.run.return_value = mock_history

    mock_session = AsyncMock()
    mock_session.context = None
    mock_session.aclose = AsyncMock()

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("browser_use.Agent", return_value=mock_agent):
            with patch("browser_use.BrowserSession", return_value=mock_session):
                with patch("vibeqa_mcp.engine.browser.make_browser_profile"):
                    with patch("vibeqa_mcp.storage.files.screenshot_path", return_value="/tmp/shot.png"):
                        flow = make_flow()
                        case = await execute_flow(
                            flow=flow,
                            app_url="https://example.com",
                            run_id="run1",
                            case_id="case1",
                            storage_state=None,
                            headless=True,
                        )

    assert case.status == "fail"


@pytest.mark.asyncio
async def test_run_flows_parallel():
    """run_flows executes multiple flows and returns one case per flow."""
    from vibeqa_mcp.engine.regression import run_flows

    mock_history = MagicMock()
    mock_history.final_result.return_value = "Success"

    mock_agent = AsyncMock()
    mock_agent.run.return_value = mock_history

    mock_session = AsyncMock()
    mock_session.context = None
    mock_session.aclose = AsyncMock()

    flows = [make_flow(f"flow{i}", f"Goal {i}") for i in range(3)]

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("browser_use.Agent", return_value=mock_agent):
            with patch("browser_use.BrowserSession", return_value=mock_session):
                with patch("vibeqa_mcp.engine.browser.make_browser_profile"):
                    with patch("vibeqa_mcp.storage.files.screenshot_path", return_value="/tmp/shot.png"):
                        cases = await run_flows(
                            flows=flows,
                            app_url="https://example.com",
                            run_id="run1",
                            storage_state=None,
                            headless=True,
                        )

    assert len(cases) == 3
    assert all(c.run_id == "run1" for c in cases)


@pytest.mark.asyncio
async def test_run_flows_semaphore():
    """run_flows respects semaphore and does not exceed 5 concurrent flows."""
    from vibeqa_mcp.engine.regression import run_flows

    concurrent_count = 0
    max_concurrent = 0

    async def slow_execute(*args, **kwargs):
        nonlocal concurrent_count, max_concurrent
        concurrent_count += 1
        max_concurrent = max(max_concurrent, concurrent_count)
        await asyncio.sleep(0.05)
        concurrent_count -= 1

        from vibeqa_mcp.schemas import FailureCase
        return FailureCase(
            run_id=kwargs.get("run_id", "run1"),
            flow_id=kwargs.get("flow", make_flow()).flow_id,
            flow_name="test",
            status="pass",
        )

    flows = [make_flow(f"flow{i}") for i in range(10)]

    with patch("vibeqa_mcp.engine.regression.execute_flow", side_effect=slow_execute):
        cases = await run_flows(
            flows=flows,
            app_url="https://example.com",
            run_id="run1",
            storage_state=None,
            headless=True,
        )

    assert len(cases) == 10
    assert max_concurrent <= 5

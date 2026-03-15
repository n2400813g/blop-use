"""Tests for engine/discovery.py."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_discover_flows_returns_fallback_without_api_key():
    """Returns fallback flows when GOOGLE_API_KEY is not set."""
    from blop.engine.discovery import discover_flows

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=[])

    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page

    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context

    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    with patch.dict(os.environ, {}, clear=True):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            flows = await discover_flows("https://example.com")

    assert len(flows) >= 3
    assert all("flow_name" in f and "goal" in f for f in flows)


@pytest.mark.asyncio
async def test_discover_flows_with_gemini_response():
    """Parses Gemini response into flow dicts."""
    from blop.engine.discovery import discover_flows

    gemini_response = json.dumps([
        {"flow_name": "login_flow", "goal": "Log in with valid credentials", "likely_assertions": ["redirect to dashboard"]},
        {"flow_name": "nav_test", "goal": "Click main navigation links", "likely_assertions": ["pages load"]},
        {"flow_name": "form_submit", "goal": "Fill and submit contact form", "likely_assertions": ["success message"]},
    ])

    mock_response = MagicMock()
    mock_response.content = gemini_response

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = mock_response

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=[])

    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page

    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context

    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("browser_use.llm.ChatGoogle", return_value=mock_llm):
                flows = await discover_flows("https://example.com")

    assert len(flows) >= 3
    assert flows[0]["flow_name"] == "login_flow"


@pytest.mark.asyncio
async def test_discover_flows_count_clamped():
    """Result is always 3-8 flows."""
    from blop.engine.discovery import discover_flows

    # Return more than 8
    many_flows = [
        {"flow_name": f"flow_{i}", "goal": f"Goal {i}", "likely_assertions": []}
        for i in range(15)
    ]
    gemini_response = json.dumps(many_flows)

    mock_response = MagicMock()
    mock_response.content = gemini_response

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = mock_response

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=[])

    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page
    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("browser_use.llm.ChatGoogle", return_value=mock_llm):
                flows = await discover_flows("https://example.com")

    assert 3 <= len(flows) <= 8


@pytest.mark.asyncio
async def test_discover_flows_with_repo_path(tmp_path):
    """Uses repo path when provided."""
    from blop.engine.discovery import discover_flows

    # Create a dummy tsx file
    page_dir = tmp_path / "pages"
    page_dir.mkdir()
    (page_dir / "index.tsx").write_text("export default function Home() {}")

    fallback_response = json.dumps([
        {"flow_name": "home_page", "goal": "Visit home page", "likely_assertions": ["page loads"]},
        {"flow_name": "nav_test", "goal": "Test navigation", "likely_assertions": ["links work"]},
        {"flow_name": "form_test", "goal": "Test forms", "likely_assertions": ["submit works"]},
    ])

    mock_response = MagicMock()
    mock_response.content = fallback_response
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = mock_response

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("browser_use.llm.ChatGoogle", return_value=mock_llm):
            flows = await discover_flows("https://example.com", repo_path=str(tmp_path))

    assert len(flows) >= 3

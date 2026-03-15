from __future__ import annotations

from typing import Optional

from blop.engine import discovery
from blop.schemas import DiscoverResult


async def discover_test_flows(app_url: str, repo_path: Optional[str] = None) -> dict:
    flows = await discovery.discover_flows(app_url, repo_path)
    return DiscoverResult(app_url=app_url, flows=flows, flow_count=len(flows)).model_dump()

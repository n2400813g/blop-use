"""BrowserProfile factory — ported from vibetest/agents.py."""
from __future__ import annotations

from browser_use import BrowserProfile


def make_browser_profile(headless: bool, storage_state: str | None = None) -> BrowserProfile:
    browser_args = [
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=TranslateUI",
        "--disable-component-extensions-with-background-pages",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
    ]
    if headless:
        browser_args.append("--headless=new")

    kwargs: dict = dict(
        headless=headless,
        disable_security=True,
        user_data_dir=None,
        args=browser_args,
        ignore_default_args=["--enable-automation"],
        wait_for_network_idle_page_load_time=1.0,
        maximum_wait_page_load_time=5.0,
        wait_between_actions=0.3,
    )
    if storage_state:
        kwargs["storage_state"] = storage_state
    return BrowserProfile(**kwargs)

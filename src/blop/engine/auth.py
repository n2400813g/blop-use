"""Auth engine — ported from vibetest/auth.py, adapted for AuthProfile."""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional

from blop.schemas import AuthProfile

_auth_cache: dict[str, dict] = {}
# Per-profile lock to prevent concurrent logins racing each other
_login_locks: dict[str, asyncio.Lock] = {}


def _get_lock(key: str) -> asyncio.Lock:
    if key not in _login_locks:
        _login_locks[key] = asyncio.Lock()
    return _login_locks[key]


async def resolve_storage_state(profile: AuthProfile) -> Optional[str]:
    """Return path to a valid Playwright storage_state.json, or None."""
    if profile.auth_type == "env_login":
        return await _env_login(profile)
    elif profile.auth_type == "storage_state":
        return _storage_state(profile)
    elif profile.auth_type == "cookie_json":
        return await _cookie_json(profile)
    return None


async def _env_login(profile: AuthProfile) -> Optional[str]:
    cache_key = profile.profile_name
    os.makedirs(".blop", exist_ok=True)
    state_path = os.path.join(".blop", f"auth_state_{cache_key}.json")

    # Fast path: in-memory cache hit
    entry = _auth_cache.get(cache_key)
    if entry and time.time() < entry["expires"] and os.path.exists(entry["path"]):
        return entry["path"]

    # Serialize concurrent callers — only one login per profile at a time
    async with _get_lock(cache_key):
        # Re-check after acquiring lock (another coroutine may have just finished)
        entry = _auth_cache.get(cache_key)
        if entry and time.time() < entry["expires"] and os.path.exists(entry["path"]):
            return entry["path"]

        username_env = profile.username_env or "TEST_USERNAME"
        password_env = profile.password_env or "TEST_PASSWORD"
        username = os.getenv(username_env)
        password = os.getenv(password_env)
        login_url = profile.login_url or os.getenv("LOGIN_URL") or os.getenv("TEST_AUTH_URL")

        # No credentials — fall back to existing state file if available
        if not (username and password and login_url):
            if os.path.exists(state_path):
                return state_path
            return None

        from playwright.async_api import async_playwright

        username_selector = os.getenv("TEST_USERNAME_SELECTOR", "")
        password_selector = os.getenv("TEST_PASSWORD_SELECTOR", "")

        _user_selectors = [s for s in [username_selector] if s] + [
            "input[name='username']", "input[name='email']",
            "input[type='email']", "#email", "input[placeholder*='email' i]",
            "input[placeholder*='username' i]",
        ]
        _pass_selectors = [s for s in [password_selector] if s] + [
            "input[name='password']", "input[type='password']", "#password",
        ]

        async def _try_fill(page, selectors: list[str], value: str) -> str:
            for sel in selectors:
                try:
                    el = await page.wait_for_selector(sel, timeout=5000)
                    if el:
                        await el.fill(value)
                        return sel
                except Exception:
                    continue
            raise RuntimeError(f"Could not find input with any of: {selectors}")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(login_url)
                await _try_fill(page, _user_selectors, username)
                working_pass_sel = await _try_fill(page, _pass_selectors, password)
                await page.press(working_pass_sel, "Enter")
                await page.wait_for_load_state("networkidle", timeout=15000)

                # Validate login succeeded: current URL must not be the login page
                current_url = page.url
                login_failed = (
                    login_url.rstrip("/") in current_url.rstrip("/")
                    or "login" in current_url.lower()
                    or "auth" in current_url.lower()
                    or "signin" in current_url.lower()
                )
                if login_failed:
                    await browser.close()
                    # Login failed — fall back to existing state file if available
                    if os.path.exists(state_path):
                        return state_path
                    return None

                await context.storage_state(path=state_path)
                await browser.close()

        except Exception:
            # Login threw — fall back to existing state file if available
            if os.path.exists(state_path):
                return state_path
            return None

    _auth_cache[cache_key] = {"path": state_path, "expires": time.time() + 3600}
    return state_path


def _storage_state(profile: AuthProfile) -> Optional[str]:
    path = profile.storage_state_path or os.getenv("STORAGE_STATE_PATH")
    if path and os.path.exists(path):
        return path
    return None


async def _cookie_json(profile: AuthProfile) -> Optional[str]:
    cookie_path = profile.cookie_json_path or os.getenv("COOKIE_JSON_PATH")
    if not cookie_path or not os.path.exists(cookie_path):
        return None

    from playwright.async_api import async_playwright

    with open(cookie_path) as f:
        cookies = json.load(f)

    os.makedirs(".blop", exist_ok=True)
    state_path = os.path.join(".blop", f"auth_state_{profile.profile_name}.json")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)
        await context.storage_state(path=state_path)
        await browser.close()

    _auth_cache[profile.profile_name] = {"path": state_path, "expires": time.time() + 3600}
    return state_path

"""Resilient browser interaction helpers with CSS → text → vision fallback chain."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page


async def validate_selector(page: "Page", selector: str) -> bool:
    try:
        return await page.locator(selector).count() > 0
    except Exception:
        return False


async def scroll_into_view(page: "Page", selector: str) -> None:
    try:
        await page.locator(selector).scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass


async def wait_for_stable(page: "Page", selector: str, timeout: int = 3000) -> None:
    try:
        loc = page.locator(selector)
        await loc.wait_for(state="visible", timeout=timeout)
    except Exception:
        pass


async def safe_click(
    page: "Page",
    selector: str,
    timeout: int = 5000,
    fallback_vision: bool = True,
) -> bool:
    """Try CSS selector click, then text match, then vision fallback."""
    try:
        await page.locator(selector).click(timeout=timeout)
        return True
    except Exception:
        pass

    # Try text match fallback
    try:
        await page.get_by_text(selector).first.click(timeout=timeout)
        return True
    except Exception:
        pass

    if fallback_vision:
        from vibeqa_mcp.engine.vision import click_by_vision
        try:
            await click_by_vision(page, selector)
            return True
        except Exception:
            pass

    return False


async def safe_fill(
    page: "Page",
    selector: str,
    value: str,
    timeout: int = 5000,
    fallback_vision: bool = True,
) -> bool:
    """Try CSS selector fill, then vision fallback."""
    try:
        await page.locator(selector).fill(value, timeout=timeout)
        return True
    except Exception:
        pass

    if fallback_vision:
        from vibeqa_mcp.engine.vision import find_element_coords
        try:
            coords = await find_element_coords(page, selector)
            if coords:
                await page.mouse.click(coords[0], coords[1])
                await page.keyboard.type(value)
                return True
        except Exception:
            pass

    return False


async def drag_and_drop(page: "Page", source: str, target: str) -> bool:
    """Playwright drag with fallback to JS dispatchEvent."""
    try:
        await page.drag_and_drop(source, target)
        return True
    except Exception:
        pass

    try:
        src = page.locator(source)
        tgt = page.locator(target)
        src_box = await src.bounding_box()
        tgt_box = await tgt.bounding_box()
        if src_box and tgt_box:
            await page.mouse.move(src_box["x"] + src_box["width"] / 2, src_box["y"] + src_box["height"] / 2)
            await page.mouse.down()
            await page.mouse.move(tgt_box["x"] + tgt_box["width"] / 2, tgt_box["y"] + tgt_box["height"] / 2)
            await page.mouse.up()
            return True
    except Exception:
        pass

    return False

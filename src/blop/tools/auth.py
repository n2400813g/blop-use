from __future__ import annotations

from typing import Optional

from blop.engine import auth as auth_engine
from blop.schemas import AuthProfile, AuthProfileResult
from blop.storage import sqlite


async def save_auth_profile(
    profile_name: str,
    auth_type: str,
    login_url: Optional[str] = None,
    username_env: Optional[str] = "TEST_USERNAME",
    password_env: Optional[str] = "TEST_PASSWORD",
    storage_state_path: Optional[str] = None,
    cookie_json_path: Optional[str] = None,
) -> dict:
    profile = AuthProfile(
        profile_name=profile_name,
        auth_type=auth_type,  # type: ignore[arg-type]
        login_url=login_url,
        username_env=username_env,
        password_env=password_env,
        storage_state_path=storage_state_path,
        cookie_json_path=cookie_json_path,
    )

    storage_path: Optional[str] = None
    try:
        storage_path = await auth_engine.resolve_storage_state(profile)
    except Exception:
        pass

    await sqlite.save_auth_profile(profile, storage_path)

    return AuthProfileResult(
        profile_name=profile_name,
        auth_type=auth_type,
        status="saved",
        note="Credentials are read from environment variables at run time",
    ).model_dump()

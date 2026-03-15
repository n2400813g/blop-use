from __future__ import annotations

import os


def ensure_run_dirs(run_id: str) -> str:
    """Create per-run subdirectories under runs/ and return the base run dir."""
    for sub in ("screenshots", "traces", "console"):
        os.makedirs(os.path.join("runs", sub, run_id), exist_ok=True)
    return os.path.join("runs", run_id)


def screenshot_path(run_id: str, case_id: str, step: int) -> str:
    dir_ = os.path.join("runs", "screenshots", run_id, case_id)
    os.makedirs(dir_, exist_ok=True)
    return os.path.join(dir_, f"step_{step:03d}.png")


def trace_path(run_id: str, case_id: str) -> str:
    dir_ = os.path.join("runs", "traces", run_id)
    os.makedirs(dir_, exist_ok=True)
    return os.path.join(dir_, f"{case_id}.zip")


def console_log_path(run_id: str, case_id: str) -> str:
    dir_ = os.path.join("runs", "console", run_id)
    os.makedirs(dir_, exist_ok=True)
    return os.path.join(dir_, f"{case_id}.log")


def artifacts_dir(run_id: str) -> str:
    return os.path.abspath(os.path.join("runs", run_id))

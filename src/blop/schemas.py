from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field
import uuid


class AuthProfile(BaseModel):
    profile_name: str
    auth_type: Literal["env_login", "storage_state", "cookie_json"]
    login_url: str | None = None
    username_env: str | None = "TEST_USERNAME"
    password_env: str | None = "TEST_PASSWORD"
    storage_state_path: str | None = None
    cookie_json_path: str | None = None


class FlowStep(BaseModel):
    step_id: int
    action: Literal["navigate", "click", "fill", "select", "upload", "drag", "assert", "wait"]
    selector: str | None = None
    value: str | None = None
    description: str = ""
    wait_after_secs: float = 0.5


class RecordedFlow(BaseModel):
    flow_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    flow_name: str
    app_url: str
    goal: str
    steps: list[FlowStep]
    created_at: str


class FailureCase(BaseModel):
    case_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    flow_id: str
    flow_name: str
    status: Literal["pass", "fail", "error"]
    severity: Literal["blocker", "high", "medium", "low", "none"] = "none"
    repro_steps: list[str] = []
    console_errors: list[str] = []
    network_errors: list[str] = []
    screenshots: list[str] = []
    raw_result: str = ""


class DiscoverResult(BaseModel):
    app_url: str
    flows: list[dict]
    flow_count: int


class AuthProfileResult(BaseModel):
    profile_name: str
    auth_type: str
    status: str
    note: str


class RecordedFlowResult(BaseModel):
    flow_id: str
    flow_name: str
    step_count: int
    status: str
    artifacts_dir: str


class RunStartedResult(BaseModel):
    run_id: str
    status: str
    flow_count: int
    artifacts_dir: str


class RunResult(BaseModel):
    run_id: str
    status: str
    started_at: str
    completed_at: str | None
    cases: list[FailureCase]
    severity_counts: dict[str, int]
    next_actions: list[str]
    artifacts_dir: str


class RecordedTestsResult(BaseModel):
    flows: list[dict]
    total: int


class DebugResult(BaseModel):
    case_id: str
    run_id: str
    status: str
    screenshots: list[str]
    console_log: str
    repro_steps: list[str]

from __future__ import annotations

from dataclasses import dataclass, field
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
    # Hybrid replay fields
    target_text: str | None = None
    dom_fingerprint: str | None = None
    url_before: str | None = None
    url_after: str | None = None
    screenshot_path: str | None = None


class RecordedFlow(BaseModel):
    flow_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    flow_name: str
    app_url: str
    goal: str
    steps: list[FlowStep]
    created_at: str
    assertions_json: list[str] = []
    entry_url: str | None = None


@dataclass
class SiteInventory:
    app_url: str
    routes: list[str]
    buttons: list[dict]
    links: list[dict]
    forms: list[dict]
    headings: list[str]
    auth_signals: list[str]
    business_signals: list[str]
    crawled_pages: int = 0

    def to_dict(self) -> dict:
        return {
            "app_url": self.app_url,
            "routes": self.routes,
            "buttons": self.buttons,
            "links": self.links,
            "forms": self.forms,
            "headings": self.headings,
            "auth_signals": self.auth_signals,
            "business_signals": self.business_signals,
            "crawled_pages": self.crawled_pages,
        }


@dataclass
class ReplayStepResult:
    step_id: int
    action: str
    status: str  # pass | fail | skip | repaired
    replay_mode: str  # selector | text_lookup | vision_repair | agent_repair | skipped
    error: str | None = None
    screenshot_path: str | None = None


@dataclass
class ReplayTrace:
    flow_id: str
    flow_name: str
    run_mode: str  # strict_steps | hybrid_repair | goal_fallback
    step_results: list[ReplayStepResult] = field(default_factory=list)
    assertion_results: list[dict] = field(default_factory=list)
    step_failure_index: int | None = None
    console_errors: list[str] = field(default_factory=list)
    network_errors: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    raw_result: str = ""


class FailureCase(BaseModel):
    case_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    flow_id: str
    flow_name: str
    status: Literal["pass", "fail", "error", "blocked"]
    severity: Literal["blocker", "high", "medium", "low", "none"] = "none"
    repro_steps: list[str] = []
    console_errors: list[str] = []
    network_errors: list[str] = []
    screenshots: list[str] = []
    raw_result: str = ""
    replay_mode: str = "goal_fallback"
    step_failure_index: int | None = None
    assertion_failures: list[str] = []
    assertion_results: list[dict] = []


class DiscoverResult(BaseModel):
    app_url: str
    flows: list[dict]
    flow_count: int
    inventory_summary: dict = {}
    quality: dict = {}


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
    step_failure_index: int | None = None
    replay_mode: str = ""
    assertion_failures: list[str] = []
    why_failed: str = ""

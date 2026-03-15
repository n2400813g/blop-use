# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**blop** (formerly vibetest) is an AI-powered QA testing tool that uses Browser-Use agents (backed by Google Gemini) to autonomously test web applications. It exposes 7 MCP tools via a FastMCP server that integrates with Cursor and Claude Code.

## Setup & Installation

```bash
uv venv && source .venv/bin/activate
uv pip install -e .
playwright install chromium
```

Required environment variable: `GOOGLE_API_KEY` (Google Gemini access).

See `.env.example` for all optional env vars.

## Running the MCP Server

```bash
blop-mcp
# or
blop
# or
python -m blop.server
```

The MCP server config for Cursor is in `.cursor/mcp.json`.

## Architecture

All new logic lives in `src/blop/`. The `vibetest/` package is a backward-compat shim.

The system follows a **discover → auth → record → run → results** pipeline:

1. **`server.py`** — FastMCP entry point exposing 7 tools. Suppresses all logging on import.

2. **`config.py`** — All env var reading in one place. Also sets `ANONYMIZED_TELEMETRY=false` and `BROWSER_USE_LOGGING_LEVEL=CRITICAL`.

3. **`schemas.py`** — Pydantic v2 models for all tool I/O: `AuthProfile`, `FlowStep`, `RecordedFlow`, `FailureCase`, and output result types.

4. **`engine/browser.py`** — `make_browser_profile()` factory. Always disables user data dir, disables security, sets network idle timeouts.

5. **`engine/auth.py`** — `resolve_storage_state()` handles `env_login`, `storage_state`, `cookie_json`. Auth state cached 1h per profile.

6. **`engine/discovery.py`** — Scans URL (or repo) and calls Gemini to produce 3–8 flow dicts with `{flow_name, goal, likely_assertions}`.

7. **`engine/recording.py`** — `record_flow()` runs a Browser-Use agent and captures each action as a `FlowStep` list.

8. **`engine/regression.py`** — `execute_flow()` replays a `RecordedFlow`, captures per-step screenshots, console/network errors. `run_flows()` runs in parallel (semaphore=5). Pass/fail via keyword matching.

9. **`engine/interaction.py`** — Resilient click/fill/drag helpers with CSS → text → vision fallback chain.

10. **`engine/vision.py`** — Gemini screenshot fallback: `find_element_coords()`, `click_by_vision()`, `assert_by_vision()`.

11. **`engine/classifier.py`** — `classify_case()` assigns severity via Gemini. `classify_run()` aggregates. `_generate_next_actions()` returns 3 concrete fixes.

12. **`storage/sqlite.py`** — aiosqlite. 5 tables: `auth_profiles`, `recorded_flows`, `runs`, `run_cases`, `artifacts`. `init_db()` creates/migrates on startup.

13. **`storage/files.py`** — Path helpers for `runs/screenshots/`, `runs/traces/`, `runs/console/`.

14. **`reporting/results.py`** — `build_report()` aggregates run+cases into structured response.

## 7 MCP Tools

| Tool | Purpose |
|------|---------|
| `discover_test_flows` | Scan URL/repo → 3-8 candidate flows |
| `save_auth_profile` | Persist auth config (env_login/storage_state/cookie_json) |
| `record_test_flow` | Run agent for a goal and capture steps |
| `run_regression_test` | Replay recorded flows in parallel (async, poll for status) |
| `get_test_results` | Retrieve run results and severity report |
| `list_recorded_tests` | List all recorded flows |
| `debug_test_case` | Re-run a case headed+verbose for evidence |

## Artifacts

Screenshots, traces, and console logs are in `runs/<type>/<run_id>/`. SQLite DB at `.blop/runs.db`.

## Key Implementation Notes

- All logging is suppressed on `config.py` import to prevent JSON-RPC interference.
- `make_browser_profile()` always disables user data dir and browser security features.
- Gemini `gemini-2.0-flash-exp` for agents; `gemini-1.5-flash` for planning/classification.
- `run_regression_test` fires an `asyncio.create_task` and returns immediately — caller must poll `get_test_results`.
- `vibetest/` package re-exports `run` from `blop.server` for backward compatibility.

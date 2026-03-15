# blop

AI-powered QA testing via MCP — discover, record, and run regression tests with Browser-Use agents backed by Google Gemini.

An MCP server that lets your IDE (Cursor or Claude Code) orchestrate full QA cycles: scan your app for test flows, record them with Browser-Use, replay them as regression tests, and get structured bug reports with severity labels and repro steps back in your editor.

---

## 60-Second Quickstart

```bash
# 1. Install
uv venv && source .venv/bin/activate
uv pip install -e .

# 2. Install browser
playwright install chromium --with-deps --no-shell

# 3. Set API key
export GOOGLE_API_KEY="your_google_api_key"
```

Copy `.env.example` to `.env` and fill in your values.

---

## Cursor Setup

Edit `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "blop": {
      "command": "uv",
      "args": ["--directory", "/path/to/blop-use", "run", "python", "-m", "blop.server"],
      "env": {
        "GOOGLE_API_KEY": "your_api_key",
        "APP_BASE_URL": "https://your-app.com"
      }
    }
  }
}
```

---

## Claude Code Setup

```bash
claude mcp add blop /path/to/blop-use/.venv/bin/blop-mcp \
  -e GOOGLE_API_KEY="your_api_key"
```

Verify with `/mcp` — you should see `blop: connected`.

---

## Starter Prompt

```
Use blop to test https://myapp.com:

1. discover_test_flows("https://myapp.com") — find candidate test flows
2. save_auth_profile("main", "env_login", login_url="https://myapp.com/login") — save auth
3. record_test_flow("https://myapp.com", "login_flow", "Log in and reach the dashboard", profile_name="main")
4. run_regression_test("https://myapp.com", [flow_id], profile_name="main")
5. get_test_results(run_id) — show failures grouped by severity
```

---

## Tool Reference

### `discover_test_flows(app_url, repo_path=None)`
Scans the app (or local source) and generates 3–8 test flow candidates via Gemini.

Returns: `{app_url, flows: [{flow_name, goal, likely_assertions}], flow_count}`

---

### `save_auth_profile(profile_name, auth_type, ...)`
Persist an authentication profile for use in test runs.

| Arg | Description |
|-----|-------------|
| `profile_name` | Unique identifier |
| `auth_type` | `"env_login"`, `"storage_state"`, or `"cookie_json"` |
| `login_url` | Login page URL (env_login) |
| `username_env` | Env var name for username (default: `TEST_USERNAME`) |
| `password_env` | Env var name for password (default: `TEST_PASSWORD`) |
| `storage_state_path` | Path to Playwright `storage_state.json` |
| `cookie_json_path` | Path to JSON cookie array |

Returns: `{profile_name, auth_type, status, note}`

---

### `record_test_flow(app_url, flow_name, goal, profile_name=None)`
Run a Browser-Use agent to accomplish `goal` and capture the steps as a replayable flow.

Returns: `{flow_id, flow_name, step_count, status, artifacts_dir}`

---

### `run_regression_test(app_url, flow_ids, profile_name=None, headless=True)`
Replay recorded flows against `app_url` in parallel. Returns immediately — poll `get_test_results` for status.

Returns: `{run_id, status: "running", flow_count, artifacts_dir}`

---

### `get_test_results(run_id)`
Retrieve structured results for a test run.

Returns:
```json
{
  "run_id": "...",
  "status": "completed",
  "severity_counts": {"blocker": 0, "high": 1, "medium": 2, "low": 0, "pass": 5, "error": 0},
  "failed_cases": [{"case_id": "...", "flow_name": "...", "severity": "high", "repro_steps": [...]}],
  "next_actions": ["Fix login form validation", ...],
  "artifacts_dir": "runs/..."
}
```

---

### `list_recorded_tests()`
List all recorded flows.

Returns: `{flows: [{flow_id, flow_name, app_url, goal, created_at}], total}`

---

### `debug_test_case(run_id, case_id)`
Re-run a failed case in headed mode with verbose screenshot capture.

Returns: `{case_id, run_id, status, screenshots, console_log, repro_steps}`

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GOOGLE_API_KEY` | Yes | — | Google AI (Gemini) API key |
| `APP_BASE_URL` | No | — | Default app URL |
| `LOGIN_URL` | No | — | Login page URL |
| `TEST_USERNAME` | No | — | Login username for `env_login` |
| `TEST_PASSWORD` | No | — | Login password for `env_login` |
| `STORAGE_STATE_PATH` | No | — | Default Playwright storage state path |
| `COOKIE_JSON_PATH` | No | — | Default cookie JSON path |
| `BLOP_DB_PATH` | No | `.blop/runs.db` | SQLite database path |
| `BLOP_HEADLESS` | No | `true` | Default headless mode |
| `BLOP_MAX_STEPS` | No | `50` | Max agent steps per flow |

---

## Artifact Output

```
runs/
  screenshots/<run_id>/<case_id>/
    step_000.png
    step_001.png
    ...
  traces/<run_id>/
    <case_id>.zip
  console/<run_id>/
    <case_id>.log
.blop/
  runs.db          # SQLite (auth_profiles, recorded_flows, runs, run_cases, artifacts)
  auth_state_*.json
```

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Requirements

- Python 3.11+
- Google API key with Gemini access
- Cursor or Claude Code with MCP support

---

## Backward Compatibility

The `vibetest/` package shim re-exports from the new `blop` package.

---

Powered by [Browser Use](https://github.com/browser-use/browser-use)

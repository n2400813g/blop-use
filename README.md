# blop — AI-Powered QA Testing for Your Web App

**blop** lets you test any web application by just describing what you want to test in plain English. It opens a real browser, clicks through your app like a human would, and tells you what's broken — complete with screenshots, severity labels, and suggested fixes.

You don't write test code. You just talk to it.

---

## What does it actually do?

1. **Discovers** your app's key flows by crawling it (login, pricing, contact, checkout, etc.)
2. **Records** those flows by watching an AI agent use your app
3. **Replays** them on demand to catch regressions
4. **Reports** exactly what broke, how severe it is, and what to fix

It plugs into **Cursor** or **Claude Code** as an MCP tool — meaning you just ask it to run tests in a chat window, the same way you'd ask a colleague.

---

## Before you start — what you'll need

| What | Where to get it | Takes |
|------|----------------|-------|
| Python 3.11 or newer | [python.org/downloads](https://python.org/downloads) | 5 min |
| `uv` (fast Python installer) | Run `curl -LsSf https://astral.sh/uv/install.sh \| sh` in Terminal | 1 min |
| Google API key (free tier works) | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) | 2 min |
| Cursor or Claude Code | [cursor.com](https://cursor.com) or `npm i -g @anthropic-ai/claude-code` | 5 min |

---

## Installation — step by step

Open Terminal and run these commands one at a time:

```bash
# 1. Go into the blop folder
cd /path/to/blop-use

# 2. Create a Python environment
uv venv && source .venv/bin/activate

# 3. Install blop
uv pip install -e .

# 4. Install the browser blop controls
playwright install chromium --with-deps --no-shell
```

You should see no errors. If you do, check the [Troubleshooting](#troubleshooting) section below.

---

## Configure your credentials

Copy the example config file and fill it in:

```bash
cp .env.example .env
```

Then open `.env` in any text editor and fill in your values:

```
# Required — get this from aistudio.google.com/app/apikey
GOOGLE_API_KEY=your_key_here

# Your app's URL
APP_BASE_URL=https://your-app.com

# Login details (only needed for testing authenticated pages)
LOGIN_URL=https://your-app.com/login
TEST_USERNAME=your@email.com
TEST_PASSWORD=your_password
```

Everything else can stay as-is.

---

## Connect blop to your IDE

### Cursor

1. Open Cursor
2. Go to **Settings → MCP**
3. Click **Add MCP Server** and paste this (update the path to where you cloned blop):

```json
{
  "mcpServers": {
    "blop": {
      "command": "uv",
      "args": ["--directory", "/path/to/blop-use", "run", "python", "-m", "blop.server"],
      "env": {
        "GOOGLE_API_KEY": "your_key_here",
        "APP_BASE_URL": "https://your-app.com",
        "LOGIN_URL": "https://your-app.com/login",
        "TEST_USERNAME": "your@email.com",
        "TEST_PASSWORD": "your_password"
      }
    }
  }
}
```

4. Restart Cursor. You should see **blop** listed as a connected tool in Settings → MCP.

### Claude Code

Run this once in your terminal:

```bash
claude mcp add blop /path/to/blop-use/.venv/bin/blop-mcp \
  -e GOOGLE_API_KEY="your_key_here" \
  -e APP_BASE_URL="https://your-app.com" \
  -e LOGIN_URL="https://your-app.com/login" \
  -e TEST_USERNAME="your@email.com" \
  -e TEST_PASSWORD="your_password"
```

Type `/mcp` in Claude Code to verify — you should see `blop: connected`.

---

## Your first test in 5 minutes

Open a chat window in Cursor or Claude Code and paste this (swap in your app URL):

```
Use blop to test https://your-app.com

1. Call discover_test_flows to find what flows exist on the site
2. Record the most important 2-3 flows
3. Run a regression test on all of them
4. Show me the results with severity levels
```

That's it. blop will crawl the site, record the flows, run the tests, and report back — all without you writing a single line of test code.

---

## The 7 tools — explained in plain English

### 1. `discover_test_flows` — *"What should I test?"*

Crawls your app and asks AI to figure out what the important user journeys are. Returns a list of suggested flows with descriptions of what to verify.

**Basic usage:**
```
discover_test_flows("https://your-app.com")
```

**With more context (gets better results):**
```
discover_test_flows(
  app_url="https://your-app.com",
  business_goal="Find all revenue-critical flows like checkout and upgrade",
  max_depth=2
)
```

**Parameters:**
| Parameter | What it does | Example |
|-----------|-------------|---------|
| `app_url` | The website to scan | `"https://app.example.com"` |
| `business_goal` | Tell it what matters most to your business | `"Focus on checkout and onboarding"` |
| `profile_name` | Use a logged-in account to scan private pages | `"my-auth-profile"` |
| `max_depth` | How deep to crawl (1 = homepage only, 2 = homepage + linked pages) | `2` |
| `command` | Free-text instruction — blop figures out the rest | `"Discover auth flows for the dashboard"` |

**What you get back:**
```json
{
  "flows": [
    {
      "flow_name": "user_login",
      "goal": "User logs in with email and password and reaches the dashboard",
      "severity_if_broken": "blocker",
      "confidence": 0.92
    },
    {
      "flow_name": "pricing_page_upgrade",
      "goal": "Visitor views pricing, clicks Pro plan CTA, reaches checkout",
      "severity_if_broken": "high",
      "confidence": 0.85
    }
  ],
  "inventory_summary": {
    "auth_signals": ["sign in", "/login"],
    "business_signals": ["pricing", "checkout"]
  },
  "quality": {
    "passed": true,
    "warnings": []
  }
}
```

---

### 2. `save_auth_profile` — *"Here are my login credentials"*

Saves your login details so blop can test pages that require being signed in. Your password is only stored locally on your machine.

**Basic usage (username + password from your .env file):**
```
save_auth_profile(
  profile_name="my-app-login",
  auth_type="env_login",
  login_url="https://your-app.com/login"
)
```

**Auth types explained:**

| Type | When to use | Example |
|------|-------------|---------|
| `env_login` | You have a username + password | Standard email/password login |
| `storage_state` | You have a browser session file from Playwright | SSO, OAuth, MFA flows |
| `cookie_json` | You have exported browser cookies | When you can't automate login |

**Tips:**
- blop caches sessions for 1 hour — it won't re-login every time you run tests
- Your credentials are read from environment variables, never stored as plain text in the database
- For SSO/Google login, use `storage_state` — log in manually once, export the session, point blop at it

---

### 3. `record_test_flow` — *"Watch and learn this flow"*

Runs an AI agent in a real browser to accomplish a goal, and saves every step it takes. You can then replay this recording as many times as you want.

**Basic usage:**
```
record_test_flow(
  app_url="https://your-app.com",
  flow_name="user_signup",
  goal="Sign up for a new account with email and verify the welcome screen appears"
)
```

**With authentication (for flows behind login):**
```
record_test_flow(
  app_url="https://your-app.com",
  flow_name="create_new_project",
  goal="Log in, create a new project called 'Test Project', and verify it appears in the project list",
  profile_name="my-app-login"
)
```

**Writing good goals — what makes the difference:**

| ❌ Vague (gets generic results) | ✅ Specific (gets reliable tests) |
|--------------------------------|----------------------------------|
| `"Test the login"` | `"Log in with email and password, verify the dashboard shows the user's name in the top right corner"` |
| `"Check pricing"` | `"Navigate to pricing, verify Free, Pro ($35/mo), and Enterprise tiers are visible, click the Pro CTA and confirm it leads to checkout"` |
| `"Test the form"` | `"Fill in the contact form with name, company email, and message, submit it, and verify a confirmation message appears"` |

**What gets captured per step:**
- The element clicked or filled (selector + visible text)
- A screenshot at that moment
- The URL before and after
- Final assertions generated by AI from the end state

---

### 4. `run_regression_test` — *"Check if everything still works"*

Replays recorded flows against your app. Returns immediately — you poll for results with `get_test_results`.

**Basic usage:**
```
run_regression_test(
  app_url="https://your-app.com",
  flow_ids=["abc123", "def456"]
)
```

**With auth and hybrid mode:**
```
run_regression_test(
  app_url="https://your-app.com",
  flow_ids=["abc123", "def456"],
  profile_name="my-app-login",
  run_mode="hybrid"
)
```

**Run modes explained:**

| Mode | What it does | When to use |
|------|-------------|-------------|
| `hybrid` *(default)* | Tries saved steps first; if a selector breaks, AI repairs that single step | Best for most cases |
| `strict_steps` | Follows saved steps exactly — fails immediately if anything doesn't match | CI/CD where you want strict enforcement |
| `goal_fallback` | Ignores saved steps, replays using only the original goal description | When the UI has changed significantly |

---

### 5. `get_test_results` — *"What broke?"*

Polls a running test and returns structured results. Call it repeatedly until `status` is `"completed"`.

```
get_test_results("your-run-id-here")
```

**Understanding the results:**

```json
{
  "status": "completed",
  "severity_counts": {
    "blocker": 1,
    "high": 0,
    "medium": 2,
    "pass": 4
  },
  "failed_cases": [
    {
      "flow_name": "checkout_flow",
      "severity": "blocker",
      "replay_mode": "hybrid_repair",
      "step_failure_index": 3,
      "assertion_failures": ["Payment page should load after clicking Subscribe"],
      "repro_steps": ["Go to /pricing", "Click 'Get Pro Plan'", "Observe: redirected to homepage instead of checkout"]
    }
  ]
}
```

**Severity levels — what they mean for your business:**

| Level | Meaning | Action |
|-------|---------|--------|
| 🔴 **blocker** | Core workflow is completely broken. Users can't complete a key task. | Fix before shipping anything |
| 🟠 **high** | Major feature broken, significant user impact | Fix in current sprint |
| 🟡 **medium** | Partial issue, workaround possible | Fix in next sprint |
| 🟢 **low** | Cosmetic or edge case | Backlog |
| ✅ **pass** | Everything worked | No action needed |

**`replay_mode` tells you how the test ran:**
- `strict_steps` — selector matched, step ran exactly as recorded
- `hybrid_repair` — original selector broke, AI found the element another way
- `goal_fallback` — step-by-step replay failed entirely, fell back to full agent replay

---

### 6. `list_recorded_tests` — *"What tests do I have?"*

Lists every flow you've ever recorded.

```
list_recorded_tests()
```

Returns a list with `flow_id`, `flow_name`, `app_url`, `goal`, and `created_at`. Use the `flow_id` values in `run_regression_test`.

---

### 7. `debug_test_case` — *"Why exactly did this fail?"*

Re-runs a specific failed case in a visible browser window (not headless), captures every screenshot, and generates a plain-English explanation of what went wrong.

```
debug_test_case(
  run_id="your-run-id",
  case_id="the-failed-case-id"
)
```

**What you get back:**
```json
{
  "status": "fail",
  "step_failure_index": 3,
  "replay_mode": "hybrid_repair",
  "assertion_failures": ["Dashboard should show user inbox after login"],
  "why_failed": "The login succeeded but the session cookie was not persisted between the auth step and the dashboard navigation. The app redirected to /login again instead of /dashboard.",
  "repro_steps": ["Navigate to /auth", "Fill email and password", "Click Sign In", "Session lost — redirect back to /auth"],
  "screenshots": ["runs/screenshots/run123/case456/step_003.png"]
}
```

---

## Real-world testing scenarios for B2B SaaS

### Scenario 1 — New app, zero knowledge

```
Use blop to discover and test https://new-saas-app.com from scratch.

1. Call discover_test_flows with business_goal="Find all revenue-critical flows including signup, checkout, and onboarding"
2. Record the top 5 flows it finds
3. Run regression on all of them
4. Show me anything with severity "high" or "blocker"
```

### Scenario 2 — Before a release

```
We're about to ship a new version. Use blop to run a pre-release check on https://staging.myapp.com:

1. list_recorded_tests — show what flows we have
2. run_regression_test on all flows against staging
3. get_test_results — compare pass/fail to last run
4. debug_test_case on anything that changed from pass to fail
```

### Scenario 3 — Test the full authenticated product

```
Test the authenticated product experience on https://app.myapp.com:

1. save_auth_profile("prod-user", "env_login", login_url="https://app.myapp.com/login")
2. record_test_flow for: dashboard load, core feature (e.g. "create new project"), settings page, and billing/upgrade flow
3. run_regression_test with profile_name="prod-user"
4. get_test_results — show me the full breakdown
```

### Scenario 4 — Investigate a specific bug report

```
A user reported the checkout button isn't working. Use blop to investigate:

1. record_test_flow("https://myapp.com", "checkout_bug", "Navigate to pricing, click the Pro plan CTA, and verify checkout loads")
2. run_regression_test on that flow
3. get_test_results — check step_failure_index and assertion_failures
4. debug_test_case on the failure to get screenshots and a plain-English explanation
```

### Scenario 5 — Auth-gated flows with SSO

If your app uses Google/Okta/SAML login that can't be automated:

```bash
# Step 1: Log in manually in your browser, then export the session
# In Chrome: DevTools → Application → Storage → Export session state
# Save as: /path/to/my-session.json

# Step 2: Tell blop to use it
```
```
save_auth_profile(
  profile_name="sso-session",
  auth_type="storage_state",
  storage_state_path="/path/to/my-session.json"
)
```

---

## How auth profiles work

```
Your .env file                     blop
─────────────────────────────────────────────────────────────
TEST_USERNAME=user@company.com  →  Reads at runtime
TEST_PASSWORD=secret            →  Never stored in DB
                                   ↓
                              Opens login page
                              Fills credentials
                              Saves session cookie
                                   ↓
                         .blop/auth_state_profile.json
                         (valid for 1 hour, then re-logs in)
```

blop tries these login field selectors automatically, in order:
1. `input[name="username"]`
2. `input[name="email"]`
3. `input[type="email"]`
4. `#email`
5. Any input with "email" in the placeholder

You can override with env vars `TEST_USERNAME_SELECTOR` and `TEST_PASSWORD_SELECTOR` if your login form is unusual.

---

## Where your test data lives

```
your-project/
├── .env                          ← Your credentials (never commit this)
├── .blop/
│   ├── runs.db                   ← All test history (SQLite)
│   └── auth_state_*.json         ← Cached login sessions
└── runs/
    ├── screenshots/
    │   └── <run_id>/<case_id>/
    │       ├── step_000.png       ← Screenshot at each step
    │       └── step_001.png
    ├── traces/
    │   └── <run_id>/<case_id>.zip ← Playwright trace (open with trace viewer)
    └── console/
        └── <run_id>/<case_id>.log ← Browser console errors
```

---

## Troubleshooting

**"blop not found" or "command not found"**
Make sure you've activated the virtual environment: `source .venv/bin/activate`

**"GOOGLE_API_KEY not set"**
Check your `.env` file exists in the blop-use folder and has your key on the `GOOGLE_API_KEY=` line. Alternatively, set it directly in the MCP config JSON.

**Login keeps failing**
1. Double-check your `TEST_USERNAME` and `TEST_PASSWORD` in `.env`
2. Try visiting your `LOGIN_URL` manually to confirm the credentials work
3. If your login uses unusual field names, add `TEST_USERNAME_SELECTOR=input[name="your-field"]` to `.env`
4. For SSO/MFA, use `auth_type="storage_state"` instead

**Tests are all passing but you know something is broken**
The regression engine uses AI vision to evaluate assertions — it shouldn't produce false positives. If something is marked pass that looks wrong, use `debug_test_case` to re-run it with full screenshot capture and see what the browser actually showed.

**"MCP server not connected" in Cursor**
1. Check the path in `mcp.json` points to where you actually cloned blop-use
2. Make sure `uv` is installed (`which uv` in Terminal)
3. Restart Cursor fully (Cmd+Q, reopen)

**The browser opens but does nothing / hangs**
Your `BLOP_MAX_STEPS` limit (default 50) may be too low for complex flows. Add `BLOP_MAX_STEPS=100` to `.env`.

---

## Environment variables — full reference

| Variable | Required | Default | What it does |
|----------|----------|---------|-------------|
| `GOOGLE_API_KEY` | **Yes** | — | Gemini API key. Get one free at [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| `APP_BASE_URL` | No | — | Default app URL (used as fallback if no URL passed to tools) |
| `LOGIN_URL` | No | — | Where blop navigates to log in |
| `TEST_USERNAME` | No | — | Login email/username |
| `TEST_PASSWORD` | No | — | Login password |
| `TEST_USERNAME_SELECTOR` | No | auto-detected | CSS selector for the username input field |
| `TEST_PASSWORD_SELECTOR` | No | auto-detected | CSS selector for the password input field |
| `STORAGE_STATE_PATH` | No | — | Path to a saved Playwright session (for SSO/OAuth) |
| `COOKIE_JSON_PATH` | No | — | Path to exported browser cookies (JSON array) |
| `BLOP_DB_PATH` | No | `.blop/runs.db` | Where blop stores its database |
| `BLOP_HEADLESS` | No | `true` | `false` = show browser window during tests (useful for debugging) |
| `BLOP_MAX_STEPS` | No | `50` | Max steps the AI agent takes per flow |

---

## Requirements

- Python 3.11+
- Google API key with Gemini access (free tier works)
- Cursor or Claude Code with MCP support
- macOS, Linux, or Windows with WSL2

---

Powered by [Browser Use](https://github.com/browser-use/browser-use) and [Google Gemini](https://ai.google.dev/)

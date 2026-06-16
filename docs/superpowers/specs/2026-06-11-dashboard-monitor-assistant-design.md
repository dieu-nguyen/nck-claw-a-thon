# Dashboard Monitoring Assistant — Design Spec

**Date:** 2026-06-11
**Last updated:** 2026-06-16
**Status:** Implemented (v2)
**Owner:** (you)

## 1. Purpose

A fintech monitoring assistant that automates a daily workflow: scanning Apache
Superset dashboards, detecting abnormal metrics, investigating root causes, and
delivering findings via email and a web UI.

It replaces the manual morning routine of opening multiple dashboards, reading
key numbers, comparing them against thresholds and past periods, and
investigating anything that looks off.

### Core workflow

```
Load prompt files → for each flow: LLM agent fetches charts, investigates,
  concludes (normal / warning / critical)
  → send email report (if SMTP configured)
  → display results in web UI
```

## 2. Scope

### In scope (v2 — implemented)
- On-demand "run now" via a web UI button (no login required; API token in page).
- Live step-by-step progress streamed to the browser via Server-Sent Events (SSE).
- Per-flow result boxes in the UI: one box per `.md` prompt file, showing status
  banner, run metadata, analysis, recommendations, and collapsible step log.
- Reading chart data from Apache Superset via direct REST API (client-side
  filtering — Rison queries not used).
- **Prompt-driven flows:** each monitoring flow is a natural-language `.md` file
  that tells the LLM what to check, what counts as abnormal, how to investigate,
  and what to report. No fixed rules or config schema required.
- **LLM agent loop per flow:** the LLM autonomously fetches charts
  (`search_dashboards`, `list_charts`, `search_charts`, `get_chart_data`),
  reasons over the data, and concludes with a structured finding
  (normal / warning / critical + summary + analysis + recommendations).
  Up to 50 steps per flow.
- **Email delivery** via `send_email` tool (SMTP/STARTTLS). Recipients and
  format are defined per-prompt, not globally. Skipped silently if `SMTP_HOST`
  is not set.
- **Per-step JSONL logging:** every LLM call is recorded with its raw response,
  parsed action, tool result, and finish reason — for debugging misclassifications.
- Microsoft Teams incoming webhook (optional, skip if `TEAMS_WEBHOOK_URL` unset).
- `POST /run` JSON API for external callers (cron, curl).
- `--dry-run` CLI mode.

### Out of scope
- Power Automate / Teams Workflow scheduling.
- Adaptive Card rich formatting in Teams.
- Free-form conversational Q&A.
- Writing back to any system (read-only Superset access by design).
- Multi-agent coordination or memory across runs.
- Authentication on the web UI (token embedded in page HTML).

## 3. Approach

**Prompt-driven LLM agent per flow.**

Each monitoring flow is fully described in a natural-language `.md` file. The
file is both the configuration and the instructions — it tells the agent which
dashboard and charts to fetch, what constitutes an anomaly, how to distinguish
local vs systemic issues, and what format to use in the email report. There is
no separate YAML config, no fixed rule schema, and no hard-coded thresholds.

This was chosen over the original declarative playbook + rule engine approach
because:
- Rules in YAML are brittle and don't reflect the nuanced, context-dependent
  logic that domain experts actually apply.
- Prompt files are writable and maintainable by non-technical stakeholders
  without touching code.
- The LLM handles trend analysis, contribution weighting, and local vs systemic
  distinction — reasoning that fixed threshold rules cannot.

### What kind of system is this?

A **prompt-driven LLM agent loop**: the LLM drives all control flow for each
flow — deciding which charts to fetch, when it has enough data to conclude, and
what the finding means. The harness enforces a step budget (max 50 steps) and
read-only Superset access. Each prompt file is the full scope constraint.

## 4. Architecture

```
  Browser / curl ──▶  GET /          (web UI, no auth)
  Browser       ──▶  GET /run/stream (SSE, token in query string)
  curl / cron   ──▶  POST /run       (Bearer token auth)
                          │
                          ▼
              ┌─────────────────────────────────┐
              │       Monitoring Engine          │
              │                                  │──▶ Superset REST API
              │  scan PROMPTS_DIR for *.md files │     (search_dashboards,
              │  for each prompt file:           │      list_charts,
              │    LLM agent loop                │      search_charts,
              │    (up to 50 steps)         ──▶ LLM    get_chart_data)
              │    → CheckResult                 │
              │  assemble report JSON            │──▶ SMTP server
              └──────────────┬───────────────────┘    (send_email)
                             │
                   ┌─────────┴──────────┐
                   ▼                    ▼
         SSE stream to browser   POST to Teams webhook
         (live step progress +   (plain text, optional)
          final report)
```

### Components

- **Prompt files (`prompts/*.md`)** — the maintained artifact. Each `.md` file
  is one monitoring flow. The filename becomes the check ID. The first
  `# Tên tác vụ` heading becomes the display name shown in the UI.
- **Monitoring engine (`src/engine.py`)** — scans `PROMPTS_DIR` for `*.md`
  files, runs each through the LLM agent loop, collects `CheckResult` objects.
  Emits SSE step events and writes per-step JSONL log entries.
- **LLM agent loop** — per-flow agentic loop. LLM receives the prompt as its
  user message and calls tools via JSON responses until it concludes with
  `{"action": "done", ...}`. After `send_email`, the model is forced to conclude
  immediately to prevent double-sends.
- **Superset client (`src/superset_client.py`)** — read-only REST API wrapper
  with retry/backoff. All search methods use client-side filtering (Rison format
  rejected by this Superset version).
- **Report builder (`src/report_builder.py`)** — assembles `CheckResult` list
  into a report dict; separates anomalies from normal results; sorts anomalies
  critical → warning.
- **Run log (`src/run_log.py`)** — per-run JSONL audit log in `RUN_LOG_DIR`.
  Records `run_start`, `llm_step` (one per LLM call with full raw response and
  tool result), `check_complete`, and `run_complete`.
- **FastAPI app (`src/main.py`)** — serves the web UI, `/run/stream` SSE
  endpoint, `POST /run` JSON API, and Teams webhook poster.

### Hosting
- GreenNode AgentBase runtime (Docker container, FastAPI, port 8080,
  `linux/amd64`).
- LLM: GreenNode AgentBase MaaS (`minimax/minimax-m2.5`), accessed via
  OpenAI-compatible SDK. Configurable via `LLM_MODEL` env var.
- Superset credentials stored as AgentBase runtime env vars.
- **Stateless** — no memory store. Each run is independent.

### Integrations & dependencies
- **Superset** — REST API v1 (`/api/v1/chart/data`, `/api/v1/dashboard/{id}/charts`,
  `/api/v1/chart/`, `/api/v1/dashboard/`). Client-side search filtering.
- **LLM** — OpenAI-compatible endpoint at GreenNode MaaS
  (`https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1`).
- **Email** — SMTP/STARTTLS (`smtplib`). Host, port, credentials from env vars.
  Skipped if `SMTP_HOST` is empty.
- **Teams** — incoming webhook (plain text). Optional; skipped if
  `TEAMS_WEBHOOK_URL` is unset.
- **Scheduling** — external (cron job, curl, any HTTP caller hitting `POST /run`
  with a bearer token).

## 5. Prompt files

### Location
`prompts/` directory (configurable via `PROMPTS_DIR` env var). The engine
discovers all `*.md` files at runtime — add a file, get a new flow; delete a
file, remove a flow. No code changes needed.

### Required heading
Each prompt file must start with:
```markdown
# Tên tác vụ
<Display name shown in the web UI>
```
This is parsed at runtime to label the task box. The filename (without `.md`)
is the internal `check_id`.

### What a prompt file contains
Everything the agent needs to execute one monitoring flow:
- Which dashboard and charts to fetch (via `search_dashboards` → `list_charts`
  → `get_chart_data`).
- What metrics to look at and what counts as normal vs abnormal.
- How to reason about the data (weight by volume, distinguish local vs systemic,
  compute contribution to total drop).
- What status to assign (normal / warning / critical) and under what conditions.
- Email format, subject line rules, and recipient list for `send_email`.

### Available tools (declared in system prompt)
| Tool | Description |
|---|---|
| `search_dashboards(name)` | Find dashboard by name substring, returns `[{id, name}]` |
| `list_charts(dashboard_id)` | List all charts on a dashboard, returns `[{id, name}]` |
| `search_charts(name)` | Find charts by name substring |
| `get_chart_data(chart_id)` | Fetch chart data by numeric ID |
| `send_email(to, subject, body)` | Send email via SMTP; skipped if SMTP unconfigured |

### Agent conclusion schema
Every flow must end with:
```json
{
  "action": "done",
  "is_abnormal": true | false,
  "status": "normal" | "warning" | "critical",
  "summary": "<1-2 sentence plain language summary>",
  "analysis": "<root cause analysis, or empty string if normal>",
  "recommendations": ["<action 1>", "<action 2>"]
}
```

## 6. Run flow

1. Engine scans `PROMPTS_DIR` for `*.md` files (sorted alphabetically).
2. For each prompt file:
   a. Parse `# Tên tác vụ` heading for display name.
   b. Emit `{"__task_start__": "<name>", "check_id": "<id>"}` SSE event — UI
      opens a new task box.
   c. Start LLM agent loop (max 50 steps):
      - LLM calls a tool (returns JSON `{"action": "...", "args": {...}}`).
      - Tool is executed; result appended as a user message.
      - Meaningful actions emit human-readable SSE step messages to the UI.
      - Each LLM call is logged as a `llm_step` JSONL record.
      - After `send_email` succeeds, the LLM is forced to conclude immediately.
      - On non-JSON response, the model is nudged to retry as JSON.
      - On step budget exhaustion, LLM is asked for a best-effort conclusion.
   d. Produce a `CheckResult`.
3. Assemble report JSON.
4. Send SSE `{"__report__": <report>}` — UI finalizes all task boxes.
5. Post plain-text message to Teams webhook (if `TEAMS_WEBHOOK_URL` is set).
6. Return report JSON to HTTP caller (for `POST /run`).

### Agent loop guardrails
- **Read-only Superset access** — `send_email` is the only side-effectful tool.
- **Step-bound** — hard cap of 50 steps per flow; best-effort conclusion on
  exhaustion.
- **No double email** — after `send_email`, model receives an explicit
  "do not send again, respond with done" instruction.
- **SMTP skip** — if `SMTP_HOST` is empty, `send_email` returns a skip message
  and the loop continues to `done` normally.
- **Per-flow isolation** — an error in one flow does not stop other flows.
- **Non-JSON nudge** — if LLM responds with prose, it is appended as an
  assistant message and the user message asks for JSON retry.

## 7. Web UI

Single-page HTML served at `GET /`. No login required — the API token is
embedded in the page and used for the SSE stream.

### Layout (post-run, per task box)
```
┌─ task-box ────────────────────────────────────────────┐
│ ✅ Giám sát Bank Link SR              [Bình thường]   │  ← header (always visible)
│ ✅ Tất cả chỉ số bình thường                          │  ← status banner
│ 🕐 2026-06-16T08:00:00 UTC  ⏱ Hoàn thành 87s  📊→   │  ← meta: time, elapsed, link
│ SR tổng hôm nay ổn định ở 97.2%...                    │  ← result summary
│ → Không cần hành động                                  │  ← recommendations
│ ▶ Xem các bước thực hiện                              │  ← click to expand steps
└────────────────────────────────────────────────────────┘
```

- One box per prompt file, in alphabetical filename order.
- Steps stream live inside the box while running; collapsed by default after
  completion; click toggle to expand/collapse.
- Status badge colours: blue (running) → green/yellow/red (done).
- Left border colour matches status: green (normal), amber (warning), red (critical).
- Engine errors (Superset unreachable, etc.) appear as a separate orange error
  card below the task boxes.

### SSE event protocol
| Event data | Meaning |
|---|---|
| `{"__task_start__": "name", "check_id": "id"}` | Open a new task box |
| Plain string (e.g. `"📊 Đang lấy dữ liệu…"`) | Append step line inside current box |
| `{"__report__": <report>}` | Finalize all boxes with results |
| `{"__error__": "message"}` | Fatal engine error |

## 8. Email delivery

Configured per prompt file — recipient list, subject format, and body template
are all in the `.md` file. The engine does not know or care about email content.

Subject convention (from `bank_link_sr.md`):
```
[Bank Link SR] ✅ Bình thường — SR {sr}% — {dd/mm/yyyy}
[Bank Link SR] ⚠️ Cảnh báo — SR {sr}% — {dd/mm/yyyy}
[Bank Link SR] 🔴 Nghiêm trọng — SR {sr}% — {dd/mm/yyyy}
```

If `SMTP_HOST` is not set, `send_email` is a no-op — the UI shows
`"📧 Bỏ qua gửi email (SMTP chưa được cấu hình)"` and the agent
proceeds to `done` normally. No error is raised.

## 9. Teams integration

Optional. If `TEAMS_WEBHOOK_URL` is set, a plain-text summary is posted after
each run. Format and behaviour unchanged from v1.

## 10. API

### `GET /`
Serves the web UI (HTML). No auth.

### `GET /run/stream?token=<API_TOKEN>`
Server-Sent Events stream. Returns SSE events as described in section 7.

### `POST /run`
Run all monitoring flows synchronously and return JSON report.

**Auth:** `Authorization: Bearer <API_TOKEN>`

**Response:**
```json
{
  "status": "all_clear" | "issues_found",
  "run_ts": "2026-06-16T08:00:00",
  "anomaly_count": 1,
  "total_checked": 2,
  "checked_names": ["Giám sát Bank Link SR"],
  "anomalies": [
    {
      "check_id": "bank_link_sr",
      "name": "Giám sát Bank Link SR",
      "status": "warning",
      "summary": "...",
      "analysis": "...",
      "recommendations": ["..."],
      "extra_charts_fetched": 4
    }
  ],
  "normal": [
    { "check_id": "...", "name": "...", "summary": "..." }
  ],
  "errors": []
}
```

### `GET /health`
Returns `{"status": "ok"}`. No auth.

### `--dry-run` (CLI)
```bash
python -m src.main --dry-run
```
Runs the full engine, prints the report JSON to stdout. Does **not** post to
Teams (even if `TEAMS_WEBHOOK_URL` is set).

## 11. Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SUPERSET_BASE_URL` | Yes | — | Superset instance base URL |
| `SUPERSET_USERNAME` | Yes | — | Superset login username |
| `SUPERSET_PASSWORD` | Yes | — | Superset login password |
| `LLM_BASE_URL` | Yes | — | OpenAI-compatible LLM endpoint |
| `LLM_API_KEY` | Yes | — | LLM API key |
| `LLM_MODEL` | Yes | — | Model path (e.g. `minimax/minimax-m2.5`) |
| `API_TOKEN` | Yes | — | Token for `POST /run` and `/run/stream` |
| `PROMPTS_DIR` | No | `./prompts` | Directory containing `*.md` prompt files |
| `RUN_LOG_DIR` | No | `./logs` | Directory for JSONL run logs |
| `TEAMS_WEBHOOK_URL` | No | — | Teams incoming webhook URL; skip if unset |
| `SMTP_HOST` | No | — | SMTP server hostname; skip email if unset |
| `SMTP_PORT` | No | `587` | SMTP port (STARTTLS) |
| `SMTP_USER` | No | — | SMTP login username |
| `SMTP_PASSWORD` | No | — | SMTP login password |
| `EMAIL_FROM` | No | `SMTP_USER` | From address; falls back to `SMTP_USER` |

## 12. Error handling & reliability

- **Per-flow isolation** — a failing flow is recorded as an error `CheckResult`;
  all other flows continue.
- **Superset connectivity** — 2 retries with exponential backoff per request.
  Chart fetch errors are surfaced as tool error responses to the LLM, which
  concludes with what it has.
- **LLM non-JSON response** — nudge retry (append bad response + JSON
  instruction, continue loop). Max tokens set to 4096 to avoid truncation.
- **Step budget exhaustion** — LLM is prompted for a best-effort conclusion.
- **Double email prevention** — after `send_email`, model is explicitly told not
  to send again and must respond with `done`.
- **SMTP not configured** — `send_email` returns a skip message; no exception.
- **Teams posting failures** — captured in `report["teams_post_error"]`; never
  block or fail the run.
- **Run log** — per-run JSONL file records every `llm_step` (raw LLM response,
  action, tool result, finish reason) for post-hoc debugging of
  misclassifications.

## 13. Logging (per-step)

Every LLM call within a flow is written as a `llm_step` event to the run JSONL
log. Fields:

| Field | Description |
|---|---|
| `ts` | UTC timestamp |
| `event` | `"llm_step"` |
| `check_id` | Prompt file stem |
| `step` | Step number within this flow |
| `action` | Parsed action name (or `"(invalid_json)"`) |
| `llm_raw` | Full raw LLM response string |
| `finish_reason` | OpenAI finish reason (`stop`, `length`, etc.) |
| `args` | Tool arguments (trimmed for email body) |
| `tool_result` | Tool output appended to the message history |

For `done` steps: additionally logs `is_abnormal`, `status`, `summary`,
`analysis`, `recommendations`.

Use these logs to diagnose cases where the web UI shows "normal" but the
dashboard data should have triggered an alert.

## 14. File map

```
nck-claw-a-thon/
├── prompts/
│   └── bank_link_sr.md         # Bank Link SR monitoring flow
├── mocks/
│   └── sr_theo_ngan_hang.csv   # 30-day mock SR data (10 banks)
├── src/
│   ├── engine.py               # LLM agent loop; SSE emits; per-step logging
│   ├── superset_client.py      # Superset REST API client (client-side filtering)
│   ├── report_builder.py       # Assemble CheckResults → report JSON
│   ├── run_log.py              # JSONL audit log writer
│   └── main.py                 # FastAPI app: web UI, SSE, /run, Teams poster
├── tests/
│   ├── test_config.py
│   ├── test_report_builder.py
│   └── test_superset_client.py
├── logs/                       # Per-run JSONL logs (gitignored)
├── Dockerfile
├── requirements.txt
└── .env.example
```

# Dashboard Monitoring Assistant — Design Spec

**Date:** 2026-06-11
**Last updated:** 2026-06-14
**Status:** Implemented (v1)
**Owner:** (you)

## 1. Purpose

A personal work assistant for a fintech team that automates a repetitive daily
workflow: scanning Apache Superset dashboards, detecting abnormal metrics,
investigating the root cause, and reporting findings in plain language to
Microsoft Teams.

It replaces the manual morning routine of opening multiple dashboards, reading
key numbers, comparing them against thresholds and past periods, and
investigating anything that looks off.

### Core workflow

```
Load prompt files → for each flow: LLM agent fetches charts, investigates,
  concludes (normal / warning / critical)
  → post plain-text report to Teams channel
```

## 2. Scope

### In scope (v1 — implemented)
- Scheduled daily run (morning report) and on-demand "run now" via any HTTP
  caller (cron job, curl, external scheduler).
- Reading chart data from Apache Superset via direct REST API.
- **Prompt-driven flows:** each monitoring flow is a natural-language `.md` file
  that tells the LLM what to check, what counts as abnormal, how to investigate,
  and what to report. No fixed rules or config schema required.
- **LLM agent loop per flow:** the LLM autonomously fetches charts
  (`get_chart_data`, `list_charts`), reasons over the data, and concludes with a
  structured finding (normal / warning / critical + summary + analysis +
  recommendations). Up to 12 steps per flow.
- Plain-language reporting to a Microsoft Teams channel via incoming webhook
  (plain text/markdown message posted directly by the agent).

### Out of scope (later phases)
- Email delivery (needs Microsoft Graph/Outlook access).
- Power Automate / Teams Workflow scheduling (requires premium license — use
  external cron instead).
- Adaptive Card rich formatting in Teams (currently plain text).
- Free-form conversational Q&A in Teams.
- Taking actions / writing back to any system (read-only by design).
- Multi-agent coordination or memory across runs.

## 3. Approach

**Prompt-driven LLM agent per flow.**

Each monitoring flow is fully described in a natural-language `.md` file. The
file is both the configuration and the instructions — it tells the agent what
charts to fetch, what constitutes an anomaly, how to distinguish local vs
systemic issues, and what format to report in. There is no separate YAML
config, no fixed rule schema, and no hard-coded thresholds.

This was chosen over the original declarative playbook + rule engine approach
because:
- Rules in YAML are brittle and don't reflect the nuanced, context-dependent
  logic that domain experts (PO/Biz) actually apply.
- Prompt files are writable and maintainable by non-technical stakeholders
  without touching code or config schemas.
- The LLM can handle the full range of "what's normal" reasoning — trend
  analysis, contribution weighting, local vs systemic distinction — that fixed
  threshold/deviation rules cannot.

### What kind of system is this?
A **prompt-driven LLM agent loop**: the LLM drives all control flow for each
flow — deciding which charts to fetch, when it has enough data to conclude, and
what the finding means. The harness only enforces a step budget (max 12 steps)
and read-only tool access. This is a fully agentic approach, constrained by the
prompt file and the step cap.

## 4. Architecture

```
  External scheduler ──▶  POST /run  (Bearer token auth)
  (cron / curl)               │
                              ▼
                    ┌─────────────────────────────────┐
                    │       Monitoring Engine          │
                    │                                  │──▶ Superset REST API
                    │  scan PROMPTS_DIR for *.md files │     (get_chart_data,
                    │  for each prompt file:           │      list_charts)
                    │    LLM agent loop                │
                    │    (up to 12 steps)         ──▶ LLM (GreenNode AgentBase
                    │    → CheckResult                 │     MaaS, OpenAI-compat)
                    │  assemble report JSON            │
                    └──────────────┬───────────────────┘
                                   │
                                   ▼
                    POST to Teams incoming webhook
                    (plain text message)
                                   │
                                   ▼
                           Teams channel
```

### Components

- **Prompt files (`prompts/*.md`)** — the maintained artifact. Each `.md` file
  is one monitoring flow. Filename becomes the check ID. Written in natural
  language by PO/Biz or engineers.
- **Monitoring engine (`src/engine.py`)** — scans `PROMPTS_DIR` for `*.md`
  files, runs each through the LLM agent loop, collects `CheckResult` objects.
- **LLM agent loop** — per-flow agentic loop inside the engine. LLM receives the
  prompt as its user message and calls `get_chart_data`/`list_charts` tools via
  JSON responses until it concludes with `{"action": "done", ...}`.
- **Superset client (`src/superset_client.py`)** — read-only REST API wrapper
  with retry/backoff. Exposes `get_chart_data(chart_id)` and
  `list_charts(dashboard_id)`.
- **Report builder (`src/report_builder.py`)** — assembles `CheckResult` list
  into a report dict; sorts anomalies critical → warning.
- **Teams notifier (`src/main.py`)** — formats and POSTs a plain-text message
  to the Teams incoming webhook after each run.
- **Run log (`src/run_log.py`)** — per-run JSONL audit log in `RUN_LOG_DIR`.

### Hosting
- GreenNode AgentBase runtime (Docker container, FastAPI).
- LLM: GreenNode AgentBase MaaS (`minimax/minimax-m2.5`), accessed via
  OpenAI-compatible SDK. Model is configurable via `LLM_MODEL` env var.
- Superset credentials stored as AgentBase secrets.
- **Stateless** — no memory store. Each run is independent.

### Integrations & dependencies
- **Superset** — direct REST API v1 (`/api/v1/chart/data`,
  `/api/v1/dashboard/{id}/charts`). No MCP at runtime.
- **LLM** — OpenAI-compatible endpoint at GreenNode MaaS
  (`https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1`).
- **Teams** — incoming webhook (plain text message). The agent posts directly;
  no Power Automate or Teams Workflow required.
- **Scheduling** — external (cron job, curl, any HTTP caller hitting `POST /run`
  with a bearer token). Not managed by the agent.

## 5. Prompt files

### Location
`prompts/` directory (configurable via `PROMPTS_DIR` env var). The engine
discovers all `*.md` files at runtime — add a file, get a new flow; delete a
file, remove a flow. No code changes needed.

### What a prompt file contains
Everything the agent needs to execute one monitoring flow:
- Which charts to fetch and what they represent.
- What metrics to look at and what counts as normal vs abnormal.
- How to reason about the data (e.g. weight by volume, distinguish local vs
  systemic, compute contribution to total drop).
- What status to assign (normal / warning / critical) and under what conditions.
- What format to use for the report output.

### Example structure (`prompts/bank_link_sr.md`)
The Bank Link SR flow instructs the agent to:
1. Fetch the SR overview chart, the daily series chart, and the per-bank table.
2. Compute baseline from the 7–14 day series; flag if SR < 95%, drops ≥ 2pp
   vs yesterday or baseline, min SR < 90%, or more banks below SLA than yesterday.
3. If abnormal: classify as local (1-few banks) or systemic (≥60% banks drop
   together); rank banks by contribution to total SR drop.
4. Conclude with status, summary, analysis, and recommended actions.

### Agent conclusion schema
The LLM must end every flow with:
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
   a. Read prompt text — this is the full user message for the agent.
   b. Start LLM agent loop (max 12 steps):
      - LLM calls `get_chart_data(chart_id)` or `list_charts(dashboard_id)` to
        fetch data, or responds with `{"action": "done", ...}` to conclude.
      - Tool results are appended as user messages.
      - On step budget exhaustion, LLM is asked for a best-effort conclusion.
   c. Produce a `CheckResult` (check_id, check_name, is_abnormal, status,
      summary, analysis, recommendations, extra_charts_fetched, error).
3. Assemble report JSON (status, anomaly count, per-flow findings, errors).
4. Post plain-text message to Teams webhook (if `TEAMS_WEBHOOK_URL` is set).
5. Return report JSON to the HTTP caller.

On-demand and scheduled triggers call the same `POST /run` endpoint.

### Agent loop guardrails
- **Read-only** — tools are limited to `get_chart_data` and `list_charts`.
- **Step-bound** — hard cap of 12 steps per flow; best-effort conclusion on
  exhaustion.
- **Fully open chart access** — the prompt file is the scope constraint; no
  allowlist enforced at the harness level.
- **Per-flow isolation** — an error in one flow does not stop other flows.
- **Fail-safe** — unhandled exceptions produce an error `CheckResult`; the run
  always completes and always posts to Teams.

## 7. Teams integration

### Mechanism: incoming webhook (direct HTTP POST)
The agent posts a plain-text/markdown message to a Teams channel incoming
webhook URL. No Power Automate, no Teams Workflow, no premium license needed.

Configure by setting `TEAMS_WEBHOOK_URL` in the agent's environment. If not
set, Teams posting is skipped silently (useful for dry-run / local testing).

### Message format

**All clear:**
```
✅ Daily Report — 2026-06-14T08:00:00 UTC
All 2 checks are normal.

Checked: Bank Link SR, Transaction Volume
```

**Issues found:**
```
⚠️ Daily Report — 2026-06-14T08:00:00 UTC
1 of 2 checks need attention.

🔴 Bank Link SR
SR tổng 93.2%, giảm 4.1% so với hôm qua.
Lỗi cục bộ: Bank X kéo SR xuống 2.3 điểm %.
• Kiểm tra log gateway tới Bank X
• Liên hệ đầu mối kỹ thuật Bank X

Checked: Bank Link SR, Transaction Volume
```

### Format intent
- **Verdict first** (✅/⚠️/🔴 + count) for one-glance triage.
- Each anomaly: status icon, name, LLM-written summary, analysis, and
  bulleted recommended actions.
- **Sorted critical → warning** — worst findings first.
- **"Checked:" footer** — confirms full coverage.
- Teams posting errors are captured in the report JSON
  (`teams_post_error`) but never fail the run.

### Scheduling
Handled externally — cron job or any HTTP client calling `POST /run` with the
bearer token. The agent has no built-in scheduler.

## 8. API

### `POST /run`
Run all monitoring flows and post to Teams.

**Auth:** `Authorization: Bearer <API_TOKEN>`

**Response:**
```json
{
  "status": "all_clear" | "issues_found",
  "run_ts": "2026-06-14T08:00:00",
  "anomaly_count": 1,
  "total_checked": 2,
  "checked_names": ["Bank Link SR", "Transaction Volume"],
  "anomalies": [
    {
      "check_id": "bank_link_sr",
      "name": "Bank Link Sr",
      "status": "critical",
      "summary": "...",
      "analysis": "...",
      "recommendations": ["..."],
      "extra_charts_fetched": 2
    }
  ],
  "errors": [],
  "teams_post_error": null
}
```

### `GET /health`
Returns `{"status": "ok"}`. No auth required.

### `--dry-run` (CLI)
```bash
python -m src.main --dry-run
```
Runs the full engine, prints the report JSON to stdout. Does **not** post to
Teams (even if `TEAMS_WEBHOOK_URL` is set).

## 9. Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SUPERSET_BASE_URL` | Yes | — | Superset instance base URL |
| `SUPERSET_USERNAME` | Yes | — | Superset login username |
| `SUPERSET_PASSWORD` | Yes | — | Superset login password |
| `LLM_BASE_URL` | Yes | — | OpenAI-compatible LLM endpoint |
| `LLM_API_KEY` | Yes | — | LLM API key |
| `LLM_MODEL` | Yes | — | Model path (e.g. `minimax/minimax-m2.5`) |
| `API_TOKEN` | Yes | — | Bearer token for `POST /run` |
| `PROMPTS_DIR` | No | `./prompts` | Directory containing `*.md` prompt files |
| `RUN_LOG_DIR` | No | `./logs` | Directory for JSONL run logs |
| `TEAMS_WEBHOOK_URL` | No | — | Teams incoming webhook URL; skip posting if unset |

## 10. Error handling & reliability

- **Per-flow isolation** — a failing flow (Superset error, LLM error, parse
  error) is recorded as an error `CheckResult`; all other flows continue.
- **Superset connectivity** — 2 retries with exponential backoff per request.
  Chart fetch errors are surfaced as tool error responses to the LLM, which can
  then conclude with what it has.
- **LLM failures** — unhandled exceptions produce an error result; the run
  always completes.
- **Step budget exhaustion** — LLM is prompted for a best-effort conclusion
  rather than the run being aborted.
- **Teams posting failures** — captured in `report["teams_post_error"]`; never
  block or fail the run.
- **Run log** — per-run JSONL file records `run_start`, per-flow
  `check_complete`, and `run_complete` events for audit.

## 11. Testing strategy

- **Engine unit tests** — `CheckResult` defaults, prompt file discovery,
  `run_engine` raises on empty directory. LLM calls are not mocked (agentic loop
  is too dynamic; validate via dry-run against real Superset).
- **Report builder tests** — all-clear, single anomaly, mixed, sorting
  (critical → warning), error surfacing.
- **Superset client tests** — mocked HTTP responses for success, retry on
  timeout, failure after max retries, `list_charts`.
- **End-to-end dry run** — `python -m src.main --dry-run` against real Superset.
  Primary validation before go-live; review JSON output and Teams message
  manually before enabling the webhook.

## 12. File map

```
nck-claw-a-thon/
├── prompts/
│   └── bank_link_sr.md        # Bank Link SR monitoring flow (example)
├── src/
│   ├── engine.py              # LLM agent loop; scans prompts dir
│   ├── superset_client.py     # Superset REST API client
│   ├── report_builder.py      # Assemble CheckResults → report JSON
│   ├── run_log.py             # JSONL audit log writer
│   └── main.py                # FastAPI app, /run + /health, Teams poster
├── tests/
│   ├── test_config.py         # Engine CheckResult + file discovery tests
│   ├── test_report_builder.py # Report assembly tests
│   └── test_superset_client.py
├── Dockerfile
├── requirements.txt
└── .env.example
```

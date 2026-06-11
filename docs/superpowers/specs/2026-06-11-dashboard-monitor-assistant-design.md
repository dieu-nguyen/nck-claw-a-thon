# Dashboard Monitoring Assistant — Design Spec

**Date:** 2026-06-11
**Status:** Approved (design), pending implementation plan
**Owner:** (you)

## 1. Purpose

A personal work assistant for a fintech team that automates a repetitive daily
workflow: scanning Apache Superset dashboards, detecting abnormal metrics,
drilling into detail charts to find the reason, and reporting the findings in
plain language to Microsoft Teams.

It replaces the manual morning routine of opening multiple dashboards, reading
key numbers, comparing them against thresholds and past periods, and
investigating anything that looks off.

### Core workflow

```
Scan dashboards → detect abnormal (threshold breach OR deviation vs past)
  → drill into the detail chart(s) to find why
  → report the anomaly + its reason in plain language, via Teams.
```

## 2. Scope

### In scope (v1)
- Scheduled daily run (morning report) and on-demand "run now" from Teams.
- Reading metric values from specified Superset charts.
- Threshold rules and deviation-vs-past rules per metric.
- Ordered, multi-chart drill-down on anomalies.
- **Bounded LLM deep-dive** (hybrid): when the fixed drill-down isn't enough to
  explain an anomaly, the LLM may investigate additional charts within a
  whitelisted, read-only scope and budget.
- Plain-language reasoning over the drill-down trail (LLM).
- Reporting to a Teams channel/chat as an Adaptive Card.

### Out of scope (later phases)
- **Email summarizing** (Phase 2 — needs Microsoft Graph/Outlook access).
- Free-form conversational Q&A in Teams.
- Taking actions / writing back to any system (read-only by design).
- Unbounded autonomous exploration (the deep-dive is constrained by scope + budget).

## 3. Approach

**Approach A (hybrid) — Declarative Monitoring Playbook with bounded LLM deep-dive.**

Checks are declared once in a config file and the agent executes the playbook
**deterministically** each run. This deterministic backbone was chosen over a
fully autonomous exploratory agent (less predictable, harder to audit, costlier)
and over direct SQL on warehouse tables (bypasses the curated charts already
trusted, duplicates business logic).

On top of that backbone, a **bounded agentic escalation** is layered in: when the
fixed drill-down does not give the LLM enough to confidently explain an anomaly,
the LLM may autonomously investigate additional charts — but only within a
whitelisted, read-only scope and a strict budget (see §6.1). This gives the
flexibility of an agent for the hard cases while preserving determinism,
auditability, and predictable cost for the common case.

### What kind of system is this?
This is best described as **LLM-augmented automation with a bounded agentic
escalation**, not a fully autonomous agent. The LLM does not drive normal
control flow — the playbook + rule engine decide what to check, what is
abnormal, and the default drill-down. The LLM's role is (a) synthesizing the
plain-language reason, and (b) *only when it is low-confidence*, taking
constrained read-only investigative actions to find the root cause.

Rationale: matches the known/fixed drill-down paths, is auditable (every
conclusion traces to charts + rules — important in fintech), has predictable
cost in the common case, and is easy to extend by editing config — while still
handling complex anomalies that the fixed map can't fully explain.

## 4. Architecture

The agent is a single GreenNode AgentBase runtime exposing **one HTTP run
endpoint** ("run the playbook, return findings as JSON"). Teams Workflows
decide *when* to call it and *how* to render the result.

```
                    ┌─────────────────────────────────────────────┐
   Scheduler ──────▶│                                              │
   (Teams Workflow  │            Monitoring Engine                 │
    recurrence)     │                                              │──▶ Superset (read chart data)
                    │  1. load + validate playbook                 │
   Teams "run now"──▶│  2. for each check: fetch chart → eval rule  │──▶ LLM      (reason + deep-dive)
   (Teams Workflow) │  3. if abnormal: fetch fixed drill-down       │
                    │  4. LLM reasons; if low-confidence → bounded  │
                    │     deep-dive (read-only, scoped, budgeted)   │
                    │  5. assemble report (JSON)                   │
                    └──────────────────────┬───────────────────────┘
                                           │ findings JSON
                                           ▼
                              Teams Workflow renders Adaptive Card
                                           ▼
                                   Teams channel/chat
```

### Components
- **Playbook config** — declarative YAML list of checks (the maintained artifact).
- **Monitoring engine** — deterministic loop: fetch → evaluate → drill down →
  collect findings. Pure and testable in isolation.
- **Superset client** — reads chart data (via available Superset tooling).
  Exposes read-only tools `get_chart_data(chart_id)` and
  `list_charts(dashboard_id)` used by both the engine and the deep-dive loop.
- **LLM reporter** — turns structured findings into plain-language reasons + digest.
- **Deep-dive investigator** — bounded read-only loop the LLM may enter when
  low-confidence; explores whitelisted dashboards within a fetch/step budget (§6.1).
- **Report builder** — assembles findings into the JSON the Workflow renders.
- **Run log** — per-run, per-check record of what was fetched and concluded,
  including every deep-dive action and reasoning step (audit).

### Hosting
- GreenNode AgentBase runtime.
- Platform LLM (managed) for reasoning/summaries.
- Superset credentials stored as AgentBase secrets/identity.
- **Stateless** — no memory store required in v1 (baselines come from charts).

### Integrations & dependencies
- **Superset access — direct REST API (v1).** The agent calls Superset's
  chart-data endpoints directly over HTTP using stored credentials. No MCP
  server or gateway is required at run-time. The Superset client is implemented
  behind a small internal interface (`get_chart_data`, `list_charts`) so the
  data source could later be swapped for an MCP-backed implementation without
  touching the engine or deep-dive loop.
- **No MCP at run-time.** MCP/Resource Gateway is intentionally deferred; it
  would only be worthwhile to centralize auth/policy/audit across multiple agents.
- **No "skills" at run-time.** Cursor/AgentBase *skills* are build-time authoring
  aids (scaffolding, Superset/DataHub discovery, deployment); they are not a
  component of the running agent.
- **LLM** via AgentBase platform LLM (managed key).
- **Teams** is fully decoupled: the agent only returns JSON; Teams Workflows
  handle scheduling, the run trigger, and rendering.

## 5. Monitoring Playbook (config schema)

A YAML file listing checks plus a global `deep_dive` default. Each check = one
metric to watch.

```yaml
# Global default for the bounded LLM deep-dive (per-check override allowed)
deep_dive:
  enabled: auto              # auto | off
  trigger: low_confidence    # low_confidence | high_severity | always
  max_extra_charts: 5        # hard cap on additional charts per finding
  max_steps: 6               # hard cap on investigation loop iterations
  scope:
    dashboard_ids: [12, 18]  # the ONLY dashboards the LLM may explore

checks:
  - id: payment_success_rate
    name: "Payment Success Rate"
    summary_chart_id: 412          # Superset chart with the headline metric
    metric: success_rate           # which value to read from the chart result
    rules:
      - type: threshold            # absolute floor/ceiling
        op: ">="                   # >=, >, <=, <, ==
        value: 98.0
      - type: deviation            # vs past period
        compare_to: yesterday      # yesterday | last_week | 7d_avg
        max_drop_pct: 2.0          # flag if it drops more than this vs baseline
    drilldown:                     # ordered list, fetched in sequence on anomaly
      - chart_id: 415
        describe: "success rate broken down by payment method"
      - chart_id: 417
        describe: "success rate by bank / issuer"
      - chart_id: 419
        describe: "top failure reason codes"
    deep_dive: auto                # per-check override: auto | off
    severity: high                 # high | medium | low

  - id: txn_volume
    name: "Transaction Volume"
    summary_chart_id: 420
    metric: total_txns
    rules:
      - type: deviation
        compare_to: last_week
        max_drop_pct: 15.0
    drilldown:
      - chart_id: 421
        describe: "volume by channel and merchant"
    deep_dive: off                 # deterministic-only for this check
    severity: medium
```

### Field semantics
- `summary_chart_id` + `metric` — the headline number read each morning.
- `rules` — one or more. A check is **abnormal if ANY rule fails**.
  - `threshold` — absolute comparison (`op`, `value`).
  - `deviation` — compare current value vs `compare_to` baseline; fail if drop
    exceeds `max_drop_pct`.
- `drilldown` — ordered list of detail charts (the fixed map). Fetched in
  sequence **only when the check is abnormal**. Default behavior: **fetch all**
  charts in the list. (Optional future `stop_when` to short-circuit.)
- `deep_dive` — `auto` (allow bounded escalation per the global rules) or `off`
  (force deterministic-only for this check). Overrides the global default.
- `severity` — controls report ordering/surfacing.

### Deep-dive config (global `deep_dive`)
- `enabled` — `auto` lets checks escalate; `off` disables it system-wide.
- `trigger` — `low_confidence` (default; escalate only when the LLM cannot
  confidently explain the anomaly from the data it has), `high_severity` (only
  for high-severity anomalies), or `always` (any anomaly).
- `max_extra_charts` / `max_steps` — hard budget; the loop stops when either is
  reached and reports with what it has.
- `scope.dashboard_ids` — the **only** dashboards the LLM may read during a
  deep-dive. Anything outside this list is off-limits.

### Baselines
- The baseline for a `deviation` rule **always comes from the chart's own data**
  (e.g. a time series or period-over-period field in the same chart result).
  This keeps the agent's numbers identical to the dashboard — no drift.
- If a `deviation` rule is configured but the chart does **not** carry the past
  value, the agent **skips that comparison** (and notes it quietly), while still
  applying any `threshold` rules on the same check.
- No history/memory store in v1.

## 6. Run flow

1. **Load + validate playbook** (fail fast on bad config: missing chart ids,
   malformed rules).
2. For each check:
   a. Fetch `summary_chart_id` data via Superset.
   b. Read `metric`; evaluate all `rules`.
   c. If abnormal → fetch each `drilldown` chart in order.
   d. Hand summary + drill-down trail to the LLM → plain-language reason **plus a
      confidence self-assessment**.
   e. If the deep-dive `trigger` condition is met (e.g. low confidence) and
      deep-dive is enabled for this check → run the bounded investigation loop
      (§6.1), then re-derive the reason from the enriched trail.
3. Assemble all findings into report JSON (verdict, anomalies, checked list,
   deep-dive tags).
4. Return JSON to caller (Teams Workflow renders it).

On-demand and scheduled triggers call the **same engine** — identical output.

### 6.1 Bounded deep-dive loop

Triggered only for an abnormal check when `trigger` matches and deep-dive is
enabled. The loop is the single place the LLM drives control flow, and it is
tightly constrained:

```
state: anomaly + fixed drill-down trail; budget = (max_extra_charts, max_steps)
loop:
  1. LLM assesses: can I now explain the root cause confidently?
       yes  → exit loop
       no   → continue
  2. budget exhausted? → exit loop (report best-effort with what we have)
  3. LLM picks ONE next chart to inspect:
       - may call list_charts(dashboard_id) within scope.dashboard_ids
       - may call get_chart_data(chart_id) for a chart in scope
  4. fetched data appended to the trail; decrement budget; record in run log
exit: LLM writes the final plain-language reason from the full trail
```

**Invariants / guardrails**
- **Read-only only** — tools are limited to `list_charts` and `get_chart_data`.
  No SQL, no writes, no actions on any system.
- **Scope-bound** — only charts on `scope.dashboard_ids` are reachable; a request
  outside scope is refused and logged.
- **Budget-bound** — stops at `max_extra_charts` or `max_steps`, whichever first.
- **Fully logged** — every tool call, chart fetched, and reasoning step recorded
  for audit.
- **Fail-safe** — on timeout/error mid-loop, fall back to the deterministic
  findings + best-effort reason; the run never fails because of a deep-dive.
- **Transparent** — findings that used a deep-dive are tagged in the report
  (e.g. `🔎 deep-dive: examined N extra charts`).

## 7. Teams integration

### Mechanism: Teams Workflows (Power Automate)
Chosen over a full Azure Bot: bidirectional (post + trigger), lighter admin
approval footprint, no bot app to maintain. The agent stays decoupled — it only
exposes one HTTP endpoint and returns structured findings.

### Workflow 1 — On-demand "run now"
1. **Trigger:** keyword in a channel, or a manual "Run" button/message.
2. **Action:** HTTP → POST to the agent's run endpoint (auth header from a
   secret stored by admin).
3. **Action:** Post card in chat/channel — render the returned JSON as an
   Adaptive Card.

### Workflow 2 — Scheduled morning report
- **Trigger:** Recurrence (e.g. daily 08:00).
- **Action:** Same HTTP POST → same endpoint.
- **Action:** Post the Adaptive Card.
- Scheduling lives in the Workflow (not the agent) so timing is managed without
  redeploying.

> Admin team handles registration/permissions. The spec will include the exact
> click-by-click Workflow steps and the fields to fill in during implementation.

## 8. Report format

### All clear
```
✅ Daily Dashboard Check — 11 Jun 2026, 08:00
All 8 monitored metrics are within normal range.
```

### Issues found (Adaptive Card, anomalies sorted by severity)
```
⚠️ Daily Dashboard Check — 11 Jun 2026, 08:00
2 of 8 metrics need attention.

──────────────────────────────
🔴 HIGH · Payment Success Rate
   Now: 95.2%   |   Threshold: ≥98%   |   vs yesterday: −3.1%
   Why: The drop is concentrated in Bank X (card issuer),
   where declines tripled overnight. Top reason code is
   "do not honour" (54% of failures), pointing to an
   issuer-side problem rather than our gateway.
   🔎 deep-dive: examined 3 extra charts
   🔗 Open dashboard
──────────────────────────────
🟡 MEDIUM · Transaction Volume
   Now: 142k   |   vs last week: −18%
   Why: Volume fell mainly on the QR channel; web and app
   are normal. Aligns with the QR provider's maintenance
   window noted in the detail chart.
   🔗 Open dashboard
──────────────────────────────
Checked: Success Rate, Volume, Settlement, Refunds,
Chargebacks, Latency, Active Users, Revenue.
```

### Format intent
- **Verdict first** (✅/⚠️ + count) for one-glance triage.
- Each anomaly shows **numbers** (now vs threshold/baseline) **and** the
  plain-language **reason** from the drill-down trail.
- **Severity ordering + color dots** — worst first.
- **Deep-dive tag** — when a finding went beyond the fixed drill-down, it's
  flagged so you know the reasoning used extra investigation (transparency).
- **"Checked:" footer** — confirms full coverage, builds trust.
- **Deep link** back to Superset per finding.

## 9. Error handling & reliability

- **Per-check isolation:** a failing check is marked "⚠️ could not evaluate"
  with the reason; the run continues and other checks still report.
- **Failures surface in the report** (never a silent "all clear"):
  `⚠️ Could not check "Settlement Latency" — chart 430 returned no data (timeout).`
- **Superset connectivity:** short retry with backoff (e.g. 2 retries). If fully
  unreachable, post a single clear failure message to Teams.
- **LLM failures:** still report raw numbers + drill-down data without the prose.
- **Deep-dive failures:** on timeout, budget exhaustion, or error inside the
  loop, fall back to the deterministic findings + best-effort reason. A deep-dive
  never blocks or fails the run.
- **Run log:** per-run, per-check record (fetched data, rule outcome,
  conclusion, and every deep-dive tool call/step) for audit and debugging.
- **Config validation:** validate the playbook on startup; typos fail fast with
  a clear message.

## 10. Testing strategy

- **Engine unit tests:** canned chart payloads (normal / threshold breach /
  deviation breach / missing baseline) → assert correct anomalies + severities.
  Pure functions, no network.
- **Playbook validation tests:** malformed configs rejected with helpful errors.
- **Superset client tests:** mocked responses incl. timeouts and empty data;
  verify per-check isolation and retry behavior.
- **Report formatting tests:** findings → correct render JSON (all-clear, single
  anomaly, multiple severities, "could not evaluate", deep-dive tag).
- **Deep-dive guardrail tests:** loop respects `max_extra_charts`/`max_steps`;
  refuses charts outside `scope.dashboard_ids`; falls back cleanly on
  timeout/error; never calls a write/non-read-only tool. Use a mocked LLM that
  requests an out-of-scope chart and an over-budget number of steps.
- **Trigger tests:** `low_confidence` / `high_severity` / `always` each escalate
  in the right cases and not otherwise.
- **End-to-end dry run:** `--dry-run` mode runs the real playbook against
  Superset and prints the report to console — no Teams, no schedule. Primary
  way to validate against real dashboards before go-live.

## 11. Open items for implementation planning

- Exact Superset chart-data API/tooling calls and the shape of chart results
  (how `metric` and baseline fields are located in a result).
- Auth model for the agent's run endpoint (header/secret managed by admin).
- Concrete Adaptive Card JSON template.
- Click-by-click Teams Workflow setup steps for the admin team.
- Initial real playbook content (the actual charts/metrics/thresholds to watch).
- How the LLM's confidence self-assessment is captured/measured to drive the
  `low_confidence` trigger (e.g. structured "confident: yes/no + why" output).
- Default deep-dive budget values (`max_extra_charts`, `max_steps`) and per-run
  cost ceiling.
```
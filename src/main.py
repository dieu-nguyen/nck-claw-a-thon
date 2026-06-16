from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Security
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.engine import run_engine
from src.report_builder import build_report
from src.run_log import RunLog
from src.superset_client import SupersetClient, SupersetError

load_dotenv()

app = FastAPI(title="Dashboard Monitoring Assistant")
_bearer = HTTPBearer()


def _get_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def _format_teams_message(report: dict) -> str:
    run_ts = report.get("run_ts", "")
    anomaly_count = report.get("anomaly_count", 0)
    total_checked = report.get("total_checked", 0)
    checked_names = report.get("checked_names", [])
    anomalies = report.get("anomalies", [])

    if anomaly_count == 0:
        header_line = f"✅ Daily Report — {run_ts} UTC"
        summary_line = f"All {total_checked} checks are normal."
    else:
        icon = "🔴" if anomaly_count == total_checked else "⚠️"
        header_line = f"{icon} Daily Report — {run_ts} UTC"
        summary_line = f"{anomaly_count} of {total_checked} checks need attention."

    lines = [header_line, summary_line]

    for a in anomalies:
        status = a.get("status", "")
        name = a.get("name", "")
        summary = a.get("summary", "")
        analysis = a.get("analysis", "")
        recommendations = a.get("recommendations", [])

        status_icon = "⚠️" if status == "warning" else "🔴"
        lines.append("")
        lines.append(f"{status_icon} {name}")
        if summary:
            lines.append(summary)
        if analysis:
            lines.append(analysis)
        if recommendations:
            for rec in recommendations:
                lines.append(f"• {rec}")

    for err in report.get("errors", []):
        lines.append("")
        lines.append(f"❌ ERROR · {err.get('name', '')} — {err.get('message', '')}")

    lines.append("")
    lines.append(f"Checked: {', '.join(checked_names)}")

    return "\n".join(lines)


async def _post_to_teams(webhook_url: str, message_text: str) -> None:
    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.2",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": message_text,
                            "wrap": True,
                            "fontType": "Monospace",
                        }
                    ],
                },
            }
        ],
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(webhook_url, json=payload)
        resp.raise_for_status()


async def _run(dry_run: bool = False) -> dict:
    prompts_dir = os.getenv("PROMPTS_DIR", "./prompts")
    log_dir = os.getenv("RUN_LOG_DIR", "./logs")
    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    superset = SupersetClient(
        base_url=_get_env("SUPERSET_BASE_URL"),
        username=_get_env("SUPERSET_USERNAME"),
        password=_get_env("SUPERSET_PASSWORD"),
    )

    with RunLog(log_dir=log_dir, run_ts=run_ts) as log:
        log.write("run_start", prompts_dir=prompts_dir, dry_run=dry_run)

        results = await run_engine(prompts_dir, superset)

        for cr in results:
            log.write(
                "check_complete",
                check_id=cr.check_id,
                is_abnormal=cr.is_abnormal,
                status=cr.status,
                error=cr.error,
            )

        report = build_report(results=results, run_ts=run_ts)
        log.write(
            "run_complete",
            status=report["status"],
            anomaly_count=report["anomaly_count"],
        )

    teams_webhook_url = os.getenv("TEAMS_WEBHOOK_URL", "")
    if teams_webhook_url and not dry_run:
        try:
            message_text = _format_teams_message(report)
            await _post_to_teams(teams_webhook_url, message_text)
        except Exception as e:
            report["teams_post_error"] = str(e)

    return report


@app.post("/run")
async def run_endpoint(credentials: HTTPAuthorizationCredentials = Security(_bearer)):
    expected = os.getenv("API_TOKEN", "")
    if not expected or credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        report = await _run()
        return Response(
            content=json.dumps(report, ensure_ascii=False),
            media_type="application/json; charset=utf-8",
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except SupersetError as e:
        raise HTTPException(status_code=502, detail=f"Superset unreachable: {e}") from e


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard_ui():
    token = os.getenv("API_TOKEN", "")
    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dashboard Monitor</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f1f5f9;
      color: #1e293b;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 48px 24px;
    }}
    h1 {{ font-size: 1.5rem; font-weight: 600; margin-bottom: 8px; color: #0f172a; }}
    .subtitle {{ font-size: 0.875rem; color: #64748b; margin-bottom: 40px; }}
    #run-btn {{
      padding: 12px 36px;
      font-size: 1rem;
      font-weight: 600;
      border: none;
      border-radius: 8px;
      cursor: pointer;
      background: #3b82f6;
      color: #fff;
      transition: background 0.2s, transform 0.1s;
    }}
    #run-btn:hover:not(:disabled) {{ background: #2563eb; }}
    #run-btn:active:not(:disabled) {{ transform: scale(0.97); }}
    #run-btn:disabled {{ background: #bfdbfe; color: #93c5fd; cursor: not-allowed; }}
    #status {{
      margin-top: 24px;
      font-size: 0.875rem;
      color: #64748b;
      min-height: 20px;
    }}
    #spinner {{
      display: none;
      margin-top: 24px;
      width: 32px; height: 32px;
      border: 3px solid #e2e8f0;
      border-top-color: #3b82f6;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    #report {{ display: none; margin-top: 40px; width: 100%; max-width: 800px; }}
    .report-header {{
      padding: 20px 24px;
      border-radius: 10px 10px 0 0;
      font-size: 1.125rem;
      font-weight: 700;
    }}
    .all-clear {{ background: #f0fdf4; border: 1px solid #86efac; color: #15803d; }}
    .issues {{ background: #fff1f2; border: 1px solid #fca5a5; color: #b91c1c; }}
    .meta {{
      padding: 12px 24px;
      background: #f8fafc;
      font-size: 0.8rem;
      color: #94a3b8;
      border-left: 1px solid #e2e8f0;
      border-right: 1px solid #e2e8f0;
    }}
    .anomaly {{
      margin-top: 1px;
      padding: 20px 24px;
      background: #fff;
      border-left: 4px solid #ef4444;
      border-right: 1px solid #e2e8f0;
    }}
    .anomaly.warning {{ border-left-color: #f59e0b; }}
    .anomaly-name {{ font-weight: 700; font-size: 1rem; margin-bottom: 8px; color: #0f172a; }}
    .anomaly-summary {{ font-size: 0.9rem; color: #334155; margin-bottom: 10px; }}
    .anomaly-analysis {{ font-size: 0.85rem; color: #475569; margin-bottom: 12px; white-space: pre-wrap; }}
    .recs {{ list-style: none; }}
    .recs li {{ font-size: 0.85rem; color: #475569; padding: 3px 0; }}
    .recs li::before {{ content: "→ "; color: #3b82f6; }}
    .error-block {{
      margin-top: 1px;
      padding: 16px 24px;
      background: #fffbeb;
      border-left: 4px solid #f59e0b;
      border-right: 1px solid #e2e8f0;
      font-size: 0.85rem;
      color: #92400e;
    }}
    .footer {{
      padding: 14px 24px;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-top: none;
      border-radius: 0 0 10px 10px;
      font-size: 0.8rem;
      color: #94a3b8;
    }}
    .normal-block {{
      margin-top: 1px;
      padding: 16px 24px;
      background: #fff;
      border-left: 1px solid #e2e8f0;
      border-right: 1px solid #e2e8f0;
      font-size: 0.875rem;
      color: #475569;
    }}
  </style>
</head>
<body>
  <h1>🔍 Dashboard Monitor</h1>
  <p class="subtitle">Bank Link SR — Monitoring Assistant</p>
  <button id="run-btn" onclick="runMonitor()">▶ Run Now</button>
  <div id="status"></div>
  <div id="spinner"></div>
  <div id="report"></div>

  <script>
    const API_TOKEN = "{token}";
    let running = false;

    async function runMonitor() {{
      if (running) return;
      running = true;

      document.getElementById("run-btn").disabled = true;
      document.getElementById("run-btn").textContent = "Running…";
      document.getElementById("report").style.display = "none";
      document.getElementById("report").innerHTML = "";
      document.getElementById("spinner").style.display = "block";
      document.getElementById("status").textContent = "Agent is fetching data and analysing…";

      try {{
        const res = await fetch("/run", {{
          method: "POST",
          headers: {{ "Authorization": "Bearer " + API_TOKEN }}
        }});
        if (!res.ok) {{
          const err = await res.json().catch(() => ({{}}));
          throw new Error(err.detail || res.statusText);
        }}
        const data = await res.json();
        renderReport(data);
        document.getElementById("status").textContent = "";
      }} catch (e) {{
        document.getElementById("status").textContent = "❌ Error: " + e.message;
      }} finally {{
        running = false;
        document.getElementById("spinner").style.display = "none";
        document.getElementById("run-btn").disabled = false;
        document.getElementById("run-btn").textContent = "▶ Run Again";
      }}
    }}

    function renderReport(r) {{
      const el = document.getElementById("report");
      const ts = r.run_ts || "";
      const anomalyCount = r.anomaly_count || 0;
      const total = r.total_checked || 0;
      const checked = (r.checked_names || []).join(", ");
      const isOk = anomalyCount === 0 && (r.errors || []).length === 0;

      let html = "";

      // Header
      if (isOk) {{
        html += `<div class="report-header all-clear">✅ All Clear — ${{anomalyCount === 0 ? "All checks normal" : ""}}</div>`;
      }} else {{
        html += `<div class="report-header issues">⚠️ ${{anomalyCount}} issue${{anomalyCount !== 1 ? "s" : ""}} found</div>`;
      }}

      html += `<div class="meta">Run: ${{ts}} UTC &nbsp;·&nbsp; Checked ${{total}} flow${{total !== 1 ? "s" : ""}}</div>`;

      // Anomalies
      for (const a of (r.anomalies || [])) {{
        const isCrit = a.status === "critical";
        const icon = isCrit ? "🔴" : "⚠️";
        const cls = isCrit ? "" : "warning";
        html += `<div class="anomaly ${{cls}}">`;
        html += `<div class="anomaly-name">${{icon}} ${{a.name}}</div>`;
        if (a.summary) html += `<div class="anomaly-summary">${{a.summary}}</div>`;
        if (a.analysis) html += `<div class="anomaly-analysis">${{a.analysis}}</div>`;
        if (a.recommendations && a.recommendations.length) {{
          html += `<ul class="recs">`;
          for (const rec of a.recommendations) html += `<li>${{rec}}</li>`;
          html += `</ul>`;
        }}
        html += `</div>`;
      }}

      // Normal flows (all clear)
      if (isOk) {{
        for (const name of (r.checked_names || [])) {{
          html += `<div class="normal-block">✅ ${{name}} — normal</div>`;
        }}
      }}

      // Errors
      for (const e of (r.errors || [])) {{
        html += `<div class="error-block">❌ ${{e.name}} — ${{e.message}}</div>`;
      }}

      // Footer
      html += `<div class="footer">Checked: ${{checked}}</div>`;

      el.innerHTML = html;
      el.style.display = "block";
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        report = asyncio.run(_run(dry_run=True))
        print(json.dumps(report, indent=2, default=str))
    else:
        import uvicorn
        uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=False)

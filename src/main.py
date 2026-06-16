from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Security
from fastapi.responses import HTMLResponse, Response, StreamingResponse
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

        results = await run_engine(prompts_dir, superset, log=log)

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


@app.get("/run/stream")
async def run_stream(token: str = Query(...)):
    expected = os.getenv("API_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="Invalid token")

    async def event_stream():
        import asyncio
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def on_step(msg: str) -> None:
            await queue.put(msg)

        async def run_task():
            try:
                prompts_dir = os.getenv("PROMPTS_DIR", "./prompts")
                log_dir = os.getenv("RUN_LOG_DIR", "./logs")
                run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                superset = SupersetClient(
                    base_url=_get_env("SUPERSET_BASE_URL"),
                    username=_get_env("SUPERSET_USERNAME"),
                    password=_get_env("SUPERSET_PASSWORD"),
                )
                with RunLog(log_dir=log_dir, run_ts=run_ts) as log:
                    log.write("run_start", prompts_dir=prompts_dir, dry_run=False)
                    results = await run_engine(prompts_dir, superset, on_step=on_step, log=log)
                    for cr in results:
                        log.write("check_complete", check_id=cr.check_id,
                                  is_abnormal=cr.is_abnormal, status=cr.status, error=cr.error)
                    report = build_report(results=results, run_ts=run_ts)
                    log.write("run_complete", status=report["status"],
                              anomaly_count=report["anomaly_count"])
                teams_webhook_url = os.getenv("TEAMS_WEBHOOK_URL", "")
                if teams_webhook_url:
                    try:
                        message_text = _format_teams_message(report)
                        await _post_to_teams(teams_webhook_url, message_text)
                    except Exception as e:
                        report["teams_post_error"] = str(e)
                await queue.put(json.dumps({"__report__": report}, ensure_ascii=False))
            except Exception as e:
                await queue.put(json.dumps({"__error__": str(e)}, ensure_ascii=False))
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_task())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {item}\n\n"
        finally:
            task.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard_ui():
    token = os.getenv("API_TOKEN", "")
    superset_base = os.getenv("SUPERSET_BASE_URL", "").rstrip("/")
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
      margin-top: 20px;
      font-size: 0.875rem;
      color: #64748b;
      min-height: 20px;
      text-align: center;
    }}
    #hint {{
      margin-top: 8px;
      font-size: 0.8rem;
      color: #94a3b8;
      min-height: 16px;
      text-align: center;
    }}
    #timer {{
      margin-top: 6px;
      font-size: 0.8rem;
      color: #94a3b8;
      font-variant-numeric: tabular-nums;
      min-height: 16px;
      text-align: center;
    }}
    #spinner {{
      display: none;
      margin-top: 20px;
      width: 32px; height: 32px;
      border: 3px solid #e2e8f0;
      border-top-color: #3b82f6;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    #task-boxes {{
      margin-top: 24px;
      width: 100%;
      max-width: 1200px;
      display: none;
      flex-direction: row;
      flex-wrap: wrap;
      gap: 16px;
    }}
    .task-box {{
      background: #fff;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      overflow: hidden;
      flex: 1 1 calc(50% - 8px);
      min-width: 320px;
    }}
    /* colour-coded left border per status */
    .task-box.status-ok    {{ border-left: 4px solid #22c55e; }}
    .task-box.status-warning {{ border-left: 4px solid #f59e0b; }}
    .task-box.status-critical {{ border-left: 4px solid #ef4444; }}
    .task-box.status-error  {{ border-left: 4px solid #f97316; }}
    /* ── header row ── */
    .task-box-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 18px;
      background: #f8fafc;
      border-bottom: 1px solid #e2e8f0;
      font-weight: 600;
      font-size: 0.9rem;
      color: #0f172a;
    }}
    .task-status-badge {{
      font-size: 0.75rem;
      font-weight: 500;
      padding: 2px 10px;
      border-radius: 999px;
      background: #e2e8f0;
      color: #64748b;
      white-space: nowrap;
    }}
    .task-status-badge.running  {{ background: #dbeafe; color: #1d4ed8; }}
    .task-status-badge.ok       {{ background: #dcfce7; color: #15803d; }}
    .task-status-badge.warning  {{ background: #fef9c3; color: #92400e; }}
    .task-status-badge.critical {{ background: #fee2e2; color: #991b1b; }}
    /* ── status banner (shown after done) ── */
    .task-status-bar {{
      display: none;
      padding: 10px 18px;
      font-size: 0.9rem;
      font-weight: 600;
      border-bottom: 1px solid #e2e8f0;
    }}
    .task-status-bar.ok       {{ background: #f0fdf4; color: #15803d; }}
    .task-status-bar.warning  {{ background: #fffbeb; color: #92400e; }}
    .task-status-bar.critical {{ background: #fef2f2; color: #991b1b; }}
    /* ── meta bar (time, elapsed, link) ── */
    .task-meta {{
      display: none;
      padding: 7px 18px;
      font-size: 0.78rem;
      color: #94a3b8;
      background: #f8fafc;
      border-bottom: 1px solid #f1f5f9;
      gap: 16px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .task-meta a {{ color: #3b82f6; text-decoration: none; }}
    .task-meta a:hover {{ text-decoration: underline; }}
    /* ── result body ── */
    .task-box-result {{
      display: none;
      padding: 14px 18px;
      font-size: 0.875rem;
      border-bottom: 1px solid #f1f5f9;
    }}
    .task-result-summary {{ color: #334155; margin-bottom: 8px; }}
    .task-result-analysis {{ font-size: 0.83rem; color: #475569; white-space: pre-wrap; margin-bottom: 10px; }}
    .task-result-recs {{ list-style: none; margin-top: 4px; }}
    .task-result-recs li {{ font-size: 0.83rem; color: #475569; padding: 2px 0; }}
    .task-result-recs li::before {{ content: "→ "; color: #3b82f6; }}
    /* ── step toggle & steps ── */
    .task-box-steps-toggle {{
      display: none;
      padding: 7px 18px;
      font-size: 0.75rem;
      color: #94a3b8;
      cursor: pointer;
      user-select: none;
      border-bottom: 1px solid #f1f5f9;
    }}
    .task-box-steps-toggle:hover {{ color: #64748b; }}
    .task-box-steps {{
      padding: 10px 18px 14px;
      font-size: 0.83rem;
      color: #475569;
      line-height: 1.9;
      background: #fafafa;
    }}
    .task-box-steps.collapsed {{ display: none; }}
  </style>
</head>
<body>
  <h1>🔍 Dashboard Monitor</h1>
  <p class="subtitle">Bank Link SR — Monitoring Assistant</p>
  <button id="run-btn" onclick="runMonitor()">▶ Chạy</button>
  <div id="status"></div>
  <div id="hint"></div>
  <div id="timer"></div>
  <div id="spinner"></div>
  <div id="task-boxes"></div>

  <script>
    const API_TOKEN = "{token}";
    const SUPERSET_BASE = "{superset_base}";
    // map check_id → superset dashboard URL (populated when task result arrives)
    const taskDashboardLinks = {{}};
    let running = false;
    let timerInterval = null;
    let startTime = null;

    function startTimer() {{
      startTime = Date.now();
      timerInterval = setInterval(() => {{
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        const m = Math.floor(elapsed / 60);
        const s = elapsed % 60;
        document.getElementById("timer").textContent =
          `⏱ ${{m > 0 ? m + "m " : ""}}${{s}}s`;
      }}, 1000);
    }}

    function stopTimer() {{
      clearInterval(timerInterval);
      timerInterval = null;
    }}

    // Per-task box tracking
    const taskBoxes = {{}};  // check_id → {{ stepsEl, resultEl, headerBadge, boxEl }}

    function openTaskBox(taskName, checkId) {{
      const boxes = document.getElementById("task-boxes");
      boxes.style.display = "flex";

      const box = document.createElement("div");
      box.className = "task-box";
      box.id = "task-" + checkId;

      // 1. Header row: task name + running badge
      const badge = document.createElement("span");
      badge.className = "task-status-badge running";
      badge.textContent = "Đang chạy…";
      const header = document.createElement("div");
      header.className = "task-box-header";
      header.innerHTML = `<span>🔄 ${{taskName}}</span>`;
      header.appendChild(badge);

      // 2. Status banner (hidden until done)
      const statusBar = document.createElement("div");
      statusBar.className = "task-status-bar";

      // 3. Meta bar: time + elapsed + dashboard link (hidden until done)
      const metaBar = document.createElement("div");
      metaBar.className = "task-meta";

      // 4. Result body: summary / analysis / recs (hidden until done)
      const resultEl = document.createElement("div");
      resultEl.className = "task-box-result";

      // 5. Step toggle (hidden until done)
      const stepsEl = document.createElement("div");
      stepsEl.className = "task-box-steps";
      const stepsToggle = document.createElement("div");
      stepsToggle.className = "task-box-steps-toggle";
      stepsToggle.textContent = "▶ Xem các bước thực hiện";
      stepsToggle.addEventListener("click", () => {{
        const collapsed = stepsEl.classList.toggle("collapsed");
        stepsToggle.textContent = collapsed ? "▶ Xem các bước thực hiện" : "▼ Ẩn các bước thực hiện";
      }});

      box.appendChild(header);
      box.appendChild(statusBar);
      box.appendChild(metaBar);
      box.appendChild(resultEl);
      box.appendChild(stepsToggle);
      box.appendChild(stepsEl);
      boxes.appendChild(box);

      taskBoxes[checkId] = {{ stepsEl, stepsToggle, statusBar, metaBar, resultEl, headerBadge: badge, headerEl: header, boxEl: box, taskName }};
    }}

    function addStep(checkId, msg) {{
      if (!taskBoxes[checkId]) return;
      const {{ stepsEl }} = taskBoxes[checkId];
      const line = document.createElement("div");
      line.textContent = msg;
      stepsEl.appendChild(line);
    }}

    function finalizeTaskBox(checkId, resultData, runTs, elapsedStr) {{
      if (!taskBoxes[checkId]) return;
      const {{ stepsEl, stepsToggle, statusBar, metaBar, resultEl, headerBadge, headerEl, boxEl, taskName }} = taskBoxes[checkId];

      const status = resultData.status || "normal";
      const isAbnormal = resultData.is_abnormal;
      const isError = status === "error";
      const summary = resultData.summary || "";
      const analysis = resultData.analysis || "";
      const recs = resultData.recommendations || [];

      let statusLabel, statusCls, icon, boxCls, bannerText;
      if (isError) {{
        statusLabel = "Lỗi"; statusCls = "critical"; icon = "❌"; boxCls = "status-error";
        bannerText = "❌ Lỗi xử lý tác vụ";
      }} else if (!isAbnormal) {{
        statusLabel = "Bình thường"; statusCls = "ok"; icon = "✅"; boxCls = "status-ok";
        bannerText = "✅ Tất cả chỉ số bình thường";
      }} else if (status === "critical") {{
        statusLabel = "Cần chú ý"; statusCls = "critical"; icon = "🔴"; boxCls = "status-critical";
        bannerText = "🔴 Phát hiện bất thường — cần chú ý";
      }} else {{
        statusLabel = "Cần theo dõi"; statusCls = "warning"; icon = "⚠️"; boxCls = "status-warning";
        bannerText = "⚠️ Phát hiện bất thường — cần theo dõi";
      }}

      // Header: update icon + badge
      headerBadge.textContent = statusLabel;
      headerBadge.className = `task-status-badge ${{statusCls}}`;
      headerEl.querySelector("span").textContent = `${{icon}} ${{taskName}}`;
      boxEl.className = `task-box ${{boxCls}}`;

      // Status banner
      statusBar.textContent = bannerText;
      statusBar.className = `task-status-bar ${{statusCls}}`;
      statusBar.style.display = "block";

      // Meta bar — per-task dashboard link
      const dashLink = taskDashboardLinks[checkId] || "";
      const linkHtml = dashLink
        ? `<a href="${{dashLink}}" target="_blank">📊 Mở dashboard →</a>`
        : "";
      metaBar.innerHTML = `<span>🕐 ${{runTs}} UTC</span><span>⏱ ${{elapsedStr}}</span>${{linkHtml}}`;
      metaBar.style.display = "flex";

      // Result body
      let html = "";
      if (summary) html += `<div class="task-result-summary">${{summary}}</div>`;
      if (analysis) html += `<div class="task-result-analysis">${{analysis}}</div>`;
      if (recs.length) {{
        html += `<ul class="task-result-recs">`;
        for (const r of recs) html += `<li>${{r}}</li>`;
        html += `</ul>`;
      }}
      if (html) {{ resultEl.innerHTML = html; resultEl.style.display = "block"; }}

      // Steps: collapse by default, show toggle
      stepsEl.classList.add("collapsed");
      stepsToggle.style.display = "block";
      stepsToggle.textContent = "▶ Xem các bước thực hiện";
    }}

    function runMonitor() {{
      if (running) return;
      running = true;
      Object.keys(taskBoxes).forEach(k => delete taskBoxes[k]);

      document.getElementById("run-btn").disabled = true;
      document.getElementById("run-btn").textContent = "Đang phân tích…";
      document.getElementById("task-boxes").style.display = "none";
      document.getElementById("task-boxes").innerHTML = "";
      document.getElementById("spinner").style.display = "block";
      document.getElementById("status").textContent = "🤖 Agent đang đọc dashboard và phân tích dữ liệu…";
      document.getElementById("hint").textContent = "Thường mất khoảng 2–3 phút, vui lòng chờ.";
      document.getElementById("timer").textContent = "";
      startTimer();

      const es = new EventSource(`/run/stream?token=${{encodeURIComponent(API_TOKEN)}}`);

      es.onmessage = (e) => {{
        let data;
        try {{ data = JSON.parse(e.data); }} catch {{ addStep(e.data); return; }}

        if (data.__task_start__) {{
          openTaskBox(data.__task_start__, data.check_id);
          return;
        }}

        if (data.__dashboard_id__ !== undefined) {{
          const checkId = data.check_id;
          if (SUPERSET_BASE && checkId) {{
            taskDashboardLinks[checkId] = `${{SUPERSET_BASE}}/superset/dashboard/${{data.__dashboard_id__}}/`;
          }}
          return;
        }}

        if (data.__task_result__) {{
          const r = data.__task_result__;
          const checkId = r.check_id;
          const elapsed = Math.round((Date.now() - startTime) / 1000);
          const elapsedStr = elapsed >= 60
            ? `Hoàn thành trong ${{Math.floor(elapsed/60)}}m ${{elapsed%60}}s`
            : `Hoàn thành trong ${{elapsed}}s`;
          finalizeTaskBox(checkId, r, new Date().toISOString().replace("T"," ").slice(0,19), elapsedStr);
          return;
        }}

        if (data.__report__) {{
          const r = data.__report__;
          es.close();
          stopTimer();
          running = false;
          document.getElementById("spinner").style.display = "none";
          document.getElementById("status").textContent = "";
          document.getElementById("hint").textContent = "";
          document.getElementById("timer").textContent = "";
          document.getElementById("run-btn").disabled = false;
          document.getElementById("run-btn").textContent = "▶ Chạy lại";
          renderErrors(r.errors);
        }} else if (data.__error__) {{
          es.close();
          stopTimer();
          running = false;
          document.getElementById("spinner").style.display = "none";
          document.getElementById("status").textContent = "❌ Lỗi: " + data.__error__;
          document.getElementById("hint").textContent = "";
          document.getElementById("run-btn").disabled = false;
          document.getElementById("run-btn").textContent = "▶ Chạy lại";
        }} else if (data.__step__ !== undefined) {{
          addStep(data.check_id, data.__step__);
        }}
      }};

      es.onerror = () => {{
        es.close();
        stopTimer();
        running = false;
        document.getElementById("spinner").style.display = "none";
        document.getElementById("status").textContent = "❌ Mất kết nối với server.";
        document.getElementById("hint").textContent = "";
        document.getElementById("run-btn").disabled = false;
        document.getElementById("run-btn").textContent = "▶ Chạy lại";
      }};
    }}

    function renderErrors(errors) {{
      if (!errors || !errors.length) return;
      const boxes = document.getElementById("task-boxes");
      const div = document.createElement("div");
      div.style.cssText = "background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:14px 18px;font-size:0.85rem;color:#9a3412;";
      div.innerHTML = errors.map(e => `⚠️ Lỗi xử lý: ${{e.name}} — ${{e.message}}`).join("<br>");
      boxes.appendChild(div);
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

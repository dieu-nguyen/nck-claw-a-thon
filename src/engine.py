from __future__ import annotations

import json
import os
import smtplib
from collections.abc import Callable, Awaitable
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from src.run_log import RunLog
from src.superset_client import SupersetClient, SupersetError

StepCallback = Callable[[str], Awaitable[None]]

_AGENT_SYSTEM_PROMPT = """\
You are a fintech monitoring analyst. You have access to five tools:

Read-only data tools:
  - search_charts(name): search for charts by name (substring match), returns list of {id, name}
  - search_dashboards(name): search for dashboards by name (substring match), returns list of {id, name}
  - get_chart_data(chart_id): fetch the data for a specific chart by numeric ID
  - list_charts(dashboard_id): list all charts on a dashboard by numeric dashboard ID

Delivery tool:
  - send_email(to, subject, body): send an email report. `to` is a comma-separated list of recipients.

Use search_charts or search_dashboards first when you only know a name, then use the
returned ID to call get_chart_data or list_charts.

At each step, respond ONLY with valid JSON in one of these schemas:
1. To use a data tool:
   {"action": "search_charts", "args": {"name": "<partial name>"}}
   {"action": "search_dashboards", "args": {"name": "<partial name>"}}
   {"action": "get_chart_data", "args": {"chart_id": <int>}}
   {"action": "list_charts", "args": {"dashboard_id": <int>}}
2. To send an email:
   {"action": "send_email", "args": {"to": "<email1,email2>", "subject": "<subject>", "body": "<body text>"}}
3. When you have finished all work (after sending email if required):
   {
     "action": "done",
     "is_abnormal": true|false,
     "status": "normal"|"warning"|"critical",
     "summary": "<1-2 sentence plain language summary>",
     "analysis": "<detailed analysis, or empty string if normal>",
     "recommendations": ["<action 1>", "<action 2>"]
   }

Follow the instructions in your user message carefully.
Stop as soon as you have enough data to reach a conclusion.
"""

_MAX_STEPS = 50  # extra headroom for send_email step


def _execute_send_email(to: str, subject: str, body: str) -> str:
    smtp_host = os.environ.get("SMTP_HOST", "")
    if not smtp_host:
        return "Email skipped: SMTP not configured."

    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    email_from = os.environ.get("EMAIL_FROM", smtp_user)

    if not email_from:
        return "Email skipped: EMAIL_FROM / SMTP_USER not configured."

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = to

    recipients = [r.strip() for r in to.split(",") if r.strip()]
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(email_from, recipients, msg.as_string())
        return f"Email sent to {to}"
    except Exception as e:
        return f"Error sending email: {e}"


@dataclass
class CheckResult:
    check_id: str
    check_name: str
    is_abnormal: bool = False
    status: str = "normal"  # "normal" | "warning" | "critical"
    summary: str = ""
    analysis: str = ""
    recommendations: list[str] = field(default_factory=list)
    extra_charts_fetched: int = 0
    error: str | None = None


def _parse_task_name(prompt_text: str, fallback: str) -> str:
    """Extract the task name from '# Tên tác vụ\n<name>' heading."""
    import re
    m = re.search(r"#\s*Tên tác vụ\s*\n+(.+)", prompt_text)
    return m.group(1).strip() if m else fallback


async def _run_prompt(
    prompt_path: Path,
    client: SupersetClient,
    on_step: StepCallback | None = None,
    log: RunLog | None = None,
) -> CheckResult:
    check_id = prompt_path.stem
    fallback_name = check_id.replace("_", " ").replace("-", " ").title()

    prompt_instructions = prompt_path.read_text(encoding="utf-8")
    check_name = _parse_task_name(prompt_instructions, fallback_name)

    cr = CheckResult(check_id=check_id, check_name=check_name)

    def log_step(step: int, **kwargs) -> None:
        if log:
            log.write("llm_step", check_id=check_id, step=step, **kwargs)

    async def emit(msg: str) -> None:
        if on_step:
            await on_step(msg)

    # Signal to the UI that a new task box should open
    await emit(json.dumps({"__task_start__": check_name, "check_id": check_id}, ensure_ascii=False))

    try:
        llm_client = AsyncOpenAI(
            api_key=os.environ.get("LLM_API_KEY"),
            base_url=os.environ.get("LLM_BASE_URL", "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"),
        )
        llm_model = os.environ.get("LLM_MODEL")
        messages: list[dict] = [
            {"role": "system", "content": _AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_instructions.strip()},
        ]

        extra_charts = 0
        steps = 0
        chart_name_map: dict[int, str] = {}  # id → name, populated by list_charts

        while steps < _MAX_STEPS:
            response = await llm_client.chat.completions.create(
                model=llm_model,
                max_tokens=4096,
                messages=messages,
            )
            steps += 1
            raw = response.choices[0].message.content or ""
            finish_reason = response.choices[0].finish_reason

            parsed = None
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                import re
                match = re.search(r'\{.*\}', raw, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group())
                    except (json.JSONDecodeError, ValueError):
                        pass
            if parsed is None:
                log_step(steps, action="(invalid_json)", llm_raw=raw,
                         finish_reason=finish_reason, tool_result="nudge: asked to retry as JSON")
                # Model responded with plain text — nudge it back to JSON
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        "Your last response was not valid JSON. "
                        "You MUST respond with ONLY a JSON object — no prose, no markdown, no explanation. "
                        'Either call a tool: {"action": "...", "args": {...}} '
                        'or conclude: {"action": "done", "is_abnormal": ..., "status": ..., '
                        '"summary": "...", "analysis": "...", "recommendations": [...]}.'
                    ),
                })
                continue

            action = parsed.get("action")

            if action == "done":
                await emit("🧠 Đang tổng hợp kết quả phân tích…")
                cr.is_abnormal = bool(parsed.get("is_abnormal", False))
                cr.status = parsed.get("status", "normal")
                cr.summary = parsed.get("summary", "")
                cr.analysis = parsed.get("analysis", "")
                cr.recommendations = parsed.get("recommendations", [])
                cr.extra_charts_fetched = extra_charts
                log_step(steps, action="done", llm_raw=raw, finish_reason=finish_reason,
                         is_abnormal=cr.is_abnormal, status=cr.status,
                         summary=cr.summary, analysis=cr.analysis,
                         recommendations=cr.recommendations)
                return cr

            messages.append({"role": "assistant", "content": raw})

            if action == "get_chart_data":
                chart_id = parsed["args"]["chart_id"]
                chart_name = chart_name_map.get(chart_id, f"chart {chart_id}")
                await emit(f"📊 Đang lấy dữ liệu: \"{chart_name}\"")
                try:
                    data = await client.get_chart_data(chart_id)
                    extra_charts += 1
                    tool_content = f"Chart {chart_id} data: {json.dumps(data, default=str)[:3000]}"
                    messages.append({"role": "user", "content": tool_content})
                    log_step(steps, action="get_chart_data", llm_raw=raw,
                             finish_reason=finish_reason,
                             args={"chart_id": chart_id, "chart_name": chart_name},
                             tool_result=tool_content)
                    await emit("🧠 Đang phân tích dữ liệu…")
                except SupersetError as e:
                    tool_content = f"Error fetching chart {chart_id}: {e}"
                    messages.append({"role": "user", "content": tool_content})
                    log_step(steps, action="get_chart_data", llm_raw=raw,
                             finish_reason=finish_reason,
                             args={"chart_id": chart_id, "chart_name": chart_name},
                             tool_result=tool_content)

            elif action == "list_charts":
                dashboard_id = parsed["args"]["dashboard_id"]
                try:
                    charts = await client.list_charts(dashboard_id)
                    slim = [{"id": c.get("id"), "name": c.get("slice_name", "")} for c in charts]
                    for c in slim:
                        if c["id"]:
                            chart_name_map[c["id"]] = c["name"]
                    await emit(f"📋 Tìm thấy {len(slim)} biểu đồ trên dashboard")
                    tool_content = f"Charts on dashboard {dashboard_id}: {json.dumps(slim, default=str)}"
                    messages.append({"role": "user", "content": tool_content})
                    log_step(steps, action="list_charts", llm_raw=raw,
                             finish_reason=finish_reason,
                             args={"dashboard_id": dashboard_id},
                             tool_result=tool_content)
                except SupersetError as e:
                    tool_content = f"Error listing charts for dashboard {dashboard_id}: {e}"
                    messages.append({"role": "user", "content": tool_content})
                    log_step(steps, action="list_charts", llm_raw=raw,
                             finish_reason=finish_reason,
                             args={"dashboard_id": dashboard_id},
                             tool_result=tool_content)

            elif action == "search_charts":
                name = parsed["args"]["name"]
                try:
                    charts = await client.search_charts(name)
                    for c in charts:
                        if c.get("id"):
                            chart_name_map[c["id"]] = c["name"]
                    tool_content = f"Charts matching '{name}': {json.dumps(charts, default=str)[:2000]}"
                    messages.append({"role": "user", "content": tool_content})
                    log_step(steps, action="search_charts", llm_raw=raw,
                             finish_reason=finish_reason,
                             args={"name": name}, tool_result=tool_content)
                except SupersetError as e:
                    tool_content = f"Error searching charts for '{name}': {e}"
                    messages.append({"role": "user", "content": tool_content})
                    log_step(steps, action="search_charts", llm_raw=raw,
                             finish_reason=finish_reason,
                             args={"name": name}, tool_result=tool_content)

            elif action == "search_dashboards":
                name = parsed["args"]["name"]
                try:
                    dashboards = await client.search_dashboards(name)
                    if dashboards:
                        await emit(f"🔍 Đã tìm thấy dashboard: \"{dashboards[0]['name']}\"")
                    tool_content = f"Dashboards matching '{name}': {json.dumps(dashboards, default=str)[:2000]}"
                    messages.append({"role": "user", "content": tool_content})
                    log_step(steps, action="search_dashboards", llm_raw=raw,
                             finish_reason=finish_reason,
                             args={"name": name}, tool_result=tool_content)
                except SupersetError as e:
                    tool_content = f"Error searching dashboards for '{name}': {e}"
                    messages.append({"role": "user", "content": tool_content})
                    log_step(steps, action="search_dashboards", llm_raw=raw,
                             finish_reason=finish_reason,
                             args={"name": name}, tool_result=tool_content)

            elif action == "send_email":
                args = parsed.get("args", {})
                if os.environ.get("SMTP_HOST", ""):
                    await emit(f"📧 Đang gửi email báo cáo tới {args.get('to', '')}…")
                else:
                    await emit("📧 Bỏ qua gửi email (SMTP chưa được cấu hình)")
                result = _execute_send_email(
                    to=args.get("to", ""),
                    subject=args.get("subject", ""),
                    body=args.get("body", ""),
                )
                log_step(steps, action="send_email", llm_raw=raw,
                         finish_reason=finish_reason,
                         args={"to": args.get("to"), "subject": args.get("subject"),
                               "body_preview": (args.get("body") or "")[:500]},
                         tool_result=result)
                # Force the model to conclude immediately — no second email
                messages.append({
                    "role": "user",
                    "content": (
                        f"{result} "
                        "Email has been sent. Do NOT send another email. "
                        "You MUST now respond with the final done JSON:\n"
                        '{"action": "done", "is_abnormal": ..., "status": ..., '
                        '"summary": "...", "analysis": "...", "recommendations": [...]}'
                    ),
                })

            else:
                messages.append({
                    "role": "user",
                    "content": (
                        f"Unknown action '{action}'. "
                        "Please respond with a valid action or conclude with \"done\"."
                    ),
                })

        # Step budget exhausted — ask for final conclusion
        messages.append({
            "role": "user",
            "content": (
                "Step budget reached. Provide your final conclusion now. "
                'Respond with {"action": "done", "is_abnormal": ..., "status": ..., '
                '"summary": "...", "analysis": "...", "recommendations": [...]}.'
            ),
        })
        response = await llm_client.chat.completions.create(
            model=llm_model,
            max_tokens=4096,
            messages=messages,
        )
        raw = response.choices[0].message.content or ""
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}

        cr.is_abnormal = bool(parsed.get("is_abnormal", False))
        cr.status = parsed.get("status", "normal")
        cr.summary = parsed.get("summary", "Step budget exhausted; could not conclude.")
        cr.analysis = parsed.get("analysis", "")
        cr.recommendations = parsed.get("recommendations", [])
        cr.extra_charts_fetched = extra_charts
        return cr

    except SupersetError as e:
        cr.error = str(e)
    except Exception as e:
        cr.error = f"Unexpected error: {e}"

    return cr


async def run_engine(
    prompts_dir: str,
    client: SupersetClient,
    on_step: StepCallback | None = None,
    log: RunLog | None = None,
) -> list[CheckResult]:
    prompt_files = sorted(Path(prompts_dir).glob("*.md"))
    if not prompt_files:
        raise FileNotFoundError(f"No .md prompt files found in: {prompts_dir}")

    results: list[CheckResult] = []
    for prompt_path in prompt_files:
        result = await _run_prompt(prompt_path, client, on_step=on_step, log=log)
        results.append(result)
    return results

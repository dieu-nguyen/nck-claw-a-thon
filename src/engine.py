from __future__ import annotations

import json
import os
import smtplib
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from src.superset_client import SupersetClient, SupersetError

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
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    email_from = os.environ.get("EMAIL_FROM", smtp_user)

    if not email_from:
        return "Error: EMAIL_FROM / SMTP_USER not configured"

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


async def _run_prompt(prompt_path: Path, client: SupersetClient) -> CheckResult:
    check_id = prompt_path.stem
    check_name = check_id.replace("_", " ").replace("-", " ").title()

    cr = CheckResult(check_id=check_id, check_name=check_name)

    prompt_instructions = prompt_path.read_text(encoding="utf-8")

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

        while steps < _MAX_STEPS:
            response = await llm_client.chat.completions.create(
                model=llm_model,
                max_tokens=4096,
                messages=messages,
            )
            steps += 1
            raw = response.choices[0].message.content or ""

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
                cr.is_abnormal = bool(parsed.get("is_abnormal", False))
                cr.status = parsed.get("status", "normal")
                cr.summary = parsed.get("summary", "")
                cr.analysis = parsed.get("analysis", "")
                cr.recommendations = parsed.get("recommendations", [])
                cr.extra_charts_fetched = extra_charts
                return cr

            messages.append({"role": "assistant", "content": raw})

            if action == "get_chart_data":
                chart_id = parsed["args"]["chart_id"]
                try:
                    data = await client.get_chart_data(chart_id)
                    extra_charts += 1
                    messages.append({
                        "role": "user",
                        "content": f"Chart {chart_id} data: {json.dumps(data, default=str)[:3000]}",
                    })
                except SupersetError as e:
                    messages.append({
                        "role": "user",
                        "content": f"Error fetching chart {chart_id}: {e}",
                    })

            elif action == "list_charts":
                dashboard_id = parsed["args"]["dashboard_id"]
                try:
                    charts = await client.list_charts(dashboard_id)
                    # Trim to id+name only so the agent can easily pick chart IDs
                    slim = [{"id": c.get("id"), "name": c.get("slice_name", "")} for c in charts]
                    messages.append({
                        "role": "user",
                        "content": f"Charts on dashboard {dashboard_id}: {json.dumps(slim, default=str)}",
                    })
                except SupersetError as e:
                    messages.append({
                        "role": "user",
                        "content": f"Error listing charts for dashboard {dashboard_id}: {e}",
                    })

            elif action == "search_charts":
                name = parsed["args"]["name"]
                try:
                    charts = await client.search_charts(name)
                    messages.append({
                        "role": "user",
                        "content": f"Charts matching '{name}': {json.dumps(charts, default=str)[:2000]}",
                    })
                except SupersetError as e:
                    messages.append({
                        "role": "user",
                        "content": f"Error searching charts for '{name}': {e}",
                    })

            elif action == "search_dashboards":
                name = parsed["args"]["name"]
                try:
                    dashboards = await client.search_dashboards(name)
                    messages.append({
                        "role": "user",
                        "content": f"Dashboards matching '{name}': {json.dumps(dashboards, default=str)[:2000]}",
                    })
                except SupersetError as e:
                    messages.append({
                        "role": "user",
                        "content": f"Error searching dashboards for '{name}': {e}",
                    })

            elif action == "send_email":
                args = parsed.get("args", {})
                result = _execute_send_email(
                    to=args.get("to", ""),
                    subject=args.get("subject", ""),
                    body=args.get("body", ""),
                )
                messages.append({
                    "role": "user",
                    "content": result,
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


async def run_engine(prompts_dir: str, client: SupersetClient) -> list[CheckResult]:
    prompt_files = sorted(Path(prompts_dir).glob("*.md"))
    if not prompt_files:
        raise FileNotFoundError(f"No .md prompt files found in: {prompts_dir}")

    results: list[CheckResult] = []
    for prompt_path in prompt_files:
        result = await _run_prompt(prompt_path, client)
        results.append(result)
    return results

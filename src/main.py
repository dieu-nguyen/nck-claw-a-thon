from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Security
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
        return await _run()
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except SupersetError as e:
        raise HTTPException(status_code=502, detail=f"Superset unreachable: {e}") from e


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        report = asyncio.run(_run(dry_run=True))
        print(json.dumps(report, indent=2, default=str))
    else:
        import uvicorn
        uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=False)

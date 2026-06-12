from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from openai import AsyncOpenAI

from src.config import DeepDiveConfig
from src.deep_dive import DeepDiveInvestigator
from src.engine import CheckResult, run_engine
from src.llm_reporter import LLMReporter
from src.playbook import PlaybookLoadError, load_playbook
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


def _should_deep_dive(
    cr: CheckResult,
    check_deep_dive: str,
    dd_config: DeepDiveConfig,
    reason_confident: bool,
) -> bool:
    if dd_config.enabled == "off" or check_deep_dive == "off":
        return False
    trigger = dd_config.trigger
    if trigger == "always":
        return True
    if trigger == "high_severity" and cr.severity == "high":
        return True
    if trigger == "low_confidence" and not reason_confident:
        return True
    return False


async def _run(dry_run: bool = False) -> dict:
    playbook_path = os.getenv("PLAYBOOK_PATH", "playbook.yaml")
    log_dir = os.getenv("RUN_LOG_DIR", "./logs")
    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    playbook = load_playbook(playbook_path)

    superset = SupersetClient(
        base_url=_get_env("SUPERSET_BASE_URL"),
        username=_get_env("SUPERSET_USERNAME"),
        password=_get_env("SUPERSET_PASSWORD"),
    )
    llm_client = AsyncOpenAI(
        base_url=_get_env("LLM_BASE_URL"),
        api_key=_get_env("LLM_API_KEY"),
    )
    model = os.getenv("LLM_MODEL", "gpt-4o")
    reporter = LLMReporter(client=llm_client, model=model)
    investigator = DeepDiveInvestigator(
        superset=superset, llm_client=llm_client, model=model
    )

    with RunLog(log_dir=log_dir, run_ts=run_ts) as log:
        log.write(
            "run_start",
            playbook_path=playbook_path,
            dry_run=dry_run,
            checks=len(playbook.checks),
        )

        results = await run_engine(playbook, superset)

        reasons = {}
        for cr, check in zip(results, playbook.checks):
            log.write(
                "check_complete",
                check_id=cr.check_id,
                is_abnormal=cr.is_abnormal,
                error=cr.error,
            )
            if not cr.is_abnormal or cr.error:
                continue

            reason_resp = await reporter.explain(cr)
            log.write(
                "llm_reason",
                check_id=cr.check_id,
                confident=reason_resp.confident,
                reason=reason_resp.reason[:200],
            )

            if _should_deep_dive(cr, check.deep_dive, playbook.deep_dive, reason_resp.confident):
                dd_result = await investigator.investigate(cr, playbook.deep_dive)
                log.write(
                    "deep_dive",
                    check_id=cr.check_id,
                    steps=dd_result.steps_taken,
                    extra_charts=dd_result.extra_charts_examined,
                    audit=dd_result.audit_log,
                )
                cr.deep_dive_used = True
                cr.deep_dive_charts_examined = dd_result.extra_charts_examined
                reason_resp.reason = dd_result.final_reason

            reasons[cr.check_id] = reason_resp

        report = build_report(results=results, reasons=reasons, run_ts=run_ts)
        log.write(
            "run_complete",
            status=report["status"],
            anomaly_count=report["anomaly_count"],
        )

    return report


@app.post("/run")
async def run_endpoint(credentials: HTTPAuthorizationCredentials = Security(_bearer)):
    expected = os.getenv("API_TOKEN", "")
    if not expected or credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        return await _run()
    except PlaybookLoadError as e:
        raise HTTPException(status_code=500, detail=f"Playbook error: {e}") from e
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

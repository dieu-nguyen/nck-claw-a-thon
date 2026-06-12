from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class RunLog:
    def __init__(self, log_dir: str, run_ts: str):
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        safe_ts = run_ts.replace(":", "-").replace("T", "_")
        self._path = Path(log_dir) / f"run_{safe_ts}.jsonl"
        self._f = self._path.open("a", encoding="utf-8")

    def write(self, event: str, **data):
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **data,
        }
        self._f.write(json.dumps(record, default=str) + "\n")
        self._f.flush()

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

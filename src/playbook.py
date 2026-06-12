import yaml
from pathlib import Path

from pydantic import ValidationError

from src.config import Playbook


class PlaybookLoadError(Exception):
    pass


def load_playbook(path: str) -> Playbook:
    p = Path(path)
    if not p.exists():
        raise PlaybookLoadError(f"Playbook not found: {path}")
    try:
        raw = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise PlaybookLoadError(f"YAML parse error: {e}") from e
    try:
        return Playbook.model_validate(raw or {})
    except ValidationError as e:
        raise PlaybookLoadError(f"Playbook validation failed:\n{e}") from e

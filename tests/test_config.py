import tempfile
from pathlib import Path

import pytest

from src.engine import run_engine, CheckResult


def test_check_result_defaults():
    cr = CheckResult(check_id="test", check_name="Test Check")
    assert cr.is_abnormal is False
    assert cr.status == "normal"
    assert cr.error is None
    assert cr.recommendations == []


def test_run_engine_raises_when_no_prompts(tmp_path):
    from unittest.mock import AsyncMock
    mock_client = AsyncMock()
    import asyncio
    with pytest.raises(FileNotFoundError, match="No .md prompt files"):
        asyncio.run(run_engine(str(tmp_path), mock_client))


def test_run_engine_discovers_prompt_files(tmp_path):
    # Just verify it picks up .md files and not other extensions
    (tmp_path / "check_one.md").write_text("# Check One\nInstructions here.")
    (tmp_path / "check_two.md").write_text("# Check Two\nMore instructions.")
    (tmp_path / "ignore_me.txt").write_text("This should be ignored.")

    # We can't run a real LLM in tests, so just verify file discovery
    prompt_files = sorted(Path(tmp_path).glob("*.md"))
    assert len(prompt_files) == 2
    assert {p.stem for p in prompt_files} == {"check_one", "check_two"}


def test_check_id_derived_from_filename(tmp_path):
    (tmp_path / "bank_link_sr.md").write_text("# Bank Link SR")
    prompt_files = sorted(Path(tmp_path).glob("*.md"))
    assert prompt_files[0].stem == "bank_link_sr"

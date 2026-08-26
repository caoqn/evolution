import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.adapter import EnvContext, EvalResult
from benchmarks.adapter_locabench import LOCABenchAdapter, _ensure_email_accounts


def test_ensure_email_accounts_provisions_mcp_and_task_sender(tmp_path: Path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "emails_config.json").write_text(
        json.dumps({
            "email": "sender@example.com",
            "password": "sender-pass",
            "name": "Configured Sender",
        }),
        encoding="utf-8",
    )
    config = {
        "mcp_servers": {
            "email": {
                "enabled": True,
                "params": {
                    "data_dir": "{task_workspace}/local_db/emails",
                    "email": "mcp-login@example.com",
                    "password": "mcp-pass",
                },
            },
        },
    }

    report = _ensure_email_accounts(config, task_dir)

    users_path = task_dir / "local_db" / "emails" / "users.json"
    users = json.loads(users_path.read_text(encoding="utf-8"))
    assert users["mcp-login@example.com"]["password"] == "mcp-pass"
    assert users["sender@example.com"]["password"] == "sender-pass"
    assert {entry["email"] for entry in report} == {
        "mcp-login@example.com",
        "sender@example.com",
    }
    for email in ("mcp-login@example.com", "sender@example.com"):
        mailbox = task_dir / "local_db" / "emails" / "users_data" / email
        assert (mailbox / "emails.json").is_file()
        assert (mailbox / "folders.json").is_file()
        assert (mailbox / "drafts.json").is_file()


def test_fatal_mcp_result_is_recorded_without_overriding_evaluation(tmp_path: Path):
    (tmp_path / "events.jsonl").write_text(
        json.dumps({
            "type": "tool.result",
            "data": {"output": "[FATAL MCP ERROR] session closed unexpectedly"},
        }) + "\n",
        encoding="utf-8",
    )
    item = {
        "_cached_eval": EvalResult(
            success=False,
            score=0.0,
            summary="reward=0.0 (FAIL)",
        ),
    }

    LOCABenchAdapter().post_process(
        item,
        SimpleNamespace(dir=tmp_path),
        EnvContext(data={}),
    )
    assert item["_completion_protocol"] == {
        "set_final_output": False,
        "finalize_task": False,
        "finalization_tool": None,
        "final_output_submitted": False,
        "terminate": False,
        "runner_end": False,
        "complete_handshake": False,
    }


def test_successful_fallback_may_evolve_after_fatal_mcp(tmp_path: Path):
    (tmp_path / "events.jsonl").write_text(
        "[FATAL MCP ERROR] session closed unexpectedly\n",
        encoding="utf-8",
    )
    item = {
        "_cached_eval": EvalResult(
            success=True,
            score=1.0,
            summary="reward=1.0 (PASS)",
        ),
    }

    LOCABenchAdapter().post_process(
        item,
        SimpleNamespace(dir=tmp_path),
        EnvContext(data={}),
    )

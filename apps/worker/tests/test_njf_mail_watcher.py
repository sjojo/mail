"""The scheduled watcher counts unreported NJF mail and only then turns the agent loose."""

import asyncio
from datetime import timedelta

import mistralai.workflows as workflows
from mistralai.workflows.testing import create_test_worker

from worker.workflows import njf_mail_watcher

WORKFLOW_EXECUTION_TIMEOUT = timedelta(seconds=10)


class FakeState:
    count = 0
    notify_calls: list[str] = []


@workflows.activity(name="njf-count-new-njf-mail")
async def fake_count() -> int:
    return FakeState.count


@workflows.activity(name="njf-notify-new-mail")
async def fake_notify(slot: str) -> dict:
    FakeState.notify_calls.append(slot)
    return {"status": "completed", "reply": "2 nieuwe mails gemeld."}


async def _run(temporal_env, params: dict) -> dict:
    async with create_test_worker(
        temporal_env,
        workflows=[njf_mail_watcher.NjfMailWatcherWorkflow],
        activities=[fake_count, fake_notify],
    ):
        handle = await temporal_env.client.start_workflow(
            "njf-mail-watcher",
            params,
            id=f"test-watcher-{params.get('slot') or 'auto'}-{FakeState.count}-{params.get('dry_run', False)}",
            task_queue="test-task-queue",
            execution_timeout=WORKFLOW_EXECUTION_TIMEOUT,
        )
        return await asyncio.wait_for(handle.result(), timeout=15)


async def test_no_new_mail_skips_the_agent(temporal_env) -> None:
    FakeState.count = 0
    FakeState.notify_calls = []
    result = await _run(temporal_env, {"slot": "2026-11-19T10"})
    assert result["new_thread_count"] == 0
    assert result["notified"] is False
    assert result["status"] == "geen nieuwe mail"
    assert FakeState.notify_calls == []


async def test_dry_run_counts_but_never_notifies(temporal_env) -> None:
    FakeState.count = 3
    FakeState.notify_calls = []
    result = await _run(temporal_env, {"slot": "2026-11-19T11", "dry_run": True})
    assert result["new_thread_count"] == 3
    assert result["dry_run"] is True
    assert result["notified"] is False
    assert result["status"] == "dry-run"
    assert FakeState.notify_calls == []


async def test_new_mail_starts_one_agent_turn(temporal_env) -> None:
    FakeState.count = 2
    FakeState.notify_calls = []
    result = await _run(temporal_env, {"slot": "2026-11-19T12"})
    assert result["new_thread_count"] == 2
    assert result["notified"] is True
    assert result["status"] == "completed"
    assert result["reply"] == "2 nieuwe mails gemeld."
    assert FakeState.notify_calls == ["2026-11-19T12"]


async def test_slot_defaults_to_the_current_hour(temporal_env) -> None:
    FakeState.count = 0
    FakeState.notify_calls = []
    result = await _run(temporal_env, {})
    assert ":00:00" in result["slot"]
    assert len(result["slot"]) >= 16


def test_new_mail_query_excludes_reported_threads() -> None:
    assert njf_mail_watcher.NEW_MAIL_QUERY == "in:inbox -label:njf-melding"

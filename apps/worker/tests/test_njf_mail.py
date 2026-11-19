"""The NJF mail digest workflow turns Gmail search hits into a summarized digest."""

import asyncio
import json
from datetime import timedelta

import mistralai.workflows as workflows
from mistralai.workflows.testing import create_test_worker

from worker.workflows import njf_mail

WORKFLOW_EXECUTION_TIMEOUT = timedelta(seconds=10)


@workflows.activity(name="njf-search-threads")
async def fake_search(query: str, page_size: int) -> list[dict]:
    assert "NJF" in query or "stichtingnjf" in query
    return [
        {
            "thread_id": "t1",
            "subject": "NJF 2026 | Nagenieten en Evalueren",
            "sender": "contact@stichtingnjf.nl",
            "date": "2026-05-20T20:45:40+02:00",
            "unread": False,
            "snippet": "Feedbackformulier voor 20 juni",
        },
        {
            "thread_id": "t2",
            "subject": "Beschikbaarheid opgegeven voor: kennismaking NJF - Circuspunt",
            "sender": "service@datumprikker.nl",
            "date": "2026-06-02T12:20:05+00:00",
            "unread": True,
            "snippet": "Afspraak bekijken",
        },
    ][:page_size]


@workflows.activity(name="njf-fetch-thread")
async def fake_fetch(thread_id: str) -> dict:
    return {"thread_id": thread_id, "content": f"Volledige body van {thread_id}"}


@workflows.activity(name="njf-summarize-threads")
async def fake_summarize(refs: list[dict], contents: list[dict]) -> njf_mail.DigestPayload:
    assert [c["thread_id"] for c in contents] == [r["thread_id"] for r in refs]
    return njf_mail.DigestPayload(
        digest_markdown="# NJF digest",
        threads=[
            njf_mail.ThreadSummary(
                thread_id=ref["thread_id"],
                subject=ref["subject"],
                summary="Samenvatting",
                action_items=["Actiepunt"],
            )
            for ref in refs
        ],
    )


async def test_njf_mail_digest_aggregates_threads(temporal_env) -> None:
    async with create_test_worker(
        temporal_env,
        workflows=[njf_mail.NjfMailDigestWorkflow],
        activities=[fake_search, fake_fetch, fake_summarize],
    ):
        handle = await temporal_env.client.start_workflow(
            "njf-mail-digest",
            {"days_back": 7, "max_threads": 2},
            id="test-njf-mail-digest",
            task_queue="test-task-queue",
            execution_timeout=WORKFLOW_EXECUTION_TIMEOUT,
        )
        result = await asyncio.wait_for(handle.result(), timeout=15)
        assert result["thread_count"] == 2
        assert result["digest_markdown"] == "# NJF digest"
        assert [t["thread_id"] for t in result["threads"]] == ["t1", "t2"]
        assert "newer_than:7d" in result["query"]


async def test_njf_mail_digest_handles_empty_inbox(temporal_env) -> None:
    @workflows.activity(name="njf-search-threads")
    async def empty_search(query: str, page_size: int) -> list[dict]:
        return []

    async with create_test_worker(
        temporal_env,
        workflows=[njf_mail.NjfMailDigestWorkflow],
        activities=[empty_search, fake_fetch, fake_summarize],
    ):
        handle = await temporal_env.client.start_workflow(
            "njf-mail-digest",
            {"days_back": 7, "max_threads": 5},
            id="test-njf-mail-digest-empty",
            task_queue="test-task-queue",
            execution_timeout=WORKFLOW_EXECUTION_TIMEOUT,
        )
        result = await asyncio.wait_for(handle.result(), timeout=15)
        assert result["thread_count"] == 0
        assert result["threads"] == []


def test_thread_refs_parses_connector_shapes() -> None:
    thread = {
        "id": "t1",
        "messages": [
            {
                "id": "m1",
                "subject": "NJF 2026",
                "sender": "contact@stichtingnjf.nl",
                "date": "2026-05-20T20:45:40+02:00",
                "snippet": "s",
                "labelIds": ["UNREAD", "INBOX"],
            }
        ],
    }
    expected = [
        {
            "thread_id": "t1",
            "subject": "NJF 2026",
            "sender": "contact@stichtingnjf.nl",
            "date": "2026-05-20T20:45:40+02:00",
            "unread": True,
            "snippet": "s",
        }
    ]
    assert njf_mail._thread_refs({"threads": [thread]}) == expected
    assert njf_mail._thread_refs(json.dumps({"threads": [thread]})) == expected
    assert njf_mail._thread_refs({"content": [{"type": "text", "text": json.dumps({"threads": [thread]})}]}) == expected
    assert njf_mail._thread_refs({"result": {"threads": [thread]}}) == expected
    assert njf_mail._thread_refs({"threads": []}) == []
    assert njf_mail._thread_refs({}) == []


def test_thread_content_joins_bodies() -> None:
    result = {
        "messages": [
            {"plaintextBody": "eerste bericht"},
            {"plaintextBody": None, "snippet": "alleen snippet"},
        ]
    }
    assert njf_mail._thread_content(result) == "eerste bericht\n\n---\n\nalleen snippet"

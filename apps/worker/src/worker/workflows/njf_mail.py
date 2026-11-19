"""NJF mail digest: search NJF-related Gmail threads, read them, and summarize.

Runs on behalf of the triggering user (`on_behalf_of=True`) and uses their
"njf-mail" Gmail credential, so no secrets live in code.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pydantic
import mistralai.workflows as workflows
from mistralai.workflows import Depends
from mistralai.workflows.plugins.mistralai.connectors import (
    ToolCallClient,
    connector,
    uses_connectors,
)

gmail = connector("gmail", credentials_name="njf-mail")

MAX_BODY_CHARS = 6000

DEFAULT_QUERY_TEMPLATE = (
    "(from:stichtingnjf.nl OR from:nederlandsjongleerfestival.nl "
    "OR (from:datumprikker.nl NJF)) newer_than:{days}d"
)


class NjfMailDigestInput(pydantic.BaseModel):
    days_back: int = 30
    max_threads: int = 10
    query: str | None = None


class ThreadRef(pydantic.BaseModel):
    thread_id: str
    subject: str = ""
    sender: str = ""
    date: str = ""
    unread: bool = False
    snippet: str = ""


class ThreadSummary(pydantic.BaseModel):
    thread_id: str
    subject: str
    summary: str
    action_items: list[str] = pydantic.Field(default_factory=list)


class DigestPayload(pydantic.BaseModel):
    digest_markdown: str
    threads: list[ThreadSummary]


class NjfMailDigestOutput(pydantic.BaseModel):
    query: str
    thread_count: int
    digest_markdown: str
    threads: list[ThreadSummary]


def _as_dict(result: Any) -> dict:
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return dict(result)


def _thread_refs(result: Any) -> list[dict]:
    threads = _as_dict(result).get("threads") or []
    refs: list[dict] = []
    for thread in threads:
        thread = _as_dict(thread)
        messages = thread.get("messages") or []
        message = _as_dict(messages[-1]) if messages else {}
        labels = message.get("labelIds") or []
        thread_id = thread.get("id") or message.get("id")
        if not thread_id:
            continue
        refs.append(
            ThreadRef(
                thread_id=thread_id,
                subject=message.get("subject") or "",
                sender=message.get("sender") or "",
                date=message.get("date") or "",
                unread="UNREAD" in labels,
                snippet=message.get("snippet") or "",
            ).model_dump()
        )
    return refs


def _thread_content(result: Any) -> str:
    messages = _as_dict(result).get("messages") or []
    parts: list[str] = []
    for message in messages:
        message = _as_dict(message)
        body = message.get("plaintextBody") or message.get("snippet") or ""
        if body:
            parts.append(body[:MAX_BODY_CHARS])
    return "\n\n---\n\n".join(parts)


@workflows.activity(name="njf-search-threads", start_to_close_timeout=timedelta(seconds=60))
async def search_njf_threads(
    query: str,
    page_size: int,
    client: ToolCallClient = Depends(gmail),
) -> list[dict]:
    result = await client.call_tool(
        tool_name="search_threads",
        arguments={"query": query, "pageSize": page_size},
    )
    return _thread_refs(result)


@workflows.activity(name="njf-fetch-thread", start_to_close_timeout=timedelta(seconds=60))
async def fetch_thread(
    thread_id: str,
    client: ToolCallClient = Depends(gmail),
) -> dict:
    result = await client.call_tool(
        tool_name="get_thread",
        arguments={"threadId": thread_id, "messageFormat": "FULL_CONTENT"},
    )
    return {"thread_id": thread_id, "content": _thread_content(result)}


@workflows.activity(name="njf-summarize-threads", start_to_close_timeout=timedelta(minutes=5))
async def summarize_threads(refs: list[dict], contents: list[dict]) -> DigestPayload:
    from mistralai.workflows.plugins.mistralai import get_mistral_client

    client = get_mistral_client()
    by_id = {c["thread_id"]: c["content"] for c in contents}
    payload = [
        {
            "thread_id": ref["thread_id"],
            "subject": ref["subject"],
            "sender": ref["sender"],
            "date": ref["date"],
            "unread": ref["unread"],
            "snippet": ref["snippet"],
            "content": by_id.get(ref["thread_id"], ""),
        }
        for ref in refs
    ]
    system = (
        "You triage email about Stichting NJF / Nederlands Jongleerfestival (NJF). "
        "For every thread give a 1-3 sentence summary in Dutch and the action items "
        "with deadlines for the recipient. Also produce digest_markdown in Dutch with "
        "sections 'Nieuw', 'Actiepunten' and 'Afspraken'. "
        "Answer only with JSON matching the requested schema."
    )
    response = await client.chat.parse_async(
        model="mistral-large-latest",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format=DigestPayload,
    )
    return response


@workflows.workflow.define(name="njf-mail-digest", on_behalf_of=True)
@uses_connectors(gmail)
class NjfMailDigestWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, params: NjfMailDigestInput) -> NjfMailDigestOutput:
        query = params.query or DEFAULT_QUERY_TEMPLATE.format(days=params.days_back)
        refs = await search_njf_threads(query=query, page_size=params.max_threads)
        contents: list[dict] = []
        for ref in refs:
            contents.append(await fetch_thread(thread_id=ref["thread_id"]))
        if not refs:
            return NjfMailDigestOutput(
                query=query,
                thread_count=0,
                digest_markdown="Geen NJF-mail gevonden voor deze query.",
                threads=[],
            )
        digest = await summarize_threads(refs=refs, contents=contents)
        return NjfMailDigestOutput(
            query=query,
            thread_count=len(refs),
            digest_markdown=digest.digest_markdown,
            threads=digest.threads,
        )

"""Scheduled NJF mail watcher.

Runs unattended (as the deployment's service account), so it cannot use the
user-scoped ``njf-mail`` Gmail credential directly. Instead it uses a user API
key from the workspace secret ``njf-watcher-user-api-key`` (bound as
``NJF_WATCHER_USER_API_KEY``): with that key it acts as the user, so the Gmail
``njf-mail`` credential resolves.

Each run first counts unreported inbox threads (``in:inbox -label:njf-melding``)
and only when there are some, starts a CHECK-IN turn in the persistent session of
the deployed ``njf-mail-watcher`` agent (idempotency key below). The agent's reply
lands in that session, which the user reads and continues in Vibe — the
notification. The session is deliberately left open.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pydantic
import mistralai.workflows as workflows
from mistralai.workflows import workflow

from worker.workflows import njf_mail

AGENT_NAME = "njf-mail-watcher"
GMAIL_CREDENTIALS_NAME = "njf-mail"
SESSION_IDEMPOTENCY_KEY = "njf-mail-hoofdgesprek"
USER_API_KEY_ENV_VAR = "NJF_WATCHER_USER_API_KEY"
NOTIFIED_LABEL_SEARCH_TOKEN = "njf-melding"
NEW_MAIL_QUERY = f"in:inbox -label:{NOTIFIED_LABEL_SEARCH_TOKEN}"
NO_NEW_MAIL_REPLY = "GEEN_NIEUWE_MAIL"
TURN_TIMEOUT_SECONDS = 600

CHECK_IN_PROMPT = (
    "CHECK-IN {slot}: controleer de NJF-inbox op nieuwe mail zonder het label "
    '"NJF/Melding" en meld elke nieuwe mail in dit gesprek met een korte '
    "samenvatting en suggesties."
)


class NjfMailWatcherInput(pydantic.BaseModel):
    slot: str | None = None
    dry_run: bool = False


class NjfMailWatcherOutput(pydantic.BaseModel):
    slot: str
    new_thread_count: int
    dry_run: bool
    notified: bool
    status: str
    reply: str | None = None


def _user_api_key() -> str:
    import os

    api_key = os.environ.get(USER_API_KEY_ENV_VAR, "").strip()
    if not api_key:
        raise RuntimeError(
            f"Missing {USER_API_KEY_ENV_VAR}: bind workspace secret "
            "'njf-watcher-user-api-key' to the deployment. A scheduled run acts as "
            "the deployment's service account and cannot resolve the user-scoped "
            f"gmail credential '{GMAIL_CREDENTIALS_NAME}' without a user API key."
        )
    return api_key


@workflows.activity(name="njf-count-new-njf-mail", start_to_close_timeout=timedelta(seconds=60))
async def count_new_njf_mail() -> int:
    from mistralai.client import Mistral

    client = Mistral(api_key=_user_api_key())
    result = await client.beta.connectors.call_tool_async(
        connector_id_or_name="gmail",
        credentials_name=GMAIL_CREDENTIALS_NAME,
        tool_name="search_threads",
        arguments={"query": NEW_MAIL_QUERY, "pageSize": 50},
    )
    payload = {
        "content": [{"text": getattr(block, "text", None)} for block in result.content or []],
    }
    return len(njf_mail._thread_refs(payload))


@workflows.activity(
    name="njf-notify-new-mail",
    start_to_close_timeout=timedelta(minutes=15),
    retry_policy_max_attempts=2,
)
async def notify_new_mail(slot: str) -> dict[str, Any]:
    from mistralai import agents

    deployed_agent = agents.Agent.load_from(agent_name=AGENT_NAME, api_key=_user_api_key())
    session = await deployed_agent.start_session(idempotency_key=SESSION_IDEMPOTENCY_KEY)
    state = await session.run(
        CHECK_IN_PROMPT.format(slot=slot),
        idempotency_key=f"check:{slot}",
        timeout=TURN_TIMEOUT_SECONDS,
    )
    if isinstance(state, agents.Completed):
        return {"status": "completed", "reply": state.text}
    if isinstance(state, agents.Running):
        return {"status": "running", "reply": None}
    if isinstance(state, agents.Blocked):
        raise RuntimeError(f"Agent session waits on a callback: {[c.kind for c in state.callbacks]}")
    if isinstance(state, agents.Failed):
        raise RuntimeError(f"Agent turn failed: {state.error.message}")
    raise RuntimeError(f"Unexpected agent turn state: {state!r}")


@workflows.workflow.define(name="njf-mail-watcher")
class NjfMailWatcherWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, params: NjfMailWatcherInput) -> NjfMailWatcherOutput:
        now = workflow.now()
        slot = params.slot or now.replace(minute=0, second=0, microsecond=0).isoformat()
        count = await count_new_njf_mail()
        if params.dry_run:
            return NjfMailWatcherOutput(
                slot=slot,
                new_thread_count=count,
                dry_run=True,
                notified=False,
                status="dry-run",
            )
        if count == 0:
            return NjfMailWatcherOutput(
                slot=slot,
                new_thread_count=0,
                dry_run=False,
                notified=False,
                status="geen nieuwe mail",
            )
        result = await notify_new_mail(slot)
        reply = result.get("reply")
        notified = (
            result["status"] == "completed"
            and bool(reply)
            and reply.strip() != NO_NEW_MAIL_REPLY
        )
        return NjfMailWatcherOutput(
            slot=slot,
            new_thread_count=count,
            dry_run=False,
            notified=notified,
            status=result["status"],
            reply=reply,
        )

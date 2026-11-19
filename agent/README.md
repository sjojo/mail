# njf-mail-watcher agent

Agents SDK agent (`mistralai-agents`) that watches the NJF Gmail mailbox and posts a
Dutch summary with action suggestions into one persistent session whenever new mail
arrives. Deployed as the Mistral-managed (hardened) deployment `njf-mail-watcher`.

- Only the `gmail` connector with credential `njf-mail` is attached, so the agent can
  never see the personal mailbox.
- The hourly trigger is the `njf-mail-watcher` workflow in `../apps/worker`: it polls the
  inbox through the connectors API with a user API key from the workspace secret
  `njf-watcher-user-api-key`, and starts a CHECK-IN turn in this agent's persistent
  session (idempotency key `njf-mail-hoofdgesprek`).
- In Vibe: open <https://chat.mistral.ai/managed-agents/njf-mail-watcher> — new mail
  appears there as a new message; reply in the same conversation for follow-ups.
- Mails that were already reported get the Gmail label `NJF/Melding`, so they are never
  reported twice.

Local development: `uv sync`, set `MISTRAL_API_KEY` in `.env`, then
`uv run --env-file .env agents chat` (talks to the agent in your terminal).

Deploy (creates the managed agent record + the managed deployment, and prints the
Vibe chat URL). Run from this directory with the branch pushed:

```bash
cp .env.example .env   # put your MISTRAL_API_KEY in .env (never commit it)
uv sync
uv run --frozen --env-file .env agents deploy
```

The deploy builds the pushed commit (`Dockerfile` here); the container runs
`agents run-worker`. The agent appears in Vibe at
`https://chat.mistral.ai/managed-agents/njf-mail-watcher`.

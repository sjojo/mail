"""The watcher agent is pinned to the NJF gmail credential and nothing else."""

from main import (
    AGENT_NAME,
    GMAIL_CONNECTOR,
    GMAIL_CREDENTIALS_NAME,
    NO_NEW_MAIL_REPLY,
    NOTIFIED_LABEL,
    agent,
)


def test_agent_name_matches_deployment() -> None:
    assert agent.name == AGENT_NAME == "njf-mail-watcher"


def test_agent_uses_only_the_njf_gmail_credential() -> None:
    assert GMAIL_CONNECTOR.connector_name == "gmail"
    assert GMAIL_CONNECTOR.credentials_name == GMAIL_CREDENTIALS_NAME == "njf-mail"
    assert GMAIL_CONNECTOR.auto_auth is False


def test_instructions_define_the_notification_contract() -> None:
    instructions = agent.instructions or ""
    assert NOTIFIED_LABEL in instructions
    assert NO_NEW_MAIL_REPLY in instructions
    assert "CHECK-IN" in instructions

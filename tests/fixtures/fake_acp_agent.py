"""仅供自动化测试的最小ACP stdio Agent。"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from acp import (
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    run_agent,
    update_agent_message_text,
)
from acp.schema import AgentCapabilities, Implementation, PromptCapabilities


class FakeAgent:
    def __init__(self) -> None:
        self.connection = None

    def on_connect(self, connection) -> None:
        self.connection = connection

    async def initialize(self, protocol_version, **_kwargs):
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(
                prompt_capabilities=PromptCapabilities(image=True)
            ),
            agent_info=Implementation(
                name="soulvise-test-agent",
                title="Soulvise Test ACP",
                version="1.0",
            ),
        )

    async def new_session(self, **_kwargs):
        return NewSessionResponse(session_id=uuid4().hex)

    async def prompt(self, session_id, **_kwargs):
        await self.connection.session_update(
            session_id=session_id,
            update=update_agent_message_text("模拟协议回复"),
        )
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, **_kwargs) -> None:
        return None


if __name__ == "__main__":
    asyncio.run(run_agent(FakeAgent()))


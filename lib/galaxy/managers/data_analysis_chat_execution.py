"""Orchestration manager for data analysis chat execution flows."""

from typing import Any

from galaxy.exceptions import ObjectNotFound
from galaxy.managers.chat import ChatManager
from galaxy.managers.chat_execution import ChatExecutionService
from galaxy.managers.chat_execution_streams import ChatExecutionStreamsManager
from galaxy.managers.context import ProvidesHistoryContext
from galaxy.model import User
from galaxy.schema.schema import PyodideResultPayload


class DataAnalysisChatExecutionManager:
    """Coordinate data analysis execution result handling and stream updates."""

    def __init__(
        self,
        chat_manager: ChatManager,
        chat_execution_service: ChatExecutionService,
        chat_execution_streams: ChatExecutionStreamsManager,
    ):
        self.chat_manager = chat_manager
        self.chat_execution_service = chat_execution_service
        self.chat_execution_streams = chat_execution_streams

    def ensure_exchange_access(self, trans: ProvidesHistoryContext, exchange_id: int):
        exchange = self.chat_manager.get_exchange_by_id(trans, exchange_id)
        if exchange is None:
            raise ObjectNotFound("Chat exchange not found")
        return exchange

    async def submit_pyodide_result(
        self,
        exchange_id: int,
        payload: PyodideResultPayload,
        trans: ProvidesHistoryContext,
        user: User,
    ) -> dict[str, Any]:
        self.ensure_exchange_access(trans, exchange_id)
        response_payload = await self.chat_execution_service.handle_pyodide_result(exchange_id, payload, trans, user)
        if response_payload.get("agent_response"):
            await self.chat_execution_streams.broadcast_exec_followup(
                exchange_id,
                {
                    "type": "exec_followup",
                    "exchange_id": exchange_id,
                    "task_id": payload.task_id,
                    "payload": response_payload,
                },
            )
        return response_payload

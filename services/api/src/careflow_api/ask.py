"""Ask the clinic (M2-09): the API's side of the phone-only agent in `careflow_rag.agent`.

The API owns the agent's ports, all bound to the verified token:

- `retrieve`: the M2-08 handbook search in the clinic's row-level security scope, with one
  embedder per process (`config/rag.yaml`);
- `queue_status`: the token's own session only, from the queue service (M1-10);
- `flag`: the refused question stored for the nurse, with `nurse.flag` and `rag.refused` in the
  same transaction (`nurse_flags`).

The last answered turns of each session (3 by default) are kept in this process's memory only,
for follow-up questions; refused questions are never kept. There is no kiosk route: ADR-006 defers
kiosk RAG and wayfinding.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Final

from careflow_api.events.outbox import OutboxPublisher
from careflow_api.nurse_flags import record_medical_question
from careflow_api.queue.service import QueueService, utc_now
from careflow_api.rls import clinic_session
from careflow_contracts import RetrievedChunk
from careflow_providers.base import ChatProvider
from careflow_rag.agent import AskAgent, AskPorts, AskResult, Lang, MedicalFlag, QueueSnapshot, Turn
from careflow_rag.config import RagConfig
from careflow_rag.embed import Embedder, HashEmbedder, QwenEmbedder
from careflow_rag.retrieve import retrieve

#: Sessions whose turns are remembered; the oldest is forgotten first.
MAX_SESSIONS: Final = 1000


class OtherSession(PermissionError):
    """A port was asked about a session other than the token's own."""


class AskService:
    """One per API process."""

    def __init__(
        self,
        config: RagConfig,
        provider: Callable[[], ChatProvider],
        *,
        embedder: Embedder | None = None,
    ) -> None:
        self.config = config
        self.agent = AskAgent(provider, config.ask)
        self._embedder = embedder
        self._embedder_lock = threading.Lock()
        self._turns: OrderedDict[str, deque[Turn]] = OrderedDict()

    def embedder(self) -> Embedder:
        with self._embedder_lock:
            if self._embedder is None:
                self._embedder = (
                    QwenEmbedder() if self.config.embedder == "qwen" else HashEmbedder()
                )
            return self._embedder

    def history(self, session_id: str) -> list[Turn]:
        return list(self._turns.get(session_id, ()))

    def remember(self, session_id: str, turn: Turn) -> None:
        turns = self._turns.pop(session_id, None) or deque(maxlen=self.config.history_turns)
        turns.append(turn)
        self._turns[session_id] = turns
        while len(self._turns) > MAX_SESSIONS:
            self._turns.popitem(last=False)

    def ports(
        self,
        clinic_id: uuid.UUID,
        session_id: str,
        *,
        session_state: str,
        queue: QueueService,
        events: OutboxPublisher,
    ) -> AskPorts:
        """The agent's tools for one question, bound to the token's clinic and session."""

        async def retrieve_port(question: str, lang: Lang) -> list[RetrievedChunk]:
            async with clinic_session(clinic_id) as db:
                return await retrieve(
                    db,
                    question,
                    lang,
                    clinic_id,
                    embedder=self.embedder(),
                    k=self.config.k,
                    mode=self.config.mode,
                )

        async def queue_port(asked_for: str) -> QueueSnapshot:
            if asked_for != session_id:  # the model never chooses a session: this cannot happen
                raise OtherSession(asked_for)
            async with clinic_session(clinic_id) as db:
                status = await queue.companion_status(
                    db, session_id=session_id, session_state=session_state
                )
            return QueueSnapshot(
                state=status.state.value,
                position=status.position,
                wait_estimate_minutes=status.wait_estimate_minutes,
                token=status.token.token_code if status.token else None,
                room=status.room,
            )

        async def flag_port(flag: MedicalFlag) -> str:
            if flag.session_id != session_id:
                raise OtherSession(flag.session_id)
            return await record_medical_question(clinic_id, flag, events=events, now=utc_now())

        return AskPorts(retrieve=retrieve_port, queue_status=queue_port, flag=flag_port)

    async def ask(
        self, question: str, lang: Lang, *, session_id: str, ports: AskPorts
    ) -> AskResult:
        result = await self.agent.ask(
            question, lang, session_id=session_id, ports=ports, history=self.history(session_id)
        )
        if not result.refused_medical:
            self.remember(session_id, Turn(question=question, answer=result.answer))
        return result


__all__ = ["AskService", "OtherSession"]

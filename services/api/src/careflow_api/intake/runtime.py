"""`IntakeRuntime`: the LangGraph intake graph (M1-05) running behind the API (M1-12).

One compiled graph over an `AsyncPostgresSaver` on a psycopg pool; the thread id is the session
id. Work on one session is serialised twice: an in-process `asyncio.Lock` and a Postgres
advisory lock (`pg_advisory_xact_lock(hashtext(session_id))`), held for the whole graph step, so
two requests, in this process or another, never advance the same thread at once.

The checkpoint tables are created by `careflow-api setup-checkpoints` (as the migrator, after
`make migrate`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from careflow_contracts.generated.events.alert_raised_schema import AlertRaisedData
from careflow_contracts.generated.events.envelope_schema import EventType
from careflow_contracts.generated.nurse_decision_schema import NurseDecision
from careflow_contracts.generated.structured_facts_schema import Language
from careflow_intake.checkpoint import libpq_url, serializer
from careflow_intake.facts import FactEntry
from careflow_intake.feels_worse import feels_worse_turns_and_facts
from careflow_intake.graph import (
    IntakeGraph,
    NotWaiting,
    add_facts,
    answer,
    build_graph,
    decide,
    pending,
    thread_config,
    waiting_at,
)
from careflow_intake.patient_details import PatientType, patient_turns_and_facts
from careflow_intake.ports import IntakePorts
from careflow_intake.state import IntakeSettings, IntakeState, structured_facts

logger = logging.getLogger("careflow_api.intake")

type Result = dict[str, Any]


class PatientAlreadySet(RuntimeError):
    """The session already has a different answer to "Who is the patient?"."""


class IntakeRuntime:
    def __init__(
        self,
        graph: IntakeGraph,
        ports: IntakePorts,
        *,
        pool: AsyncConnectionPool[Any] | None = None,
        settings: IntakeSettings | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.graph = graph
        self.ports = ports
        self.pool = pool
        self.settings = settings or IntakeSettings()
        self.clock = clock
        self._locks: dict[str, asyncio.Lock] = {}

    @classmethod
    @asynccontextmanager
    async def open(
        cls, database_url: str, ports: IntakePorts, settings: IntakeSettings | None = None
    ) -> AsyncIterator[IntakeRuntime]:
        """A runtime on its own connection pool, closed on exit."""
        pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
            libpq_url(database_url),
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            min_size=1,
            max_size=10,
            open=False,
        )
        await pool.open()
        try:
            saver = AsyncPostgresSaver(cast(Any, pool), serde=serializer())
            graph = build_graph(ports, saver, settings=settings)
            yield cls(graph, ports, pool=pool, settings=settings)
        finally:
            await pool.close()

    @asynccontextmanager
    async def serialized(self, session_id: str) -> AsyncIterator[None]:
        """One step at a time per session, across processes."""
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            if self.pool is None:
                yield
                return
            async with self.pool.connection() as conn, conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (session_id,))
                yield

    async def start(
        self,
        *,
        session_id: str,
        clinic_id: str,
        arrived_at: datetime,
        language: Language = Language.en,
        extra: dict[str, Any] | None = None,
    ) -> Result:
        """Run a new intake until its first pause (`extra`: more initial state, e.g. the
        check-in's patient identifier)."""
        state = cast(
            IntakeState,
            {
                "session_id": session_id,
                "clinic_id": clinic_id,
                "language": language,
                "arrived_at": arrived_at,
                **(extra or {}),
            },
        )
        async with self.serialized(session_id):
            return await self.graph.ainvoke(state, thread_config(session_id))

    async def submit_turn(self, session_id: str, text: str, input_mode: str = "text") -> Result:
        """The patient's answer and how it came; raises `NotWaiting` or `InvalidAnswer`."""
        async with self.serialized(session_id):
            return await answer(self.graph, session_id, text, input_mode)

    async def set_patient(
        self,
        session_id: str,
        *,
        patient_type: PatientType,
        age_years: int | None = None,
        age_months: int | None = None,
    ) -> IntakeState:
        """The kiosk's "Who is the patient?" answer (M2-04), once: its turns, a child's stated age
        facts and `patient_type`, then the safety rules at once. The same answer again changes
        nothing; a different one raises `PatientAlreadySet`. Raises `NotWaiting` without a
        checkpoint."""
        async with self.serialized(session_id):
            config = thread_config(session_id)
            state = cast(IntakeState, (await self.graph.aget_state(config)).values)
            if not state:
                raise NotWaiting(f"session {session_id} has no intake")
            existing = state.get("patient_type")
            if existing is not None:
                if existing != patient_type:
                    raise PatientAlreadySet(f"session {session_id} is already {existing}")
                return state
            turns, facts = patient_turns_and_facts(
                language=state["language"],
                turn_number=len(state.get("turns", [])) + 1,
                patient_type=patient_type,
                age_years=age_years,
                age_months=age_months,
                now=self.ports.clock(),
            )
            values = {"patient_type": patient_type, "turns": turns, "facts": facts}
            return await self._merge_and_evaluate(session_id, values)

    async def add_facts(self, session_id: str, facts: Sequence[FactEntry]) -> None:
        async with self.serialized(session_id):
            await add_facts(self.graph, session_id, facts)

    async def report_feels_worse(
        self, session_id: str, *, codes: Sequence[str], now: datetime
    ) -> IntakeState:
        """An "I feel worse" press (M2-07): its turns and the facts its ticked answers state, then
        the safety rules at once (new rule ids go out as `alert.raised`). Raises `NotWaiting`
        without a checkpoint."""
        async with self.serialized(session_id):
            config = thread_config(session_id)
            state = cast(IntakeState, (await self.graph.aget_state(config)).values)
            if not state:
                raise NotWaiting(f"session {session_id} has no intake")
            turns, facts = feels_worse_turns_and_facts(
                language=state["language"],
                turn_number=len(state.get("turns", [])) + 1,
                patient_type=state.get("patient_type", "adult"),
                codes=codes,
                now=now,
            )
            return await self._merge_and_evaluate(session_id, {"turns": turns, "facts": facts})

    async def add_facts_and_evaluate(
        self, session_id: str, facts: Sequence[FactEntry]
    ) -> IntakeState:
        """Merge external facts (device or nurse vitals), then run the safety rules at once
        (safety rule 1): new alerts go into the state and out as `alert.raised`, one per rule id
        (idempotency key `{session_id}:{rule_id}`, as the graph's own rules node uses)."""
        async with self.serialized(session_id):
            return await self._merge_and_evaluate(session_id, {"facts": list(facts)})

    async def _merge_and_evaluate(self, session_id: str, values: dict[str, Any]) -> IntakeState:
        """Under the session's lock: merge `values`, then run the rules and emit new alerts."""
        config = thread_config(session_id)
        await self.graph.aupdate_state(config, values)
        state = cast(IntakeState, (await self.graph.aget_state(config)).values)
        now = self.ports.clock()
        outcome = self.ports.rules.run(
            structured_facts(state), now=now, arrived_at=state.get("arrived_at")
        )
        known = {alert.rule_id for alert in state.get("alerts", [])}
        await self.graph.aupdate_state(
            config,
            {
                "alerts": list(outcome.alerts),
                "needed_facts": list(outcome.needed_facts),
                "ruleset_version": outcome.ruleset_version,
            },
        )
        for alert in outcome.alerts:
            if alert.rule_id in known:
                continue
            known.add(alert.rule_id)
            data = AlertRaisedData(clinic_id=state["clinic_id"], session_id=session_id, alert=alert)
            key = f"{session_id}:{alert.rule_id.value}"
            await self.ports.events.emit(EventType.alert_raised, data, key)
        return cast(IntakeState, (await self.graph.aget_state(config)).values)

    async def update(self, session_id: str, values: IntakeState | dict[str, Any]) -> None:
        """Merge values into the session's state (the channels' reducers apply)."""
        async with self.serialized(session_id):
            await self.graph.aupdate_state(thread_config(session_id), values)

    async def resume_nurse(self, session_id: str, decision: NurseDecision) -> Result:
        """The nurse's decision; raises `NotWaiting` or `InvalidNurseDecision`."""
        async with self.serialized(session_id):
            return await decide(self.graph, session_id, decision, settings=self.settings)

    async def state(self, session_id: str) -> IntakeState | None:
        """The session's current state, or None when it has no checkpoint."""
        snapshot = await self.graph.aget_state(thread_config(session_id))
        if not snapshot.values:
            return None
        return cast(IntakeState, snapshot.values)

    async def waiting_for(self, session_id: str) -> tuple[str | None, dict[str, Any] | None]:
        """Where the session is paused (`await_answer`, `nurse_review`) and the pause's value."""
        snapshot = await self.graph.aget_state(thread_config(session_id))
        for node in ("await_answer", "nurse_review"):
            if waiting_at(snapshot, node):
                return node, pending(snapshot)
        return None, None

    async def load(self, clinic_id: str, session_id: str) -> IntakeState | None:
        """The briefing's `StateSource` (M1-16): the state of a session of this clinic."""
        state = await self.state(session_id)
        if state is None or state.get("clinic_id") != clinic_id:
            return None
        return state


__all__ = ["IntakeRuntime", "NotWaiting", "PatientAlreadySet"]

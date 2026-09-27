from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from secrets import token_urlsafe
from typing import Any, Protocol

import redis.asyncio as redis

from voice_agent.config.settings import DEFAULT_CALL_SESSION_TTL_SECONDS, Settings

REDIS_SESSION_LOCK_TTL_SECONDS = 10
REDIS_SESSION_LOCK_ACQUIRE_TIMEOUT_SECONDS = 5.0
REDIS_SESSION_LOCK_RETRY_SECONDS = 0.05
CALL_SESSION_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
CALL_SESSION_IDENTIFIER_FIELDS = frozenset({"idempotency_key", "call_id", "request_uuid"})
REDIS_UNLOCK_IF_TOKEN_MATCHES_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0
"""
TERMINAL_CALL_STATUSES = frozenset(
    {"busy", "canceled", "cancelled", "completed", "failed", "no-answer", "no_answer"}
)


class CallSessionStoreError(ValueError):
    """Raised when a call-session update cannot be indexed safely."""


@dataclass(frozen=True, slots=True)
class CallSessionUpdate:
    idempotency_key: str | None = None
    call_id: str | None = None
    request_uuid: str | None = None
    api_id: str | None = None
    message: str | None = None
    direction: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    status: str | None = None
    event: str | None = None
    stream_id: str | None = None
    transcript: str | None = None
    last_event: dict[str, Any] | None = None


class CallSessionStore(Protocol):
    async def upsert(self, update: CallSessionUpdate) -> dict[str, Any]: ...

    async def reserve_idempotency_key(self, update: CallSessionUpdate) -> dict[str, Any] | None: ...

    async def get(self, identifier: str) -> dict[str, Any] | None: ...

    async def health_check(self) -> bool: ...

    async def close(self) -> None: ...


class InMemoryCallSessionStore:
    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._sessions_by_identifier: dict[str, dict[str, Any]] = {}
        self._now = now or _utc_now
        self._lock = asyncio.Lock()

    async def upsert(self, update: CallSessionUpdate) -> dict[str, Any]:
        async with self._lock:
            existing = self._load_existing(update)
            merged = _merge_session(
                existing=existing,
                update=update,
                now=_format_time(self._now()),
            )
            self._store_aliases(merged)
            return dict(merged)

    async def reserve_idempotency_key(self, update: CallSessionUpdate) -> dict[str, Any] | None:
        idempotency_key = _normalise_identifier(
            update.idempotency_key,
            field_name="idempotency_key",
            strict=True,
        )
        if idempotency_key is None:
            raise CallSessionStoreError("Idempotency reservations require idempotency_key.")
        async with self._lock:
            existing = await self.get(idempotency_key)
            if existing is not None:
                return existing
            merged = _merge_session(
                existing=None,
                update=update,
                now=_format_time(self._now()),
            )
            self._store_aliases(merged)
            return None

    async def get(self, identifier: str) -> dict[str, Any] | None:
        clean_identifier = _normalise_identifier(identifier)
        if clean_identifier is None:
            return None
        session = self._sessions_by_identifier.get(clean_identifier)
        return dict(session) if session is not None else None

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    def _load_existing(self, update: CallSessionUpdate) -> dict[str, Any] | None:
        sessions: list[dict[str, Any]] = []
        for identifier in _update_identifiers(update):
            session = self._sessions_by_identifier.get(identifier)
            if session is not None:
                sessions.append(dict(session))
        return _merge_existing_sessions(sessions)

    def _store_aliases(self, session: dict[str, Any]) -> None:
        for identifier in _session_identifiers(session):
            self._sessions_by_identifier[identifier] = dict(session)


class RedisCallSessionStore:
    def __init__(
        self,
        *,
        redis_url: str,
        key_prefix: str = "voice_agent:call_sessions:",
        ttl_seconds: int = DEFAULT_CALL_SESSION_TTL_SECONDS,
        socket_timeout_seconds: float = 2.0,
        connect_timeout_seconds: float = 2.0,
        health_check_interval_seconds: float = 30.0,
        max_connections: int = 50,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._redis: Any = redis.from_url(  # type: ignore[no-untyped-call]
            redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout_seconds,
            socket_connect_timeout=connect_timeout_seconds,
            health_check_interval=int(health_check_interval_seconds),
            max_connections=max_connections,
        )
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds
        self._now = now or _utc_now

    async def upsert(self, update: CallSessionUpdate) -> dict[str, Any]:
        locked_identifiers = _update_identifiers(update)
        while True:
            async with self._identifier_locks(locked_identifiers):
                existing = await self._load_existing(locked_identifiers)
                merged = _merge_session(
                    existing=existing,
                    update=update,
                    now=_format_time(self._now()),
                )
                merged_identifiers = _session_identifiers(merged)
                expanded_identifiers = _merge_identifier_sets(
                    locked_identifiers,
                    merged_identifiers,
                )
                if expanded_identifiers != locked_identifiers:
                    locked_identifiers = expanded_identifiers
                    continue
                await self._store_session_aliases(merged)
                return merged

    async def _store_session_aliases(self, session: dict[str, Any]) -> None:
        serialized = json.dumps(session, separators=(",", ":"), sort_keys=True)
        for identifier in _session_identifiers(session):
            await self._redis.set(self._key(identifier), serialized, ex=self._ttl_seconds)

    async def reserve_idempotency_key(self, update: CallSessionUpdate) -> dict[str, Any] | None:
        idempotency_key = _normalise_identifier(
            update.idempotency_key,
            field_name="idempotency_key",
            strict=True,
        )
        if idempotency_key is None:
            raise CallSessionStoreError("Idempotency reservations require idempotency_key.")
        merged = _merge_session(existing=None, update=update, now=_format_time(self._now()))
        serialized = json.dumps(merged, separators=(",", ":"), sort_keys=True)
        reservation_created = await self._redis.set(
            self._key(idempotency_key),
            serialized,
            nx=True,
            ex=self._ttl_seconds,
        )
        if reservation_created:
            return None
        return await self._load_existing_reservation(idempotency_key)

    async def get(self, identifier: str) -> dict[str, Any] | None:
        clean_identifier = _normalise_identifier(identifier)
        if clean_identifier is None:
            return None
        raw = await self._redis.get(self._key(clean_identifier))
        if raw is None:
            return None
        return _normalise_session(_decode_stored_session(raw, identifier=clean_identifier))

    async def health_check(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except redis.RedisError:
            return False

    async def close(self) -> None:
        await self._redis.aclose()

    async def _load_existing(self, identifiers: tuple[str, ...]) -> dict[str, Any] | None:
        sessions: list[dict[str, Any]] = []
        for identifier in identifiers:
            session = await self.get(identifier)
            if session is not None:
                sessions.append(session)
        return _merge_existing_sessions(sessions)

    async def _load_existing_reservation(self, idempotency_key: str) -> dict[str, Any] | None:
        locked_identifiers: tuple[str, ...] = (idempotency_key,)
        while True:
            async with self._identifier_locks(locked_identifiers):
                existing = await self._load_existing(locked_identifiers)
                if existing is None:
                    return None
                expanded_identifiers = _merge_identifier_sets(
                    locked_identifiers,
                    _session_identifiers(existing),
                )
                if expanded_identifiers != locked_identifiers:
                    locked_identifiers = expanded_identifiers
                    continue
                await self._store_session_aliases(existing)
                return existing

    def _key(self, identifier: str) -> str:
        clean_identifier = _normalise_identifier(identifier, strict=True)
        if clean_identifier is None:
            raise CallSessionStoreError("Call session storage key identifier must not be blank.")
        return self._key_prefix + clean_identifier

    def _lock_key(self, identifier: str) -> str:
        clean_identifier = _normalise_identifier(identifier, strict=True)
        if clean_identifier is None:
            raise CallSessionStoreError("Call session lock identifier must not be blank.")
        return f"{self._key_prefix}locks:{clean_identifier}"

    @asynccontextmanager
    async def _identifier_locks(self, identifiers: tuple[str, ...]) -> AsyncIterator[None]:
        lock_keys = tuple(sorted({self._lock_key(identifier) for identifier in identifiers}))
        if not lock_keys:
            yield
            return

        token = token_urlsafe(24)
        acquired_keys: list[str] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + REDIS_SESSION_LOCK_ACQUIRE_TIMEOUT_SECONDS
        try:
            for lock_key in lock_keys:
                while True:
                    acquired = await self._redis.set(
                        lock_key,
                        token,
                        nx=True,
                        ex=REDIS_SESSION_LOCK_TTL_SECONDS,
                    )
                    if acquired:
                        acquired_keys.append(lock_key)
                        break
                    if loop.time() >= deadline:
                        raise CallSessionStoreError(
                            "Timed out acquiring Redis call-session alias lock."
                        )
                    await asyncio.sleep(REDIS_SESSION_LOCK_RETRY_SECONDS)
            yield
        finally:
            for lock_key in reversed(acquired_keys):
                await self._release_lock(lock_key, token)

    async def _release_lock(self, lock_key: str, token: str) -> None:
        eval_script = getattr(self._redis, "eval", None)
        if callable(eval_script):
            await eval_script(REDIS_UNLOCK_IF_TOKEN_MATCHES_SCRIPT, 1, lock_key, token)
            return

        value = await self._redis.get(lock_key)
        if value == token:
            await self._redis.delete(lock_key)


def build_call_session_store(settings: Settings) -> CallSessionStore:
    if settings.session_backend == "memory":
        return InMemoryCallSessionStore()
    if not settings.redis_url:
        raise ValueError("redis_url is required for Redis call-session storage.")
    return RedisCallSessionStore(
        redis_url=settings.redis_url,
        ttl_seconds=settings.call_session_ttl_seconds,
        socket_timeout_seconds=settings.redis_socket_timeout_seconds,
        connect_timeout_seconds=settings.redis_connect_timeout_seconds,
        health_check_interval_seconds=settings.redis_health_check_interval_seconds,
        max_connections=settings.redis_max_connections,
    )


def _merge_session(
    *,
    existing: dict[str, Any] | None,
    update: CallSessionUpdate,
    now: str,
) -> dict[str, Any]:
    identifiers = _update_identifiers(update)
    if not identifiers:
        raise CallSessionStoreError("Call session updates require call_id or request_uuid.")

    merged = _normalise_session(existing or {})
    if merged["created_at"] is None:
        merged["created_at"] = now
    merged["updated_at"] = now

    should_preserve_terminal_lifecycle = _should_preserve_terminal_lifecycle(
        existing_status=merged.get("status"),
        update_status=update.status,
    )
    for field in fields(update):
        value = _normalise_update_value(field.name, getattr(update, field.name))
        if value is not None:
            if should_preserve_terminal_lifecycle and field.name in {"status", "event"}:
                continue
            merged[field.name] = value
    return merged


def _normalise_session(data: dict[str, Any]) -> dict[str, Any]:
    normalised: dict[str, Any] = {
        "idempotency_key": None,
        "call_id": None,
        "request_uuid": None,
        "api_id": None,
        "message": None,
        "direction": None,
        "from_number": None,
        "to_number": None,
        "status": None,
        "event": None,
        "stream_id": None,
        "transcript": None,
        "created_at": None,
        "updated_at": None,
        "last_event": None,
    }
    for key in normalised:
        value = data.get(key)
        if key == "last_event":
            normalised[key] = value if isinstance(value, dict) else None
        elif key in CALL_SESSION_IDENTIFIER_FIELDS:
            normalised[key] = _normalise_identifier(value, field_name=key)
        elif value is None:
            normalised[key] = None
        elif isinstance(value, str):
            clean_value = value.strip()
            normalised[key] = clean_value if clean_value else None
    return normalised


def _decode_stored_session(raw: str, *, identifier: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CallSessionStoreError(
            f"Stored Redis call session for {identifier} was not valid JSON."
        ) from exc
    if not isinstance(data, dict):
        raise CallSessionStoreError(
            f"Stored Redis call session for {identifier} was not a JSON object."
        )
    return data


def _should_preserve_terminal_lifecycle(
    *,
    existing_status: object,
    update_status: str | None,
) -> bool:
    if not is_terminal_call_status(existing_status):
        return False
    return update_status is None or not is_terminal_call_status(update_status)


def is_terminal_call_status(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in TERMINAL_CALL_STATUSES


def _merge_existing_sessions(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not sessions:
        return None

    merged = _normalise_session({})
    normalised_sessions = [_normalise_session(session) for session in sessions]
    for normalised in normalised_sessions:
        for key, value in normalised.items():
            if value is not None and merged[key] is None:
                merged[key] = value
    terminal_lifecycle = _select_terminal_lifecycle_session(normalised_sessions)
    if terminal_lifecycle is not None:
        merged["status"] = terminal_lifecycle["status"]
        merged["event"] = terminal_lifecycle["event"]
    else:
        latest_lifecycle = _select_latest_lifecycle_session(normalised_sessions)
        if latest_lifecycle is not None:
            merged["status"] = latest_lifecycle["status"]
            merged["event"] = latest_lifecycle["event"]
    return merged


def _select_terminal_lifecycle_session(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    terminal_sessions = [
        session for session in sessions if is_terminal_call_status(session.get("status"))
    ]
    if not terminal_sessions:
        return None
    return max(terminal_sessions, key=_session_updated_at)


def _select_latest_lifecycle_session(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    lifecycle_sessions = [
        session
        for session in sessions
        if session.get("status") is not None or session.get("event") is not None
    ]
    if not lifecycle_sessions:
        return None
    return max(lifecycle_sessions, key=_session_updated_at)


def _session_updated_at(session: dict[str, Any]) -> str:
    value = session.get("updated_at")
    return value if isinstance(value, str) else ""


def _update_identifiers(update: CallSessionUpdate) -> tuple[str, ...]:
    return _compact_identifiers(
        _normalise_identifier(
            update.idempotency_key,
            field_name="idempotency_key",
            strict=True,
        ),
        _normalise_identifier(update.call_id, field_name="call_id", strict=True),
        _normalise_identifier(update.request_uuid, field_name="request_uuid", strict=True),
    )


def _session_identifiers(session: dict[str, Any]) -> tuple[str, ...]:
    return _compact_identifiers(
        session.get("idempotency_key"),
        session.get("call_id"),
        session.get("request_uuid"),
    )


def _merge_identifier_sets(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> tuple[str, ...]:
    return _compact_identifiers(*left, *right)


def _compact_identifiers(*values: object) -> tuple[str, ...]:
    identifiers: list[str] = []
    for value in values:
        identifier = _normalise_identifier(value)
        if identifier is not None and identifier not in identifiers:
            identifiers.append(identifier)
    return tuple(identifiers)


def _normalise_identifier(
    value: object,
    *,
    field_name: str = "identifier",
    strict: bool = False,
) -> str | None:
    if not isinstance(value, str):
        return None
    identifier = value.strip()
    if not identifier:
        return None
    if CALL_SESSION_IDENTIFIER_RE.fullmatch(identifier) is not None:
        return identifier
    if strict:
        raise CallSessionStoreError(
            f"Call session {field_name} must be 1-128 characters using letters, "
            "numbers, dot, underscore, colon, or hyphen."
        )
    return None


def _normalise_update_value(field_name: str, value: object) -> object:
    if field_name in CALL_SESSION_IDENTIFIER_FIELDS:
        return _normalise_identifier(value, field_name=field_name, strict=True)
    if field_name == "last_event" or not isinstance(value, str):
        return value
    clean_value = value.strip()
    return clean_value if clean_value else None


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

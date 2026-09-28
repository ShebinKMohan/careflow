"""Record/replay for any provider: the one cassette mechanism in the repo.

`CAREFLOW_PROVIDER_MODE=record` calls the wrapped provider and saves each response; `add` answers
what the cassette holds and records only the prompts it lacks (a new journey's, M2-12), so earlier
recordings are never re-recorded; `replay` answers from the cassette or raises `CassetteMiss`;
anything else passes straight through.
`CAREFLOW_CASSETTE=<path>` selects the file. Entries are keyed by the SHA-256 of the canonical
`(task, model, messages, temperature)`. M1-06, M1-19, M2-09, M2-11 and M5-06 reuse this.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, Literal

from careflow_providers.base import ChatProvider, ChatResult, Message
from careflow_providers.errors import CassetteMiss

Mode = Literal["record", "add", "replay", "off"]


def cassette_key(task: str, model: str, messages: Sequence[Message], temperature: float) -> str:
    canonical = json.dumps(
        {"task": task, "model": model, "messages": list(messages), "temperature": temperature},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReplayProvider:
    def __init__(
        self,
        inner: ChatProvider | None,
        *,
        task: str,
        model: str,
        cassette: str | Path | None = None,
        mode: Mode | None = None,
    ) -> None:
        self.task = task
        self.model_name = model
        self._inner = inner
        self._mode: Mode = mode or _mode_from_env()
        path = cassette or os.environ.get("CAREFLOW_CASSETTE")
        if path is None:
            raise ValueError("ReplayProvider needs a cassette path (CAREFLOW_CASSETTE)")
        self._path = Path(path)
        self._entries: dict[str, dict[str, Any]] = (
            json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}
        )

    @property
    def mode(self) -> Mode:
        return self._mode

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._entries, indent=2, sort_keys=True, ensure_ascii=False)
        self._path.write_text(text + "\n", encoding="utf-8")

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        key = cassette_key(self.task, self.model_name, messages, temperature)
        entry = self._entries.get(key)
        if self._mode == "replay":
            if entry is None:
                raise CassetteMiss(f"no recording for task {self.task!r} (key {key[:12]}...)")
            return ChatResult(text=entry["text"], model=entry["model"])
        if self._mode == "add" and entry is not None:  # recorded before: kept as it is
            return ChatResult(text=entry["text"], model=entry["model"])
        if self._inner is None:
            raise CassetteMiss("record and pass-through modes need a real provider to wrap")
        result = await self._inner.chat(messages, temperature=temperature, max_tokens=max_tokens)
        if self._mode in ("record", "add"):
            self._entries[key] = {"task": self.task, "model": result.model, "text": result.text}
            self._save()
        return result

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield (await self.chat(messages)).text


def provider_mode() -> Mode:
    """`CAREFLOW_PROVIDER_MODE` (record, add, replay or off; unset is off)."""
    return _mode_from_env()


def _mode_from_env() -> Mode:
    value = os.environ.get("CAREFLOW_PROVIDER_MODE", "off")
    if value not in ("record", "add", "replay", "off"):
        raise ValueError(
            f"CAREFLOW_PROVIDER_MODE must be record, add, replay or off, not {value!r}"
        )
    return value  # type: ignore[return-value]

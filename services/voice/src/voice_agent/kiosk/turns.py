"""`POST /v1/sessions/{id}/turns` on the CareFlow API, with the patient's session token."""

from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import ValidationError

from careflow_contracts.api_models import InputMode, TurnRequest, TurnResponse
from careflow_contracts.generated.structured_facts_schema import Language
from voice_agent.kiosk.auth import PatientSession


class TurnError(Exception):
    """The turn did not reach the intake graph; `code` goes to the kiosk as an error message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TurnPoster(Protocol):
    async def post(self, session: PatientSession, *, text: str, language: str) -> TurnResponse: ...


_STATUS_CODES = {
    401: "unauthorized",
    403: "unauthorized",
    404: "session_not_found",
    409: "conflict",
}


class TurnsClient:
    def __init__(
        self, *, base_url: str, timeout_seconds: float, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def post(self, session: PatientSession, *, text: str, language: str) -> TurnResponse:
        body = TurnRequest(
            input_mode=InputMode.voice, text=text[:2000], language=Language(language)
        )
        url = f"{self._base_url}/v1/sessions/{session.session_id}/turns"
        try:
            response = await self._client.post(
                url,
                json=body.model_dump(mode="json"),
                headers={"Authorization": f"Bearer {session.token}"},
            )
        except httpx.HTTPError as exc:
            raise TurnError("api_unavailable") from exc
        if response.status_code >= 400:
            code = _STATUS_CODES.get(response.status_code)
            if code is None:
                code = "api_unavailable" if response.status_code >= 500 else "turn_rejected"
            raise TurnError(code)
        try:
            return TurnResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise TurnError("bad_api_response") from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

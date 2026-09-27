"""`FHIR_BASE_URL` moves every FHIR call the API makes, not only the health check (M1-12).

Before, the check-in lookup, the write-back and the task completion read the hospital adapter
file (port 8080) while the health check and the briefing read the setting, so a stack pointed at
another FHIR server still wrote to the default one.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

from careflow_api import hospital
from careflow_api.config import get_settings
from careflow_api.hospital import fhir_endpoint, hospital_adapter
from careflow_api.intake.ports import CachedEhrReader
from careflow_api.routers.health import check_fhir


@pytest.fixture
def moved(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with socket.socket() as probe:  # a port nothing listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}/fhir"
    monkeypatch.setenv("FHIR_BASE_URL", url)
    get_settings.cache_clear()
    hospital._adapter.cache_clear()  # noqa: SLF001 - the cache under test
    yield url
    get_settings.cache_clear()
    hospital._adapter.cache_clear()  # noqa: SLF001


async def test_one_setting_moves_every_fhir_call(moved: str) -> None:
    assert fhir_endpoint() == moved
    assert hospital_adapter().fhir_base_url == moved
    assert CachedEhrReader().reader.adapter.fhir_base_url == moved  # check-in and the graph
    assert hospital_adapter().identifier_systems["keypad"]  # the rest of the file still applies
    assert await check_fhir(fhir_endpoint()) == "down"  # nothing listens there

"""One FHIR endpoint override: `base_url`, else `FHIR_BASE_URL`, else the adapter file."""

from __future__ import annotations

import pytest

from careflow_integration.adapters.loader import FHIR_BASE_URL_ENV, load_adapter
from careflow_integration.fhir.reader import FhirEhrReader

FILE_URL = "http://localhost:8080/fhir"


def test_the_file_decides_without_an_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FHIR_BASE_URL_ENV, raising=False)
    assert load_adapter().fhir_base_url == FILE_URL


def test_fhir_base_url_moves_every_adapter_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FHIR_BASE_URL_ENV, "http://127.0.0.1:18080/fhir")
    adapter = load_adapter()
    assert adapter.fhir_base_url == "http://127.0.0.1:18080/fhir"
    # Nothing else in the file changes.
    assert adapter.identifier_systems == load_adapter(base_url=FILE_URL).identifier_systems
    assert FhirEhrReader().adapter.fhir_base_url == "http://127.0.0.1:18080/fhir"


def test_an_explicit_base_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FHIR_BASE_URL_ENV, "http://127.0.0.1:18080/fhir")
    assert load_adapter(base_url="http://hapi.test/fhir").fhir_base_url == "http://hapi.test/fhir"

"""The hospital adapter the API uses (M1-08), with the one FHIR endpoint override.

`Settings.fhir_base_url` (`FHIR_BASE_URL`, from the environment or `.env`) wins over the
adapter file's `fhir_base_url`; unset, the file decides. Everything in the API that talks to FHIR
takes its endpoint from here, so no call is left on the default port when the stack moves.
"""

from __future__ import annotations

from functools import cache

from careflow_api.config import get_settings
from careflow_integration.adapters.loader import HospitalAdapter, load_adapter


def hospital_adapter() -> HospitalAdapter:
    return _adapter(get_settings().fhir_base_url)


@cache
def _adapter(base_url: str | None) -> HospitalAdapter:
    return load_adapter(base_url=base_url)


def fhir_endpoint() -> str:
    return hospital_adapter().fhir_base_url


__all__ = ["fhir_endpoint", "hospital_adapter"]

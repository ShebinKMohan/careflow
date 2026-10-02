"""Public guide for the isolated synthetic installation, separate from the original UI.

Only explicitly enabled on a serverless dev installation with internal synthetic FHIR.
The displayed password belongs exclusively to the seeded demo roles; no real account/key,
device secret or database credential is exposed. Local and hospital installations omit it.
"""

import os
from html import escape

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from careflow_api.hosting.fhir import DEMO_FHIR_URL
from careflow_integration.fhir.client import FhirClient, FhirError

router = APIRouter()


@router.get("/demo/fhir/{kind}/{resource_id}")
async def demo_source(kind: str, resource_id: str) -> JSONResponse:
    """Read-only synthetic source links for the original doctor's provenance popovers."""
    if kind not in {
        "Patient",
        "Condition",
        "AllergyIntolerance",
        "MedicationStatement",
        "MedicationRequest",
        "Appointment",
        "Observation",
        "Encounter",
        "Bundle",
        "Composition",
        "Provenance",
        "QuestionnaireResponse",
        "Task",
        "Flag",
    }:
        raise HTTPException(404)
    try:
        async with FhirClient(DEMO_FHIR_URL) as client:
            resource = await client.read(kind, resource_id)
    except FhirError as exc:
        raise HTTPException(404 if exc.status == 404 else 503) from exc
    return JSONResponse(
        resource,
        media_type="application/fhir+json",
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


@router.get("/demo", response_class=HTMLResponse)
async def demo_guide() -> HTMLResponse:
    password = escape(os.environ.get("CAREFLOW_DEV_PASSWORD", ""))
    return HTMLResponse(
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>CareFlow demo access</title><body>"
        "<h1>CareFlow demo access</h1>"
        "<p>Research prototype. Use fictional details only. Not for clinical use.</p>"
        "<p><a href='https://careflow-omega-seven.vercel.app'>Open patient check-in</a></p>"
        "<p>The original product screens and options are available. Scan the welcome QR to "
        "begin on your phone, or use the QR during intake to continue the same visit.</p>"
        "<p>Returning demo patient: patient ID <strong>100001</strong>, birth year "
        "<strong>1968</strong>. New fictional patients can register without an account.</p>"
        "<h2>Explore staff workflows</h2>"
        "<p><a href='https://careflow-staff.vercel.app/login'>Open staff sign-in</a></p>"
        "<ul><li>Nurse: <code>nurse.a@careflow.example</code></li>"
        "<li>Doctor: <code>doctor.a@careflow.example</code></li>"
        "<li>Admin status: <code>admin.a@careflow.example</code></li></ul>"
        f"<p>Shared demo password: <code>{password}</code></p>"
        "<p>These roles act on a shared synthetic hospital. A nurse confirms the final priority "
        "and issues a queue token; the doctor can then open the sourced briefing.</p>"
        "<p>The hosted demo uses Gemini cloud text, embeddings and Live voice. Hospital records "
        "and vitals are synthetic. The optional trained urgency checkpoint is not bundled: "
        "deterministic alerts remain active and the nurse selects the level.</p>"
        "<p>Free-tier quotas apply. Voice requires consent and browser microphone permission; "
        "typing remains available if the speech service is unavailable.</p></body></html>",
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )

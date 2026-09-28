"""Record the model's reply to the spoken answer of e2e/journeys/consent-voice.spec.ts (M2-05).

The journey's answer is spoken: the voice service hears the synthetic recording
services/voice/tests/fixtures/audio/chest_pain.wav, its local Whisper transcribes it as
TRANSCRIPT (docs/evidence/M1-14.md, five of five runs) and it posts that transcript as a voice
turn. The API's extraction reply for it is replayed from e2e/replays like every other journey's.
The local model and the voice models are never loaded together (one local model at a time), so
this script records the reply by making the voice service's own request, with the model up:

    E2E_RECORD=add LOCAL_LLM=1 VOICE=0 \
        CAREFLOW_STACK=careflow-e2e CAREFLOW_PORT_OFFSET=30000 make up
    python3 scripts/stack.py run --stack careflow-e2e -- uv run python e2e/record_voice_turn.py

It checks in the chest-pain persona with its synthetic card, as the journey does, records the
consent and posts the turn. The dev kiosk's secret is read from .env and never printed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

from careflow_api.cli import dev_id

TRANSCRIPT = "I have chest pain since this morning."
CHEST_PAIN_CARD = "784-1968-5433012-1"  # infra/seed/personas.yaml, synthetic
CONSENT_VERSION = "2026-09-27.1"  # apps/kiosk/src/consent/hasConsent.ts
ROOT = Path(__file__).resolve().parents[1]


def dev_secret() -> str:
    value = os.environ.get("CAREFLOW_DEV_DEVICE_SECRET")
    if value:
        return value
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("CAREFLOW_DEV_DEVICE_SECRET="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit("CAREFLOW_DEV_DEVICE_SECRET is not set: run python3 scripts/gen_dev_env.py")


def main() -> int:
    clinic, kiosk = str(dev_id("clinic-a")), str(dev_id("clinic-a", "kiosk"))
    with httpx.Client(base_url=os.environ["CAREFLOW_API_URL"], timeout=180) as api:
        signed_in = api.post(
            "/v1/auth/device-token",
            json={"device_id": kiosk, "device_type": "kiosk", "device_secret": dev_secret()},
        )
        signed_in.raise_for_status()
        device = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}
        tapped = api.post(
            "/v1/checkin/id-tap",
            headers=device,
            json={
                "clinic_id": clinic,
                "kiosk_id": kiosk,
                "mock_card_payload": {
                    "card_type": "mock_national_id",
                    "id_number": CHEST_PAIN_CARD,
                },
                "language": "en",
            },
        )
        tapped.raise_for_status()
        session_id = tapped.json()["session_id"]
        patient = {"Authorization": f"Bearer {tapped.json()['session_token']}"}
        agreed = {
            "camera": True,
            "voice": True,
            "data_use": True,
            "consent_version": CONSENT_VERSION,
        }
        api.post(
            f"/v1/sessions/{session_id}/consent", headers=patient, json=agreed
        ).raise_for_status()
        turn = api.post(
            f"/v1/sessions/{session_id}/turns",
            headers=patient,
            json={"input_mode": "voice", "text": TRANSCRIPT, "language": "en"},
        )
        turn.raise_for_status()
        question = turn.json().get("next_question") or {}
    print(f"recorded the voice turn; the next question asks about {question.get('key')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

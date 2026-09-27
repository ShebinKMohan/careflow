"""The kiosk browser audio channel (M1-14): `WS /kiosk/audio`.

The voice service is the kiosk's ears and mouth. The intake graph behind the CareFlow API stays
the only "brain": final transcripts go to `POST /v1/sessions/{id}/turns` and the next question
comes back to be spoken.
"""

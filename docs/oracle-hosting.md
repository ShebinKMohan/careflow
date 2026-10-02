# Oracle Always Free CPU deployment

Status on 2026-10-02: **not deployed**. The owner authorized Oracle hosting
without a GPU, paid services or an account upgrade. The existing application
screens, options, contracts and workflows must be preserved.

## Provisioning evidence

The logged-in console confirmed India South (Hyderabad), `ap-hyderabad-1`, as
the account's home region. Only AD-1 was available. The root compartment's
compute inventory was empty before setup.

An instance named `careflow-demo-arm` was requested with:

| Setting | Value |
| --- | --- |
| Shape | `VM.Standard.A1.Flex`, labelled Always Free-eligible |
| Compute | 2 OCPUs, 12 GB RAM |
| Image | Canonical Ubuntu 24.04 Minimal aarch64, build 2026.09.18-0 |
| Boot volume | 100 GB, Balanced / 10 VPU |
| Fault domain | Let Oracle choose |
| SSH access | Dedicated locally generated public key; private key stays outside Git |
| Optional agents | Custom Logs Monitoring and Cloud Guard Workload Protection disabled |

Oracle returned:

> Out of capacity for shape VM.Standard.A1.Flex in availability domain AD-1.

A smaller 1-OCPU / 12-GB request was also submitted. Its result page became
unavailable when the original browser tab closed; no success is claimed.
The subsequently loaded compute inventory still showed no instances.
The VCN inventory checked after the first failure was also empty.

No server address or SSH connection was obtained. No CareFlow source, database,
model weights or credentials were uploaded to Oracle. No paid account upgrade,
GPU, paid API or public Vercel redeployment was performed.

Oracle's [Always Free documentation](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
explains that this error means insufficient host capacity. A different
availability domain or a later attempt is recommended. This account has only
one available domain. Compute outside the home region must not be used as an
assumed free workaround.

## Deployment after capacity is available

1. Obtain the 2-OCPU / 12-GB A1 instance within the current free allowance.
   Check for existing instances and volumes before retrying, to avoid duplicate
   resources. Retain the locally generated SSH key. The console's new-network
   wizard left public IPv4 assignment disabled; assign an address after the
   public subnet exists, or select an existing public subnet before launch.
2. Install the locked Python workspace and the repository's original Postgres,
   Valkey and synthetic HAPI FHIR infrastructure. Generate fresh deployment
   secrets on the server; do not reuse or publish browser credentials.
3. Use the explicit ARM Linux CPU runtime entrypoints described in
   [the CPU hosting notes](pi-hosting.md). The current language model is the
   original Qwen3.5-9B family through the pinned Linux GGUF runtime. Speech uses
   Whisper large-v3-turbo, Kokoro English and MMS Arabic through the documented
   CPU adapters. Do not call Apple MLX directly on Linux.
4. Benchmark real extraction and speech on the Oracle machine before choosing
   the public demo configuration. CPU performance on Oracle is **not measured**.
   The missing selected Laya checkpoint is a separate artifact prerequisite;
   provisioning a server does not restore it.
5. Keep live CPU inference and the previously documented
   `CAREFLOW_DEMO_MODE` override explicit. For a live-inference benchmark leave
   the demo flag off. If the owner chooses the synthetic override, retain its
   README disclosure; do not present scripted results as live model inference.
6. Establish stable HTTPS/WSS routes, including the original companion and
   staff applications. Keep database, FHIR and model ports private. Configure
   the Vercel kiosk's existing backend, voice and companion variables without
   redesigning its interface or removing any options.
7. Verify identification/registration, consent, English/Arabic typed and spoken
   intake, vitals simulation, nurse review, queue, doctor briefing, QR phone
   handover and synthetic FHIR write-back through the public URLs. Record actual
   timing and failures. Update CV product links only after these journeys pass.

This document records an attempted infrastructure setup, not a successful
deployment or an end-to-end validation result.

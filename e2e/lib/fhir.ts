/**
 * What the write-back left in the stack's HAPI FHIR server (M1-09), read with Playwright's request
 * API. The session's Encounter carries `urn:careflow:session|<id>`; everything else written for
 * the triage references that Encounter; the doctor's document Bundle carries
 * `urn:careflow:briefing|<id>`.
 */
import { type APIRequestContext, expect, request } from "@playwright/test";

import { STACK } from "./stack";

type Json = Record<string, unknown> & { entry?: { resource: Record<string, unknown> }[]; total?: number };

export const NEWS2_TOTAL = "1104051000000101"; // SNOMED CT, packages/contracts/fhir/mapping.yaml

export class Hapi {
  private constructor(private readonly api: APIRequestContext) {}

  static async open(): Promise<Hapi> {
    // `no-cache`: HAPI reuses a search's results for 60 s, so a search made before the write-back
    // landed would keep answering "none" to a poll (M2-01's Arabic journey searched that early).
    const headers = { Accept: "application/fhir+json", "Cache-Control": "no-cache" };
    return new Hapi(await request.newContext({ baseURL: `${STACK.fhir}/`, extraHTTPHeaders: headers }));
  }

  async close(): Promise<void> {
    await this.api.dispose();
  }

  async search(type: string, params: Record<string, string>): Promise<Record<string, unknown>[]> {
    const response = await this.api.get(type, { params: { ...params, _count: "100" } });
    expect(response.status(), `GET ${type}`).toBe(200);
    const bundle = (await response.json()) as Json;
    return (bundle.entry ?? []).map((entry) => entry.resource);
  }

  /** How many resources of `type` the server holds. */
  async count(type: string): Promise<number> {
    const response = await this.api.get(type, { params: { _summary: "count" } });
    expect(response.status(), `GET ${type}?_summary=count`).toBe(200);
    return Number(((await response.json()) as Json).total ?? Number.NaN);
  }

  /** The session's Encounter, waiting briefly for the write-back that follows the decision. */
  async encounter(sessionId: string): Promise<Record<string, unknown>> {
    let found: Record<string, unknown>[] = [];
    await expect
      .poll(async () => (found = await this.search("Encounter", { identifier: `urn:careflow:session|${sessionId}` })).length, {
        message: "one Encounter for the session",
        timeout: 30_000,
      })
      .toBe(1);
    return found[0]!;
  }

  async forEncounter(type: string, encounterId: string, extra: Record<string, string> = {}): Promise<Record<string, unknown>[]> {
    return this.search(type, { encounter: `Encounter/${encounterId}`, ...extra });
  }

  async documents(sessionId: string): Promise<Record<string, unknown>[]> {
    return this.search("Bundle", { identifier: `urn:careflow:briefing|${sessionId}` });
  }
}

export function codes(resource: Record<string, unknown>, element: "code" | "priority"): string[] {
  const concept = resource[element] as { coding?: { code?: string }[] } | undefined;
  return (concept?.coding ?? []).map((coding) => String(coding.code));
}

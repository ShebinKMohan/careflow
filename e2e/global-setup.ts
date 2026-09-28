/**
 * Before the journeys (M2-12): free every room an earlier run left held. A test stack keeps its
 * patients between runs, and a journey that failed half-way can leave a called or arrived token in a
 * room; the journeys need free rooms to call their own patients in. Clinic A's nurse closes those
 * tokens (seen), as the queue controls would. Nothing else is touched.
 */
import { request } from "@playwright/test";

import { staffTokenFor } from "./lib/actors";
import { STACK } from "./lib/stack";

export default async function freeHeldRooms(): Promise<void> {
  const headers = { Authorization: `Bearer ${await staffTokenFor("nurse")}` };
  const api = await request.newContext({ baseURL: STACK.api });
  try {
    const response = await api.get("/v1/nurse/worklist", { headers });
    if (response.status() !== 200) throw new Error(`the nurse's worklist answered ${response.status()}: is the stack up?`);
    const rows = (await response.json()) as { queue_token?: { token_id: string; status: string } }[];
    for (const token of rows.map((row) => row.queue_token)) {
      if (token && (token.status === "called" || token.status === "arrived")) {
        const closed = await api.post(`/v1/queue/tokens/${encodeURIComponent(token.token_id)}/close`, { headers });
        if (closed.status() !== 200) throw new Error(`could not free the room held by ${token.token_id}: ${closed.status()}`);
      }
    }
  } finally {
    await api.dispose();
  }
}

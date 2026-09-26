import { describe, expect, expectTypeOf, it } from "vitest";

import { createCareflowClient, type Schema } from "../ts/index";

/** A fetch that records each request and answers with a fixed JSON body. */
function recorder(body: unknown, status = 200) {
  const requests: Request[] = [];
  const fetch = async (input: Request) => {
    requests.push(input);
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  };
  return { requests, fetch };
}

describe("createCareflowClient", () => {
  it("sends the bearer token in the Authorization header and types the response", async () => {
    const worklist: Schema<"Worklist"> = [];
    const { requests, fetch } = recorder(worklist);
    const api = createCareflowClient("http://mock.test", () => "token-abc", { fetch });
    const { data, error } = await api.GET("/v1/nurse/worklist");
    expect(error).toBeUndefined();
    expect(data).toEqual([]);
    expect(requests[0]?.url).toBe("http://mock.test/v1/nurse/worklist");
    expect(requests[0]?.headers.get("Authorization")).toBe("Bearer token-abc");
    expectTypeOf(data).not.toBeAny();
    expectTypeOf(data).toEqualTypeOf<Schema<"Worklist"> | undefined>();
  });

  it("sends no Authorization header without a token (login, QR claim)", async () => {
    const { requests, fetch } = recorder({
      session_id: "ses_routine",
      session_token: "example-session-token-routine",
      patient_display: "L. K.",
      language: "en",
    });
    const api = createCareflowClient("http://mock.test", async () => null, { fetch });
    const { data } = await api.POST("/v1/checkin/qr/claim", { body: { qr_token: "example-qr-token-0001" } });
    expect(requests[0]?.headers.has("Authorization")).toBe(false);
    expect(data?.patient_display).toBe("L. K.");
    expectTypeOf(data).toEqualTypeOf<Schema<"CheckinResult"> | undefined>();
  });

  it("types problem responses and path parameters", async () => {
    const { requests, fetch } = recorder({ type: "about:blank", title: "Unauthorized", status: 401 }, 401);
    const api = createCareflowClient("http://mock.test", () => undefined, { fetch });
    const { data, error } = await api.GET("/v1/board/{department}", { params: { path: { department: "ed" } } });
    expect(data).toBeUndefined();
    expect(error).toMatchObject({ status: 401 });
    expect(requests[0]?.url).toBe("http://mock.test/v1/board/ed");
    expectTypeOf(error).not.toBeAny();
  });
});

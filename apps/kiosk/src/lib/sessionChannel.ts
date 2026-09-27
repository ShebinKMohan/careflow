import { API_BASE_URL, patientApi } from "./api";

/** The API's WebSocket base (`http` becomes `ws`). */
export function wsBaseUrl(apiBase: string = API_BASE_URL): string {
  return apiBase.replace(/^http/, "ws").replace(/\/+$/, "");
}

/**
 * The session's live channel (M1-11), for the QR hand-over (M2-06): a one-time ticket from
 * `POST /v1/ws-ticket`, then `/v1/ws/sessions/{id}?ticket=…` (never the session token in a URL).
 * Calls `onFrame` with each frame's type; returns the function that closes the socket.
 */
export async function watchSession(
  token: string,
  sessionId: string,
  onFrame: (type: string) => void,
): Promise<() => void> {
  const { data } = await patientApi(token).POST("/v1/ws-ticket", {
    body: { channel: "session", session_id: sessionId },
  });
  if (!data) return () => undefined;
  const query = new URLSearchParams({ ticket: data.ticket });
  const socket = new WebSocket(`${wsBaseUrl()}/v1/ws/sessions/${encodeURIComponent(sessionId)}?${query.toString()}`);
  socket.onmessage = (event: MessageEvent<unknown>) => {
    try {
      const frame = JSON.parse(String(event.data)) as { type?: unknown };
      if (typeof frame.type === "string") onFrame(frame.type);
    } catch {
      // not a frame: ignore
    }
  };
  return () => {
    socket.onmessage = null;
    socket.close(1000);
  };
}

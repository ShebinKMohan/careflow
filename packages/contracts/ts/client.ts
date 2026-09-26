/**
 * Typed CareFlow API client (openapi-fetch over the generated `paths`). Works against the real
 * API (`http://localhost:8000`) and the Prism mock (`http://localhost:4010`, `make mock`).
 * Tokens travel only in the Authorization header, never in a URL.
 */
import createClient, { type Client, type ClientOptions, type Middleware } from "openapi-fetch";

import type { paths } from "./openapi";

export type CareflowClient = Client<paths>;

/** Returns the current bearer token, or nothing for anonymous calls (login, QR claim). */
export type TokenSource = () => string | null | undefined | Promise<string | null | undefined>;

export function createCareflowClient(
  baseUrl: string,
  getToken: TokenSource,
  options: Omit<ClientOptions, "baseUrl"> = {},
): CareflowClient {
  const client = createClient<paths>({ ...options, baseUrl });
  const bearer: Middleware = {
    async onRequest({ request }) {
      const token = await getToken();
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      return request;
    },
  };
  client.use(bearer);
  return client;
}

/** Typed CareFlow API contract, generated from packages/contracts/openapi.yaml by `make contracts`. */
export type { components, operations, paths, webhooks } from "./openapi";

type Schemas = import("./openapi").components["schemas"];

/** Shorthand for a named schema: `Schema<"CheckinResult">`. */
export type Schema<Name extends keyof Schemas> = Schemas[Name];

import { describe, expect, it } from "vitest";

import { PROTOTYPE_FOOTER } from "../src/index";

describe("PROTOTYPE_FOOTER", () => {
  it("carries the research-prototype disclaimer exactly", () => {
    expect(PROTOTYPE_FOOTER).toBe("Research prototype. Synthetic data only. Not for clinical use.");
  });
});

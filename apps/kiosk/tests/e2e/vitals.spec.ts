import { mkdirSync, readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

// The simulator's hypoxic readings (seed 42), produced by services/api and kept in sync by
// services/api/tests/test_devices_simulator.py::test_kiosk_e2e_fixture_is_the_simulator_output.
const HYPOXIC = JSON.parse(
  readFileSync(new URL("./fixtures/vitals-hypoxic.json", import.meta.url), "utf8"),
) as Record<string, unknown>[];

test("?scenario=hypoxic shows SpO2 of 90 or less within 5 s (mock API)", async ({ page }) => {
  // A signed-in kiosk (the mock accepts any device token).
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      "careflow.kiosk.device",
      JSON.stringify({ token: "example-kiosk-token", clinicId: "clinic_demo", deviceId: "kiosk_01" }),
    );
  });
  // The mock API: the dev route starts the simulator; the session status replays its readings.
  let requested: unknown = null;
  await page.route("**/v1/dev/sessions/*/vitals-sim", async (route) => {
    requested = route.request().postDataJSON();
    await route.fulfill({
      status: 202,
      json: { session_id: "ses_e2e", scenario: "hypoxic", frames: HYPOXIC.length, simulated: true },
    });
  });
  let poll = 0;
  await page.route("**/v1/sessions/*/status", async (route) => {
    const vitals = HYPOXIC[Math.min(poll, HYPOXIC.length - 1)];
    poll += 1;
    await route.fulfill({ json: { state: "intake", intake_revision: 1, vitals } });
  });

  const opened = Date.now();
  await page.goto("/vitals?scenario=hypoxic&session=ses_e2e");
  const spo2 = page.getByRole("region", { name: "Oxygen (SpO2)" }).getByTestId("vital-value");
  await expect(spo2).toHaveText(/^\d+$/, { timeout: 5_000 });
  const value = Number(await spo2.textContent());
  const elapsed = Date.now() - opened;
  console.log(`hypoxic: SpO2 ${value} shown ${elapsed} ms after opening the kiosk`);
  expect(value).toBeLessThanOrEqual(90);
  expect(elapsed).toBeLessThan(5_000);
  expect(requested).toEqual({ scenario: "hypoxic" });
  await expect(page.getByText("Simulated device")).toBeVisible();
  await expect(page.getByText("Pulse oximeter: warns about known skin-tone limits")).toBeVisible();
  await expect(page.getByRole("contentinfo")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const evidence = new URL("../../../../docs/evidence/M1-03/", import.meta.url).pathname;
  mkdirSync(evidence, { recursive: true });
  await page.screenshot({ path: `${evidence}kiosk-vitals-hypoxic.png`, fullPage: true });
});

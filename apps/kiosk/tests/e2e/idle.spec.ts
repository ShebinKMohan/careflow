import { readFileSync } from "node:fs";

import { expect, type Page, test } from "@playwright/test";

const CHECKIN = JSON.parse(readFileSync(new URL("../fixtures/api/checkin.json", import.meta.url), "utf8")) as Record<string, unknown>;
const DEVICE = { token: "example-kiosk-token", clinicId: "clinic_demo", deviceId: "kiosk_01" };

async function checkIn(page: Page) {
  await page.addInitScript((device) => {
    window.sessionStorage.setItem("careflow.kiosk.device", JSON.stringify(device));
  }, DEVICE);
  await page.route("**/v1/checkin/keypad", (route) => route.fulfill({ json: CHECKIN }));
  await page.goto("/");
  await page.getByRole("main").getByRole("button", { name: "English" }).click();
  await expect(page).toHaveURL(/\/consent$/);
  await page.getByRole("button", { name: "Agree", exact: true }).click();
  await expect(page).toHaveURL(/\/checkin$/);
  for (const digit of "100001") await page.getByRole("button", { name: digit, exact: true }).click();
  await page.getByRole("button", { name: "Check in" }).click();
  await expect(page).toHaveURL(/\/patient$/);
  await page.getByRole("button", { name: "Me", exact: true }).click();
  await expect(page).toHaveURL(/\/intake$/);
  await expect(page.getByText("Welcome, M. S.")).toBeVisible();
}

test("80 s without input wipes the session and returns to the start", async ({ page }) => {
  await page.clock.install();
  await checkIn(page);
  await page.clock.fastForward("01:20");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();

  // Back to /intake without a reload: the patient is gone from memory, not just off screen.
  await page.evaluate(() => Object.assign(window, { sameDocument: true }));
  await page.goBack();
  await expect(page).toHaveURL(/\/intake$/);
  await expect(page.getByText("Please check in first.")).toBeVisible();
  expect(await page.evaluate(() => "sameDocument" in window)).toBe(true);

  await page.goForward();
  await page.getByRole("main").getByRole("button", { name: "English" }).click();
  await page.getByRole("button", { name: "Agree", exact: true }).click();
  await expect(page.getByLabel("Keypad ID")).toHaveValue("");
  const stored = await page.evaluate(() => ({
    session: Object.entries(window.sessionStorage),
    local: window.localStorage.length,
  }));
  // Only the kiosk's own sign-in remains; nothing about the patient.
  expect(stored.session.map(([key]) => key)).toEqual(["careflow.kiosk.device"]);
  expect(JSON.stringify(stored.session)).not.toMatch(/ses_e2e|example-session-token-e2e|M\. S\./);
  expect(stored.local).toBe(0);
});

test("after 60 s the kiosk asks, and \"I'm still here\" keeps the session", async ({ page }) => {
  await page.clock.install();
  await checkIn(page);
  await page.clock.fastForward("01:01");
  const dialog = page.getByRole("alertdialog", { name: "Are you still there?" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(/Starting over in \d+ seconds\./);
  await dialog.getByRole("button", { name: "I'm still here" }).click();
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/\/intake$/);
  await expect(page.getByText("Welcome, M. S.")).toBeVisible();
});

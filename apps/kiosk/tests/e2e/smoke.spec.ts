import { mkdirSync } from "node:fs";

import { expect, type Page, test } from "@playwright/test";

const FOOTER_EN = "Research prototype. Synthetic data only. Not for clinical use.";
const FOOTER_AR = "نموذج بحثي. بيانات اصطناعية فقط. ليس للاستخدام السريري.";
const EVIDENCE_DIR = new URL("../../../../docs/evidence/M0-08/", import.meta.url).pathname;

async function signInKiosk(page: Page) {
  await page.goto("/setup");
  await page.getByLabel("Device ID").fill("kiosk_01");
  await page.getByLabel("Device secret").fill("example-device-secret-0001");
  await page.getByRole("button", { name: "Sign in kiosk" }).click();
  await expect(page).toHaveURL(/\/checkin$/);
}

test("the footer is visible on every page", async ({ page }) => {
  for (const path of ["/", "/setup", "/checkin", "/intake"]) {
    await page.goto(path);
    await expect(page.getByRole("contentinfo"), path).toHaveText(FOOTER_EN);
    await expect(page.getByRole("contentinfo"), path).toBeVisible();
  }
});

test("keypad 100001 shows the mocked session within 1 s", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("main").getByRole("button", { name: "English" }).click();
  await expect(page).toHaveURL(/\/checkin$/);
  await signInKiosk(page);
  for (const digit of "100001") await page.getByRole("button", { name: digit, exact: true }).click();
  const started = Date.now();
  await page.getByRole("button", { name: "Check in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, M. S." })).toBeVisible({ timeout: 1_000 });
  const elapsed = Date.now() - started;
  test.info().annotations.push({ type: "keypad-to-session-ms", description: String(elapsed) });
  console.log(`keypad submit -> mocked session shown in ${elapsed} ms`);
  expect(elapsed).toBeLessThan(1_000);
  await expect(page.getByText("Session ses_chest_pain")).toBeVisible();
});

test("choosing Arabic switches the page to right-to-left", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("main").getByRole("button", { name: "العربية" }).click();
  await expect(page).toHaveURL(/\/checkin$/);
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.locator("html")).toHaveAttribute("lang", "ar");
  await expect(page.getByRole("contentinfo")).toHaveText(FOOTER_AR);
  await page.evaluate(() => document.fonts.ready); // stable screenshots: web fonts loaded
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  await page.screenshot({ path: `${EVIDENCE_DIR}kiosk-checkin-ar.png`, fullPage: true });
});

import { mkdirSync, readFileSync } from "node:fs";

import { expect, type Locator, type Page, test } from "@playwright/test";

// The API is stubbed per route with these fixtures: Prism serves one static example per route
// and cannot drive a multi-step intake.
const fixture = <T,>(name: string): T =>
  JSON.parse(readFileSync(new URL(`../fixtures/api/${name}.json`, import.meta.url), "utf8")) as T;

const DEVICE = { token: "example-kiosk-token", clinicId: "clinic_demo", deviceId: "kiosk_01" };
const EVIDENCE_DIR = new URL("../../../../docs/evidence/M1-13/", import.meta.url).pathname;
const DIGITS = "100001"; // the chest-pain persona's keypad id (M0-05 seed)

/** The smallest side of any button in `scope`: touch targets must be at least 56 px each way. */
async function smallestButtonSide(scope: Locator): Promise<number> {
  const sides = await scope.getByRole("button").evaluateAll((buttons) =>
    buttons.map((button) => {
      const box = button.getBoundingClientRect();
      return Math.min(box.width, box.height);
    }),
  );
  return Math.round(Math.min(...sides) * 10) / 10;
}

async function signedInKiosk(page: Page) {
  await page.addInitScript((device) => {
    window.sessionStorage.setItem("careflow.kiosk.device", JSON.stringify(device));
  }, DEVICE);
}

test("keypad, three answers (tap, typed, severity), vitals, the token and room, then a fresh start", async ({ page }) => {
  await page.clock.install();
  await signedInKiosk(page);
  const urls: string[] = [];
  page.on("request", (request) => urls.push(request.url()));

  let checkinBody: unknown = null;
  await page.route("**/v1/checkin/keypad", async (route) => {
    checkinBody = route.request().postDataJSON();
    await route.fulfill({ json: fixture("checkin") });
  });
  const turns = fixture<unknown[]>("turns");
  const sent: { body: unknown; auth: string | undefined }[] = [];
  await page.route("**/v1/sessions/*/turns", async (route) => {
    sent.push({ body: route.request().postDataJSON(), auth: route.request().headers().authorization });
    await route.fulfill({ json: turns[sent.length - 1] });
  });
  await page.route("**/v1/dev/sessions/*/vitals-sim", (route) =>
    route.fulfill({ status: 202, json: { session_id: "ses_e2e", scenario: "normal", frames: 1, simulated: true } }),
  );
  const done = fixture<unknown[]>("done-status");
  let donePolls = 0;
  await page.route("**/v1/sessions/*/status", async (route) => {
    const patient = route.request().headers().authorization === "Bearer example-session-token-e2e";
    if (!patient) return route.fulfill({ json: fixture("vitals-status") }); // the vitals panel (kiosk token)
    const body = done[Math.min(donePolls, done.length - 1)];
    donePolls += 1;
    return route.fulfill({ json: body });
  });

  await page.goto("/");
  await page.getByRole("main").getByRole("button", { name: "Start on this kiosk", exact: true }).click();
  await expect(page).toHaveURL(/\/consent$/);
  await page.getByRole("button", { name: "Agree", exact: true }).click();
  await expect(page).toHaveURL(/\/checkin$/);
  const targets: Record<string, number> = { keypad: await smallestButtonSide(page.getByRole("main")) };
  for (const digit of DIGITS) await page.getByRole("button", { name: digit, exact: true }).click();
  await expect(page.getByLabel("Keypad ID")).toHaveValue("••••••");
  await page.getByRole("button", { name: "Check in" }).click();

  await expect(page).toHaveURL(/\/patient$/);
  await page.getByRole("button", { name: "Me", exact: true }).click();
  await expect(page).toHaveURL(/\/intake$/);
  await expect(page.getByText("Welcome, M. S.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "What is the main reason for your visit today?" })).toBeVisible();
  targets.complaints = await smallestButtonSide(page.getByRole("main"));
  await page.getByRole("button", { name: "Chest pain" }).click();  // 1: a tap

  await expect(page.getByRole("heading", { name: "When did this start?" })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("Thank you. A nurse will be with you shortly. Please stay here.");
  await page.getByLabel("Or type your answer").fill("About an hour ago");
  await page.getByRole("button", { name: "Send" }).click();  // 2: typed text

  await expect(page.getByRole("heading", { name: /how bad is your pain/ })).toBeVisible();
  targets.severity = await smallestButtonSide(page.getByRole("main"));
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${EVIDENCE_DIR}kiosk-intake-severity.png`, fullPage: true });
  await page.getByRole("group", { name: "Tap a number from 0 to 10" }).getByRole("button", { name: "8", exact: true }).click();  // 3: severity

  await expect(page).toHaveURL(/\/vitals$/);
  await page.getByRole("button", { name: "Measure vitals" }).click();
  await expect(page.getByRole("region", { name: "Pulse" }).getByTestId("vital-value")).toHaveText("96");
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(page).toHaveURL(/\/done$/);
  await expect(page.getByText("Please take a seat. A nurse will review your details.")).toBeVisible();
  await expect(page.getByTestId("token-code")).toHaveText("A-014", { timeout: 10_000 });
  await expect(page.getByTestId("room")).toHaveText("Please go to Resus 2.");
  await page.screenshot({ path: `${EVIDENCE_DIR}kiosk-done-token.png`, fullPage: true });

  expect(sent.map((s) => s.body)).toEqual([
    { input_mode: "tap", text: "Chest pain", language: "en" },
    { input_mode: "text", text: "About an hour ago", language: "en" },
    { input_mode: "tap", text: "8", language: "en" },
  ]);
  expect(sent.every((s) => s.auth === "Bearer example-session-token-e2e")).toBe(true);
  // The keypad digits travel only in the POST body, never in a URL.
  // The session keeps the language picked on the first screen (M2-01).
  expect(checkinBody).toEqual({ clinic_id: "clinic_demo", kiosk_id: "kiosk_01", digits: DIGITS, language: "en" });
  expect(urls.filter((url) => url.includes(DIGITS))).toEqual([]);
  // Nothing about the patient is stored in the browser.
  const stored = await page.evaluate(() => ({
    session: Object.keys(window.sessionStorage),
    local: window.localStorage.length,
    cookies: document.cookie,
  }));
  expect(stored.session).toEqual(["careflow.kiosk.device"]);
  expect(stored.local).toBe(0);
  expect(stored.cookies).not.toContain("ses_e2e");

  console.log(`smallest button side in px: ${JSON.stringify(targets)}`);
  for (const [screen, side] of Object.entries(targets)) expect(side, screen).toBeGreaterThanOrEqual(56);

  // 30 s after the token is shown, the kiosk starts over for the next patient.
  await page.clock.fastForward("00:31");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();
});

test("three failed keypad attempts send the patient to reception", async ({ page }) => {
  await signedInKiosk(page);
  await page.route("**/v1/checkin/keypad", (route) =>
    route.fulfill({ status: 404, contentType: "application/problem+json", json: { title: "Not found", status: 404 } }),
  );
  await page.goto("/");
  await page.getByRole("main").getByRole("button", { name: "Start on this kiosk", exact: true }).click();
  await expect(page).toHaveURL(/\/consent$/);
  await page.getByRole("button", { name: "Agree", exact: true }).click();
  await expect(page).toHaveURL(/\/checkin$/);
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    for (const digit of "999999") await page.getByRole("button", { name: digit, exact: true }).click();
    await page.getByRole("button", { name: "Check in" }).click();
    const message = attempt < 3 ? "We couldn't find a match. Please try again or see reception." : "Please see reception.";
    await expect(page.getByRole("main").getByRole("alert")).toHaveText(message);
    await expect(page.getByLabel("Keypad ID")).toHaveValue("");
  }
  await expect(page.getByRole("button", { name: "1", exact: true })).toBeDisabled();
});

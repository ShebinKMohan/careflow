import { expect, test } from "@playwright/test";

import { kioskPage, RequestLog } from "../lib/actors";
import { chooseConsent, consentPosted, forMe, keypadCheckIn, t } from "../lib/consent";

// Check-in path 1 (M2-12): the silent keypad, on the real stack. A wrong code gets the same
// generic words whatever it is; the persona's code finds the persona in HAPI and opens its intake.
// The digits travel only in the keypad's POST body, never in a URL. One wrong code only: the
// kiosk's guessing limit (5 in 5 minutes) is shared with every journey on the stack.
test("check-in by keypad: a wrong code is told generically, the persona's code checks in, digits never in a URL", async ({ browser }) => {
  const kiosk = await kioskPage(browser, "normal");
  const log = new RequestLog();
  log.watch(kiosk.context());
  await chooseConsent(kiosk, "agree");
  await expect(kiosk).toHaveURL(/\/checkin$/);

  for (const digit of "999990") await kiosk.getByRole("button", { name: digit, exact: true }).click();
  const refused = kiosk.waitForResponse((response) => new URL(response.url()).pathname === "/v1/checkin/keypad");
  await kiosk.getByRole("button", { name: t("checkin.submit"), exact: true }).click();
  expect((await refused).status(), "an unknown code is a generic 404").toBe(404);
  await expect(kiosk.getByRole("alert").filter({ hasText: t("checkin.failed") })).toBeVisible();

  const saved = consentPosted(kiosk);
  const started = await keypadCheckIn(kiosk, "100001");
  expect((await saved).status()).toBe(200);
  await forMe(kiosk);
  const display = started.patient_display ?? "";
  expect(display, "the persona is greeted by initials only").toMatch(/^[A-Z]\. [A-Z]\.$/);
  await expect(kiosk.getByText(display)).toBeVisible();

  for (const code of ["999990", "100001"]) {
    expect(log.requests.filter((sent) => sent.url.includes(code)), `${code} is never in a URL`).toEqual([]);
    const carried = log.carrying(code).map((sent) => `${sent.method} ${new URL(sent.url).pathname}`);
    expect(new Set(carried), `${code} only in the keypad's POST body`).toEqual(new Set(["POST /v1/checkin/keypad"]));
  }
});

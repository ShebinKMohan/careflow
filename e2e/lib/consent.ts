/**
 * The consent screen and the check-in paths as a patient uses them (M2-05), and what the journeys
 * check afterwards: every `getUserMedia` call the kiosk makes, and the consent the API recorded.
 */
import { expect, type Page, request, type Response } from "@playwright/test";

import { msg } from "./i18n";
import { STACK } from "./stack";

export const t = (key: string) => msg("kiosk", "en", key);

export type ConsentChoice = "agree" | "withoutCameraVoice" | "decline";

/** The CheckinResult of a check-in (the kiosk keeps the token in memory only). */
export interface Started {
  session_id: string;
  session_token: string;
  /** The patient's initials, as the kiosk greets them. */
  patient_display?: string;
}

/** Counts every `navigator.mediaDevices.getUserMedia` call on the page, across reloads, then lets
 * the call through: the camera and the microphone both open only through it. */
export async function countMediaRequests(page: Page): Promise<() => number> {
  let calls = 0;
  await page.exposeFunction("careflowMediaRequested", () => {
    calls += 1;
  });
  await page.addInitScript(() => {
    const devices = navigator.mediaDevices;
    if (!devices?.getUserMedia) return;
    const original = devices.getUserMedia.bind(devices);
    devices.getUserMedia = async (constraints?: MediaStreamConstraints) => {
      await (window as unknown as { careflowMediaRequested(): Promise<void> }).careflowMediaRequested();
      return original(constraints);
    };
  });
  return () => calls;
}

/** The first screen in English, then the consent screen's `choice`. */
export async function chooseConsent(page: Page, choice: ConsentChoice): Promise<void> {
  await page.goto("/");
  await page.getByRole("main").getByRole("button", { name: t("experience.kioskAction") }).click();
  await expect(page).toHaveURL(/\/consent$/);
  await page.getByRole("button", { name: t(`safety.consent.${choice}`), exact: true }).click();
}

const isPost = (response: Response, path: RegExp) =>
  path.test(new URL(response.url()).pathname) && response.request().method() === "POST";

/** The kiosk's next consent POST (`POST /v1/sessions/{id}/consent`). */
export const consentPosted = (page: Page) => page.waitForResponse((response) => isPost(response, /^\/v1\/sessions\/[^/]+\/consent$/));

/** Check in with the keypad; returns the CheckinResult. */
export async function keypadCheckIn(page: Page, digits: string): Promise<Started> {
  for (const digit of digits) await page.getByRole("button", { name: digit, exact: true }).click();
  const answer = page.waitForResponse((response) => isPost(response, /^\/v1\/checkin\/keypad$/));
  await page.getByRole("button", { name: t("checkin.submit"), exact: true }).click();
  const checkin = await answer;
  expect(checkin.status(), "the persona is found in the stack's HAPI").toBe(200);
  return (await checkin.json()) as Started;
}

/** Tap persona `persona`'s synthetic card on the simulated reader; returns the CheckinResult. */
export async function tapCard(page: Page, persona: string): Promise<Started> {
  const reader = page.getByRole("region", { name: t("checkin.simulatedReader") });
  await expect(reader, "the reader is labelled as a simulation").toContainText(t("checkin.simulatedReaderNote"));
  const answer = page.waitForResponse((response) => isPost(response, /^\/v1\/checkin\/id-tap$/));
  await reader.getByRole("button", { name: new RegExp(`\\(${persona}\\)`) }).click();
  const tapped = await answer;
  expect(tapped.status(), "the card's persona is found in the stack's HAPI").toBe(200);
  return (await tapped.json()) as Started;
}

/** "Who is the patient?" answered "Me": the intake opens. */
export async function forMe(page: Page): Promise<void> {
  await expect(page).toHaveURL(/\/patient$/);
  await page.getByRole("button", { name: t("safety.child.me"), exact: true }).click();
  await expect(page).toHaveURL(/\/intake$/);
}

/** The consent the API holds for the session: its status and, when 200, the record. */
export async function recordedConsent(started: Started): Promise<{ status: number; record: Record<string, unknown> | null }> {
  const api = await request.newContext({ baseURL: STACK.api });
  try {
    const response = await api.get(`/v1/sessions/${started.session_id}/consent`, {
      headers: { Authorization: `Bearer ${started.session_token}` },
    });
    return { status: response.status(), record: response.status() === 200 ? await response.json() : null };
  } finally {
    await api.dispose();
  }
}

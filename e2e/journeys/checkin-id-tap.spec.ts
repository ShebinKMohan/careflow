import { expect, test } from "@playwright/test";

import { kioskPage, RequestLog } from "../lib/actors";
import { chooseConsent, consentPosted, forMe, t, tapCard } from "../lib/consent";
import { STACK } from "../lib/stack";

// Check-in path 2 (M2-12): the ID tap, on the real stack. The reader is the "Simulated ID reader"
// panel (M2-05), labelled as a mock, with the personas' synthetic cards: never a real ID service.
// The card finds its persona in HAPI through the FHIR adapter (POST _search, the number in the body
// only). The number travels only in the tap's POST body, never in a URL or an answer.
const STROKE_CARD = "784-1955-2530829-2"; // infra/seed/personas.yaml, synthetic

test("check-in by the simulated ID reader: labelled a mock, the card finds its persona, the number never in a URL or an answer", async ({ browser }) => {
  const kiosk = await kioskPage(browser, "normal");
  const log = new RequestLog();
  log.watch(kiosk.context());
  const answers: string[] = [];
  kiosk.on("response", async (response) => {
    if (response.url().startsWith(STACK.api)) answers.push(await response.text().catch(() => ""));
  });
  await chooseConsent(kiosk, "agree");
  await expect(kiosk).toHaveURL(/\/checkin$/);
  const reader = kiosk.getByRole("region", { name: t("checkin.simulatedReader") });
  await expect(reader).toContainText(t("checkin.simulatedReaderNote"));
  await expect(reader, "the panel shows the numbers masked").not.toContainText(STROKE_CARD);

  const saved = consentPosted(kiosk);
  const started = await tapCard(kiosk, "stroke");
  expect((await saved).status()).toBe(200);
  await forMe(kiosk);
  const display = started.patient_display ?? "";
  expect(display).toMatch(/^[A-Z]\. [A-Z]\.$/);
  await expect(kiosk.getByText(display)).toBeVisible();

  expect(log.requests.filter((sent) => sent.url.includes(STROKE_CARD)), "never in a URL").toEqual([]);
  const carried = log.carrying(STROKE_CARD).map((sent) => `${sent.method} ${new URL(sent.url).pathname}`);
  expect(carried, "only in the tap's POST body").toEqual(["POST /v1/checkin/id-tap"]);
  expect(answers.filter((body) => body.includes(STROKE_CARD)), "never in an answer").toEqual([]);
});

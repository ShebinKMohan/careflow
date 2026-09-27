import { mkdirSync, readFileSync } from "node:fs";

import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

// The doctor briefing, the public board and the admin status (M1-17) against stubbed HTTP
// (page.route) with the synthetic, contract-derived fixtures in tests/fixtures/api/. Mock-backed:
// the same screens on the real API and a persisted briefing are the journeys' job (M1-19, M2-12).
const fixture = <T,>(name: string): T =>
  JSON.parse(readFileSync(new URL(`../fixtures/api/${name}.json`, import.meta.url), "utf8")) as T;

const EVIDENCE_DIR = new URL("../../../../docs/evidence/M1-17/", import.meta.url).pathname;
const FHIR = (process.env.NEXT_PUBLIC_FHIR_BASE_URL ?? "http://localhost:8080/fhir").replace(/\/+$/, "");
const LABELS = ["EHR", "Patient said", "Device", "Nurse", "Model inferred"];
const LEAKED = ["Mariam", "Saleh", "Omar", "Haddad", "Layla", "Karim", "crushing chest pain"];

interface Source {
  kind: string;
  quote?: string;
  resource_type?: string;
  id?: string;
}
interface Briefing {
  sections: { items: { provenance: string; source: Source; source_missing?: boolean }[] }[];
}

async function signedIn(page: Page, token: string) {
  await page.addInitScript((value) => window.sessionStorage.setItem("careflow.staff.token", value), token);
}

async function axe(page: Page, name: string) {
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`), `${name}: serious or critical`).toEqual([]);
}

async function evidence(page: Page, name: string) {
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  await page.screenshot({ path: `${EVIDENCE_DIR}${name}.png`, fullPage: true });
}

test("the briefing shows all five labelled sources, opens them by keyboard, and passes axe", async ({ page }) => {
  const briefing = fixture<Briefing>("briefing-chest-pain");
  await signedIn(page, "example-doctor-token");
  await page.route("**/v1/doctor/sessions/*/briefing", (route) => route.fulfill({ json: briefing }));
  await page.goto("/doctor/ses_chest_pain");
  const view = page.getByTestId("briefing");
  await expect(view).toBeVisible();
  for (const label of LABELS) await expect(view.getByText(label, { exact: true }).first()).toBeVisible();
  const items = briefing.sections.flatMap((section) => section.items);
  await expect(view.locator("[data-provenance]")).toHaveCount(items.length); // no line without its chip
  await expect(view.getByText("Source missing")).toBeVisible();

  // The patient's words: Tab to the first "Patient said" chip, Enter opens, Escape closes.
  const patient = items.find((item) => item.provenance === "PATIENT_STATED" && !item.source_missing)!;
  const chip = view.getByRole("button", { name: /Patient said/ }).first();
  await chip.focus();
  await page.keyboard.press("Enter");
  await expect(chip).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("dialog", { name: "Source" }).locator("mark")).toHaveText(patient.source.quote!);
  await evidence(page, "briefing-patient-source");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(chip).toBeFocused();

  // The EHR source links to the resource on the FHIR server.
  const ehr = items.find((item) => item.provenance === "EHR" && !item.source_missing)!;
  await view.getByRole("button", { name: /^EHR/ }).first().click();
  await expect(page.getByRole("link", { name: "Open in the EHR" })).toHaveAttribute(
    "href",
    `${FHIR}/${ehr.source.resource_type}/${ehr.source.id}`,
  );
  await page.keyboard.press("Escape");
  await axe(page, "briefing");
});

test("a dropped summary says so and the facts stay", async ({ page }) => {
  await signedIn(page, "example-doctor-token");
  await page.route("**/v1/doctor/sessions/*/briefing", (route) => route.fulfill({ json: fixture("briefing-dropped") }));
  await page.goto("/doctor/ses_chest_pain");
  await expect(page.getByText("Summary unavailable. Showing verified facts only.")).toBeVisible();
  await expect(page.getByTestId("briefing").locator("[data-provenance]").first()).toBeVisible();
});

test("the public board shows numbers and rooms only, flags a lost connection, and passes axe", async ({ page }) => {
  let failing = false;
  await signedIn(page, "example-board-token");
  await page.route("**/v1/board/*", (route) =>
    failing ? route.abort("connectionrefused") : route.fulfill({ json: fixture("board-with-leaked-fields") }),
  );
  await page.goto("/board?department=ed");
  const board = page.getByTestId("board");
  await expect(board.getByText("A-014")).toBeVisible();
  await expect(board.getByText("Resus 2")).toBeVisible();
  await expect(board.getByText("2 people waiting")).toBeVisible();
  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const leaked of LEAKED) expect(text, "a public board must not show patient details").not.toContain(leaked.toLowerCase());
  expect(text).not.toMatch(/\blevel\b|ktas|urgency/);
  await axe(page, "board");
  await evidence(page, "board");

  failing = true;
  await expect(page.getByTestId("board-stale")).toContainText("Connection lost. This board may be out of date", { timeout: 10_000 });
  await expect(board.getByText("A-014")).toBeVisible();
  await evidence(page, "board-connection-lost");
  failing = false;
  await expect(page.getByTestId("board-stale")).toHaveCount(0, { timeout: 10_000 });
});

test("the admin status lists every AI task, marks stubs, names a fixture model and passes axe", async ({ page }) => {
  const providers = fixture<{ tasks: { task: string }[] }>("admin-providers");
  await signedIn(page, "example-admin-token");
  await page.route("**/v1/admin/status", (route) => route.fulfill({ json: fixture("admin-status") }));
  await page.route("**/v1/admin/providers", (route) => route.fulfill({ json: providers }));
  await page.goto("/admin/status");
  for (const { task } of providers.tasks) await expect(page.locator(`[data-task="${task}"]`)).toBeVisible();
  const urgencyPort = page.locator('[data-row="port:urgency"]');
  await expect(urgencyPort.getByText("Stub")).toBeVisible();
  await expect(page.getByText(/NOT A RESULT/)).toBeVisible();
  await expect(page.locator('[data-row="hapi-fhir"]').getByText("Down")).toBeVisible();
  await expect(page.locator('[data-row="fhir-writeback"]')).toContainText("2 failed write-backs in this clinic");
  await expect(page.locator('[data-row="speech:stt"]')).toContainText("voice service not answering");
  await axe(page, "admin");
  await evidence(page, "admin-status");
});

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { nurseDecides, staffPage, staffTokenFor } from "../lib/actors";
import { msg } from "../lib/i18n";
import { STACK } from "../lib/stack";

const evidence = resolve(import.meta.dirname, "../../docs/evidence/ADAPTIVE-20260929/live");
const t = (key: string) => msg("kiosk", "en", key);
type Question = { key: string | null; text: string; question_id: string };
type Registration = { session_id: string; session_token: string; first_question?: Question };
type Scenario = {
  id: string;
  visitFor: "self" | "child" | "elder";
  returning: boolean;
  patientName: string;
  age: string;
  sex: "Male" | "Female";
  complaint: string;
  yes: string[];
  pain: string;
  location: string;
  relevant: RegExp;
};
const scenarios: Scenario[] = [
  { id: "new-adult", visitFor: "self", returning: false, patientName: "Alex Demo Adaptive", age: "29", sex: "Male", complaint: "I twisted my left ankle playing football yesterday afternoon. It hurts when I walk and the pain is four out of ten.", yes: ["injury", "pain_present"], pain: "4", location: "My left ankle", relevant: /pain|injury|onset|severity/ },
  { id: "returning-adult", visitFor: "self", returning: true, patientName: "", age: "", sex: "Female", complaint: "I have had a tight pain in the middle of my chest since seven this morning. I feel sweaty and the pain is seven out of ten.", yes: ["pain_present", "chest_pain.present", "chest_pain.sweating"], pain: "7", location: "The middle of my chest", relevant: /chest_pain|pain|onset/ },
  { id: "caregiver-child", visitFor: "child", returning: true, patientName: "Evan Demo Child", age: "7", sex: "Male", complaint: "My child has had a fever since yesterday evening and is drinking less than usual. He wakes up normally when I talk to him.", yes: ["infection.fever_reported", "child_amber.poor_feeding"], pain: "2", location: "No specific pain", relevant: /child_|infection|onset/ },
  { id: "caregiver-elder", visitFor: "elder", returning: false, patientName: "Mira Demo Elder", age: "72", sex: "Female", complaint: "My mother has had a fever and shaking chills since yesterday evening. She feels weak, but she is awake and speaking normally.", yes: ["infection.fever_reported", "infection.rigors"], pain: "2", location: "No specific pain", relevant: /infection|onset|severity/ },
];
const results: Record<string, unknown> = {};
function save() {
  mkdirSync(evidence, { recursive: true });
  writeFileSync(`${evidence}/stage-results.json`, JSON.stringify(results, null, 2));
  // Keep individual cases if Playwright starts a new worker after a failed test.
  for (const [id, stage] of Object.entries(results)) writeFileSync(`${evidence}/${id}.json`, JSON.stringify(stage, null, 2));
}
function factIds(value: unknown): Set<string> {
  const ids = new Set<string>();
  function visit(item: unknown) {
    if (!item || typeof item !== "object") return;
    if ("fact_id" in item && typeof item.fact_id === "string") ids.add(item.fact_id);
    for (const child of Object.values(item)) visit(child);
  }
  visit(value);
  return ids;
}
function sourcedPaths(value: unknown, prefix = ""): string[] {
  if (!value || typeof value !== "object") return [];
  if ("fact_id" in value) return [prefix];
  return Object.entries(value).flatMap(([key, child]) => sourcedPaths(child, prefix ? `${prefix}.${key}` : key));
}
async function shot(page: Page, name: string) {
  mkdirSync(`${evidence}/screenshots`, { recursive: true });
  await page.screenshot({ path: `${evidence}/screenshots/${name}.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${name}: no page overflow`).toBe(true);
}
async function apiJson(page: Page, path: string, token: string) {
  const response = await page.request.get(`${STACK.api}${path}`, { headers: { Authorization: `Bearer ${token}` }, timeout: 30_000 });
  expect(response.status(), path).toBe(200);
  return response.json();
}
async function register(page: Page, scenario: Scenario, stage: Record<string, unknown>): Promise<Registration> {
  await page.goto("/");
  await expect(page.getByRole("button", { name: t("experience.kioskAction"), exact: true })).toBeEnabled();
  await shot(page, `${scenario.id}-welcome`);
  await page.getByRole("button", { name: t("experience.kioskAction"), exact: true }).click();
  await expect(page.getByRole("heading", { name: t("reception.identityTitles.identity"), exact: true })).toBeVisible();
  await shot(page, `${scenario.id}-identity`);
  if (scenario.returning) {
    await page.getByRole("button", { name: t("reception.identityKinds.patient_id"), exact: true }).click();
    await page.getByLabel(t("reception.identityKinds.patient_id"), { exact: true }).fill("100005");
    await page.getByRole("checkbox", { name: t("reception.lookupConsent"), exact: true }).check();
    const began = Date.now();
    const lookup = page.waitForResponse(r => r.url().endsWith("/v1/identity/lookup") && r.request().method() === "POST");
    await page.getByRole("button", { name: t("reception.findDetails"), exact: true }).click();
    expect((await lookup).status()).toBe(200);
    stage.lookupMs = Date.now() - began;
    await page.getByLabel(t("reception.birthYear"), { exact: true }).fill("1992");
    const verify = page.waitForResponse(r => r.url().endsWith("/v1/identity/verify") && r.request().method() === "POST");
    await page.getByRole("button", { name: t("reception.verifyIdentity"), exact: true }).click();
    const verified = await verify;
    expect(verified.status()).toBe(200);
    const profile = await verified.json();
    stage.verifiedName = profile.patient.name;
    stage.verifiedHistory = profile.history;
  } else await page.getByRole("button", { name: t("reception.newPatient"), exact: true }).click();
  await page.getByRole("button", { name: t(`reception.visitors.${scenario.visitFor}.title`), exact: true }).click();
  if (!scenario.returning || scenario.visitFor !== "self") {
    await page.getByLabel(t(scenario.visitFor === "self" ? "reception.name" : "reception.patientName"), { exact: true }).fill(scenario.patientName);
    await page.getByLabel(t("reception.age"), { exact: true }).fill(scenario.age);
    await page.getByRole("button", { name: scenario.sex, exact: true }).click();
  }
  if (scenario.visitFor !== "self") {
    const helper = page.getByLabel(t("reception.helperName"), { exact: true });
    if (!scenario.returning) await helper.fill("Taylor Demo Helper");
    else expect(await helper.inputValue()).toBe(stage.verifiedName);
  }
  await shot(page, `${scenario.id}-patient-details`);
  await page.getByRole("button", { name: t("reception.continue"), exact: true }).click();
  await expect(page).toHaveURL(/\/consent$/);
  await page.getByRole("button", { name: t("safety.consent.withoutCameraVoice"), exact: true }).click();
  await expect(page.getByRole("button", { name: t("reception.startConversation"), exact: true })).toBeEnabled();
  await shot(page, `${scenario.id}-ready`);
  const started = page.waitForResponse(r => r.url().endsWith("/v1/checkin/adaptive") && r.request().method() === "POST");
  const began = Date.now();
  await page.getByRole("button", { name: t("reception.startConversation"), exact: true }).click();
  const response = await started;
  stage.registrationMs = Date.now() - began;
  expect(response.status()).toBe(200);
  await expect(page).toHaveURL(/\/intake$/);
  return response.json() as Promise<Registration>;
}
async function intake(page: Page, registration: Registration, scenario: Scenario, stage: Record<string, unknown>) {
  let question: Question | null = registration.first_question ?? { key: "chief_complaint", text: t("reception.adaptiveFirstQuestion"), question_id: "initial" };
  const turns: { key: string | null; question: string; mode: string; answer: string; latencyMs: number }[] = [];
  stage.turns = turns;
  await shot(page, `${scenario.id}-ai-conversation`);
  // The compulsory paediatric danger bank may exceed the ordinary adult intake cap.
  const limit = scenario.visitFor === "child" ? 40 : 12;
  for (let i = 0; question && i < limit; i++) {
    const key: string = question.key ?? "";
    let answer = "No";
    const choices = page.getByRole("group", { name: t("intake.tapLabel"), exact: true });
    const scale = page.getByRole("group", { name: t("intake.scaleLabel"), exact: true });
    let mode = "text";
    if (key === "chief_complaint") answer = scenario.complaint;
    else if (key.includes("onset") || key.includes("last_known")) answer = scenario.id === "new-adult" ? "Yesterday afternoon" : scenario.id === "returning-adult" ? "Seven this morning" : "Yesterday evening";
    else if (key === "pain_location") answer = scenario.location;
    else if (key === "pain_score" || key === "severity") { answer = scenario.pain; mode = "tap"; }
    else if (await choices.isVisible()) { answer = key === "" ? "OK" : scenario.yes.includes(key) ? "Yes" : "No"; mode = "tap"; }
    else if (key === "allergies_confirm") answer = "I have no known allergies";
    else if (key === "arrival_mode") answer = "I walked in";
    const responsePromise = page.waitForResponse(r => r.url().endsWith(`/v1/sessions/${registration.session_id}/turns`) && r.request().method() === "POST", { timeout: 45_000 });
    const began = Date.now();
    if (mode === "tap") await (key === "pain_score" || key === "severity" ? scale : choices).getByRole("button", { name: answer, exact: true }).click();
    else { await page.getByLabel(t("intake.typeLabel"), { exact: true }).fill(answer); await page.getByRole("button", { name: t("intake.send"), exact: true }).click(); }
    const response = await responsePromise;
    expect(response.status(), `${scenario.id}: ${key}`).toBe(200);
    turns.push({ key: question.key, question: question.text, mode, answer, latencyMs: Date.now() - began });
    const body = await response.json();
    question = body.done ? null : body.next_question;
    if (key === "chief_complaint") {
      const facts = await apiJson(page, `/v1/sessions/${registration.session_id}/facts`, await staffTokenFor("nurse"));
      stage.firstNaturalAnswerFacts = facts;
      expect(facts.extraction_status, "the real extraction completed rather than only retaining fallback patient text").toBe("complete");
      expect(facts.chief_complaint?.provenance).toBe("PATIENT_STATED");
      expect(facts.chief_complaint?.source.kind).toBe("transcript_span");
      await shot(page, `${scenario.id}-adaptive-followup`);
    }
    save();
  }
  expect(turns.some(turn => turn.key === "chief_complaint"), "every adaptive visit records the patient's or caregiver's actual reason for attending").toBe(true);
  if (scenario.visitFor === "child") expect(turns.some(turn => /^(child_danger|child_amber)\./.test(turn.key ?? "")), "paediatric safety questions are retained").toBe(true);
  else {
    const coveredInAnswer = sourcedPaths(stage.firstNaturalAnswerFacts).some(path => scenario.relevant.test(path));
    expect(coveredInAnswer || turns.some(turn => turn.key !== "chief_complaint" && scenario.relevant.test(turn.key ?? "")), "concern-specific facts are extracted or asked about rather than forcing duplicate questions").toBe(true);
  }
  expect(question, "conversation finished within its configured turn budget").toBeNull();
  if (scenario.visitFor === "child") {
    await expect(page).toHaveURL(/\/done$/);
    stage.vitals = "Adult sensor skipped intentionally; paediatric observations require nurse review.";
  } else {
    await expect(page).toHaveURL(/\/vitals$/);
    await shot(page, `${scenario.id}-vitals-ready`);
    const began = Date.now();
    await page.getByRole("button", { name: t("vitals.guide.start"), exact: true }).click();
    await shot(page, `${scenario.id}-vitals-measuring`);
    await expect(page).toHaveURL(/\/done$/, { timeout: 60_000 });
    stage.vitalsMs = Date.now() - began;
  }
  await shot(page, `${scenario.id}-receipt`);
}

for (const scenario of scenarios) test(`${scenario.id}: live adaptive intake, sourced triage and nurse-confirmed ticket`, async ({ page, browser }) => {
  const stage: Record<string, unknown> = { startedAt: new Date().toISOString() };
  results[scenario.id] = stage;
  try {
    const providers = await apiJson(page, "/v1/admin/providers", await staffTokenFor("admin"));
    const extraction = providers.tasks.find((task: { task: string }) => task.task === "extraction");
    stage.extractionProvider = extraction;
    expect(extraction).toMatchObject({ provider: "local", model: "qwen3.5-9b-4bit", available: true });
    const registration = await register(page, scenario, stage);
    stage.sessionId = registration.session_id;
    const patientRecord = await page.request.get(`${STACK.fhir}/Patient`, {
      params: { identifier: scenario.returning && scenario.visitFor === "self" ? "https://careflow.example/fhir/sid/keypad-id|100005" : `urn:careflow:adaptive|${registration.session_id}` },
    });
    expect(patientRecord.status()).toBe(200);
    const patientBundle = await patientRecord.json();
    expect(patientBundle.total, "one actual patient record belongs to this registration").toBe(1);
    const expectedPatientId = String(patientBundle.entry[0].resource.id);
    stage.fhirPatientId = expectedPatientId;
    if (scenario.returning && scenario.visitFor === "self") expect(expectedPatientId).toBe("persona-routine");
    if (scenario.visitFor !== "self") expect(expectedPatientId).not.toBe("persona-routine");
    await intake(page, registration, scenario, stage);
    const nurseToken = await staffTokenFor("nurse");
    const triage = await apiJson(page, `/v1/sessions/${registration.session_id}/triage`, nurseToken);
    const facts = await apiJson(page, `/v1/sessions/${registration.session_id}/facts`, nurseToken);
    const reception = await apiJson(page, `/v1/sessions/${registration.session_id}/reception`, nurseToken);
    stage.triage = triage; stage.facts = facts; stage.reception = reception;
    expect(reception.intake_mode).toBe("adaptive");
    expect(reception.visit_for).toBe(scenario.visitFor);
    if (scenario.visitFor === "child") { expect(triage.suggestion).toBeNull(); expect(facts.vitals?.heart_rate).toBeUndefined(); }
    else {
      expect(triage.suggestion?.model_id).toBe("laya-ktas");
      expect(triage.suggestion?.model_version).toBe("laya-ktas-ebf31b5e-s42");
      expect(triage.suggestion?.reasons.length).toBeGreaterThanOrEqual(1);
      expect(triage.suggestion?.reasons.length).toBeLessThanOrEqual(3);
      expect(facts.vitals?.heart_rate.provenance).toBe("DEVICE_MEASURED");
      const sources = factIds(facts);
      for (const reason of triage.suggestion.reasons) {
        expect(reason.fact_ids.length, "each model reason cites actual recorded facts").toBeGreaterThan(0);
        for (const id of reason.fact_ids) expect(sources.has(id), `source ${id} exists in this patient's facts`).toBe(true);
      }
    }
    if (scenario.id === "returning-adult") { expect(reception.identity_status).toBe("verified_demo"); expect(triage.alerts.some((a: {rule_id:string}) => a.rule_id === "CHEST_PAIN_ECG_10MIN")).toBe(true); }
    if (scenario.visitFor !== "self") { expect(reception.patient.name).toBe(scenario.patientName); expect(reception.caregiver.name).not.toBe(scenario.patientName); }
    const nurse = await staffPage(browser, "nurse", `/nurse/${registration.session_id}`);
    try {
      await expect(nurse.getByTestId("clinical-snapshot")).toBeVisible();
      if (scenario.visitFor !== "child") await expect(nurse.locator(".staff-model-identity")).toContainText("laya-ktas");
      await shot(nurse, `${scenario.id}-nurse-review`);
      const began = Date.now();
      const [decision] = await Promise.all([
        nurse.waitForResponse(r => r.url().endsWith(`/v1/nurse/sessions/${registration.session_id}/decision`) && r.request().method() === "POST", { timeout: 90_000 }),
        nurseDecides(nurse, scenario.visitFor === "child" ? 3 : 4),
      ]);
      stage.decisionMs = Date.now() - began;
      expect(decision.status()).toBe(200);
      const decisionBody = await decision.json();
      stage.decision = decisionBody;
      expect(decisionBody.token?.token_code).toBeTruthy();
      await expect(page.getByTestId("token-code")).toBeVisible({ timeout: 60_000 });
      await expect(page.getByText(t("patientReceipt.confirmed"), { exact: true })).toBeVisible();
      await shot(page, `${scenario.id}-queue-ticket`);
      await expect.poll(async () => {
        const response = await page.request.get(`${STACK.fhir}/Encounter`, { params: { identifier: `urn:careflow:session|${registration.session_id}` }, timeout: 15_000 });
        expect(response.status()).toBe(200);
        const bundle = await response.json();
        const encounter = bundle.entry?.[0]?.resource;
        stage.fhirEncounter = { count: bundle.total ?? 0, id: encounter?.id, patientReference: encounter?.subject?.reference };
        return bundle.total ?? 0;
      }, { message: "this visit's nurse-confirmed record reaches FHIR", timeout: 30_000 }).toBe(1);
      expect((stage.fhirEncounter as { patientReference?: string }).patientReference).toBe(`Patient/${expectedPatientId}`);
      stage.completedAt = new Date().toISOString();
    } finally { await nurse.context().close(); }
  } finally { save(); }
});

test("resume owned ankle: nurse confirmation, actual token and correct FHIR patient", async ({ browser }) => {
  test.skip(process.env.CAREFLOW_RESUME_OWNED_VISIT !== "1", "Run explicitly to resume the preserved, unconfirmed synthetic ankle visit.");
  const previous = JSON.parse(readFileSync(`${evidence}/compact-run/new-adult.json`, "utf8"));
  const sessionId = String(previous.sessionId);
  expect(sessionId).toMatch(/^[a-f0-9-]{36}$/);
  const stage: Record<string, unknown> = { startedAt: new Date().toISOString(), sessionId, priorEvidence: "compact-run/new-adult.json", fhirPatientId: previous.fhirPatientId };
  results["new-adult-resumed"] = stage;
  const nurse = await staffPage(browser, "nurse", `/nurse/${sessionId}`);
  try {
    await expect(nurse.getByTestId("clinical-snapshot")).toBeVisible();
    const nurseToken = await staffTokenFor("nurse");
    const triage = await apiJson(nurse, `/v1/sessions/${sessionId}/triage`, nurseToken);
    stage.triage = triage;
    expect(triage.suggestion).toMatchObject({ model_id: "laya-ktas", model_version: "laya-ktas-ebf31b5e-s42", level: 4 });
    await expect(nurse.getByRole("button", { name: "Confirm level 4", exact: true })).toBeVisible();
    expect(await nurse.getByRole("region", { name: "Change level", exact: true }).count()).toBe(0);
    await shot(nurse, "new-adult-resumed-nurse-review");
    const began = Date.now();
    const [decision] = await Promise.all([
      nurse.waitForResponse(r => r.url().endsWith(`/v1/nurse/sessions/${sessionId}/decision`) && r.request().method() === "POST", { timeout: 90_000 }),
      nurseDecides(nurse, 4),
    ]);
    stage.decisionMs = Date.now() - began;
    expect(decision.status()).toBe(200);
    const result = await decision.json();
    stage.decision = result;
    expect(result.decision.action).toBe("CONFIRM");
    expect(result.decision.suggestion_id).toBe(triage.suggestion.suggestion_id);
    expect(result.token?.token_code).toBeTruthy();
    await expect(nurse.getByTestId("decision-result")).toContainText(result.token.token_code);
    await shot(nurse, "new-adult-resumed-nurse-confirmed");
    await expect.poll(async () => {
      const response = await nurse.request.get(`${STACK.fhir}/Encounter`, { params: { identifier: `urn:careflow:session|${sessionId}` }, timeout: 15_000 });
      expect(response.status()).toBe(200);
      const bundle = await response.json();
      const encounter = bundle.entry?.[0]?.resource;
      stage.fhirEncounter = { count: bundle.total ?? 0, id: encounter?.id, patientReference: encounter?.subject?.reference };
      return bundle.total ?? 0;
    }, { timeout: 30_000, message: "only this owned visit's confirmed Encounter reaches the actual patient record" }).toBe(1);
    expect((stage.fhirEncounter as { patientReference?: string }).patientReference).toBe(`Patient/${previous.fhirPatientId}`);
    stage.completedAt = new Date().toISOString();
    stage.boundary = "Resumed the same session at the staff UI. The original patient browser context had closed; no post-confirmation patient receipt screenshot is claimed.";
  } finally { save(); await nurse.context().close(); }
});

test("anonymous urgent help: actionable request, real rule, vitals and nurse confirmation", async ({ page, browser }) => {
  const stage: Record<string, unknown> = { startedAt: new Date().toISOString() };
  results["anonymous-urgent-help"] = stage;
  try {
    await page.goto("/");
    await page.getByRole("button", { name: t("experience.kioskAction"), exact: true }).click();
    await page.getByRole("button", { name: t("reception.urgentHelp"), exact: true }).click();
    const form = page.getByRole("region", { name: t("reception.urgentTitle"), exact: true });
    await form.getByRole("checkbox", { name: t("reception.symptoms.chest_pain"), exact: true }).check();
    await form.getByRole("checkbox", { name: t("reception.urgentConsent"), exact: true }).check();
    const responsePromise = page.waitForResponse(r => r.url().endsWith("/v1/checkin/urgent") && r.request().method() === "POST");
    const began = Date.now();
    await form.getByRole("button", { name: t("reception.requestHelp"), exact: true }).click();
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    const registration = await response.json() as Registration;
    stage.requestMs = Date.now() - began;
    stage.sessionId = registration.session_id;
    await expect(page).toHaveURL(/\/vitals$/);
    const nurseToken = await staffTokenFor("nurse");
    const beforeVitals = await apiJson(page, `/v1/sessions/${registration.session_id}/triage`, nurseToken);
    stage.beforeVitals = beforeVitals;
    expect(beforeVitals.suggestion).toBeNull();
    expect(beforeVitals.alerts.some((a: { rule_id: string }) => a.rule_id === "CHEST_PAIN_ECG_10MIN")).toBe(true);
    const worklist = await apiJson(page, "/v1/nurse/worklist", nurseToken);
    const row = worklist.find((item: { session_id: string }) => item.session_id === registration.session_id);
    stage.worklistItem = row;
    expect(row.urgent_help).toMatchObject({ patient_type: "adult", emergency_symptoms: ["chest_pain"] });
    expect(row.reception_context).toBeUndefined();
    const nurse = await staffPage(browser, "nurse", "/nurse");
    try {
      const reference = `CF-${registration.session_id.replaceAll("-", "").slice(0, 8).toUpperCase()}`;
      await expect(nurse.getByTestId("urgent-help-requests")).toContainText(reference);
      await shot(nurse, "anonymous-urgent-help-worklist");
      await shot(page, "anonymous-urgent-help-vitals");
      await page.getByRole("button", { name: t("vitals.guide.start"), exact: true }).click();
      await expect(page).toHaveURL(/\/done$/, { timeout: 60_000 });
      const facts = await apiJson(page, `/v1/sessions/${registration.session_id}/facts`, nurseToken);
      stage.facts = facts;
      expect(facts.patient?.age_years).toBeUndefined();
      expect(facts.patient?.fhir_patient_id).toBeUndefined();
      expect(facts.vitals.heart_rate.provenance).toBe("DEVICE_MEASURED");
      await nurse.goto(`/nurse/${registration.session_id}`);
      await expect(nurse.getByTestId("clinical-snapshot")).toBeVisible();
      const decisionResponse = nurse.waitForResponse(r => r.url().endsWith(`/v1/nurse/sessions/${registration.session_id}/decision`) && r.request().method() === "POST", { timeout: 90_000 });
      await nurseDecides(nurse, 3);
      const decision = await decisionResponse;
      expect(decision.status()).toBe(200);
      stage.decision = await decision.json();
      await expect(page.getByTestId("token-code")).toBeVisible({ timeout: 60_000 });
      await expect(page.getByText(t("patientReceipt.confirmed"), { exact: true })).toBeVisible();
      await shot(page, "anonymous-urgent-help-ticket");
      stage.completedAt = new Date().toISOString();
    } finally { await nurse.context().close(); }
  } finally { save(); }
});

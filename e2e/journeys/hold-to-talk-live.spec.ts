import { mkdirSync, writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { staffTokenFor } from "../lib/actors";
import { msg } from "../lib/i18n";
import { STACK } from "../lib/stack";

const evidence = new URL("../../docs/evidence/ADAPTIVE-20260929/", import.meta.url).pathname;
const locale = process.env.PTT_LANGUAGE === "ar" ? "ar" : "en";
const t = (key: string) => msg("kiosk", locale, key);
const spokenComplaint = locale === "ar" ? /صدر/ : /chest pain since this morning/i;
type MediaProbe = { requests: Array<MediaStreamConstraints | undefined>; streams: MediaStream[] };
type VoiceMessage = { type: string; text?: string; question?: { text?: string }; code?: string; at: number };

test("a held synthetic answer reaches local Whisper and Qwen, with microphone off after release", async ({ page }) => {
  mkdirSync(`${evidence}screenshots`, { recursive: true });
  await page.context().addCookies([{ name: "NEXT_LOCALE", value: locale, url: STACK.kiosk }]);
  await page.context().grantPermissions(["microphone", "camera"], { origin: STACK.kiosk });
  await page.addInitScript(() => {
    const probe: MediaProbe = { requests: [], streams: [] };
    (window as unknown as { careflowPttProbe: MediaProbe }).careflowPttProbe = probe;
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      probe.requests.push(constraints);
      const stream = await original(constraints);
      probe.streams.push(stream);
      return stream;
    };
  });
  const messages: VoiceMessage[] = [];
  let audioSent = 0;
  let audioAfterEnd = 0;
  let endSentAt = 0;
  let firstTtsAt = 0;
  let startMode = "";
  page.on("websocket", (socket) => {
    if (!socket.url().includes("/kiosk/audio")) return;
    socket.on("framesent", ({ payload }) => {
      if (typeof payload !== "string") { audioSent += 1; if (endSentAt) audioAfterEnd += 1; return; }
      const data = JSON.parse(payload) as { type: string; input_mode?: string };
      if (data.type === "start") startMode = data.input_mode ?? "continuous";
      if (data.type === "end_utterance") endSentAt = Date.now();
    });
    socket.on("framereceived", ({ payload }) => {
      if (typeof payload !== "string") { firstTtsAt ||= Date.now(); return; }
      messages.push({ ...JSON.parse(payload) as VoiceMessage, at: Date.now() });
    });
  });

  await page.goto(STACK.kiosk);
  await page.getByRole("button", { name: t("experience.kioskAction"), exact: true }).click();
  await expect(page).toHaveURL(/\/checkin$/);
  await page.getByRole("button", { name: t("reception.newPatient"), exact: true }).click();
  await page.getByRole("button", { name: t("reception.visitors.self.title"), exact: true }).click();
  await page.getByLabel(t("reception.name"), { exact: true }).fill("Taylor Voice Demo");
  await page.getByLabel(t("reception.age"), { exact: true }).fill("44");
  await page.getByRole("button", { name: t("reception.sexOptions.male"), exact: true }).click();
  await page.getByRole("button", { name: t("reception.continue"), exact: true }).click();
  await expect(page).toHaveURL(/\/consent$/);
  await page.getByRole("button", { name: t("safety.consent.agree"), exact: true }).click();
  const registered = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/checkin/adaptive"));
  await page.getByRole("button", { name: t("reception.startConversation"), exact: true }).click();
  const registration = await registered;
  expect(registration.status()).toBe(200);
  const started = await registration.json() as { session_id: string };
  await expect(page).toHaveURL(/\/intake$/);
  const question = page.locator(".intake-question h2");
  const firstQuestion = (await question.textContent())?.trim();
  const audioRequests = () => page.evaluate(() => (window as unknown as { careflowPttProbe: MediaProbe }).careflowPttProbe.requests.filter((item) => item?.audio).length);
  expect(await audioRequests()).toBe(0);
  expect(audioSent).toBe(0);

  const button = page.locator("[data-hold-to-talk]");
  await button.focus();
  await page.keyboard.down("Space");
  await expect(button).toContainText(t("holdToTalk.listening"));
  expect(await audioRequests()).toBe(1);
  await page.waitForTimeout(4_500); // play the complete synthetic WAV, including its trailing silence
  expect(messages.filter((item) => item.type === "final")).toHaveLength(0);
  expect(audioSent).toBeGreaterThan(0);
  await page.keyboard.up("Space");
  await expect(button).toContainText(t("holdToTalk.processing"));
  const audioTrackStates = await page.evaluate(() => (window as unknown as { careflowPttProbe: MediaProbe }).careflowPttProbe.streams.flatMap((stream) => stream.getAudioTracks().map((track) => track.readyState)));
  expect(audioTrackStates).toEqual(["ended"]);
  expect(startMode).toBe("push_to_talk");
  expect(endSentAt).toBeGreaterThan(0);

  await expect(page.getByTestId("heard")).toContainText(spokenComplaint, { timeout: 60_000 });
  await expect(question).not.toHaveText(firstQuestion ?? "", { timeout: 35_000 });
  await expect.poll(() => firstTtsAt, { timeout: 20_000 }).toBeGreaterThan(0);
  expect(messages.filter((item) => item.type === "error")).toEqual([]);
  expect(audioAfterEnd).toBe(0);
  expect(await audioRequests()).toBe(1); // playback has not restarted the microphone
  const nurseToken = await staffTokenFor("nurse");
  const factsResponse = await page.request.get(`${STACK.api}/v1/sessions/${started.session_id}/facts`, { headers: { Authorization: `Bearer ${nurseToken}` } });
  expect(factsResponse.status()).toBe(200);
  const facts = await factsResponse.json();
  expect(facts.chief_complaint).toMatchObject({ provenance: "PATIENT_STATED", source: { kind: "transcript_span" } });
  expect(String(facts.chief_complaint.source.quote).toLowerCase()).toMatch(locale === "ar" ? /صدر/ : /chest pain/i);
  const triageResponse = await page.request.get(`${STACK.api}/v1/sessions/${started.session_id}/triage`, { headers: { Authorization: `Bearer ${nurseToken}` } });
  expect(triageResponse.status()).toBe(200);
  const triage = await triageResponse.json() as { warnings?: string[] };
  const final = messages.find((item) => item.type === "final")!;
  const next = messages.find((item) => item.type === "question")!;
  const admin = await staffTokenFor("admin");
  const providersResponse = await page.request.get(`${STACK.api}/v1/admin/providers`, { headers: { Authorization: `Bearer ${admin}` } });
  expect(providersResponse.status()).toBe(200);
  const providers = await providersResponse.json();
  expect(JSON.stringify(providers)).not.toContain('"status":"replay"');
  const result = {
    scenario: `Synthetic microphone, one held ${locale === "ar" ? "Arabic" : "English"} answer; no real room audio`,
    language: locale,
    session_id: started.session_id,
    transcript: final.text,
    next_question: next.question?.text ?? next.text,
    release_to_transcript_ms: final.at - endSentAt,
    transcript_to_question_ms: next.at - final.at,
    release_to_first_tts_ms: firstTtsAt - endSentAt,
    capture_frames: audioSent,
    audio_frames_after_release: audioAfterEnd,
    audio_track_states_on_release: audioTrackStates,
    microphone_requests_including_playback: await audioRequests(),
    providers,
    warnings: triage.warnings ?? [],
    extraction_incomplete: (triage.warnings ?? []).includes("extraction_incomplete"),
  };
  writeFileSync(`${evidence}voice-${locale}-live.json`, `${JSON.stringify(result, null, 2)}\n`);
  await page.screenshot({ path: `${evidence}screenshots/hold-to-talk-${locale}-reply.png`, fullPage: true });
  expect(triage.warnings ?? [], "the next question comes from complete extraction, not its fallback").not.toContain("extraction_incomplete");
  console.log(JSON.stringify({ voice: "passed", release_to_transcript_ms: result.release_to_transcript_ms, transcript_to_question_ms: result.transcript_to_question_ms, release_to_first_tts_ms: result.release_to_first_tts_ms }));
});

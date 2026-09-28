"use client";

import { createCareflowClient } from "@careflow/contracts/client";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { CONSENT_VERSION, markConsentSaved } from "@/consent/hasConsent";
import { API_BASE_URL, api } from "@/lib/api";
import type { ReceptionDraft } from "@/lib/reception";
import { readDevice, subscribeDevice, useKiosk } from "@/lib/session";
import { KioskIcon, type KioskIconName } from "./KioskIcon";

const directoryApi = createCareflowClient(API_BASE_URL, () => undefined);
const STEPS = ["identity", "who", "patient", "ready"] as const;
const ICONS = { general_medicine: "doctor", cardiology: "heart", paediatrics: "child", orthopaedics: "bone", dermatology: "skin", ent: "ear", ophthalmology: "eye", womens_health: "women", emergency: "cross", diagnostics: "lab", unsure: "help", dental: "tooth" } satisfies Record<string, KioskIconName>;
const iconFor = (id: string): KioskIconName => ICONS[id as keyof typeof ICONS] ?? "doctor";
const VISITORS = ["self", "child", "elder", "dependent"] as const;
const SYMPTOMS = ["chest_pain", "short_of_breath", "face_droop", "arm_weakness", "speech_difficulty", "drowsy_or_confused"] as const;
const VISITOR_ICONS: Record<ReceptionDraft["visitFor"], KioskIconName> = { self: "person", child: "child", elder: "person", dependent: "shield" };
type Directory = { departments: Array<{ id: string; name: string; description: string; doctors: Array<{ id: string; name: string; availability: string }> }> };

/** Identify and confirm the actual patient before consent and adaptive clinical intake. */
export function ReceptionWizard() {
  const t = useTranslations("reception");
  const locale = useLocale() === "ar" ? "ar" : "en";
  const router = useRouter();
  const { patient, consent, receptionDraft: draft, updateReceptionDraft: update, start, setPatientType, setQuestion, setConsent } = useKiosk();
  const ready = useSyncExternalStore(subscribeDevice, () => readDevice() !== null, () => false);
  const [directory, setDirectory] = useState<Directory | null>(null);
  const [sending, setSending] = useState(false);
  const [identityResult, setIdentityResult] = useState<"no_match" | "unavailable" | "verification_failed" | null>(null);
  const [submitFailed, setSubmitFailed] = useState(false);
  const [urgentOpen, setUrgentOpen] = useState(false);
  const [urgentPatientType, setUrgentPatientType] = useState<"adult" | "child">("adult");
  const [urgentConsent, setUrgentConsent] = useState(false);
  const [urgentFailed, setUrgentFailed] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const inFlight = useRef(false);
  const step = patient ? 3 : Math.min(3, Math.max(0, draft.receptionStep));
  const age = Number(draft.age);
  const verifiedPatient = Boolean(draft.patientIdentityToken);
  const caregiverNeeded = draft.visitFor !== "self";
  const validPatient = draft.name.trim().length >= 2 && draft.name.trim().length <= 100 && draft.age.trim() !== "" && Number.isInteger(age) && age >= 0 && age <= 130 && draft.sex !== "" && (draft.visitFor !== "child" || age < 16) && (draft.visitFor !== "elder" || age >= 16) && (!caregiverNeeded || draft.caregiverName.trim().length >= 2);
  const validLookup = ready && draft.identityConsent && draft.identityValue.trim().length >= 3;
  const validVerification = /^\d{4}$/.test(draft.birthYear) && Number(draft.birthYear) >= 1890 && Number(draft.birthYear) <= new Date().getFullYear();
  const department = directory?.departments.find((item) => item.id === draft.departmentId);
  const departmentName = (id: string, fallback: string) => Object.hasOwn(ICONS, id) ? t(`departments.${id as keyof typeof ICONS}.title`) : fallback;
  const go = (receptionStep: number) => update({ receptionStep });

  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    void directoryApi.GET("/v1/reception/directory", { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted && result.data) setDirectory(result.data);
    }).catch(() => undefined).finally(() => clearTimeout(timeout));
    return () => { controller.abort(); clearTimeout(timeout); };
  }, []);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: "instant" }); }, [step]);

  function resetIdentity(value: string, kind = draft.identityKind) {
    update({ identityValue: value, identityKind: kind, identityChallenge: "", maskedName: "", birthYear: "", patientIdentityToken: "", matchedProfile: null, matchedIdentityToken: "", matchedHistory: null, history: null });
    setIdentityResult(null);
  }
  function newPatient() {
    update({ receptionStep: 1, name: "", age: "", sex: "", phone: draft.identityKind === "mobile" ? draft.identityValue.trim() : "", matchedProfile: null, matchedIdentityToken: "", patientIdentityToken: "", history: null, matchedHistory: null, identityChallenge: "", caregiverName: "", caregiverPhone: "", caregiverIdentityToken: "" });
    setIdentityResult(null);
  }
  function selectVisitor(visitFor: ReceptionDraft["visitFor"]) {
    const profile = draft.matchedProfile;
    if (visitFor !== draft.visitFor) {
      if (visitFor === "self" && profile) {
        update({ name: profile.name, age: String(profile.age_years), sex: profile.sex, phone: profile.phone ?? "", patientIdentityToken: draft.matchedIdentityToken, history: draft.matchedHistory, caregiverName: "", caregiverPhone: "", caregiverIdentityToken: "" });
      } else if (visitFor === "child" && profile && profile.age_years < 16) {
        // A verified child's own record belongs to the patient; the adult helper is entered separately.
        update({ name: profile.name, age: String(profile.age_years), sex: profile.sex, phone: profile.phone ?? "", patientIdentityToken: draft.matchedIdentityToken, history: draft.matchedHistory, caregiverName: "", caregiverPhone: "", caregiverIdentityToken: "" });
      } else if (visitFor !== "self" && profile && profile.age_years >= 16) {
        // An identified adult is the helper here. Never carry their clinical record into a child/dependent's visit.
        update({ name: "", age: "", sex: "", phone: "", patientIdentityToken: "", history: null, caregiverName: profile.name, caregiverPhone: profile.phone ?? "", caregiverIdentityToken: draft.matchedIdentityToken });
      } else if (visitFor !== "self") {
        update({ name: "", age: "", sex: "", phone: "", patientIdentityToken: "", history: null, caregiverPhone: draft.caregiverPhone || draft.phone });
      } else {
        update({ name: "", age: "", sex: "", phone: draft.identityKind === "mobile" ? draft.identityValue.trim() : "", patientIdentityToken: "", history: null, caregiverName: "", caregiverPhone: "", caregiverIdentityToken: "" });
      }
    }
    update({ visitFor, caregiverRelationship: visitFor === "child" ? "parent" : visitFor === "elder" ? "family" : "carer", receptionStep: 2 });
  }

  async function lookup(event: FormEvent) {
    event.preventDefault();
    if (!validLookup || inFlight.current) return;
    inFlight.current = true; setSending(true); setIdentityResult(null);
    const result = await api.POST("/v1/identity/lookup", { body: { claim_key: draft.claimKey, kind: draft.identityKind, value: draft.identityValue.trim(), consent: { camera: false, voice: false, data_use: true, consent_version: CONSENT_VERSION } }, signal: AbortSignal.timeout(12_000) }).catch(() => null);
    inFlight.current = false; setSending(false);
    const data = result?.data;
    if (data?.status === "match" && data.challenge_token) update({ identityChallenge: data.challenge_token, maskedName: data.masked_name ?? "", birthYear: "" });
    else setIdentityResult(data?.status === "no_match" ? "no_match" : "unavailable");
  }

  async function verify(event: FormEvent) {
    event.preventDefault();
    if (!validVerification || inFlight.current || !draft.identityChallenge) return;
    inFlight.current = true; setSending(true); setIdentityResult(null);
    const result = await api.POST("/v1/identity/verify", { body: { claim_key: draft.claimKey, challenge_token: draft.identityChallenge, birth_year: Number(draft.birthYear) }, signal: AbortSignal.timeout(12_000) }).catch(() => null);
    inFlight.current = false; setSending(false);
    if (!result?.data) { setIdentityResult("verification_failed"); return; }
    const { patient, verification_token, history } = result.data;
    update({ name: patient.name, age: String(patient.age_years), sex: patient.sex, phone: patient.phone ?? "", matchedProfile: patient, matchedIdentityToken: verification_token, patientIdentityToken: verification_token, matchedHistory: history, history, visitFor: "self", receptionStep: 1 });
  }

  function next(event: FormEvent) {
    event.preventDefault();
    if (step === 1) { selectVisitor(draft.visitFor); return; }
    if (step === 2 && validPatient) {
      go(3);
      if (!consent) router.push("/consent");
    }
  }
  function returnToVisit() { router.push(patient?.question ? "/intake" : patient?.patientType === "child" ? "/done" : "/vitals"); }
  function back() { if (patient) returnToVisit(); else if (step === 0) router.push("/"); else go(step - 1); }

  async function requestUrgentHelp() {
    if (!urgentConsent || !ready || inFlight.current || patient) return;
    inFlight.current = true; setSending(true); setUrgentFailed(false);
    const result = await api.POST("/v1/checkin/urgent", {
      body: { claim_key: draft.claimKey, language: locale, patient_type: urgentPatientType, emergency_symptoms: draft.emergencySymptoms, consent: { camera: false, voice: false, data_use: true, consent_version: CONSENT_VERSION } },
      signal: AbortSignal.timeout(15_000),
    }).catch(() => null);
    inFlight.current = false; setSending(false);
    if (!result?.data) { setUrgentFailed(true); return; }
    setConsent({ camera: false, voice: false, dataUse: true });
    markConsentSaved();
    start(result.data, { question_id: "urgent_help", text: t("helpRecorded"), key: "chief_complaint", notice: null });
    setQuestion(null);
    setPatientType(urgentPatientType);
    update({ urgentRequest: true });
    router.push(urgentPatientType === "child" ? "/done" : "/vitals");
  }

  async function begin(event: FormEvent) {
    event.preventDefault();
    if (patient) { returnToVisit(); return; }
    if (!validPatient || !ready || inFlight.current || !draft.sex) return;
    if (!consent) { router.push("/consent"); return; }
    inFlight.current = true; setSending(true); setSubmitFailed(false);
    const result = await api.POST("/v1/checkin/adaptive", {
      body: { claim_key: draft.claimKey, language: locale, patient: { name: draft.name.trim(), age_years: age, sex: draft.sex, ...(draft.phone.trim() ? { phone: draft.phone.trim() } : {}) }, ...(draft.patientIdentityToken ? { patient_identity_token: draft.patientIdentityToken } : {}), visit_for: draft.visitFor,
        ...(caregiverNeeded ? { caregiver: { name: draft.caregiverName.trim(), relationship: draft.caregiverRelationship, ...(draft.caregiverPhone.trim() ? { phone: draft.caregiverPhone.trim() } : {}), ...(draft.caregiverIdentityToken ? { identity_token: draft.caregiverIdentityToken } : {}) } } : {}),
        department_id: draft.departmentId || "unsure", ...(draft.doctorId ? { doctor_id: draft.doctorId } : {}), emergency_symptoms: draft.emergencySymptoms,
        consent: { camera: consent.camera, voice: consent.voice, data_use: consent.dataUse, consent_version: CONSENT_VERSION } }, signal: AbortSignal.timeout(15_000),
    }).catch(() => null);
    inFlight.current = false; setSending(false);
    if (!result?.data) { setSubmitFailed(true); return; }
    start(result.data, { question_id: "q_chief_complaint_kiosk", text: t("adaptiveFirstQuestion"), key: "chief_complaint", notice: null });
    markConsentSaved();
    setPatientType(draft.visitFor === "child" || age < 16 ? "child" : "adult");
    router.push("/intake");
  }

  const primaryLabel = patient ? t("returnToVisit") : sending ? t("working") : step === 0 ? t(draft.identityChallenge ? "verifyIdentity" : "findDetails") : step === 3 ? t(consent ? "startConversation" : "privacyChoices") : t("continue");
  const canContinue = patient ? true : step === 0 ? draft.identityChallenge ? validVerification : validLookup : step === 1 ? true : validPatient;
  const onSubmit = step === 0 ? draft.identityChallenge ? verify : lookup : step === 3 ? begin : next;

  return (
    <div className="reception-wizard">
      <div className="reception-progress" aria-label={t("progress")}><span>{t("step", { current: step + 1, total: STEPS.length })}</span><ol>{STEPS.map((name, index) => <li key={name} aria-current={step === index ? "step" : undefined} data-complete={step > index}><span>{index < step ? <KioskIcon name="check" /> : index + 1}</span>{t(`identitySteps.${name}`)}</li>)}</ol></div>
      <div className="reception-help"><span><KioskIcon name="shield" />{t("demoData")}</span><button type="button" onClick={() => setUrgentOpen((value) => !value)} aria-expanded={urgentOpen}><KioskIcon name="cross" />{t("urgentHelp")}</button></div>
      {urgentOpen ? <section className="reception-alert" role="region" aria-label={t("urgentTitle")}>
        <h2>{t("urgentTitle")}</h2><p>{t("immediateHelp")}</p>
        {!patient ? <>
          <fieldset><legend>{t("urgentFor")}</legend><div className="reception-sex-options">{(["adult", "child"] as const).map((kind) => <button type="button" key={kind} disabled={sending} aria-pressed={urgentPatientType === kind} onClick={() => setUrgentPatientType(kind)}>{t(`urgentTypes.${kind}`)}</button>)}</div></fieldset>
          <p>{t("urgentSymptomsOptional")}</p>
          <div className="reception-urgent-options">{SYMPTOMS.map((symptom) => <label key={symptom}><input type="checkbox" disabled={sending} checked={draft.emergencySymptoms.includes(symptom)} onChange={() => update({ emergencySymptoms: draft.emergencySymptoms.includes(symptom) ? draft.emergencySymptoms.filter((item) => item !== symptom) : [...draft.emergencySymptoms, symptom] })} />{t(`symptoms.${symptom}`)}</label>)}</div>
          <label className="reception-identity-consent"><input type="checkbox" checked={urgentConsent} disabled={sending} onChange={(event) => setUrgentConsent(event.target.checked)} />{t("urgentConsent")}</label>
          <p>{t("helpNotSent")}</p>
          {urgentFailed ? <p role="alert">{t("helpFailed")}</p> : null}
          <button type="button" className="reception-primary" disabled={sending || !urgentConsent || !ready} onClick={() => void requestUrgentHelp()}><KioskIcon name="cross" />{t(sending ? "working" : "requestHelp")}</button>
        </> : null}
        <button type="button" className="reception-secondary" disabled={sending} onClick={() => setUrgentOpen(false)}>{t("continueWithHelp")}</button>
      </section> : null}
      <form className="reception-step" onSubmit={(event) => void onSubmit(event)}>
        <div className="reception-heading"><p>{t(`identityEyebrows.${STEPS[step]!}`)}</p><h1 ref={heading} tabIndex={-1}>{patient ? t(draft.urgentRequest ? "helpRecorded" : "savedDetailsTitle") : t(`identityTitles.${STEPS[step]!}`)}</h1><span>{patient ? t(draft.urgentRequest ? "urgentSavedDescription" : "savedDetailsDescription") : t(`identityDescriptions.${STEPS[step]!}`)}</span></div>
        {step === 0 ? <div className="reception-fields">
          {!draft.identityChallenge ? <>
            <div className="reception-sex-options reception-field-full">{(["mobile", "patient_id"] as const).map((kind) => <button type="button" key={kind} aria-pressed={draft.identityKind === kind} disabled={sending} onClick={() => resetIdentity("", kind)}><KioskIcon name={kind === "mobile" ? "phone" : "card"} className="h-5 w-5" />{t(`identityKinds.${kind}`)}</button>)}</div>
            <label className="reception-field reception-field-full">{t(`identityKinds.${draft.identityKind}`)}<input type={draft.identityKind === "mobile" ? "tel" : "text"} inputMode={draft.identityKind === "mobile" ? "tel" : "text"} autoComplete="off" maxLength={64} disabled={sending} value={draft.identityValue} onChange={(event) => resetIdentity(event.target.value)} placeholder={t(draft.identityKind === "mobile" ? "mobilePlaceholder" : "patientIdPlaceholder")} /></label>
            <label className="reception-identity-consent"><input type="checkbox" checked={draft.identityConsent} disabled={sending} onChange={(event) => update({ identityConsent: event.target.checked })} />{t("lookupConsent")}</label>
            <p className="reception-note">{t("lookupHint")}</p>
          </> : <>
            <div className="reception-review-section"><strong>{t("profileFound")}</strong><p>{draft.maskedName}</p><p className="reception-note">{t("verificationHint")}</p></div>
            <label className="reception-field reception-field-full">{t("birthYear")}<input type="text" inputMode="numeric" autoComplete="off" maxLength={4} disabled={sending} value={draft.birthYear} onChange={(event) => update({ birthYear: event.target.value.replace(/\D/g, "") })} placeholder="YYYY" /></label>
            <button className="reception-existing" type="button" disabled={sending} onClick={() => resetIdentity(draft.identityValue)}>{t("differentIdentity")}</button>
          </>}
          {identityResult ? <div className="reception-directory-loading" role="status"><p>{t(`identityResults.${identityResult}`)}</p></div> : null}
          <button className="reception-existing" type="button" onClick={newPatient} disabled={sending}>{t("newPatient")}</button>
        </div> : null}
        {step === 1 ? <>
          {draft.matchedProfile ? <p className="reception-selected-department"><KioskIcon name="check" /><strong>{t("welcomeBack", { name: draft.matchedProfile.name })}</strong></p> : null}
          <div className="reception-choice-grid reception-purpose">{VISITORS.map((visitFor) => <button type="button" className="reception-choice-card" key={visitFor} aria-label={t(`visitors.${visitFor}.title`)} aria-describedby={`visitor-${visitFor}-description`} aria-pressed={draft.visitFor === visitFor} onClick={() => selectVisitor(visitFor)}><span className="reception-icon"><KioskIcon name={VISITOR_ICONS[visitFor]} /></span><span className="reception-choice-copy"><strong>{t(`visitors.${visitFor}.title`)}</strong><span id={`visitor-${visitFor}-description`}>{t(`visitors.${visitFor}.description`)}</span></span><KioskIcon name="arrow" /></button>)}</div>
        </> : null}
        {step === 2 ? <div className="reception-fields">
          <p className="reception-note reception-field-full">{t(verifiedPatient ? "verifiedDetails" : caregiverNeeded ? "dependentDetails" : "newDetails")}</p>
          <label className="reception-field reception-field-full">{t(caregiverNeeded ? "patientName" : "name")}<input autoComplete="off" maxLength={100} readOnly={verifiedPatient} value={draft.name} onChange={(event) => update({ name: event.target.value })} placeholder={t("namePlaceholder")} required /></label>
          <label className="reception-field">{t("age")}<input type="number" inputMode="numeric" min={draft.visitFor === "elder" ? 16 : 0} max={draft.visitFor === "child" ? 15 : 130} readOnly={verifiedPatient} value={draft.age} onChange={(event) => update({ age: event.target.value })} placeholder={t("agePlaceholder")} required /></label>
          <label className="reception-field">{t("phone")}<input type="tel" autoComplete="off" maxLength={30} value={draft.phone} onChange={(event) => update({ phone: event.target.value })} placeholder={t("phonePlaceholder")} /></label>
          <fieldset className="reception-field reception-field-full"><legend>{t("sex")}</legend><div className="reception-sex-options">{(["female", "male", "other", "unknown"] as const).map((sex) => <button type="button" key={sex} disabled={verifiedPatient} aria-pressed={draft.sex === sex} onClick={() => update({ sex })}>{t(`sexOptions.${sex}`)}</button>)}</div></fieldset>
          {caregiverNeeded ? <fieldset className="reception-fields reception-field-full"><legend>{t("helperDetails")}</legend><label className="reception-field">{t("helperName")}<input autoComplete="off" maxLength={100} readOnly={Boolean(draft.caregiverIdentityToken)} value={draft.caregiverName} onChange={(event) => update({ caregiverName: event.target.value })} required /></label><label className="reception-field">{t("helperPhone")}<input type="tel" autoComplete="off" maxLength={30} value={draft.caregiverPhone} onChange={(event) => update({ caregiverPhone: event.target.value })} /></label><p className="reception-note reception-field-full">{t("separateHistory")}</p>{draft.caregiverIdentityToken ? <button type="button" className="reception-existing" onClick={() => update({ caregiverName: "", caregiverPhone: "", caregiverIdentityToken: "" })}>{t("differentHelper")}</button> : null}</fieldset> : null}
          {verifiedPatient ? <button className="reception-existing" type="button" onClick={() => { resetIdentity(""); go(0); }}>{t("differentIdentity")}</button> : null}
          <details className="reception-field-full"><summary>{t("optionalDepartment")}</summary><p className="reception-note">{t("optionalDepartmentHint")}</p><div className="reception-choice-grid"><button type="button" className="reception-choice-card" aria-pressed={!draft.departmentId} onClick={() => update({ departmentId: "", doctorId: "" })}><span className="reception-icon"><KioskIcon name="help" /></span><strong>{t("teamWillGuide")}</strong></button>{directory?.departments.map((item) => <button type="button" className="reception-choice-card" key={item.id} aria-pressed={draft.departmentId === item.id} onClick={() => update({ departmentId: item.id, doctorId: "" })}><span className="reception-icon"><KioskIcon name={iconFor(item.id)} /></span><span className="reception-choice-copy"><strong>{departmentName(item.id, item.name)}</strong></span></button>)}</div></details>
        </div> : null}
        {step === 3 && draft.urgentRequest ? <div className="reception-review-section"><p>{t("urgentSavedDescription")}</p><p>{t("urgentNoDelay")}</p></div> : null}
        {step === 3 && !draft.urgentRequest ? <div className="reception-review">
          <section className="reception-review-section"><header><span><KioskIcon name={VISITOR_ICONS[draft.visitFor]} />{t(`visitors.${draft.visitFor}.title`)}</span>{!patient ? <button type="button" onClick={() => go(2)}><KioskIcon name="edit" />{t("edit")}</button> : null}</header><dl><div><dt>{t("patientName")}</dt><dd>{draft.name}</dd></div><div><dt>{t("age")}</dt><dd>{t("ageValue", { age })}</dd></div><div><dt>{t("sex")}</dt><dd>{draft.sex ? t(`sexOptions.${draft.sex}`) : "—"}</dd></div>{caregiverNeeded ? <div><dt>{t("helperName")}</dt><dd>{draft.caregiverName}</dd></div> : null}<div><dt>{t("steps.department")}</dt><dd>{department ? departmentName(department.id, department.name) : t("teamWillGuide")}</dd></div></dl></section>
          <div className="reception-review-section"><header><span><KioskIcon name="voice" />{t("conversationNext")}</span></header><p className="reception-note">{t(consent ? "privacySaved" : "privacyBeforeMedia")}</p></div>
          {draft.patientIdentityToken ? <p className="reception-note"><KioskIcon name="record" />{t("recordAvailable")}</p> : null}
        </div> : null}
        {submitFailed ? <p className="reception-alert" role="alert">{t("adaptiveFailed")}</p> : null}
        {step === 2 && !validPatient ? <p className="reception-note" role="status">{t(caregiverNeeded ? "dependentRequired" : "patientRequired")}</p> : null}
        <div className="reception-actions"><button type="button" className="reception-secondary" disabled={sending} onClick={back}><KioskIcon name="back" />{t("back")}</button><button type="submit" className="reception-primary" disabled={sending || !canContinue || (step === 3 && !ready && !patient)}>{primaryLabel}<KioskIcon name="arrow" /></button></div>
      </form>
    </div>
  );
}

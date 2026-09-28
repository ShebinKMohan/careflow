"use client";

import type { Schema } from "@careflow/contracts";
import { createCareflowClient } from "@careflow/contracts/client";
import { setLocaleCookie } from "@careflow/ui/i18n";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { API_BASE_URL } from "@/lib/api";
import { newClaimKey } from "@/lib/phoneVisit";
import { readSession, readToken, saveSession, saveToken } from "@/lib/session";

import { ClaimForm } from "./ClaimForm";
import { CompanionIcon } from "./CompanionIcon";

const client = createCareflowClient(API_BASE_URL, () => null);
const HOSPITAL = process.env.NEXT_PUBLIC_HOSPITAL_NAME ?? "Northstar General Hospital";
const STEPS = ["identity", "recipient", "details", "preferences", "consent"] as const;
type Step = typeof STEPS[number];
type Stage = "loading" | "missing" | "resume" | "verify" | Step;
type Recipient = Schema<"AdaptiveCheckinRequest">["visit_for"];
type Patient = Schema<"ReceptionPatient">;
type ErrorKey = "invalid" | "expired" | "busy" | "unavailable" | "limited" | "verificationFailed" | "noMatch" | null;
const RECIPIENTS: Recipient[] = ["self", "child", "elder", "dependent"];
const EMERGENCY: Schema<"ReceptionSymptom">[] = ["chest_pain", "short_of_breath", "face_droop", "arm_weakness", "speech_difficulty", "drowsy_or_confused"];
const DEFAULT_PATIENT = { name: "", age: "", sex: "" as Patient["sex"] | "", phone: "" };

/** Identity and consent precede the server's actual adaptive intake. Drafts and identity proofs stay in memory. */
export function MobileStart() {
  const t = useTranslations("identityFlow");
  const r = useTranslations("reception");
  const tStart = useTranslations("start");
  const router = useRouter();
  const language = useLocale() === "ar" ? "ar" : "en";
  const heading = useRef<HTMLHeadingElement>(null);
  const qr = useRef<string | null>(null);
  const key = useRef<string | null>(null);
  const initialized = useRef(false);
  const lock = useRef(false);
  const submitted = useRef<Schema<"AdaptiveCheckinRequest"> | null>(null);
  const urgentSubmitted = useRef<Schema<"UrgentCheckinRequest"> | null>(null);
  const [stage, setStage] = useState<Stage>("loading");
  const [kind, setKind] = useState<"mobile" | "patient_id">("mobile");
  const [identifier, setIdentifier] = useState("");
  const [lookupConsent, setLookupConsent] = useState(false);
  const [challenge, setChallenge] = useState<Schema<"IdentityLookupResult"> | null>(null);
  const [birthYear, setBirthYear] = useState("");
  const [verified, setVerified] = useState<Schema<"IdentityVerifyResult"> | null>(null);
  const [recipient, setRecipient] = useState<Recipient | null>(null);
  const [patient, setPatient] = useState(DEFAULT_PATIENT);
  const [caregiverName, setCaregiverName] = useState("");
  const [caregiverPhone, setCaregiverPhone] = useState("");
  const [relationship, setRelationship] = useState<Schema<"CaregiverRequest">["relationship"]>("family");
  const [directory, setDirectory] = useState<Schema<"ReceptionDirectory"> | null>(null);
  const [directoryFailed, setDirectoryFailed] = useState(false);
  const [departmentId, setDepartmentId] = useState("unsure");
  const [doctorId, setDoctorId] = useState("");
  const [dataConsent, setDataConsent] = useState(false);
  const [voiceConsent, setVoiceConsent] = useState(false);
  const [emergency, setEmergency] = useState<Schema<"ReceptionSymptom">[]>([]);
  const [urgentConsent, setUrgentConsent] = useState(false);
  const [urgentChild, setUrgentChild] = useState(false);
  const [urgentSymptoms, setUrgentSymptoms] = useState<Schema<"ReceptionSymptom">[]>([]);
  const [working, setWorking] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [creationKind, setCreationKind] = useState<"urgent" | "adaptive" | null>(null);
  const [error, setError] = useState<ErrorKey>(null);

  async function loadDirectory() {
    setDirectoryFailed(false);
    try {
      const { data } = await client.GET("/v1/reception/directory", { signal: AbortSignal.timeout(15_000) });
      if (data) setDirectory(data); else setDirectoryFailed(true);
    } catch { setDirectoryFailed(true); }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (initialized.current) return;
      initialized.current = true;
      const token = new URLSearchParams(window.location.hash.slice(1)).get("t");
      const preferred = new URLSearchParams(window.location.search).get("l");
      if ((preferred === "en" || preferred === "ar") && preferred !== language) { setLocaleCookie(preferred); router.refresh(); }
      if (token) {
        qr.current = token;
        key.current = newClaimKey();
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
        setStage("identity");
        void loadDirectory();
      } else setStage(readToken() && readSession() ? "resume" : "missing");
    }, 0);
    return () => window.clearTimeout(timer);
  }, [language, router]);

  useEffect(() => { heading.current?.focus(); }, [stage]);

  useEffect(() => {
    if (error !== "limited") return;
    const timer = window.setTimeout(() => setError(null), 60_000);
    return () => window.clearTimeout(timer);
  }, [error]);

  const verifiedSelf = recipient === "self" && verified !== null;
  const dependent = recipient !== null && recipient !== "self";
  const detailsReady = patient.name.trim().length >= 2 && patient.name.trim().length <= 100 && patient.age !== "" && Number.isInteger(Number(patient.age)) && Number(patient.age) >= 0 && Number(patient.age) <= 130 && patient.sex !== "" && (recipient !== "child" || Number(patient.age) < 16) && (!dependent || (caregiverName.trim().length >= 2 && caregiverName.trim().length <= 100));
  const lookupReady = lookupConsent && identifier.trim().length >= (kind === "mobile" ? 7 : 3);
  const department = directory?.departments.find((item) => item.id === departmentId);
  const currentYear = new Date().getFullYear();

  function move(next: Stage) { if (!working && !uncertain) { setError(null); setStage(next); } }
  function goNew() { if (working || uncertain) return; setVerified(null); setChallenge(null); setBirthYear(""); setRecipient(null); setPatient({ ...DEFAULT_PATIENT, phone: kind === "mobile" ? identifier.trim() : "" }); move("recipient"); }
  function chooseRecipient(next: Recipient) {
    if (next === recipient) return;
    setRecipient(next);
    if (next === "self") setPatient(verified ? { name: verified.patient.name, age: String(verified.patient.age_years), sex: verified.patient.sex, phone: verified.patient.phone ?? "" } : { ...DEFAULT_PATIENT, phone: kind === "mobile" ? identifier.trim() : "" });
    else {
      setPatient(DEFAULT_PATIENT);
      setCaregiverName(verified?.patient.name ?? "");
      setCaregiverPhone(verified?.patient.phone ?? (kind === "mobile" ? identifier.trim() : ""));
      setRelationship(next === "child" ? "parent" : next === "elder" ? "family" : "carer");
    }
  }
  function requestError(status: number): ErrorKey { return status === 401 || status === 410 ? "expired" : status === 429 ? "limited" : status === 409 ? "busy" : status === 422 ? "invalid" : "unavailable"; }

  async function lookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!lookupReady || uncertain || lock.current || !qr.current || !key.current) return;
    lock.current = true; setWorking(true); setError(null); setVerified(null); setChallenge(null);
    try {
      const { data, response } = await client.POST("/v1/identity/lookup", { signal: AbortSignal.timeout(20_000), body: {
        qr_token: qr.current, claim_key: key.current, kind, value: identifier.trim(),
        consent: { camera: false, voice: false, data_use: true, consent_version: "2026-09-28.1" },
      } });
      if (!data) { setError(requestError(response.status)); return; }
      if (data.status === "match" && data.challenge_token) { setChallenge(data); setBirthYear(""); setStage("verify"); }
      else setError(data.status === "no_match" ? "noMatch" : "unavailable");
    } catch { setError("unavailable"); }
    finally { lock.current = false; setWorking(false); }
  }

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (uncertain || lock.current || !qr.current || !key.current || !challenge?.challenge_token || !/^\d{4}$/.test(birthYear)) return;
    lock.current = true; setWorking(true); setError(null);
    try {
      const { data, response } = await client.POST("/v1/identity/verify", { signal: AbortSignal.timeout(20_000), body: { qr_token: qr.current, claim_key: key.current, challenge_token: challenge.challenge_token, birth_year: Number(birthYear) } });
      if (!data) { setError(response.status === 429 ? "limited" : response.status >= 500 ? "unavailable" : "verificationFailed"); return; }
      setVerified(data); setRecipient(null); setStage("recipient");
    } catch { setError("unavailable"); }
    finally { lock.current = false; setWorking(false); }
  }

  async function urgentHelp() {
    if (!urgentConsent || lock.current || submitted.current || !qr.current || !key.current) return;
    lock.current = true; setWorking(true); setError(null); setUncertain(true);
    const body: Schema<"UrgentCheckinRequest"> = urgentSubmitted.current ?? {
      qr_token: qr.current, claim_key: key.current, language, patient_type: urgentChild ? "child" : "adult",
      emergency_symptoms: urgentSymptoms,
      consent: { camera: false, voice: false, data_use: true, consent_version: "2026-09-28.1" },
    };
    urgentSubmitted.current = body; setCreationKind("urgent");
    try {
      const { data, response } = await client.POST("/v1/checkin/urgent", { signal: AbortSignal.timeout(20_000), body });
      if (!data) {
        setError(requestError(response.status));
        if (response.status < 500 && response.status !== 409) { urgentSubmitted.current = null; setUncertain(false); setCreationKind(null); }
        return;
      }
      saveSession({ sessionId: data.session_id, display: data.patient_display, language: data.language, patientType: body.patient_type });
      saveToken(data.session_token); setLocaleCookie(data.language); window.location.replace("/status");
    } catch { setError("unavailable"); }
    finally { lock.current = false; setWorking(false); }
  }

  async function begin() {
    if (lock.current || urgentSubmitted.current || !qr.current || !key.current || !recipient || !detailsReady || !patient.sex || !dataConsent) return;
    lock.current = true; setWorking(true); setError(null); setUncertain(true);
    const body: Schema<"AdaptiveCheckinRequest"> = submitted.current ?? {
      qr_token: qr.current, claim_key: key.current, language, visit_for: recipient,
      patient: { name: patient.name.trim(), age_years: Number(patient.age), sex: patient.sex, ...(patient.phone.trim() ? { phone: patient.phone.trim() } : {}) },
      ...(verifiedSelf ? { patient_identity_token: verified!.verification_token } : {}),
      ...(dependent ? { caregiver: { name: caregiverName.trim(), relationship, ...(caregiverPhone.trim() ? { phone: caregiverPhone.trim() } : {}), ...(verified ? { identity_token: verified.verification_token } : {}) } } : {}),
      consent: { camera: false, voice: voiceConsent, data_use: true, consent_version: "2026-09-28.1" },
      department_id: departmentId, ...(doctorId ? { doctor_id: doctorId } : {}), emergency_symptoms: emergency,
    };
    submitted.current = body; setCreationKind("adaptive");
    try {
      const { data, response } = await client.POST("/v1/checkin/adaptive", { signal: AbortSignal.timeout(40_000), body });
      if (!data) {
        setError(requestError(response.status));
        if (response.status < 500 && response.status !== 409) { submitted.current = null; setUncertain(false); setCreationKind(null); }
        return;
      }
      saveSession({ sessionId: data.session_id, display: data.patient_display, language: data.language, reception: true, patientType: Number(patient.age) < 16 ? "child" : "adult", voiceConsent });
      saveToken(data.session_token); setLocaleCookie(data.language);
      window.location.replace("/");
    } catch { setError("unavailable"); }
    finally { lock.current = false; setWorking(false); }
  }

  if (stage === "resume") return <ClaimForm />;
  if (stage === "loading") return <div className="companion-loading" role="status"><span className="companion-spinner" aria-hidden="true" /><p>{tStart("loading")}</p></div>;
  if (stage === "missing") return <section className="companion-panel"><h1 className="companion-recovery-title">{tStart("missingTitle")}</h1><p className="companion-muted">{tStart("missing")}</p><Link href="/" className="companion-text-link">{tStart("home")}</Link></section>;
  const step = stage === "verify" ? 0 : STEPS.indexOf(stage);
  const back: Stage | null = stage === "verify" ? "identity" : step > 0 ? STEPS[step - 1]! : null;
  const titleStage = stage;

  return <div className="companion-intake reception-phone">
    <div className="companion-page-heading"><span className="companion-eyebrow">{HOSPITAL}</span><h1 ref={heading} tabIndex={-1}>{t(`${titleStage}Title`)}</h1><p>{t(`${titleStage}Intro`)}</p></div>
    <div className="reception-phone-progress"><p>{r("step", { current: step + 1, total: STEPS.length })}<strong>{t(`${STEPS[step]!}Step`)}</strong></p><ol aria-label={r("progress")}>{STEPS.map((item, index) => <li key={item} aria-current={index === step ? "step" : undefined} aria-label={t(`${item}Step`)} className={index <= step ? "is-current" : ""} />)}</ol></div>
    <section className="companion-panel reception-form-panel">
      {stage === "identity" ? <div className="companion-consent-content">
        {verified ? <div className="companion-next"><CompanionIcon name="check" /><div><strong>{t("recordVerified")}</strong><p dir="auto">{verified.patient.name}</p><button type="button" className="companion-text-link" onClick={() => move("recipient")}>{r("continue")}</button></div></div> : null}
        <form className="companion-form reception-fields" onSubmit={(event) => void lookup(event)}>
          <div className="identity-methods" role="group" aria-label={t("lookupMethod")}><button type="button" className="companion-choice" aria-pressed={kind === "mobile"} onClick={() => { setKind("mobile"); setIdentifier(""); setError(null); }}>{t("mobile")}</button><button type="button" className="companion-choice" aria-pressed={kind === "patient_id"} onClick={() => { setKind("patient_id"); setIdentifier(""); setError(null); }}>{t("patientId")}</button></div>
          <label>{t(kind === "mobile" ? "mobile" : "patientId")}<input className="companion-input" type={kind === "mobile" ? "tel" : "text"} autoComplete="off" maxLength={64} value={identifier} onChange={(event) => setIdentifier(event.target.value)} placeholder={t(kind === "mobile" ? "mobilePlaceholder" : "idPlaceholder")} dir="ltr" disabled={working} /></label>
          <label className="companion-consent-checkbox"><input type="checkbox" checked={lookupConsent} onChange={(event) => setLookupConsent(event.target.checked)} disabled={working} /><span>{t("lookupPermission")}</span></label>
          <button type="submit" className="companion-primary" disabled={!lookupReady || working || uncertain || error === "limited" || error === "expired"}>{working ? t("finding") : t("findRecord")}</button>
        </form>
        <div className="identity-new-patient"><p>{t("newPatientHint")}</p><button type="button" className="companion-choice" disabled={working} onClick={goNew}>{t("newPatient")}</button></div>
        <p className="companion-field-hint">{t("identityBoundary")}</p>
      </div> : null}
      {stage === "verify" ? <form className="companion-form reception-fields" onSubmit={(event) => void verify(event)}>
        <div className="companion-next"><CompanionIcon name="shield" /><div><strong>{t("possibleMatch")}</strong><p dir="auto">{challenge?.masked_name}</p><p>{t("verifyHint")}</p></div></div>
        <label>{t("birthYear")}<input className="companion-input" inputMode="numeric" pattern="[0-9]{4}" maxLength={4} value={birthYear} onChange={(event) => setBirthYear(event.target.value.replace(/\D/g, "").slice(0, 4))} autoComplete="off" placeholder="YYYY" dir="ltr" disabled={working} /></label>
        <button type="submit" className="companion-primary" disabled={working || uncertain || birthYear.length !== 4 || Number(birthYear) < 1890 || Number(birthYear) > currentYear || error === "limited"}>{working ? t("verifying") : t("verify")}</button><button type="button" className="companion-text-link" onClick={goNew} disabled={working}>{t("notMyRecord")}</button>
      </form> : null}
      {stage === "recipient" ? <div className="companion-consent-content">
        {verified ? <p className="identity-verified-name"><CompanionIcon name="check" />{t("verifiedAs", { name: verified.patient.name })}</p> : null}
        <div className="identity-recipients" role="group" aria-label={t("recipientTitle")}>{RECIPIENTS.map((value) => <button key={value} type="button" className="companion-choice" aria-pressed={recipient === value} onClick={() => chooseRecipient(value)}><CompanionIcon name={value === "self" ? "medical" : "family"} /><strong>{t(`recipients.${value}.label`)}</strong><span>{t(`recipients.${value}.hint`)}</span></button>)}</div>
        <button type="button" className="companion-primary" disabled={!recipient} onClick={() => move("details")}>{r("continue")}</button>
      </div> : null}
      {stage === "details" ? <form className="companion-form reception-fields" onSubmit={(event) => { event.preventDefault(); if (detailsReady) move("preferences"); }}>
        {verifiedSelf ? <div className="companion-next"><CompanionIcon name="shield" /><div><strong>{t("recordVerified")}</strong><p>{t("prefilledHint")}</p></div></div> : dependent ? <p className="companion-notice">{t("dependentHint")}</p> : null}
        <label>{r("name")}<input className="companion-input" value={patient.name} onChange={(event) => setPatient({ ...patient, name: event.target.value })} maxLength={100} autoComplete="off" required readOnly={verifiedSelf} /></label>
        <div className="reception-field-pair"><label>{r("age")}<input className="companion-input" type="number" inputMode="numeric" min={0} max={recipient === "child" ? 15 : 130} step={1} value={patient.age} onChange={(event) => setPatient({ ...patient, age: event.target.value })} required readOnly={verifiedSelf} /></label><label>{r("sex")}<select className="companion-input" value={patient.sex} onChange={(event) => setPatient({ ...patient, sex: event.target.value as Patient["sex"] })} required disabled={verifiedSelf}><option value="">{r("choose")}</option>{(["female", "male", "other", "unknown"] as const).map((value) => <option key={value} value={value}>{r(`sexOptions.${value}`)}</option>)}</select></label></div>
        <label>{r("phone")}<input className="companion-input" type="tel" value={patient.phone} maxLength={30} autoComplete="off" readOnly={verifiedSelf} onChange={(event) => setPatient({ ...patient, phone: event.target.value })} /></label>
        {verifiedSelf ? <div className="identity-history"><h2>{t("recordSummary")}</h2><p className="companion-field-hint">{verified.history.status === "ok" ? t("historyHint") : t("historyPartial")}</p>{(["allergies", "conditions", "medications"] as const).map((field) => <div key={field}><strong>{t(`history.${field}`)}</strong><p dir="auto">{verified.history[field].length ? verified.history[field].join(" · ") : t("notRecorded")}</p></div>)}<button type="button" className="companion-text-link" onClick={() => { setVerified(null); setPatient(DEFAULT_PATIENT); }}>{t("differentDetails")}</button></div> : null}
        {dependent ? <fieldset className="identity-caregiver"><legend>{t("caregiverTitle")}</legend><label>{t("caregiverName")}<input className="companion-input" value={caregiverName} maxLength={100} required readOnly={verified !== null} onChange={(event) => setCaregiverName(event.target.value)} /></label><label>{t("relationship")}<select className="companion-input" value={relationship} onChange={(event) => setRelationship(event.target.value as typeof relationship)}>{(["parent", "family", "carer", "other"] as const).map((value) => <option key={value} value={value}>{t(`relationships.${value}`)}</option>)}</select></label><label>{t("caregiverPhone")}<input className="companion-input" type="tel" value={caregiverPhone} maxLength={30} onChange={(event) => setCaregiverPhone(event.target.value)} /></label></fieldset> : null}
        <p className="companion-field-hint">{r("syntheticHint")}</p><button type="submit" className="companion-primary" disabled={!detailsReady}>{verifiedSelf ? t("confirmRecord") : r("continue")}</button>
      </form> : null}
      {stage === "preferences" ? <div className="companion-consent-content">
        <button type="button" className="companion-choice identity-unsure" aria-pressed={departmentId === "unsure"} onClick={() => { setDepartmentId("unsure"); setDoctorId(""); }}><CompanionIcon name="medical" /><span><strong>{t("unsureDepartment")}</strong><small>{t("unsureHint")}</small></span>{departmentId === "unsure" ? <CompanionIcon name="check" /> : null}</button>
        <details className="identity-directory"><summary>{t("chooseDepartment")}</summary>{directory ? <label className="reception-label">{r("department")}<select className="companion-input" value={departmentId} onChange={(event) => { setDepartmentId(event.target.value); setDoctorId(""); }}><option value="unsure">{t("unsureDepartment")}</option>{directory.departments.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : directoryFailed ? <div role="alert"><p>{r("directoryFailed")}</p><button type="button" className="companion-text-link" onClick={() => void loadDirectory()}>{r("retry")}</button></div> : <p role="status">{r("directoryLoading")}</p>}
        {department ? <label className="reception-label">{r("doctor")}<select className="companion-input" value={doctorId} onChange={(event) => setDoctorId(event.target.value)}><option value="">{r("anyDoctor")}</option>{department.doctors.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : null}<p className="companion-field-hint">{r("demoDirectory")}</p></details>
        <button type="button" className="companion-primary" onClick={() => move("consent")}>{r("continue")}</button>
      </div> : null}
      {stage === "consent" ? <div className="companion-consent-content">
        <div className="identity-visit-summary"><CompanionIcon name="medical" /><div><strong dir="auto">{patient.name}</strong><p>{t(`recipients.${recipient ?? "self"}.label`)} · {r("ageSummary", { age: Number(patient.age) })}</p>{dependent ? <small>{t("supportedBy", { name: caregiverName })}</small> : null}</div></div>
        <p>{t("adaptiveIntro")}</p><label className="companion-consent-checkbox"><input type="checkbox" checked={dataConsent} disabled={working || uncertain} onChange={(event) => setDataConsent(event.target.checked)} /><span>{t("dataPermission")}</span></label>
        <label className="companion-consent-checkbox"><input type="checkbox" checked={voiceConsent} disabled={working || uncertain} onChange={(event) => setVoiceConsent(event.target.checked)} /><span><strong>{t("voicePermission")}</strong><small>{t("voiceHint")}</small></span></label>
        <details className="identity-urgent"><summary><CompanionIcon name="alert" />{t("urgentBeforeStart")}</summary><p>{t("urgentHint")}</p>{EMERGENCY.map((item) => <label key={item}><input type="checkbox" checked={emergency.includes(item)} disabled={working || uncertain} onChange={() => setEmergency((items) => items.includes(item) ? items.filter((value) => value !== item) : [...items, item])} /><span>{r(`symptoms.${item}`)}</span></label>)}</details>
        {error && uncertain ? <p className="companion-field-hint">{t("retryLocked")}</p> : null}<button type="button" className="companion-primary" disabled={!dataConsent || working || (creationKind === "urgent") || error === "expired" || error === "limited"} onClick={() => void begin()}>{working ? t("starting") : uncertain ? t("retryStart") : emergency.length ? t("alertAndStart") : t("startConversation")}</button>
        <p className="companion-field-hint">{t("clinicalBoundary")}</p>
      </div> : null}
      {error ? <p role="alert" className="companion-notice companion-error">{error === "noMatch" || error === "verificationFailed" || error === "invalid" ? t(error) : tStart(error)}</p> : null}
      {back ? <button type="button" className="companion-text-link reception-back" disabled={working || uncertain} onClick={() => move(back)}>{r("back")}</button> : null}
    </section>
    <details className="identity-urgent">
      <summary><CompanionIcon name="alert" />{t("earlyUrgent.title")}</summary>
      <p>{t("earlyUrgent.intro")}</p>
      <label><input type="checkbox" checked={urgentChild} disabled={working || uncertain} onChange={(event) => setUrgentChild(event.target.checked)} /><span>{t("earlyUrgent.child")}</span></label>
      <details><summary>{t("earlyUrgent.symptoms")}</summary>{EMERGENCY.map((item) => <label key={item}><input type="checkbox" checked={urgentSymptoms.includes(item)} disabled={working || uncertain} onChange={() => setUrgentSymptoms((items) => items.includes(item) ? items.filter((value) => value !== item) : [...items, item])} /><span>{r(`symptoms.${item}`)}</span></label>)}</details>
      <label><input type="checkbox" checked={urgentConsent} disabled={working || uncertain} onChange={(event) => setUrgentConsent(event.target.checked)} /><span>{t("earlyUrgent.permission")}</span></label>
      <button type="button" className="companion-danger-button" disabled={!urgentConsent || working || (creationKind === "adaptive") || error === "expired" || error === "limited"} onClick={() => void urgentHelp()}>{working ? t("earlyUrgent.sending") : creationKind === "urgent" ? t("earlyUrgent.retry") : t("earlyUrgent.send")}</button>
      {creationKind === "urgent" && error ? <p role="alert">{t("earlyUrgent.failed")}</p> : null}
    </details>
    <p className="reception-emergency-help"><CompanionIcon name="alert" /><span>{r("emergencyHelp")}</span></p>
  </div>;
}

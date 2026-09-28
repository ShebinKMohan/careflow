"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

import { CompanionIcon } from "./CompanionIcon";

type Chest = "chest_pain" | "short_of_breath";
type Fast = "face_droop" | "arm_weakness" | "speech_difficulty";
type Drowsy = "yes" | "no" | "not_sure";

const CHEST: readonly Chest[] = ["chest_pain", "short_of_breath"];
const FAST: readonly Fast[] = ["face_droop", "arm_weakness", "speech_difficulty"];
const DROWSY: readonly Drowsy[] = ["yes", "no", "not_sure"];

const TOGGLE = "companion-alert-choice";
const DANGER = "companion-danger-button";

/**
 * "I feel worse" (M2-07): a sticky red button, then one screen with three quick danger questions
 * and big buttons. Every press is sent (POST /v1/companion/feels-worse): the nurse hears of it at
 * once and the safety rules run again. The patient only ever sees "the nurse has been told", or,
 * if the phone cannot reach the clinic, to tell the reception desk now. No advice, ever.
 */
export function FeelsWorse({ childSession }: { childSession: boolean }) {
  const t = useTranslations("safety.feelsWorse");
  const tChild = useTranslations("safety.child");
  const [open, setOpen] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const sendLock = useRef(false);

  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    const trigger = triggerRef.current;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    return () => {
      document.body.style.overflow = previous;
      trigger?.focus();
    };
  }, [open]);
  const [chest, setChest] = useState<Chest[]>([]);
  const [fast, setFast] = useState<Fast[]>([]);
  const [drowsy, setDrowsy] = useState<Drowsy | null>(null);
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<"told" | "failed" | null>(null);

  useEffect(() => {
    if (result === "told") dialogRef.current?.focus();
  }, [result]);

  function toggle<T>(list: T[], item: T): T[] {
    return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
  }

  async function send() {
    if (sendLock.current) return;
    sendLock.current = true;
    setSending(true);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 10_000);
    try {
      const { data } = await api.POST("/v1/companion/feels-worse", {
        signal: controller.signal,
        body: {
          answers: { chest_or_breathing: chest, face_arm_speech: fast, drowsy_or_confused: drowsy ?? "not_sure" },
          ...(note.trim() ? { note: note.trim().slice(0, 280) } : {}),
        },
      });
      setResult(data ? "told" : "failed");
    } catch {
      setResult("failed");
    } finally {
      window.clearTimeout(timer);
      setSending(false);
      sendLock.current = false;
    }
  }

  function reset() {
    setOpen(false);
    setChest([]);
    setFast([]);
    setDrowsy(null);
    setNote("");
    setResult(null);
  }

  return (
    <>
      <div className="companion-alert-spacer" aria-hidden="true" />
      <div className="companion-alert-bar">
        <div className="companion-alert-bar-inner"><p>{t("support")}</p>
        <button ref={triggerRef} type="button" className={DANGER} onClick={() => setOpen(true)} data-testid="feels-worse">
          <CompanionIcon name="alert" />{t("button")}
        </button>
        </div>
      </div>
      {open ? (
        <div className="companion-dialog-backdrop">
        <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="nurse-alert-title" className="companion-dialog" onKeyDown={(event) => {
          if (event.key === "Escape" && !sending) { event.preventDefault(); reset(); }
          if (event.key !== "Tab") return;
          const controls = dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), textarea, [href]');
          if (!controls?.length) return;
          const first = controls[0];
          const last = controls[controls.length - 1];
          if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }}>
          <div className="companion-dialog-content">
            <h2 id="nurse-alert-title">{t("title")}</h2>
            {result === "told" ? (
              <>
                <p role="status" className="text-title font-semibold" data-testid="feels-worse-told">
                  {t("told")}
                </p>
                <button type="button" className={DANGER} onClick={reset}>
                  {t("close")}
                </button>
              </>
            ) : (
              <>
                <p className="companion-muted">{t("selectHint")}</p>
                <fieldset className="flex flex-col gap-2">
                  <legend className="mb-2 font-semibold">{t("chestQuestion")}</legend>
                  <div className="flex flex-wrap gap-2">
                    {CHEST.map((item) => (
                      <button key={item} type="button" className={TOGGLE} disabled={sending} aria-pressed={chest.includes(item)} onClick={() => setChest(toggle(chest, item))}>
                        {t(item)}
                      </button>
                    ))}
                  </div>
                </fieldset>
                <fieldset className="flex flex-col gap-2">
                  <legend className="mb-2 font-semibold">{t("fastQuestion")}</legend>
                  <div className="flex flex-wrap gap-2">
                    {FAST.map((item) => (
                      <button key={item} type="button" className={TOGGLE} disabled={sending} aria-pressed={fast.includes(item)} onClick={() => setFast(toggle(fast, item))}>
                        {t(item)}
                      </button>
                    ))}
                  </div>
                </fieldset>
                <fieldset className="flex flex-col gap-2">
                  <legend className="mb-2 font-semibold">{childSession ? tChild("drowsyQuestion") : t("drowsyQuestion")}</legend>
                  <div className="flex flex-wrap gap-2">
                    {DROWSY.map((item) => (
                      <button key={item} type="button" className={TOGGLE} disabled={sending} aria-pressed={drowsy === item} onClick={() => setDrowsy(item)}>
                        {t(item)}
                      </button>
                    ))}
                  </div>
                </fieldset>
                <label className="flex flex-col gap-2 font-semibold">
                  {t("note")}
                  <textarea
                    value={note}
                    disabled={sending}
                    maxLength={280}
                    onChange={(event) => setNote(event.target.value)}
                    rows={2}
                    className="companion-input"
                  />
                </label>
                {result === "failed" ? (
                  <p role="alert" className="text-title font-bold text-danger" data-testid="feels-worse-failed">
                    {t("failed")}
                  </p>
                ) : null}
                <button type="button" className={DANGER} disabled={sending} onClick={() => void send()}>
                  {sending ? t("sending") : t("send")}
                </button>
                <button type="button" disabled={sending} className="min-h-touch font-semibold text-primary underline" onClick={reset}>
                  {t("cancel")}
                </button>
              </>
            )}
            <p className="companion-dialog-footer">{t("prototype")}</p>
          </div>
        </div>
        </div>
      ) : null}
    </>
  );
}

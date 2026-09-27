"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { api } from "@/lib/api";

type Chest = "chest_pain" | "short_of_breath";
type Fast = "face_droop" | "arm_weakness" | "speech_difficulty";
type Drowsy = "yes" | "no" | "not_sure";

const CHEST: readonly Chest[] = ["chest_pain", "short_of_breath"];
const FAST: readonly Fast[] = ["face_droop", "arm_weakness", "speech_difficulty"];
const DROWSY: readonly Drowsy[] = ["yes", "no", "not_sure"];

const TOGGLE =
  "min-h-touch rounded-control border-2 border-danger px-4 text-body font-semibold text-danger aria-pressed:bg-danger aria-pressed:text-on-danger";
const DANGER = "min-h-touch w-full rounded-control bg-danger px-6 text-title font-bold text-on-danger disabled:opacity-60";

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
  const [chest, setChest] = useState<Chest[]>([]);
  const [fast, setFast] = useState<Fast[]>([]);
  const [drowsy, setDrowsy] = useState<Drowsy | null>(null);
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<"told" | "failed" | null>(null);

  function toggle<T>(list: T[], item: T): T[] {
    return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
  }

  async function send() {
    setSending(true);
    try {
      const { data } = await api.POST("/v1/companion/feels-worse", {
        body: {
          answers: { chest_or_breathing: chest, face_arm_speech: fast, drowsy_or_confused: drowsy ?? "no" },
          ...(note.trim() ? { note: note.trim().slice(0, 280) } : {}),
        },
      });
      setResult(data ? "told" : "failed");
    } catch {
      setResult("failed");
    }
    setSending(false);
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
      <div className="h-24" aria-hidden="true" />
      <div className="fixed inset-x-0 bottom-0 z-30 border-t-2 border-border bg-surface p-3">
        <button type="button" className={DANGER} onClick={() => setOpen(true)} data-testid="feels-worse">
          {t("button")}
        </button>
      </div>
      {open ? (
        <div role="dialog" aria-modal="true" aria-label={t("title")} className="fixed inset-0 z-40 overflow-y-auto bg-surface p-4">
          <div className="mx-auto flex max-w-xl flex-col gap-5">
            <h2 className="text-title font-bold">{t("title")}</h2>
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
                <fieldset className="flex flex-col gap-2">
                  <legend className="mb-2 font-semibold">{t("chestQuestion")}</legend>
                  <div className="flex flex-wrap gap-2">
                    {CHEST.map((item) => (
                      <button key={item} type="button" className={TOGGLE} aria-pressed={chest.includes(item)} onClick={() => setChest(toggle(chest, item))}>
                        {t(item)}
                      </button>
                    ))}
                  </div>
                </fieldset>
                <fieldset className="flex flex-col gap-2">
                  <legend className="mb-2 font-semibold">{t("fastQuestion")}</legend>
                  <div className="flex flex-wrap gap-2">
                    {FAST.map((item) => (
                      <button key={item} type="button" className={TOGGLE} aria-pressed={fast.includes(item)} onClick={() => setFast(toggle(fast, item))}>
                        {t(item)}
                      </button>
                    ))}
                  </div>
                </fieldset>
                <fieldset className="flex flex-col gap-2">
                  <legend className="mb-2 font-semibold">{childSession ? tChild("drowsyQuestion") : t("drowsyQuestion")}</legend>
                  <div className="flex flex-wrap gap-2">
                    {DROWSY.map((item) => (
                      <button key={item} type="button" className={TOGGLE} aria-pressed={drowsy === item} onClick={() => setDrowsy(item)}>
                        {t(item)}
                      </button>
                    ))}
                  </div>
                </fieldset>
                <label className="flex flex-col gap-2 font-semibold">
                  {t("note")}
                  <textarea
                    value={note}
                    maxLength={280}
                    onChange={(event) => setNote(event.target.value)}
                    rows={2}
                    className="rounded-control border-2 border-border bg-surface p-3 text-body font-normal"
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
                <button type="button" className="min-h-touch font-semibold text-primary underline" onClick={reset}>
                  {t("cancel")}
                </button>
              </>
            )}
          </div>
        </div>
      ) : null}
    </>
  );
}

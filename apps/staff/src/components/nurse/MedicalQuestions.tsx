"use client";

import type { Schema } from "@careflow/contracts";
import { useFormatter, useTranslations } from "next-intl";

type Item = Schema<"WorklistItem">;

/**
 * Medical questions a waiting patient asked on their phone (M2-09). The assistant refused them
 * with a fixed message and told the patient the nurse will speak with them: it never answers a
 * medical question (safety rule 2). The question is the patient's own words, newest first. A flag,
 * not an alarm: no sound, no vibration, no pulse.
 */
export function MedicalQuestions({ item }: { item: Item }) {
  const t = useTranslations("safety.medicalQuestions");
  const format = useFormatter();
  const questions = item.medical_questions ?? [];
  if (!questions.length) return null;
  return (
    <div
      className="flex flex-col gap-2 rounded-card border-4 border-prov-patient bg-surface p-4"
      data-testid="medical-questions"
      data-session-id={item.session_id}
    >
      <p className="text-title font-bold text-prov-patient">
        {t.rich("title", { patient: item.token_code ?? item.patient_display, count: questions.length, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}
      </p>
      <ul className="flex flex-col gap-2">
        {questions.map((question) => (
          <li key={question.flag_id} className="rounded-control bg-prov-patient-tint p-2">
            <q dir="auto" lang={question.language}>
              {question.question}
            </q>
            <span className="block text-label text-ink-muted">
              {t("asked", {
                time: format.dateTime(new Date(question.asked_at), { timeStyle: "short", numberingSystem: "latn" }),
                language: t(`language.${question.language}`),
              })}
            </span>
          </li>
        ))}
      </ul>
      <p className="text-label text-ink-muted">{t("refused")}</p>
    </div>
  );
}

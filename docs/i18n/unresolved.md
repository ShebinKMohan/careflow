# Unresolved Arabic safety strings (M2-01)

A `safety.` string flagged by the back-translation check (`scripts/i18n/backtranslate.py`) is
rewritten in simpler Modern Standard Arabic up to five times. If it is still flagged after that,
the UI shows the English original beside the Arabic, and the key is listed here. `--check` (run by
`make lint`) passes a flagged `safety.` key only when it is listed here and its Arabic contains its
English original.

This is a machine check, not a review. No native speaker has read these strings, and no accuracy
figure is claimed. Follow-up: SHE-324, a native-speaker review of these five strings ("needs-owner").

Checked 27 September 2026 by `qwen3.5-9b-4bit`
(`mlx-community/Qwen3.5-9B-MLX-4bit@938d8919941c6e7efd3c7150eff7fe9d12afa631`, task
`translation_check`). A string is flagged when the model judges that the back-translation means
something else, or when chrF < 40. Each attempt below gives the Arabic tried, then what the model
translated it back to.

## `kiosk:safety.child.me`

English "Me" (the answer to "Who is the patient?"). Shown: "أنا (Me)".

1. أنا نفسي: "Myself" (chrF 35.7; judged different, "Me vs Myself").
2. أنا: "I" (chrF 0.0).
3. نفسي: "Psychological" (نفسي also means "psychological").
4. أنا المريض: "I am the patient" (chrF 11.9; judged "Subject mismatch").
5. أنا بنفسي: "Myself" (chrF 35.7; judged "Pronoun reference").

Reason: a two-letter English label cannot reach chrF 40 unless the back-translation is exactly
"Me". The Arabic answer to the question is "أنا" ("I"), which is what the screen keeps.

## `staff:safety.alerts.ecgDone`

English "ECG done". Shown: "تم إجراء تخطيط القلب (ECG done)".

1. تم إجراء تخطيط القلب: "ECG performed" (chrF 20.1; same meaning).
2. تخطيط القلب: تم: "ECG: Done" (chrF 31.1; same meaning).
3. تخطيط القلب منجز: "ECG completed" (chrF 20.1; same meaning).
4. تم عمل تخطيط القلب: "ECG performed" (chrF 20.1; same meaning).
5. تخطيط القلب تم: "ECG completed" (chrF 20.1; same meaning).

Reason: every attempt was judged to mean the same, but the model always says "performed" or
"completed", never "done", so chrF stays under 40. Attempt 1 is shown.

## `staff:safety.alerts.overdue`

English "Overdue +{time}" (the ECG deadline has passed). Shown:
"متجاوز للمهلة +{time} (Overdue +{time})".

1. تجاوز الموعد +{time}: "Skip appointment +{time}" (judged different: the verb reads as an
   order).
2. فات موعده +{time}: "Missed appointment +{time}" (judged different).
3. متجاوز للمهلة +{time}: "Timeout exceeded +{time}" (chrF 37.5; same meaning).
4. متأخر عن الموعد +{time}: "Late for appointment +{time}" (chrF 34.9; same meaning).
5. موعده فائت +{time}: "Missed appointment +{time}" (judged different, "Appointment vs time").

The original "متأخر +{time}" came back as "Delayed +{time}" and was judged different.
Attempt 3 is shown.

## `staff:safety.alerts.rules.NG143_AMBER_FLAG`

English "Child amber flag" (NICE NG143 traffic light: amber). Shown:
"علامة بلون الكهرمان لدى الطفل (Child amber flag)".

1. علامة كهرمانية لدى الطفل: "Seizure sign in child" (judged different).
2. علامة عنبرية لدى الطفل: "A tick mark on the child" (judged different).
3. علامة بلون الكهرمان لدى الطفل: "Amber mark on the child" (chrF 25.1; same meaning).
4. علامة الطفل الكهرمانية: "Child's electrocardiogram" (judged different).
5. علامة برتقالية (كهرمانية) لدى الطفل: "Orange (amber) mark on the child" (chrF 29.2; same
   meaning).

The original "علامة برتقالية لدى طفل" came back as "Orange mark on a child" and was judged a
colour mismatch. "كهرماني" (amber) shares its root with electricity, and the model misread it
twice. Attempt 3 is shown: it spells the colour out and was judged to mean the same.

## `staff:safety.triage.warnings.intake_stopped_early`

English "Intake stopped early" (the patient's intake questions ended before the end). Shown:
"توقف استبيان الاستقبال مبكراً (Intake stopped early)".

1. توقفت الأسئلة الأولية مبكراً: "Initial questions stopped early" (judged different, "Intake vs
   Initial questions").
2. توقف استبيان الاستقبال مبكراً: "Intake survey stopped early" (chrF 81.7; judged different,
   "survey").
3. توقفت أسئلة الاستقبال مبكراً: "Reception questions stopped early" (judged different).
4. توقف الاستقبال الطبي مبكراً: "Medical reception closed early" (judged different).
5. توقف التقييم الأولي مبكراً: "Initial assessment stopped early" (judged different).

The original "توقف الاستقبال مبكراً" came back as "Check-in stopped early" (الاستقبال is also the
reception desk). Arabic has no single word for "intake", and the model rejected every
description. Attempt 2 is shown: its back-translation keeps "intake".

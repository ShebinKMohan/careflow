"""Seeded gold facts for the Arabic intake scenarios (M2-03, SHE-290): step A of generation.

The gold facts, the question each answer replies to, the patient's age and the vitals are
sampled here, in code, from fixed per-category templates. The writer only puts them into words:
for each planned answer, the Arabic patient text in the scenario's dialect, an English gloss (the
reference its blind back-translation is checked against) and the keys it states (`states`,
checked by the loader). The gold never comes from the model or person who writes the dialogue,
and the expected alerts come from the rules engine on the gold facts, never from the writer.

Every rule id is covered at least 3 times across the two dialects (the plan's own test): chest
pain, FAST, sepsis (NEWS2 from the device and nurse vitals), and the child red, amber and
hard-to-wake rules. Research prototype; synthetic patients only.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Any, Final

from careflow_contracts.generated.structured_facts_schema import FactKey

CATEGORY_MIX: Final = {
    "chest_pain": 8,
    "stroke": 6,
    "sepsis": 8,
    "child": 6,
    "breathing": 4,
    "routine": 18,
}
DIALECTS: Final = ("gulf", "levantine")

# Routine visits: (English rendering of the complaint, pain location or None, symptom or None).
# The complaint states exactly these: pain (with its place) and/or one symptom, as the English
# corpus's gold does ("Blister on my left foot" is a symptom and a pain in the foot).
ROUTINE: Final = [
    ("Pain in my left wrist", "left wrist", None),
    ("A sore ankle", "right ankle", None),
    ("My knee hurts", "knee", None),
    ("Shoulder pain", "shoulder", None),
    ("A painful finger", "finger", None),
    ("Lower back pain", "lower back", None),
    ("Dry, itchy skin", None, "dry itchy skin"),
    ("An itchy scalp", None, "itchy scalp"),
    ("A blocked ear", None, "blocked ear"),
    ("Bloating after meals", None, "bloating"),
    ("Constipation", None, "constipation"),
    ("A sore throat", "throat", "sore throat"),
    ("A runny nose", None, "runny nose"),
    ("A mild headache", "head", "headache"),
    ("A blister on my foot", "foot", "blister"),
    ("Toothache", "tooth", None),
    ("A painful heel", "heel", None),
    ("Heartburn after eating", None, "heartburn"),
]
ONSETS: Final = [
    "Since this morning",
    "Since yesterday",
    "Two days ago",
    "Three days ago",
    "Since last night",
    "About a week ago",
    "An hour ago",
    "Since the afternoon",
]
LAST_KNOWN_WELL: Final = [
    "About an hour ago",
    "Half an hour ago",
    "Before breakfast",
    "Twenty minutes ago",
    "unknown",
]
ALLERGIES: Final = ["No known medicine allergies", "Allergic to penicillin", "Allergic to aspirin"]
# Kiosk device vitals by NEWS2 band: at or above 5 with an infection alerts (SEPSIS_NEWS2).
SEPSIS_HIGH: Final = [
    {"heart_rate": 125, "spo2": 93, "temperature_c": 39.4},
    {"heart_rate": 120, "spo2": 93, "temperature_c": 38.5},
    {"heart_rate": 135, "spo2": 93, "temperature_c": 37.0},
    {"heart_rate": 118, "spo2": 94, "temperature_c": 39.2},
]
SEPSIS_LOW: Final = [
    {"heart_rate": 96, "spo2": 97, "temperature_c": 38.2},
    {"heart_rate": 88, "spo2": 98, "temperature_c": 37.9},
]
SEPSIS_NURSE: Final = {
    "respiratory_rate": 24,
    "systolic_bp": 100,
    "acvpu": "A",
    "on_supplemental_o2": "no",  # a tri-state fact, as in the English corpus
}
BREATHING: Final = [
    {"heart_rate": 102, "spo2": 95, "temperature_c": 37.1},
    {"heart_rate": 88, "spo2": 97, "temperature_c": 36.7},
    {"heart_rate": 110, "spo2": 94, "temperature_c": 36.9},
]


def _plan(category: str, index: int, rng: random.Random) -> dict[str, Any]:
    """One scenario's age, sex, vitals, and ordered answers (question key, gold values)."""
    sex = rng.choice(["male", "female"])
    device: dict[str, Any] = {}
    nurse: dict[str, Any] = {}
    answers: list[tuple[str, dict[str, Any]]] = []
    if category == "chest_pain":
        age = rng.randint(38, 78)
        negative = index == 7  # one screen-negative case per dialect
        present = "no" if negative else "yes"
        # A complaint also states the facts it names: here the chest pain and its place.
        complaint: dict[str, Any] = (
            {"chief_complaint": "I feel unwell after a meal"}
            if negative
            else {
                "chief_complaint": "Tightness in my chest",
                "chest_pain.present": "yes",
                "pain_location": "chest",
            }
        )
        answers = [
            ("chief_complaint", complaint),
            ("chest_pain.present", {"chest_pain.present": present}),
        ]
        if not negative:
            answers += [
                (
                    "chest_pain.radiates",
                    {"chest_pain.radiates": rng.choice(["yes", "no", "unknown"])},
                ),
                ("chest_pain.sweating", {"chest_pain.sweating": rng.choice(["yes", "no"])}),
                ("pain_score", {"pain_score": rng.randint(4, 9), "pain_present": "yes"}),
                ("chest_pain.onset", {"chest_pain.onset": rng.choice(ONSETS[:6])}),
            ]
        else:
            answers += [("chest_pain.short_of_breath", {"chest_pain.short_of_breath": "no"})]
    elif category == "stroke":
        age = rng.randint(45, 84)
        signs = ["fast.face_droop", "fast.arm_weakness", "fast.speech_difficulty"]
        values = dict.fromkeys(signs, "no")
        if index == 5:  # unsure screen: no sign is yes, so FAST_STROKE is not raised
            values[rng.choice(signs)] = "unknown"
        else:
            for sign in rng.sample(signs, rng.choice([1, 1, 2, 3])):
                values[sign] = "yes"
        # The complaint names the first sign that is there, so it never contradicts the answers.
        complaints = {
            "fast.face_droop": "One side of my face is drooping",
            "fast.arm_weakness": "My arm feels weak",
            "fast.speech_difficulty": "My speech is slurred",
        }
        positive = [sign for sign in signs if values[sign] == "yes"]
        said: dict[str, Any] = (
            {"chief_complaint": complaints[positive[0]], positive[0]: "yes"}
            if positive
            else {"chief_complaint": "Something feels different"}
        )
        answers = [("chief_complaint", said)]
        answers += [(sign, {sign: values[sign]}) for sign in signs]
        answers.append(
            ("fast.last_known_well", {"fast.last_known_well": rng.choice(LAST_KNOWN_WELL)})
        )
    elif category == "sepsis":
        age = rng.randint(35, 88)
        high = index < 6
        device = dict(rng.choice(SEPSIS_HIGH if high else SEPSIS_LOW))
        if index in (4, 5):
            nurse = dict(SEPSIS_NURSE)
        answers = [
            ("chief_complaint", {"chief_complaint": "I feel very unwell"}),
            (
                "infection.fever_reported",
                {"infection.fever_reported": rng.choice(["yes", "yes", "no"])},
            ),
            ("infection.rigors", {"infection.rigors": rng.choice(["yes", "no", "unknown"])}),
            ("symptom_onset_at", {"symptom_onset_at": rng.choice(ONSETS)}),
        ]
    elif category == "child":
        if index < 2:  # red, under 5
            age = rng.randint(1, 4)
            red = (
                "child_danger.hard_to_wake",
                "child_danger.grunting",
                "child_danger.non_blanching_rash",
            )[index + rng.randint(0, 1)]
            answers = [("chief_complaint", {"chief_complaint": "My child is unwell"})]
            for key in (
                "child_danger.hard_to_wake",
                "child_danger.grunting",
                "child_danger.non_blanching_rash",
            ):
                value = rng.choice(["yes", "unknown"]) if key == red else "no"
                answers.append((key, {key: value}))
            answers.append(("child_amber.poor_feeding", {"child_amber.poor_feeding": "no"}))
        elif index < 4:  # amber, under 5
            age = rng.randint(1, 4)
            answers = [
                (
                    "chief_complaint",
                    {
                        "chief_complaint": "My child is not eating",
                        "child_amber.poor_feeding": "yes",
                    },
                ),
                ("child_danger.hard_to_wake", {"child_danger.hard_to_wake": "no"}),
                ("child_danger.grunting", {"child_danger.grunting": "no"}),
                ("child_amber.poor_feeding", {"child_amber.poor_feeding": "yes"}),
                (
                    "child_amber.reduced_urine_output",
                    {"child_amber.reduced_urine_output": rng.choice(["yes", "no"])},
                ),
            ]
        else:  # hard to wake, 5 to 15
            age = rng.randint(6, 13)
            answers = [
                ("chief_complaint", {"chief_complaint": "My child is not their usual self"}),
                (
                    "child_danger.hard_to_wake",
                    {"child_danger.hard_to_wake": rng.choice(["yes", "yes", "unknown"])},
                ),
                (
                    "infection.fever_reported",
                    {"infection.fever_reported": rng.choice(["yes", "no"])},
                ),
                ("symptom_onset_at", {"symptom_onset_at": rng.choice(ONSETS)}),
            ]
    elif category == "breathing":
        age = rng.randint(22, 70)
        device = dict(BREATHING[index % len(BREATHING)])
        answers = [
            (
                "chief_complaint",
                {"chief_complaint": "I am short of breath", "chest_pain.short_of_breath": "yes"},
            ),
            ("chest_pain.short_of_breath", {"chest_pain.short_of_breath": "yes"}),
            ("chest_pain.present", {"chest_pain.present": "no"}),
            ("infection.fever_reported", {"infection.fever_reported": "no"}),
            ("severity", {"severity": rng.randint(3, 8)}),
        ]
    else:  # routine
        age = rng.randint(18, 70)
        text, location, symptom = ROUTINE[index % len(ROUTINE)]
        stated: dict[str, Any] = {"chief_complaint": text}
        if location is not None:
            stated.update(pain_present="yes", pain_location=location)
        if symptom is not None:
            stated["symptoms"] = [symptom]
        answers = [
            ("chief_complaint", stated),
            ("symptom_onset_at", {"symptom_onset_at": rng.choice(ONSETS)}),
        ]
        if location is not None:
            answers += [
                ("pain_score", {"pain_score": rng.randint(1, 5), "pain_present": "yes"}),
                ("pain_location", {"pain_location": location}),
            ]
        else:
            answers.append(("severity", {"severity": rng.randint(1, 4)}))
        answers.append(("allergies_confirm", {"allergies_confirm": rng.choice(ALLERGIES)}))
    return {
        "category": category,
        "age_years": age,
        "sex": sex,
        "device_vitals": device,
        "nurse_vitals": nurse,
        "answers": [{"question_key": key, "gold": gold} for key, gold in answers],
    }


def arabic_plan(dialect: str, *, n: int = 50, seed: int = 7) -> list[dict[str, Any]]:
    """The seeded plan for one dialect: `n` scenarios in the English corpus's category mix.
    The same dialect and seed always give the same plan."""
    if dialect not in DIALECTS:
        raise ValueError(f"dialect must be one of {DIALECTS}")
    if n != sum(CATEGORY_MIX.values()):
        raise ValueError("the Arabic plan keeps the English 50-case category mix")
    rng = random.Random(f"{seed}:{dialect}")  # noqa: S311 - reproducible synthetic data
    plans: list[dict[str, Any]] = []
    for category, count in CATEGORY_MIX.items():
        for index in range(count):
            plan = _plan(category, index, rng)
            plan["id"] = f"ar-{dialect[:3]}-{category.replace('_', '-')}-{index + 1:02d}"
            plan["dialect"] = dialect
            plans.append(plan)
    return plans


def gold_facts(plan: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for answer in plan["answers"]:
        for key, value in answer["gold"].items():
            if key in facts and facts[key] != value:
                raise ValueError(f"{key} is planned with two values")
            facts[key] = value
    for key in facts:
        FactKey(key)  # every planned key is a contract key
    return facts


def coverage(plans: list[dict[str, Any]], alerts: dict[str, list[str]]) -> Counter[str]:
    """How many planned scenarios expect each rule id (`alerts`: id -> expected rule ids)."""
    return Counter(rule for plan in plans for rule in alerts[plan["id"]])


__all__ = ["CATEGORY_MIX", "DIALECTS", "arabic_plan", "coverage", "gold_facts"]

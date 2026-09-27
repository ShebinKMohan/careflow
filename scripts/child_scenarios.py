"""Write the M2-04 child answer scenarios into eval/scenarios/rules/child/ (SHE-293).

Every question of the parent's bank (services/intake/.../child_questions.py), answered yes and
"Not sure" (unknown), at 2 months, 3 years and 9 years, plus 5 all-no controls: 101 files named
`child-<age>-<question>-<answer>.yaml`. The expected alerts come from the rules' stated semantics
(ADR-035 and the rule docstrings), not from running the engine:

- under 5 (2 months, 3 years): a red answer yes or unknown -> NG143_RED_FLAG; an amber answer yes
  -> NG143_AMBER_FLAG; an amber unknown -> no alert (amber needs a yes);
- 5 to 15 (9 years): hard to wake yes or unknown -> CHILD_HARD_TO_WAKE; every other answer -> no
  alert (NG143 covers under 5s: the documented gap, ADR-035).

    uv run python scripts/child_scenarios.py          # (re)write the files
    uv run python scripts/child_scenarios.py --check  # fail on a missing, changed or stray file

They have a folder of their own: the eval harness's `rules` suite (M1-18, SHE-314) replays a
recorded baseline of the scenarios directly in eval/scenarios/rules/, and a recording is never
rewritten. services/rules/tests/test_scenarios.py runs both folders.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Final

REPO: Final = Path(__file__).resolve().parents[1]
OUT: Final = REPO / "eval" / "scenarios" / "rules" / "child"
NOW: Final = "2026-09-26T09:05:00Z"
ARRIVED: Final = "2026-09-26T09:00:00Z"
ASKED: Final = "2026-09-26T09:01:00Z"

RED: Final = (
    "child_danger.hard_to_wake",
    "child_danger.no_response_to_social_cues",
    "child_danger.weak_high_pitched_or_continuous_cry",
    "child_danger.pale_mottled_ashen_or_blue",
    "child_danger.grunting",
    "child_danger.non_blanching_rash",
    "child_danger.neck_stiffness",
    "child_danger.seizure",
)
AMBER: Final = (
    "child_amber.decreased_activity",
    "child_amber.not_responding_normally",
    "child_amber.poor_feeding",
    "child_amber.reduced_urine_output",
    "child_amber.fever_5_days_or_more",
    "infection.rigors",
    "child_amber.limb_or_joint_swelling",
    "child_amber.non_weight_bearing",
)
# (label, fact path, value, words for the description)
AGES: Final = {
    "2m": ("patient.age_months", 2, "2-month-old"),
    "3y": ("patient.age_years", 3, "3-year-old"),
    "9y": ("patient.age_years", 9, "9-year-old"),
}
CONTROLS: Final = {
    "2m": ("patient.age_months", 2, "2-month-old"),
    "11m": ("patient.age_months", 11, "11-month-old"),
    "3y": ("patient.age_years", 3, "3-year-old"),
    "4y": ("patient.age_years", 4, "4-year-old"),
    "9y": ("patient.age_years", 9, "9-year-old"),
}


def expected(key: str, answer: str, age: str) -> list[str]:
    under_5 = age != "9y"
    if key in RED:
        if under_5:
            return ["NG143_RED_FLAG"]
        return ["CHILD_HARD_TO_WAKE"] if key == "child_danger.hard_to_wake" else []
    return ["NG143_AMBER_FLAG"] if under_5 and answer == "yes" else []


def slug(key: str) -> str:
    return key.split(".", 1)[1].replace("_", "-")


def fact(path: str, value: object, provenance: str, at: str) -> str:
    shown = f'"{value}"' if isinstance(value, str) else str(value)
    return f'  - {{path: {path}, value: {shown}, provenance: {provenance}, recorded_at: "{at}"}}'


def scenario(key: str, answer: str, age: str) -> tuple[str, str]:
    path, value, words = AGES[age]
    alerts = expected(key, answer, age)
    said = "yes" if answer == "yes" else "not sure (unknown)"
    kind = "red" if key in RED else "amber"
    name = f"child-{age}-{slug(key)}-{'yes' if answer == 'yes' else 'unsure'}"
    lines = [
        f"id: {name}",
        f'description: "{words}; the parent answers {said} to {key} (M2-04, generated)."',
        # Only a scenario that expects its flag covers the feature: a covering scenario must raise
        # exactly one flag (test_single_feature_scenarios_raise_only_their_flag).
        *([f"covers: [{kind}:{key}]"] if alerts else []),
        f'now: "{NOW}"',
        f'arrived_at: "{ARRIVED}"',
        "facts:",
        fact(path, value, "PATIENT_STATED", ARRIVED),  # the parent's entry at the kiosk
        fact(key, answer, "PATIENT_STATED", ASKED),
        f"expect_alerts: [{', '.join(alerts)}]",
    ]
    return name, "\n".join(lines) + "\n"


def control(age: str) -> tuple[str, str]:
    path, value, words = CONTROLS[age]
    name = f"child-{age}-all-no"
    lines = [
        f"id: {name}",
        f'description: "{words}; the parent answers no to every question of the bank'
        ' (M2-04, generated)."',
        f'now: "{NOW}"',
        f'arrived_at: "{ARRIVED}"',
        "facts:",
        fact(path, value, "PATIENT_STATED", ARRIVED),
        *(fact(key, "no", "PATIENT_STATED", ASKED) for key in (*RED, *AMBER)),
        "expect_alerts: []",
    ]
    return name, "\n".join(lines) + "\n"


def files() -> dict[str, str]:
    out: dict[str, str] = {}
    for key in (*RED, *AMBER):
        for answer in ("yes", "unknown"):
            for age in AGES:
                name, text = scenario(key, answer, age)
                out[name] = text
    for age in CONTROLS:
        name, text = control(age)
        out[name] = text
    return out


def bank_matches() -> bool:
    """RED and AMBER are the kiosk's question bank, in its order (a new question needs files)."""
    from careflow_intake.conversation.child_questions import CHILD_AMBER, CHILD_RED

    return RED == tuple(k.value for k in CHILD_RED) and AMBER == tuple(k.value for k in CHILD_AMBER)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not bank_matches():
        print(
            "child_scenarios: RED/AMBER differ from child_questions.py; update them",
            file=sys.stderr,
        )
        return 1
    wanted = files()
    if args.check:
        stale = [
            n
            for n, t in wanted.items()
            if not (OUT / f"{n}.yaml").is_file()
            or (OUT / f"{n}.yaml").read_text(encoding="utf-8") != t
        ]
        stray = sorted(p.stem for p in OUT.glob("*.yaml") if p.stem not in wanted)
        for name in stale:
            print(f"child_scenarios: {name}.yaml is missing or differs", file=sys.stderr)
        for name in stray:
            print(f"child_scenarios: {name}.yaml is not generated; remove it", file=sys.stderr)
        return 1 if stale or stray else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in wanted.items():
        (OUT / f"{name}.yaml").write_text(text, encoding="utf-8")
    print(f"child_scenarios: wrote {len(wanted)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Reviewed-only loading, content hashes and deterministic real-contract input builders.

The expected labels are used only by ``expected_entries``. ``transcript`` and
``context_entries`` never read them, keeping the extractor blind to its gold labels.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from careflow_contracts.generated.fact_schema import IntegerFact, TriStateFact
from careflow_contracts.generated.structured_facts_schema import FactKey, Language, Turn, TurnRole
from careflow_eval.identity import require_distinct_models
from careflow_eval.models import Dataset, Scalar, Scenario
from careflow_intake import arabic
from careflow_intake.conversation.checklist import templates_for
from careflow_intake.extraction.verify import NUMBER_WORDS, find_span, number_in_quote
from careflow_intake.facts import FactEntry, fact_class, stable_fact_id

EVAL_DIR = Path(__file__).resolve().parents[2]
DATASETS_DIR = EVAL_DIR / "datasets"
SCHEMA_PATH = EVAL_DIR / "schemas" / "scenario.schema.json"
DEVICE_SCENARIOS_DIR = EVAL_DIR.parent / "services/api/src/careflow_api/devices/scenarios"
ARRIVED_AT = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
NOW = ARRIVED_AT + timedelta(minutes=5)
REVIEW_FIELDS = frozenset(
    {"reviewed_by", "reviewed_at", "review_method", "reviewed_content_sha256"}
)
VITAL_UNITS = {
    "heart_rate": "/min",
    "spo2": "%",
    "temperature_c": "Cel",
    "respiratory_rate": "/min",
    "systolic_bp": "mm[Hg]",
    "diastolic_bp": "mm[Hg]",
}
VITAL_KEYS = frozenset([*VITAL_UNITS, "acvpu", "on_supplemental_o2"])


class DatasetError(ValueError):
    """An invalid, unreviewed or empty dataset cannot be used as a baseline."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def review_content(scenario: Scenario) -> dict[str, Any]:
    """The complete, canonical judge input, excluding metadata about the review itself.

    A named device scenario includes the actual values used by context_entries. Thus a
    fixture edit invalidates its review even when the name in the scenario stays fixed.
    Inline vitals already contain their values and keep their existing representation.
    """
    body = scenario.model_dump(mode="json", exclude=set(REVIEW_FIELDS))
    if scenario.language is not Language.ar:
        # The Arabic-only fields (M2-03) are left out of an English scenario's content, so its
        # hash, its recorded review and the frozen English baseline stay valid.
        body.pop("dialect", None)
        for turn in body["turns"]:
            turn.pop("gloss_en", None)
            turn.pop("states", None)
    if isinstance(scenario.device_vitals, str):
        body["resolved_device_vitals"] = device_values(scenario)
    return body


def content_hash(scenario: Scenario) -> str:
    """Bind review to the exact judge content, including resolved external fixture inputs."""
    return hashlib.sha256(canonical_json(review_content(scenario)).encode()).hexdigest()


def review_errors(scenario: Scenario) -> list[str]:
    errors: list[str] = []
    for name in REVIEW_FIELDS:
        value = getattr(scenario, name)
        if value is None or isinstance(value, str) and not value.strip():
            errors.append(f"missing {name}")
    if scenario.reviewed_content_sha256 and scenario.reviewed_content_sha256 != content_hash(
        scenario
    ):
        errors.append("reviewed_content_sha256 does not match current scenario content")
    try:
        require_distinct_models(scenario.generated_by.model, scenario.reviewed_by or "")
    except ValueError as exc:
        errors.append(str(exc))
    return errors


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise DatasetError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_dataset(path: Path | str, include_unreviewed: bool = False) -> Dataset:
    """Load YAMLs sorted by id; inbox drafts require an explicit opt-in.

    Every admitted scenario must pass schema, source-support and rule validation. The
    dataset hash binds the sorted scenario content hashes; review timestamps do not alter
    the evaluated inputs. Editing any input/label invalidates both its review and hash.
    """
    from careflow_eval.validation import validate_scenario

    root = Path(path)
    if not root.exists() and not root.is_absolute():
        root = DATASETS_DIR / root
    if not root.exists():
        raise DatasetError(f"dataset does not exist: {root}")
    candidates = [root] if root.is_file() else sorted(root.rglob("*.yaml"))
    loaded: dict[str, Scenario] = {}
    for candidate in candidates:
        if not include_unreviewed and "_inbox" in candidate.parts:
            if root.is_file():
                raise DatasetError(f"{candidate}: inbox drafts require --include-unreviewed")
            continue
        try:
            # Our SafeLoader subclass rejects duplicate keys; Python object tags stay disabled.
            raw = yaml.load(candidate.read_text(encoding="utf-8"), Loader=_UniqueLoader)  # noqa: S506
            errors = validate_scenario(raw)
            if errors:
                raise DatasetError("; ".join(errors))
            scenario = Scenario.model_validate(raw)
            if candidate.stem != scenario.id:
                raise DatasetError("filename must equal scenario id plus .yaml")
            if not include_unreviewed and (errors := review_errors(scenario)):
                raise DatasetError("; ".join(errors))
            if scenario.id in loaded:
                raise DatasetError(f"duplicate scenario id: {scenario.id}")
            loaded[scenario.id] = scenario
        except (ValueError, yaml.YAMLError, TypeError) as exc:
            raise DatasetError(f"{candidate}: {exc}") from exc
    if not loaded:
        raise DatasetError(f"no {'reviewed ' if not include_unreviewed else ''}scenarios in {root}")
    scenarios = tuple(loaded[key] for key in sorted(loaded))
    digest = hashlib.sha256(
        canonical_json([content_hash(s) for s in scenarios]).encode()
    ).hexdigest()
    return Dataset(scenarios=scenarios, sha256=digest)


def transcript(scenario: Scenario) -> tuple[list[Turn], dict[str, FactKey]]:
    """Approved template questions plus the actual scripted patient answers."""
    bank = templates_for(scenario.language)
    turns: list[Turn] = []
    question_keys: dict[str, FactKey] = {}
    for index, scripted in enumerate(scenario.turns):
        key = scripted.question_key
        question = (
            bank.render_child(key)
            if scenario.patient.age_years < 16 and key.value in bank.child_questions
            else bank.render(key)
        )
        for offset, role, text in (
            (0, TurnRole.assistant, question),
            (1, TurnRole.patient, scripted.patient_text),
        ):
            turn_id = f"{scenario.id}-{index + 1}-{role.value}"
            turn = Turn(
                turn_id=turn_id,
                role=role,
                text=text,
                question_key=key,
                created_at=ARRIVED_AT + timedelta(seconds=index * 2 + offset),
            )
            turns.append(turn)
            if role is TurnRole.patient:
                question_keys[turn_id] = key
    return turns, question_keys


def device_values(scenario: Scenario) -> dict[str, Scalar]:
    if isinstance(scenario.device_vitals, dict):
        return scenario.device_vitals
    name = scenario.device_vitals
    allowed = {p.stem: p for p in DEVICE_SCENARIOS_DIR.glob("*.yaml")}
    if name not in allowed:
        raise ValueError(f"unknown device simulator scenario: {name}")
    body = yaml.safe_load(allowed[name].read_text(encoding="utf-8"))
    point = body["series"][0]
    names = {"hr": "heart_rate", "spo2": "spo2", "temp_c": "temperature_c"}
    if body.get("bp_cuff"):
        names.update({"sbp": "systolic_bp", "dbp": "diastolic_bp"})
    return {target: point[source] for source, target in names.items() if source in point}


def _entry(
    scenario: Scenario,
    key: FactKey,
    value: Any,
    provenance: str,
    source: dict[str, Any],
    index: int = 0,
    unit: str | None = None,
) -> FactEntry:
    base = {
        "fact_id": stable_fact_id("eval", scenario.id, key.value, provenance, str(index)),
        "provenance": provenance,
        "source": source,
        "recorded_at": ARRIVED_AT.isoformat(),
        "value": value,
    }
    if unit is not None:
        base["unit"] = unit
    fact = {"name": base} if key is FactKey.symptoms else base
    return FactEntry.model_validate({"key": key, "fact": fact})


def context_entries(scenario: Scenario) -> list[FactEntry]:
    """Only known demographics and supplied device/nurse inputs; never expected labels."""
    ehr = {"kind": "fhir", "resource_type": "Patient", "id": f"synthetic-{scenario.id}"}
    entries = [
        _entry(scenario, FactKey.patient_age_years, scenario.patient.age_years, "EHR", ehr),
        _entry(
            scenario,
            FactKey.patient_sex,
            {
                "system": "http://hl7.org/fhir/administrative-gender",
                "code": scenario.patient.sex,
            },
            "EHR",
            ehr,
        ),
    ]
    for provenance, values, source in (
        (
            "DEVICE_MEASURED",
            device_values(scenario),
            {
                "kind": "device",
                "device_id": "synthetic-eval-device",
                "reading_id": scenario.id,
            },
        ),
        (
            "NURSE_CONFIRMED",
            scenario.nurse_vitals,
            {
                "kind": "nurse",
                "staff_user_id": "synthetic-eval-nurse",
            },
        ),
    ):
        for name, value in values.items():
            if name not in VITAL_KEYS:
                raise ValueError(f"unsupported vital key: {name}")
            if name in VITAL_UNITS and (
                isinstance(value, bool) or not isinstance(value, int | float)
            ):
                raise ValueError(f"vitals.{name} requires a number")
            actual = (
                {"system": "https://careflow.example/fhir/CodeSystem/acvpu", "code": value}
                if name == "acvpu"
                else value
            )
            entries.append(
                _entry(
                    scenario,
                    FactKey(f"vitals.{name}"),
                    actual,
                    provenance,
                    source,
                    unit=VITAL_UNITS.get(name),
                )
            )
    return entries


def _supported(key: FactKey, value: Scalar, text: str) -> bool:
    expected_type = fact_class(key)
    if issubclass(expected_type, IntegerFact):
        return type(value) is int and number_in_quote(float(value), text)
    if issubclass(expected_type, TriStateFact):
        patterns = {
            "yes": r"^\s*yes\b",
            "no": r"^\s*no\b",
            "unknown": (
                r"\b(unknown|not sure|unsure|don.t know|do not know|cannot tell|can.t tell)\b"
            ),
        }
        return (
            isinstance(value, str)
            and value in patterns
            and bool(re.search(patterns[value], text, re.IGNORECASE))
        )
    return isinstance(value, str) and find_span(text, value.replace("_", " ")) is not None


def _supported_elsewhere(key: FactKey, value: Scalar, turn: Turn) -> bool:
    """Conservative cross-field support for the prompt's extract-every-statement rule.

    An unrelated bare yes/no is never evidence. This is a structural screening check;
    independent model review still checks the clinical meaning and gold completeness.
    """
    if key is FactKey.pain_present:
        if turn.question_key is FactKey.pain_score:
            first_number = re.search(rf"\b(?:{'|'.join(NUMBER_WORDS)}|\d+)\b", turn.text.casefold())
            if first_number:
                token = first_number.group()
                score = NUMBER_WORDS.get(token) if token in NUMBER_WORDS else int(token)
                if score is not None and 0 <= score <= 10:
                    return value == ("no" if score == 0 else "yes")
        negative = bool(
            re.search(
                r"\b(no pain|without pain|pain.free|does not hurt)\b", turn.text, re.IGNORECASE
            )
        )
        if value == "no":
            return negative
        if value == "yes":
            return not negative and bool(
                re.search(
                    r"\b(pain|painful|hurts|hurt|ache|aches|aching|sore|tender)\b",
                    turn.text,
                    re.IGNORECASE,
                )
            )
        return False
    if key in {
        FactKey.symptoms,
        FactKey.pain_location,
        FactKey.symptom_onset_at,
        FactKey.chest_pain_onset,
        FactKey.fast_last_known_well,
    }:
        return isinstance(value, str) and find_span(turn.text, value.replace("_", " ")) is not None
    return False


# "Not sure" in Arabic, as `careflow_intake.arabic.normalise` writes it (أ -> ا, lower case).
UNSURE_AR = (
    "مش متاكد",
    "مو متاكد",
    "مب متاكد",
    "ما متاكد",
    "مش اكيد",
    "لست متاكد",
    "غير متاكد",
    "ما ادري",
    "مدري",
    "لا ادري",
    "لا اعرف",
    "ما اعرف",
    "ما بعرف",
    "مش عارف",
    "لا اعلم",
    "يمكن",
    "ممكن",
)


def _supported_ar(key: FactKey, value: Scalar, text: str) -> bool:
    """An Arabic answer supports a gold fact it states (`ScenarioTurn.states`) when a number is
    in its words (digits, Arabic-Indic digits or Arabic number words) and "unknown" is an unsure
    answer. Yes/no and English renderings of free text rest on the statement and on the
    independent review (the back-translation and the judge), not on English patterns."""
    kind = fact_class(key)
    if issubclass(kind, IntegerFact):
        return type(value) is int and number_in_quote(float(value), text)
    if issubclass(kind, TriStateFact):
        if value == "unknown":
            said = arabic.normalise(text)
            return any(phrase in said for phrase in UNSURE_AR)
        return value in ("yes", "no")
    return isinstance(value, str) and bool(value.strip())


def expected_entries(scenario: Scenario) -> list[FactEntry]:
    """Gold contract facts with spans into real scripted answers, never fixture quotes.

    Direct answers use their question key. Additional explicit pain, location, onset and
    symptom statements may also be supported by other answers, as the production prompt
    requires. The recorded quote and offsets always belong to the original patient text.
    """
    turns, keys = transcript(scenario)
    patients = [turn for turn in turns if turn.role is TurnRole.patient]
    stated = {
        f"{scenario.id}-{index + 1}-patient": set(turn.states)
        for index, turn in enumerate(scenario.turns)
    }
    entries: list[FactEntry] = []
    for key, values in scenario.expected.facts.items():
        if key is FactKey.symptoms and not isinstance(values, list):
            raise ValueError("symptoms gold must be a list")
        if key is not FactKey.symptoms and isinstance(values, list):
            raise ValueError(f"{key.value} gold must be a scalar")
        for index, value in enumerate(values if isinstance(values, list) else [values]):
            if scenario.language is Language.ar:
                supported = [
                    t
                    for t in patients
                    if key in stated[t.turn_id] and _supported_ar(key, value, t.text)
                ]
            else:
                supported = [
                    t
                    for t in patients
                    if (keys[t.turn_id] is key and _supported(key, value, t.text))
                    or (keys[t.turn_id] is not key and _supported_elsewhere(key, value, t))
                ]
            if not supported:
                raise ValueError(f"{key.value} gold is not supported by an actual patient answer")
            turn = supported[-1]
            entries.append(
                _entry(
                    scenario,
                    key,
                    value,
                    "PATIENT_STATED",
                    {
                        "kind": "transcript_span",
                        "turn_id": turn.turn_id,
                        "start": 0,
                        "end": len(turn.text),
                        "quote": turn.text,
                    },
                    index=index,
                )
            )
    return entries

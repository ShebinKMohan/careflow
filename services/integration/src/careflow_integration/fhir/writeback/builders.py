"""Pure builders: one `TriageRecord` -> the FHIR R4 resources of its write-back (M1-09).

Builders return plain dicts, checked against the pinned FHIR models (R4B, ADR-023) before they
leave this module. Codes come from the mapping table (`codes`). Every resource carries:

- an identifier that makes its conditional create idempotent: `urn:careflow:session|<session_id>`
  for the Encounter, `urn:careflow:fact|<session_id>:<local id>` for the others (the Provenance,
  which has none in R4, is matched on the tag `urn:careflow:writeback|<session_id>`);
- `meta.tag` with the provenance of every fact it holds (safety rule 4);
- `subject` and `encounter` references where FHIR has them.

Staff and devices are logical references (by identifier): CareFlow never creates Practitioner or
Device resources in the hospital's record. Nothing here reads the clock, so one record always
builds the same resources.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from fhir.resources.R4B.communication import Communication
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.flag import Flag
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.provenance import Provenance as FhirProvenance
from fhir.resources.R4B.questionnaireresponse import QuestionnaireResponse
from fhir.resources.R4B.task import Task
from pydantic import BaseModel

from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.fact_schema import (
    DeviceSource,
    FactBase,
    ModelSource,
    NurseSource,
    Provenance,
    TriState,
    TriStateFact,
)
from careflow_contracts.generated.nurse_decision_schema import DecisionAction
from careflow_contracts.generated.structured_facts_schema import StructuredFacts, Vitals
from careflow_integration.adapters.loader import HospitalAdapter
from careflow_integration.fhir.writeback import codes
from careflow_integration.fhir.writeback.record import TriageRecord

type Json = dict[str, Any]

SESSION_SYSTEM: Final = "urn:careflow:session"
FACT_SYSTEM: Final = "urn:careflow:fact"
STAFF_SYSTEM: Final = "urn:careflow:staff"
DEVICE_SYSTEM: Final = "urn:careflow:device"
MODEL_SYSTEM: Final = "urn:careflow:model"
WRITEBACK_TAG: Final = "urn:careflow:writeback"
NAMESPACE: Final = uuid.uuid5(uuid.NAMESPACE_URL, "https://careflow.example/fhir/writeback")
ENCOUNTER: Final = "encounter"
PROVENANCE: Final = "provenance"
MODELS: Final[dict[str, type[BaseModel]]] = {
    "Encounter": Encounter,
    "Observation": Observation,
    "QuestionnaireResponse": QuestionnaireResponse,
    "Flag": Flag,
    "Task": Task,
    "Communication": Communication,
    "Provenance": FhirProvenance,
}
# Vitals written one value per Observation: local id, Vitals field, mapping fact path.
SIMPLE_VITALS: Final = (
    ("obs-heart-rate", "heart_rate", "vitals.heart_rate"),
    ("obs-respiratory-rate", "respiratory_rate", "vitals.respiratory_rate"),
    ("obs-spo2", "spo2", "vitals.spo2"),
    ("obs-temperature", "temperature_c", "vitals.temperature_c"),
)
NEWS2_INPUT_LABELS: Final = {
    "vitals.respiratory_rate": "respiratory rate",
    "vitals.spo2": "SpO2",
    "vitals.on_supplemental_o2": "air or oxygen",
    "vitals.systolic_bp": "systolic blood pressure",
    "vitals.heart_rate": "heart rate",
    "vitals.acvpu": "ACVPU",
    "vitals.temperature_c": "temperature",
}
# Danger screens summarised in the nurse hand-off: StructuredFacts field, label.
SCREENS: Final = (
    ("fast", "FAST"),
    ("chest_pain", "chest pain"),
    ("infection", "infection"),
    ("child_danger", "child danger signs"),
    ("child_amber", "child amber signs"),
)
QR_SOURCES: Final = {
    Provenance.PATIENT_STATED: "qr-patient",
    Provenance.NURSE_CONFIRMED: "qr-nurse",
}


@dataclass(frozen=True)
class Built:
    """One resource of the write-back, with its local id (e.g. `obs-heart-rate`)."""

    local_id: str
    resource: Json


@dataclass(frozen=True)
class Ids:
    """Identifiers, conditional-create matches and fullUrls for one session's write-back."""

    session_id: str

    def identifier(self, local_id: str) -> Json:
        if local_id == ENCOUNTER:
            return {"system": SESSION_SYSTEM, "value": self.session_id}
        return {"system": FACT_SYSTEM, "value": f"{self.session_id}:{local_id}"}

    def match(self, local_id: str) -> str:
        """`Bundle.entry.request.ifNoneExist` for the resource."""
        if local_id == PROVENANCE:
            return f"_tag={WRITEBACK_TAG}|{self.session_id}"
        identifier = self.identifier(local_id)
        return f"identifier={identifier['system']}|{identifier['value']}"

    def full_url(self, local_id: str) -> str:
        key = self.match(local_id).split("=", 1)[1]
        return f"urn:uuid:{uuid.uuid5(NAMESPACE, key)}"

    def ref(self, local_id: str) -> Json:
        return {"reference": self.full_url(local_id)}


@dataclass
class Context:
    """What the builders share: the record, the ids, and every fact by id."""

    record: TriageRecord
    adapter: HospitalAdapter
    ids: Ids = field(init=False)
    facts_by_id: dict[str, FactBase] = field(init=False)

    def __post_init__(self) -> None:
        self.ids = Ids(self.record.session_id)
        self.facts_by_id = {fact.fact_id: fact for fact in all_facts(self.record.facts)}

    @property
    def patient(self) -> Json:
        if self.record.patient_ref is None:
            raise ValueError("no EHR patient to write to")
        return {"reference": self.record.patient_ref}

    @property
    def encounter(self) -> Json:
        return self.ids.ref(ENCOUNTER)

    def evidence(self, alert: Alert) -> list[FactBase]:
        found = [self.facts_by_id[i] for i in alert.evidence_fact_ids if i in self.facts_by_id]
        if not found:
            raise ValueError(f"{alert.rule_id.value}: none of its evidence facts is in the record")
        return found


def all_facts(node: BaseModel) -> Iterator[FactBase]:
    """Every fact inside a StructuredFacts (nested groups and lists included)."""
    for name in type(node).model_fields:
        value = getattr(node, name)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, FactBase):
                yield item
            elif isinstance(item, BaseModel):
                yield from all_facts(item)


def value_of(fact: FactBase) -> Any:
    """Every concrete fact has a `value`; the contract's FactBase does not declare it."""
    concrete: Any = fact
    return concrete.value


def fact_at(facts: StructuredFacts, path: str) -> Any:
    """The value at a dotted StructuredFacts path (a Questionnaire linkId), or None."""
    node: Any = facts
    for part in path.split("."):
        node = getattr(node, part, None)
        if node is None:
            return None
    return node


def instant(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def quantity(value: float, unit: str) -> Json:
    return {"value": number(value), "unit": unit, "system": codes.UCUM, "code": unit}


def meta(sources: Iterable[Provenance], *extra_tags: Json) -> Json:
    """`meta.tag`: one provenance-source tag per source, in the contract's order."""
    present = set(sources)
    if not present:
        raise ValueError("every written resource carries its provenance (safety rule 4)")
    system = codes.entry("write_batch.meta_tag", "(every written resource)")["code_system"]
    tags = [{"system": system, "code": p.value} for p in Provenance if p in present]
    return {"tag": [*tags, *extra_tags]}


def nurse(staff_user_id: str) -> Json:
    return {
        "identifier": {"system": STAFF_SYSTEM, "value": staff_user_id},
        "display": "Triage nurse",
    }


def device(device_id: str) -> Json:
    return {
        "identifier": {"system": DEVICE_SYSTEM, "value": device_id},
        "display": "CareFlow vitals device",
    }


def checked(local_id: str, resource: Json) -> Built:
    """Validate against the pinned FHIR model: a failure is a builder bug, raised here."""
    MODELS[resource["resourceType"]].model_validate(resource)
    return Built(local_id, resource)


def attributed(ctx: Context, fact: FactBase) -> Json:
    """`device` for a device reading, `performer` for a nurse or patient value."""
    source = fact.source
    if isinstance(source, DeviceSource):
        return {"device": device(source.device_id)}
    if isinstance(source, NurseSource):
        return {"performer": [nurse(source.staff_user_id)]}
    if fact.provenance is Provenance.PATIENT_STATED:
        return {"performer": [ctx.patient]}
    return {}


def observation(
    ctx: Context,
    local_id: str,
    *,
    code: Json,
    sources: Iterable[Provenance],
    effective: datetime,
    category: Json | None = None,
    status: str = "final",
    **extra: Any,
) -> Built:
    body: Json = {
        "resourceType": "Observation",
        "meta": meta(sources),
        "identifier": [ctx.ids.identifier(local_id)],
        "status": status,
    }
    if category is not None:
        body["category"] = [{"coding": [category]}]
    body |= {
        "code": code,
        "subject": ctx.patient,
        "encounter": ctx.encounter,
        "effectiveDateTime": instant(effective),
        **extra,
    }
    return checked(local_id, body)


def encounter(ctx: Context) -> Built:
    decision = ctx.record.decision
    level, scale = int(decision.final_level), decision.scale.value
    priority = next(
        c
        for c in codes.options("nurse_decision.priority", "Encounter")
        if c["code"] == codes.LEVEL_TO_PRIORITY[level]
    )
    how = (
        "set by the triage nurse (override)"
        if decision.action is DecisionAction.OVERRIDE
        else "confirmed by the triage nurse"
    )
    level_system = codes.entry("nurse_decision.final_level", "Encounter")["code_system"]
    body: Json = {
        "resourceType": "Encounter",
        "meta": {},
        "identifier": [ctx.ids.identifier(ENCOUNTER)],
        "status": "triaged",
        "class": {
            "system": codes.entry("session.encounter", "Encounter")["code_system"],
            "code": ctx.adapter.encounter_class,
            "display": codes.ENCOUNTER_CLASS_DISPLAY[ctx.adapter.encounter_class],
        },
        "priority": {
            "coding": [
                priority,
                {
                    "system": level_system,
                    "code": f"{scale}-{level}",
                    "display": f"{scale} level {level}",
                },
            ],
            "text": f"{scale} level {level}, {how}",
        },
        "subject": ctx.patient,
        "period": {"start": instant(ctx.record.arrived_at)},
    }
    sources = {Provenance.NURSE_CONFIRMED}
    if (category := ctx.record.facts.complaint_category) is not None:
        body["reasonCode"] = [
            {"coding": [category.value.model_dump(mode="json", exclude_none=True)]}
        ]
        sources.add(category.provenance)
    body["meta"] = meta(sources)
    return checked(ENCOUNTER, body)


def vital_signs(ctx: Context) -> list[Built]:
    """One Observation per vital sign present, in NEWS2-input order, then the pain score."""
    vitals = ctx.record.facts.vitals
    built: list[Built] = []
    if vitals is not None:
        for local_id, name, path in SIMPLE_VITALS:
            fact = getattr(vitals, name)
            if fact is None:
                continue
            coding = [codes.coding(path, "Observation")]
            if name == "spo2":
                coding.append(codes.SPO2_PULSE_OXIMETRY)
            built.append(
                observation(
                    ctx,
                    local_id,
                    code={"coding": coding},
                    sources=[fact.provenance],
                    effective=fact.recorded_at,
                    category=codes.VITAL_SIGNS,
                    **attributed(ctx, fact),
                    valueQuantity=quantity(fact.value, fact.unit),
                )
            )
        built += [
            b for b in (blood_pressure(ctx, vitals), acvpu(ctx, vitals), oxygen(ctx, vitals)) if b
        ]
    if (pain := ctx.record.facts.pain_score) is not None:
        built.append(
            observation(
                ctx,
                "obs-pain-score",
                code={"coding": [codes.coding("pain_score", "Observation")]},
                sources=[pain.provenance],
                effective=pain.recorded_at,
                category=codes.VITAL_SIGNS,
                **attributed(ctx, pain),
                valueQuantity=quantity(pain.value, "{score}"),
            )
        )
    return built


def blood_pressure(ctx: Context, vitals: Vitals) -> Built | None:
    parts = [
        (fact, path)
        for fact, path in (
            (vitals.systolic_bp, "vitals.systolic_bp"),
            (vitals.diastolic_bp, "vitals.diastolic_bp"),
        )
        if fact is not None
    ]
    if not parts:
        return None
    return observation(
        ctx,
        "obs-blood-pressure",
        code={"coding": [codes.BP_PANEL]},
        sources=[fact.provenance for fact, _ in parts],
        effective=max(fact.recorded_at for fact, _ in parts),
        category=codes.VITAL_SIGNS,
        **attributed(ctx, parts[0][0]),
        component=[
            {
                "code": {"coding": [codes.coding(path, "Observation")]},
                "valueQuantity": quantity(fact.value, fact.unit),
            }
            for fact, path in parts
        ],
    )


def acvpu(ctx: Context, vitals: Vitals) -> Built | None:
    fact = vitals.acvpu
    if fact is None:
        return None
    sct, display = codes.ACVPU_ANSWERS[fact.value.code.value]
    system = codes.entry("vitals.acvpu", "Observation")["code_system"]
    return observation(
        ctx,
        "obs-acvpu",
        code={"coding": [codes.coding("vitals.acvpu", "Observation")]},
        sources=[fact.provenance],
        effective=fact.recorded_at,
        category=codes.VITAL_SIGNS,
        **attributed(ctx, fact),
        valueCodeableConcept={"coding": [{"system": system, "code": sct, "display": display}]},
    )


def oxygen(ctx: Context, vitals: Vitals) -> Built | None:
    """On oxygen or breathing air, carried in Observation.code (mapping.yaml open question 1)."""
    fact = vitals.on_supplemental_o2
    if fact is None or fact.value is TriState.unknown:
        return None
    on_oxygen, room_air = codes.options("vitals.on_supplemental_o2", "Observation")
    return observation(
        ctx,
        "obs-supplemental-o2",
        code={"coding": [on_oxygen if fact.value is TriState.yes else room_air]},
        sources=[fact.provenance],
        effective=fact.recorded_at,
        **attributed(ctx, fact),
    )


def news2(ctx: Context, vitals: list[Built]) -> Built | None:
    """The NEWS2 total, derived from the vital-sign Observations. A partial score is preliminary,
    with a note naming the missing inputs; never written as a full score."""
    score = ctx.record.news2
    if score is None:
        return None
    inputs = [b for b in vitals if b.local_id != "obs-pain-score"]
    if not inputs:  # nothing was measured (the vitals step was skipped, e.g. a phone hand-over):
        return None  # a total derived from nothing is not a score, and would have no provenance
    sources = [Provenance(tag["code"]) for b in inputs for tag in b.resource["meta"]["tag"]]
    extra: Json = {
        "valueQuantity": quantity(score.total, "{score}"),
        "derivedFrom": [ctx.ids.ref(b.local_id) for b in inputs],
    }
    if score.partial:
        missing = ", ".join(NEWS2_INPUT_LABELS.get(k.value, k.value) for k in score.missing)
        extra["note"] = [
            {"text": f"Partial NEWS2 from the inputs measured so far. Missing: {missing}."}
        ]
    return observation(
        ctx,
        "obs-news2",
        code={"coding": [codes.coding("rules.news2_total", "Observation")]},
        sources=sources,
        effective=score.scored_at,
        category=codes.SURVEY,
        status="preliminary" if score.partial else "final",
        **extra,
    )


def triage_level(ctx: Context) -> Built:
    """The nurse's final level (never a model suggestion), with its scale."""
    decision = ctx.record.decision
    scale = decision.scale.value
    level_system = codes.entry("nurse_decision.final_level", "Observation")["code_system"]
    [scale_coding] = [
        c for c in codes.options("nurse_decision.scale", "Observation") if c["code"] == scale
    ]
    return observation(
        ctx,
        "obs-triage-level",
        code={"coding": [codes.coding("nurse_decision.final_level", "Observation")]},
        sources=[Provenance.NURSE_CONFIRMED],
        effective=decision.decided_at,
        performer=[nurse(decision.nurse_user_id)],
        valueInteger=int(decision.final_level),
        component=[
            {
                "code": {
                    "coding": [{"system": level_system, "code": "scale", "display": "Triage scale"}]
                },
                "valueCodeableConcept": {
                    "coding": [scale_coding | {"display": codes.SCALE_DISPLAY[scale]}]
                },
            }
        ],
    )


def answer(item: Json, value: Any) -> Json:
    """One QuestionnaireResponse answer for a Questionnaire item of type string, integer or
    choice (a choice answer copies the Questionnaire's own answerOption coding)."""
    kind = item["type"]
    if kind == "string":
        return {"valueString": str(value)}
    if kind == "integer":
        return {"valueInteger": int(value)}
    if kind == "choice":
        code = str(getattr(value, "code", value))
        for option in item.get("answerOption", []):
            if option["valueCoding"]["code"] == code:
                return {"valueCoding": option["valueCoding"]}
        raise ValueError(f"{item['linkId']}: no answer option {code!r}")
    raise ValueError(f"{item['linkId']}: unsupported item type {kind!r}")


def answer_items(
    items: list[Json], ctx: Context, source: Provenance, used: list[FactBase]
) -> list[Json]:
    """QuestionnaireResponse items for the facts from one answering source, nested as the
    Questionnaire nests them (linkId = fact path)."""
    found: list[Json] = []
    for item in items:
        link = item["linkId"]
        if item["type"] == "group" and item.get("repeats"):
            found += symptom_groups(item, ctx, source, used)
        elif item["type"] == "group":
            if children := answer_items(item.get("item", []), ctx, source, used):
                found.append({"linkId": link, "item": children})
        elif isinstance(fact := fact_at(ctx.record.facts, link), FactBase) and (
            fact.provenance is source
        ):
            found.append({"linkId": link, "answer": [answer(item, value_of(fact))]})
            used.append(fact)
    return found


def symptom_groups(
    item: Json, ctx: Context, source: Provenance, used: list[FactBase]
) -> list[Json]:
    groups: list[Json] = []
    for symptom in ctx.record.facts.symptoms or []:
        if symptom.name.provenance is not source:
            continue
        children: list[Json] = []
        for child in item.get("item", []):
            value = getattr(symptom, child["linkId"].split(".", 1)[1], None)
            if value is None:
                continue
            if isinstance(value, FactBase):
                used.append(value)
                value = value_of(value)
            children.append({"linkId": child["linkId"], "answer": [answer(child, value)]})
        groups.append({"linkId": item["linkId"], "item": children})
    return groups


def questionnaire_responses(ctx: Context) -> list[Built]:
    """One response per answering source: the patient, then the nurse."""
    q = codes.questionnaire()
    built: list[Built] = []
    for source, local_id in QR_SOURCES.items():
        used: list[FactBase] = []
        items = answer_items(q["item"], ctx, source, used)
        if not items:
            continue
        who = (
            ctx.patient
            if source is Provenance.PATIENT_STATED
            else nurse(ctx.record.decision.nurse_user_id)
        )
        body: Json = {
            "resourceType": "QuestionnaireResponse",
            "meta": meta([source]),
            "identifier": ctx.ids.identifier(local_id),
            "questionnaire": codes.questionnaire_canonical(),
            "status": "completed",
            "subject": ctx.patient,
            "encounter": ctx.encounter,
            "authored": instant(max(fact.recorded_at for fact in used)),
            "source": who,
            "item": items,
        }
        if source is Provenance.NURSE_CONFIRMED:
            body["author"] = who
        built.append(checked(local_id, body))
    return built


def flag_id(alert: Alert) -> str:
    return f"flag-{alert.rule_id.value}"


def flags(ctx: Context) -> list[Built]:
    """One active clinical Flag per alert, coded with its rule and its fixed display text."""
    system = codes.code_system("rule-id")
    built: list[Built] = []
    for alert in ctx.record.alerts:
        text = alert.display_text or alert.rule_id.value
        body: Json = {
            "resourceType": "Flag",
            "meta": meta(f.provenance for f in ctx.evidence(alert)),
            "identifier": [ctx.ids.identifier(flag_id(alert))],
            "status": "active",
            "category": [{"coding": [codes.FLAG_CLINICAL]}],
            "code": {
                "coding": [{"system": system, "code": alert.rule_id.value, "display": text}],
                "text": text,
            },
            "subject": ctx.patient,
            "period": {"start": instant(alert.raised_at)},
            "encounter": ctx.encounter,
        }
        built.append(checked(flag_id(alert), body))
    return built


def tasks(ctx: Context) -> list[Built]:
    """A Task for each alert whose required action has a deadline and a task kind in the mapping
    (today: the ECG within 10 minutes). An action already done is written as completed."""
    kind = codes.coding("alert.required_action", "Task")
    built: list[Built] = []
    for alert in ctx.record.alerts:
        if alert.deadline_at is None or alert.required_action != kind["code"]:
            continue
        local_id = f"task-{alert.required_action}"
        done = alert.required_action in ctx.record.completed_actions
        body: Json = {
            "resourceType": "Task",
            "meta": meta(f.provenance for f in ctx.evidence(alert)),
            "identifier": [ctx.ids.identifier(local_id)],
            "status": "completed" if done else "requested",
            "intent": "order",
            "priority": "stat",
            "code": {"coding": [kind], "text": codes.TASK_TEXT[alert.required_action]},
            "focus": ctx.ids.ref(flag_id(alert)),
            "for": ctx.patient,
            "encounter": ctx.encounter,
            "authoredOn": instant(alert.raised_at),
            "restriction": {"period": {"end": instant(alert.deadline_at)}},
        }
        built.append(checked(local_id, body))
    return built


def vitals_summary(vitals: Vitals | None, used: list[FactBase]) -> str | None:
    if vitals is None:
        return None
    parts: list[str] = []
    for fact, label, unit in (
        (vitals.heart_rate, "HR", "/min"),
        (vitals.respiratory_rate, "RR", "/min"),
        (vitals.spo2, "SpO2", "%"),
        (vitals.temperature_c, "temp", " °C"),
    ):
        if fact is not None:
            parts.append(f"{label} {number(fact.value)}{unit}")
            used.append(fact)
    systolic, diastolic = vitals.systolic_bp, vitals.diastolic_bp
    if systolic is not None:
        pressure = f"{number(systolic.value)}"
        pressure += f"/{number(diastolic.value)}" if diastolic is not None else ""
        parts.append(f"BP {pressure} mmHg")
        used += [f for f in (systolic, diastolic) if f is not None]
    if vitals.acvpu is not None:
        parts.append(f"ACVPU {vitals.acvpu.value.code.value}")
        used.append(vitals.acvpu)
    if (o2 := vitals.on_supplemental_o2) is not None and o2.value is not TriState.unknown:
        parts.append("on oxygen" if o2.value is TriState.yes else "on air")
        used.append(o2)
    return f"Vitals: {', '.join(parts)}." if parts else None


def screens_summary(facts: StructuredFacts, used: list[FactBase]) -> str | None:
    groups: list[str] = []
    for name, label in SCREENS:
        group = getattr(facts, name)
        if group is None:
            continue
        positive = [
            (field_name, fact)
            for field_name in type(group).model_fields
            if isinstance(fact := getattr(group, field_name), TriStateFact)
            and fact.value is TriState.yes
        ]
        if positive:
            groups.append(f"{label} ({', '.join(n.replace('_', ' ') for n, _ in positive)})")
            used += [fact for _, fact in positive]
    return f"Positive screens: {'; '.join(groups)}." if groups else None


def handoff(ctx: Context, flag_built: list[Built]) -> Built:
    """The nurse hand-off: fixed templates over structured values, never the patient's words."""
    record, decision = ctx.record, ctx.record.decision
    how = (
        "set by the triage nurse (override)"
        if decision.action is DecisionAction.OVERRIDE
        else "confirmed by the triage nurse"
    )
    used: list[FactBase] = []
    lines = [f"Triage: {decision.scale.value} level {int(decision.final_level)}, {how}."]
    alert_texts = [a.display_text or a.rule_id.value for a in record.alerts]
    lines.append(f"Alerts: {' '.join(alert_texts)}" if alert_texts else "Alerts: none.")
    for alert in record.alerts:
        used += ctx.evidence(alert)
    if summary := vitals_summary(record.facts.vitals, used):
        lines.append(summary)
    if (score := record.news2) is not None:
        missing = ", ".join(NEWS2_INPUT_LABELS.get(k.value, k.value) for k in score.missing)
        lines.append(
            f"NEWS2: {score.total} (partial; missing {missing})."
            if score.partial
            else f"NEWS2: {score.total}."
        )
    if summary := screens_summary(record.facts, used):
        lines.append(summary)
    if (pain := record.facts.pain_score) is not None:
        lines.append(f"Pain score: {pain.value}/10.")
        used.append(pain)
    if (category := record.facts.complaint_category) is not None:
        lines.append(
            f"Complaint category (model-inferred): {category.value.display or category.value.code}."
        )
        used.append(category)
    body: Json = {
        "resourceType": "Communication",
        "meta": meta([Provenance.NURSE_CONFIRMED, *(f.provenance for f in used)]),
        "identifier": [ctx.ids.identifier("comm-handoff")],
        "status": "completed",
        "category": [{"coding": [codes.coding("alert.notification", "Communication")]}],
        "subject": ctx.patient,
        "encounter": ctx.encounter,
        "sent": instant(decision.decided_at),
        "recipient": [nurse(decision.nurse_user_id)],
        "payload": [{"contentString": "\n".join(lines)}],
    }
    if flag_built:
        body["about"] = [ctx.ids.ref(b.local_id) for b in flag_built]
    return checked("comm-handoff", body)


def provenance(ctx: Context, built: list[Built]) -> Built:
    """One Provenance for the write batch: every resource as a target, and an agent per source."""
    sources = {Provenance(t["code"]) for b in built for t in b.resource["meta"]["tag"]}
    types = {c["code"]: c for c in codes.options("write_batch.provenance", "Provenance")}

    def agent(kind: str, who: Json) -> Json:
        coding = types[kind] | {"display": codes.PARTICIPANT_DISPLAY[kind]}
        return {"type": {"coding": [coding]}, "who": who}

    facts = ctx.record.facts
    written = list(all_facts(facts.vitals)) if facts.vitals is not None else []
    devices = sorted({f.source.device_id for f in written if isinstance(f.source, DeviceSource)})
    agents = [agent("performer", device(d)) for d in devices]
    category = facts.complaint_category
    if category is not None and isinstance(category.source, ModelSource):
        model = category.source
        who = {
            "identifier": {"system": MODEL_SYSTEM, "value": model.model_id},
            "display": f"{model.model_id} {model.model_version}",
        }
        agents.append(agent("assembler", who))
    agents.append(agent("author", nurse(ctx.record.decision.nurse_user_id)))
    if Provenance.PATIENT_STATED in sources:
        agents.append(agent("informant", ctx.patient))
    body: Json = {
        "resourceType": "Provenance",
        "meta": meta(sources, {"system": WRITEBACK_TAG, "code": ctx.record.session_id}),
        "target": [ctx.ids.ref(b.local_id) for b in built],
        "recorded": instant(ctx.record.decision.decided_at),
        "agent": agents,
    }
    return checked(PROVENANCE, body)


def build_resources(record: TriageRecord, adapter: HospitalAdapter) -> list[Built]:
    """Every resource of the write-back, in Bundle order."""
    ctx = Context(record, adapter)
    built = [encounter(ctx)]
    vitals = vital_signs(ctx)
    built += vitals
    if (score := news2(ctx, vitals)) is not None:
        built.append(score)
    built.append(triage_level(ctx))
    built += questionnaire_responses(ctx)
    flag_built = flags(ctx)
    built += flag_built
    built += tasks(ctx)
    built.append(handoff(ctx, flag_built))
    built.append(provenance(ctx, built))
    return built


__all__ = [
    "DEVICE_SYSTEM",
    "ENCOUNTER",
    "FACT_SYSTEM",
    "MODEL_SYSTEM",
    "NAMESPACE",
    "PROVENANCE",
    "SESSION_SYSTEM",
    "STAFF_SYSTEM",
    "WRITEBACK_TAG",
    "Built",
    "Context",
    "Ids",
    "all_facts",
    "answer",
    "build_resources",
    "encounter",
    "fact_at",
    "flags",
    "handoff",
    "news2",
    "provenance",
    "questionnaire_responses",
    "tasks",
    "triage_level",
    "value_of",
    "vital_signs",
]

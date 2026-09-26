"""NEWS2 and SEPSIS_NEWS2 are monotone: making things worse never lowers the score or removes the
alert. Hypothesis, at least 500 examples per property."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Final

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from careflow_contracts import AcvpuCode, RuleId
from careflow_rules import News2Inputs, evaluate, score_news2
from careflow_rules.news2 import PARAMETERS, News2Result, Parameter
from careflow_rules.testing import build

EXAMPLES: Final = settings(max_examples=500, deadline=None)
NOW: Final = datetime(2026, 9, 26, 9, 5, tzinfo=UTC)

# Numeric parameters: generated range and the band that scores 0 (SpO2 only worsens downwards).
NUMERIC: Final[dict[Parameter, tuple[st.SearchStrategy[float], float, float]]] = {
    "respiratory_rate": (st.integers(0, 60).map(float), 12, 20),
    "spo2": (st.integers(60, 100).map(float), 96, 100),
    "systolic_bp": (st.integers(50, 280).map(float), 111, 219),
    "heart_rate": (st.integers(20, 220).map(float), 51, 90),
    "temperature_c": (st.integers(300, 440).map(lambda tenths: tenths / 10), 36.1, 38.0),
}


def value_strategy(parameter: Parameter) -> st.SearchStrategy[Any]:
    if parameter == "acvpu":
        return st.sampled_from(list(AcvpuCode))
    if parameter == "on_supplemental_o2":
        return st.booleans()
    return NUMERIC[parameter][0]


@st.composite
def some_inputs(draw: st.DrawFn) -> News2Inputs:
    """Adult inputs with any subset of the seven parameters present."""
    present = draw(st.sets(st.sampled_from(PARAMETERS)))
    values = {p: draw(value_strategy(p)) for p in present}
    return News2Inputs(**values)


@st.composite
def dense_inputs(draw: st.DrawFn) -> News2Inputs:
    """Each parameter present with probability 4/5, so most examples reach NEWS2 5 or more."""
    values = {p: draw(value_strategy(p)) for p in PARAMETERS if draw(st.integers(0, 4)) > 0}
    return News2Inputs(**values)


def worse(data: st.DataObject, parameter: Parameter, value: Any) -> Any:
    """A value at least as far from the 0-band on the same side (or anything from inside it)."""
    if parameter == "acvpu":
        return data.draw(st.sampled_from([c for c in AcvpuCode if c is not AcvpuCode.A]))
    if parameter == "on_supplemental_o2":
        return True
    strategy, low, high = NUMERIC[parameter]
    candidate = data.draw(strategy)
    if value < low:
        return min(candidate, value)
    if value > high:
        return max(candidate, value)
    return candidate


def scored(inputs: News2Inputs) -> News2Result:
    result = score_news2(inputs)
    assert result is not None
    return result


@EXAMPLES
@given(st.data())
def test_worsening_one_parameter_never_lowers_its_subscore_or_the_total(
    data: st.DataObject,
) -> None:
    inputs = data.draw(some_inputs())
    parameter: Parameter = data.draw(st.sampled_from(PARAMETERS))
    current = getattr(inputs, parameter)
    if current is None:
        current = data.draw(value_strategy(parameter))
        inputs = replace(inputs, **{parameter: current})
    worsened = replace(inputs, **{parameter: worse(data, parameter, current)})
    before, after = scored(inputs), scored(worsened)
    assert after.subscores[parameter] >= before.subscores[parameter]
    assert after.total >= before.total


@EXAMPLES
@given(st.data())
def test_adding_a_missing_parameter_never_lowers_the_total(data: st.DataObject) -> None:
    inputs = data.draw(some_inputs())
    missing: list[Parameter] = [p for p in PARAMETERS if getattr(inputs, p) is None]
    if not missing:
        return
    parameter: Parameter = data.draw(st.sampled_from(missing))
    added = replace(inputs, **{parameter: data.draw(value_strategy(parameter))})
    before, after = scored(inputs), scored(added)
    assert after.total >= before.total
    assert len(after.missing) == len(before.missing) - 1


def facts_for(inputs: News2Inputs) -> Any:
    device = {p: getattr(inputs, p) for p in PARAMETERS if getattr(inputs, p) is not None}
    if "on_supplemental_o2" in device:
        device["on_supplemental_o2"] = "yes" if device["on_supplemental_o2"] else "no"
    if "acvpu" in device:
        device["acvpu"] = device["acvpu"].value
    return build(vitals=device, infection={"fever_reported": "yes"}, age=60)


def has_sepsis_alert(inputs: News2Inputs) -> bool:
    result = evaluate(facts_for(inputs), NOW)
    return any(alert.rule_id is RuleId.SEPSIS_NEWS2 for alert in result.alerts)


@EXAMPLES
@given(st.data())
def test_worsening_never_removes_a_raised_sepsis_alert(data: st.DataObject) -> None:
    inputs = data.draw(dense_inputs())
    assume(has_sepsis_alert(inputs))
    present: list[Parameter] = [p for p in PARAMETERS if getattr(inputs, p) is not None]
    parameter: Parameter = data.draw(st.sampled_from(present))
    current = getattr(inputs, parameter)
    worsened = replace(inputs, **{parameter: worse(data, parameter, current)})
    assert has_sepsis_alert(worsened)


@EXAMPLES
@given(some_inputs())
def test_the_score_is_deterministic(inputs: News2Inputs) -> None:
    assert score_news2(inputs) == score_news2(inputs)

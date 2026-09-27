from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping
from typing import Any

_STAGES: tuple[tuple[str, str, str], ...] = (
    ("stt_end_of_turn", "Caller speech plus end-of-turn", "first_audio_to_speech_final_ms"),
    (
        "post_speech_turn_commit",
        "Post-speech turn commit",
        "last_voice_frame_to_turn_committed_ms",
    ),
    ("turn_commit", "Turn commit", "speech_final_to_turn_committed_ms"),
    ("llm_first_chunk", "LLM first chunk", "turn_committed_to_llm_first_chunk_ms"),
    ("tts_first_audio", "TTS first audio", "llm_first_chunk_to_tts_first_audio_ms"),
    ("plivo_first_play", "Plivo first playAudio", "tts_first_audio_to_first_play_audio_ms"),
)
# The kiosk browser channel (M1-14): end of speech -> final transcript -> intake API response ->
# first TTS byte. Recorded per turn by voice_agent.kiosk.loop as "kiosk_turn_latency" events.
KIOSK_TURN_LATENCY_EVENT = "kiosk_turn_latency"
_KIOSK_STAGES: tuple[tuple[str, str, str], ...] = (
    (
        "speech_end_to_transcript",
        "End of speech to final transcript",
        "speech_end_to_transcript_ms",
    ),
    (
        "transcript_to_api_response",
        "Final transcript to intake API response",
        "transcript_to_api_response_ms",
    ),
    (
        "api_response_to_first_tts_byte",
        "API response to first TTS byte",
        "api_response_to_first_tts_byte_ms",
    ),
    ("total", "End of speech to first TTS byte", "speech_end_to_first_tts_byte_ms"),
)
_SOURCE_RECOMMENDATION_MIN_COUNT = 3
_SOURCE_RECOMMENDATION_MIN_ADVANTAGE_MS = 250.0
_LLM_FIRST_CHUNK_RECOMMENDATION_MIN_P50_MS = 750.0
_LLM_FIRST_CHUNK_RECOMMENDATION_MIN_P50_WITH_OVERLAP_MS = 1000.0
_STRONG_FAST_ANSWER_FIRST_AUDIO_P50_MS = 1500.0
_SPECULATIVE_RECOMMENDATION_MIN_REUSE_RATE = 0.75
_SPECULATIVE_RECOMMENDATION_MIN_FIRST_AUDIO_LEAD_MS = 50.0


def summarize_voice_turn_latency_events(
    events: Iterable[Mapping[str, Any]],
    *,
    call_id: str | None = None,
) -> dict[str, Any]:
    clean_call_id = call_id.strip() if isinstance(call_id, str) else None
    if clean_call_id == "":
        clean_call_id = None
    events_list = list(events)
    voice_turns = [
        event
        for event in events_list
        if event.get("event") == "voice_turn_latency"
        and (clean_call_id is None or event.get("call_id") == clean_call_id)
    ]
    stages = {
        key: _summarize_stage(label=label, field=field, events=voice_turns)
        for key, label, field in _STAGES
    }
    bottleneck = _bottleneck(stages)
    ack_used_count = sum(1 for event in voice_turns if _as_bool(event.get("pre_response_ack_used")))
    speculative_response_events = [
        event for event in voice_turns if _as_bool(event.get("speculative_response_used"))
    ]
    speculative_response_used_count = len(speculative_response_events)
    speculative_response_started_count = sum(
        _speculative_response_started_count(event) for event in voice_turns
    )
    speculative_response_discarded_count = sum(
        _speculative_response_discarded_count(event) for event in voice_turns
    )
    turn_count = len(voice_turns)
    speculative_response = {
        "started_count": speculative_response_started_count,
        "used_count": speculative_response_used_count,
        "discarded_count": speculative_response_discarded_count,
        "used_rate": (speculative_response_used_count / turn_count if turn_count else 0.0),
        "reuse_rate": (
            speculative_response_used_count / speculative_response_started_count
            if speculative_response_started_count
            else 0.0
        ),
        "discarded_rate": (
            speculative_response_discarded_count / speculative_response_started_count
            if speculative_response_started_count
            else 0.0
        ),
        "generation_lead": _summarize_values(
            label="Speculative generation lead before turn commit",
            field="speculative_response_generation_started_to_commit_ms",
            values=[
                value
                for event in speculative_response_events
                if (
                    value := _as_non_negative_float(
                        event.get("speculative_response_generation_started_to_commit_ms")
                    )
                )
                is not None
            ],
        ),
        "first_audio_lead": _summarize_values(
            label="Speculative first audio lead before turn commit",
            field="speculative_response_tts_first_audio_to_commit_ms",
            values=[
                value
                for event in speculative_response_events
                if (
                    value := _as_non_negative_float(
                        event.get("speculative_response_tts_first_audio_to_commit_ms")
                    )
                )
                is not None
            ],
        ),
    }
    turn_commit_sources = _turn_commit_source_summary(voice_turns)
    response_plans = _response_plan_summary(voice_turns)
    streaming_overlap = _streaming_overlap_summary(
        events=voice_turns,
        turn_count=turn_count,
    )
    answer_first_audio = _answer_first_audio_summary(voice_turns)
    post_speech_endpointing = _post_speech_endpointing_summary(voice_turns)
    tuning_actions = _tuning_actions(
        bottleneck=bottleneck,
        streaming_overlap=streaming_overlap,
        answer_first_audio=answer_first_audio,
        speculative_response=speculative_response,
        turn_commit_sources=turn_commit_sources,
    )
    return {
        "turn_count": turn_count,
        "kiosk": summarize_kiosk_turn_latency_events(events_list),
        "outcomes": _outcome_summary(voice_turns),
        "ack_used_count": ack_used_count,
        "ack_used_rate": (ack_used_count / turn_count) if turn_count else 0.0,
        "speculative_response": speculative_response,
        "streaming_overlap": streaming_overlap,
        "turn_commit_sources": turn_commit_sources,
        "response_plans": response_plans,
        "pre_response_ack": _pre_response_ack_summary(
            events=voice_turns,
            turn_count=turn_count,
        ),
        "pre_response_ack_cache": _pre_response_ack_cache_summary(voice_turns),
        "reply_streaming": _reply_streaming_summary(voice_turns),
        "answer_first_audio": answer_first_audio,
        "post_speech_endpointing": post_speech_endpointing,
        "stages": stages,
        "bottleneck": bottleneck,
        "tuning_actions": tuning_actions,
        "recommendations": _recommendations(
            bottleneck,
            tuning_actions=tuning_actions,
        ),
    }


def summarize_kiosk_turn_latency_events(
    events: Iterable[Mapping[str, Any]],
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """p50/p95/max per kiosk turn stage (milliseconds)."""
    turns = [
        event
        for event in events
        if event.get("event") == KIOSK_TURN_LATENCY_EVENT
        and (session_id is None or event.get("session_id") == session_id)
    ]
    return {
        "turn_count": len(turns),
        "stages": {
            key: _summarize_stage(label=label, field=field, events=turns)
            for key, label, field in _KIOSK_STAGES
        },
    }


def _reply_streaming_summary(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "first_reply_chunk": _summarize_values(
            label="First speakable reply chunk",
            field="turn_committed_to_first_reply_chunk_ms",
            values=[
                value
                for event in events
                if (
                    value := _as_non_negative_float(
                        event.get("turn_committed_to_first_reply_chunk_ms")
                    )
                )
                is not None
            ],
        ),
        "llm_to_first_reply_chunk": _summarize_values(
            label="LLM first chunk to first speakable reply chunk",
            field="llm_first_chunk_to_first_reply_chunk_ms",
            values=[
                value
                for event in events
                if (
                    value := _as_non_negative_float(
                        event.get("llm_first_chunk_to_first_reply_chunk_ms")
                    )
                )
                is not None
            ],
        ),
        "first_reply_chunk_to_tts_first_audio": _summarize_values(
            label="First speakable reply chunk to TTS first audio",
            field="first_reply_chunk_to_tts_first_audio_ms",
            values=[
                value
                for event in events
                if (
                    value := _as_non_negative_float(
                        event.get("first_reply_chunk_to_tts_first_audio_ms")
                    )
                )
                is not None
            ],
        ),
    }


def _post_speech_endpointing_summary(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    """True post-speech STT/network endpointing latency: last_voice_frame -> turn_committed
    with the deliberate end-of-turn silence wait subtracted out, so the dashboard does not
    mistake the configured silence window for slow STT."""
    values: list[float] = []
    for event in events:
        commit_ms = _as_non_negative_float(event.get("last_voice_frame_to_turn_committed_ms"))
        if commit_ms is None:
            continue
        silence_ms = _as_non_negative_float(event.get("end_of_turn_silence_ms")) or 0.0
        values.append(max(0.0, commit_ms - silence_ms))
    return _summarize_values(
        label="Post-speech STT/network endpointing (silence removed)",
        field="post_speech_endpointing_ms",
        values=values,
    )


def _outcome_summary(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Count terminal turn outcomes so turn error rate is observable. Turns recorded before
    outcome tracking (and successful turns) are counted as "success"."""
    counts: dict[str, int] = {}
    for event in events:
        raw = event.get("outcome")
        outcome = raw.strip().lower() if isinstance(raw, str) and raw.strip() else "success"
        counts[outcome] = counts.get(outcome, 0) + 1
    total = len(events)
    failure_count = sum(count for outcome, count in counts.items() if outcome != "success")
    return {
        "counts": dict(sorted(counts.items())),
        "failure_count": failure_count,
        "failure_rate": (failure_count / total) if total else 0.0,
    }


def _turn_commit_source_summary(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    turn_count = len(events)
    grouped_events: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        source = _clean_turn_commit_source(event.get("turn_commit_source"))
        grouped_events.setdefault(source, []).append(event)
    return {
        source: {
            "count": len(source_events),
            "rate": (len(source_events) / turn_count) if turn_count else 0.0,
            "answer_first_audio": _answer_first_audio_summary(source_events),
        }
        for source, source_events in sorted(grouped_events.items())
    }


def _clean_turn_commit_source(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    clean_value = value.strip().lower()
    if clean_value in {
        "synchronous_stt",
        "stt_speech_final",
        "local_silence",
        "fast_final_local_silence",
        "interim_local_silence",
    }:
        return clean_value
    return "unknown"


def _response_plan_summary(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    turn_count = len(events)
    grouped_events: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        action = _clean_response_plan_action(event.get("response_plan_action"))
        grouped_events.setdefault(action, []).append(event)
    return {
        action: {
            "count": len(action_events),
            "rate": (len(action_events) / turn_count) if turn_count else 0.0,
        }
        for action, action_events in sorted(grouped_events.items())
    }


def _clean_response_plan_action(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    clean_value = value.strip().lower()
    if clean_value in {
        "streaming_llm",
        "fast_path_text",
        "fast_path_audio",
        "clarify",
        "transfer_or_escalate",
        "hold_ack_then_llm",
    }:
        return clean_value
    return "unknown"


def _pre_response_ack_cache_summary(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    cache_events = [
        event for event in events if isinstance(event.get("pre_response_ack_cache_hit"), bool)
    ]
    observed_count = len(cache_events)
    hit_count = sum(1 for event in cache_events if event.get("pre_response_ack_cache_hit") is True)
    miss_count = observed_count - hit_count
    return {
        "observed_count": observed_count,
        "hit_count": hit_count,
        "miss_count": miss_count,
        "hit_rate": (hit_count / observed_count) if observed_count else 0.0,
    }


def _streaming_overlap_summary(
    *,
    events: list[Mapping[str, Any]],
    turn_count: int,
) -> dict[str, Any]:
    overlap_values = [
        value
        for event in events
        if (
            value := _as_positive_float(
                event.get("answer_first_play_audio_to_llm_stream_completed_ms")
            )
        )
        is not None
    ]
    overlapped_count = len(overlap_values)
    return {
        "overlapped_count": overlapped_count,
        "overlapped_rate": (overlapped_count / turn_count) if turn_count else 0.0,
        "answer_audio_before_llm_completed": _summarize_values(
            label="Answer audio before LLM stream completed",
            field="answer_first_play_audio_to_llm_stream_completed_ms",
            values=overlap_values,
        ),
    }


def _summarize_stage(
    *,
    label: str,
    field: str,
    events: list[Mapping[str, Any]],
) -> dict[str, Any]:
    values = sorted(value for event in events if (value := _stage_value(event, field)) is not None)
    if not values:
        return _empty_summary(label=label, field=field)
    return _summarize_values(label=label, field=field, values=values)


def _stage_value(event: Mapping[str, Any], field: str) -> float | None:
    value = _as_non_negative_float(event.get(field))
    if field == "tts_first_audio_to_first_play_audio_ms" and _as_bool(
        event.get("speculative_response_used")
    ):
        committed_to_play = _as_non_negative_float(
            event.get("turn_committed_to_first_play_audio_ms")
        )
        if committed_to_play is not None:
            return min(value, committed_to_play) if value is not None else committed_to_play
    return value


def _pre_response_ack_summary(
    *,
    events: list[Mapping[str, Any]],
    turn_count: int,
) -> dict[str, Any]:
    ack_events = [event for event in events if _as_bool(event.get("pre_response_ack_used"))]
    used_count = len(ack_events)
    ack_delay_values = [
        value
        for event in ack_events
        if (value := _as_non_negative_float(event.get("turn_committed_to_pre_response_ack_ms")))
        is not None
    ]
    answer_delay_values = [
        value
        for event in ack_events
        if (
            value := _as_non_negative_float(
                event.get("turn_committed_to_answer_first_play_audio_ms")
            )
        )
        is not None
    ]
    estimated_savings_values: list[float] = []
    for event in ack_events:
        ack_delay = _as_non_negative_float(event.get("turn_committed_to_pre_response_ack_ms"))
        answer_delay = _as_non_negative_float(
            event.get("turn_committed_to_answer_first_play_audio_ms")
        )
        if ack_delay is None or answer_delay is None:
            continue
        estimated_savings_values.append(max(0.0, answer_delay - ack_delay))
    return {
        "used_count": used_count,
        "used_rate": (used_count / turn_count) if turn_count else 0.0,
        "ack_delay": _summarize_values(
            label="Pre-response ack first playAudio",
            field="turn_committed_to_pre_response_ack_ms",
            values=ack_delay_values,
        ),
        "answer_delay_when_ack_used": _summarize_values(
            label="Answer first playAudio when ack used",
            field="turn_committed_to_answer_first_play_audio_ms",
            values=answer_delay_values,
        ),
        "estimated_savings": _summarize_values(
            label="Estimated perceived silence avoided",
            field=(
                "turn_committed_to_answer_first_play_audio_ms - "
                "turn_committed_to_pre_response_ack_ms"
            ),
            values=estimated_savings_values,
        ),
    }


def _answer_first_audio_summary(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    values = [value for event in events if (value := _answer_first_audio_value(event)) is not None]
    return _summarize_values(
        label="Answer first playAudio",
        field="turn_committed_to_answer_first_play_audio_ms",
        values=values,
    )


def _speculative_response_started_count(event: Mapping[str, Any]) -> int:
    value = _as_non_negative_int(event.get("speculative_response_started_count"))
    if value is not None:
        return value
    return 1 if _as_bool(event.get("speculative_response_used")) else 0


def _speculative_response_discarded_count(event: Mapping[str, Any]) -> int:
    return _as_non_negative_int(event.get("speculative_response_discarded_count")) or 0


def _answer_first_audio_value(event: Mapping[str, Any]) -> float | None:
    answer_value = _as_non_negative_float(event.get("turn_committed_to_answer_first_play_audio_ms"))
    if answer_value is not None:
        return answer_value
    return _as_non_negative_float(event.get("turn_committed_to_first_play_audio_ms"))


def _empty_summary(*, label: str, field: str) -> dict[str, Any]:
    return {
        "label": label,
        "field": field,
        "count": 0,
        "p50_ms": None,
        "p95_ms": None,
        "max_ms": None,
    }


def _summarize_values(
    *,
    label: str,
    field: str,
    values: list[float],
) -> dict[str, Any]:
    if not values:
        return {
            "label": label,
            "field": field,
            "count": 0,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
        }
    sorted_values = sorted(values)
    return {
        "label": label,
        "field": field,
        "count": len(sorted_values),
        "p50_ms": _round_ms(statistics.median(sorted_values)),
        "p95_ms": _round_ms(_percentile(sorted_values, 0.95)),
        "max_ms": _round_ms(max(sorted_values)),
    }


def _bottleneck(stages: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    non_actionable_stages = {"stt_end_of_turn"}
    candidates = [
        (stage, data)
        for stage, data in stages.items()
        if stage not in non_actionable_stages and isinstance(data.get("p50_ms"), int | float)
    ]
    if not candidates:
        return None
    stage, data = max(candidates, key=lambda item: float(item[1]["p50_ms"]))
    return {
        "stage": stage,
        "label": data["label"],
        "p50_ms": data["p50_ms"],
        "field": data["field"],
    }


def _recommendations(
    bottleneck: dict[str, Any] | None,
    *,
    tuning_actions: list[dict[str, Any]],
) -> list[str]:
    if bottleneck is None:
        return ["No voice_turn_latency events found. Run a real or local media turn first."]
    stage = bottleneck["stage"]
    if stage == "post_speech_turn_commit":
        recommendations = [
            "Tune local silence, interim stability, and STT endpointing; "
            "post-speech turn commit is the dominant actionable stage."
        ]
    elif stage == "llm_first_chunk":
        recommendations = [
            "Optimize the orchestration backend first; "
            "LLM first-token latency is the dominant stage."
        ]
    elif stage == "tts_first_audio":
        recommendations = [
            "Tune or replace the TTS provider first; first audio generation is dominant."
        ]
    elif stage == "plivo_first_play":
        recommendations = [
            "Investigate outbound Plivo WebSocket sends; media delivery is dominant."
        ]
    else:
        recommendations = ["Inspect the dominant stage before changing providers or model quality."]
    for action in tuning_actions:
        recommendation = action.get("recommendation")
        if isinstance(recommendation, str) and recommendation:
            recommendations.append(recommendation)
    return recommendations


def _tuning_actions(
    *,
    bottleneck: Mapping[str, Any] | None,
    streaming_overlap: Mapping[str, Any],
    answer_first_audio: Mapping[str, Any] | None,
    speculative_response: Mapping[str, Any],
    turn_commit_sources: Mapping[str, Any],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    bottleneck_action = _bottleneck_tuning_action(
        bottleneck,
        streaming_overlap=streaming_overlap,
        answer_first_audio=answer_first_audio,
    )
    if bottleneck_action is not None:
        actions.append(bottleneck_action)
    actions.extend(_source_latency_tuning_actions(turn_commit_sources))
    speculative_action = _speculative_response_tuning_action(
        speculative_response,
        answer_first_audio=answer_first_audio,
    )
    if speculative_action is not None:
        actions.append(speculative_action)
    return actions


def _bottleneck_tuning_action(
    bottleneck: Mapping[str, Any] | None,
    *,
    streaming_overlap: Mapping[str, Any],
    answer_first_audio: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if bottleneck is None or bottleneck.get("stage") != "llm_first_chunk":
        return None
    llm_first_chunk_p50 = _as_non_negative_float(bottleneck.get("p50_ms"))
    overlapped_count = _as_non_negative_int(streaming_overlap.get("overlapped_count")) or 0
    answer_first_audio_p50 = (
        _as_non_negative_float(answer_first_audio.get("p50_ms"))
        if isinstance(answer_first_audio, Mapping)
        else None
    )
    if (
        llm_first_chunk_p50 is None
        or (
            answer_first_audio_p50 is not None
            and answer_first_audio_p50 <= _STRONG_FAST_ANSWER_FIRST_AUDIO_P50_MS
        )
        or (
            overlapped_count > 0
            and llm_first_chunk_p50 < _LLM_FIRST_CHUNK_RECOMMENDATION_MIN_P50_WITH_OVERLAP_MS
        )
        or (
            overlapped_count <= 0
            and llm_first_chunk_p50 < _LLM_FIRST_CHUNK_RECOMMENDATION_MIN_P50_MS
        )
    ):
        return None
    threshold_ms = (
        _LLM_FIRST_CHUNK_RECOMMENDATION_MIN_P50_WITH_OVERLAP_MS
        if overlapped_count > 0
        else _LLM_FIRST_CHUNK_RECOMMENDATION_MIN_P50_MS
    )
    return {
        "id": "reduce_orchestrator_first_token_latency",
        "category": "llm",
        "action": "optimize_first_token_latency",
        "targets": [
            "VOICE_AGENT_ORCHESTRATOR_URL",
            "VOICE_AGENT_ORCHESTRATOR_FIRST_CHUNK_TIMEOUT_SECONDS",
            "VOICE_AGENT_ORCHESTRATOR_REQUIRE_STREAMING_RESPONSE",
            "FAST_LLM_MODEL",
        ],
        "reason": "llm_first_chunk_p50_at_or_above_750ms",
        "evidence": {
            "stage": "llm_first_chunk",
            "llm_first_chunk_p50_ms": llm_first_chunk_p50,
            "threshold_ms": threshold_ms,
            "streaming_overlap_count": overlapped_count,
        },
        "recommendation": (
            "Orchestrator first-token latency is blocking the real answer path; "
            "move to a lower-latency model or region, keep the orchestrator warm, "
            "trim prompt/tool work before first token, and preserve streaming "
            "text/event-stream responses."
        ),
    }


def _source_latency_tuning_actions(
    turn_commit_sources: Mapping[str, Any],
) -> list[dict[str, Any]]:
    source_p50s = {
        source: p50
        for source in (
            "fast_final_local_silence",
            "interim_local_silence",
            "local_silence",
            "stt_speech_final",
            "synchronous_stt",
        )
        if (p50 := _source_answer_first_audio_p50(turn_commit_sources, source)) is not None
    }
    actions: list[dict[str, Any]] = []
    fast_final_p50 = source_p50s.get("fast_final_local_silence")
    later_source_p50s = [
        p50
        for source, p50 in source_p50s.items()
        if source not in {"fast_final_local_silence", "interim_local_silence"}
    ]
    if (
        fast_final_p50 is not None
        and later_source_p50s
        and fast_final_p50 + _SOURCE_RECOMMENDATION_MIN_ADVANTAGE_MS <= min(later_source_p50s)
    ):
        actions.append(
            {
                "id": "preserve_fast_final_local_silence",
                "category": "end_of_turn",
                "action": "keep_enabled",
                "targets": ["VOICE_AGENT_FAST_FINAL_END_OF_TURN_SILENCE_MS"],
                "reason": "source_answer_first_audio_p50_at_least_250ms_faster",
                "evidence": {
                    "source": "fast_final_local_silence",
                    "source_answer_first_audio_p50_ms": fast_final_p50,
                    "comparison_answer_first_audio_p50_ms": min(later_source_p50s),
                    "advantage_threshold_ms": _SOURCE_RECOMMENDATION_MIN_ADVANTAGE_MS,
                    "min_turn_count": _SOURCE_RECOMMENDATION_MIN_COUNT,
                },
                "recommendation": (
                    "Fast-final local silence is producing faster answers than later commit "
                    "sources; keep VOICE_AGENT_FAST_FINAL_END_OF_TURN_SILENCE_MS enabled "
                    "and tune it from recordings."
                ),
            }
        )
    interim_p50 = source_p50s.get("interim_local_silence")
    conservative_source_p50s = [
        p50 for source, p50 in source_p50s.items() if source != "interim_local_silence"
    ]
    if (
        interim_p50 is not None
        and conservative_source_p50s
        and interim_p50 + _SOURCE_RECOMMENDATION_MIN_ADVANTAGE_MS > min(conservative_source_p50s)
    ):
        actions.append(
            {
                "id": "tighten_interim_local_silence",
                "category": "end_of_turn",
                "action": "increase_or_disable",
                "targets": ["VOICE_AGENT_INTERIM_TRANSCRIPT_STABILITY_MS"],
                "reason": (
                    "source_answer_first_audio_p50_not_250ms_faster_than_conservative_source"
                ),
                "evidence": {
                    "source": "interim_local_silence",
                    "source_answer_first_audio_p50_ms": interim_p50,
                    "comparison_answer_first_audio_p50_ms": min(conservative_source_p50s),
                    "advantage_threshold_ms": _SOURCE_RECOMMENDATION_MIN_ADVANTAGE_MS,
                    "min_turn_count": _SOURCE_RECOMMENDATION_MIN_COUNT,
                },
                "recommendation": (
                    "Interim local-silence commits are not improving answer latency enough to "
                    "justify extra accuracy risk; raise "
                    "VOICE_AGENT_INTERIM_TRANSCRIPT_STABILITY_MS or disable interim commits "
                    "for this project."
                ),
            }
        )
    return actions


def _source_answer_first_audio_p50(
    turn_commit_sources: Mapping[str, Any],
    source: str,
) -> float | None:
    raw_stats = turn_commit_sources.get(source)
    if not isinstance(raw_stats, Mapping):
        return None
    count = _as_non_negative_int(raw_stats.get("count")) or 0
    if count < _SOURCE_RECOMMENDATION_MIN_COUNT:
        return None
    answer_first_audio = raw_stats.get("answer_first_audio")
    if not isinstance(answer_first_audio, Mapping):
        return None
    answer_count = _as_non_negative_int(answer_first_audio.get("count")) or 0
    if answer_count < _SOURCE_RECOMMENDATION_MIN_COUNT:
        return None
    return _as_non_negative_float(answer_first_audio.get("p50_ms"))


def _speculative_response_tuning_action(
    speculative_response: Mapping[str, Any],
    *,
    answer_first_audio: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    started_count = _as_non_negative_int(speculative_response.get("started_count")) or 0
    if started_count <= 0:
        return None
    answer_first_audio_p50 = (
        _as_non_negative_float(answer_first_audio.get("p50_ms"))
        if isinstance(answer_first_audio, Mapping)
        else None
    )
    if (
        answer_first_audio_p50 is not None
        and answer_first_audio_p50 <= _STRONG_FAST_ANSWER_FIRST_AUDIO_P50_MS
    ):
        return None
    discarded_rate = _as_non_negative_float(speculative_response.get("discarded_rate")) or 0.0
    reuse_rate = _as_non_negative_float(speculative_response.get("reuse_rate")) or 0.0
    used_count = _as_non_negative_int(speculative_response.get("used_count")) or 0
    if started_count >= 3 and discarded_rate >= 0.5:
        return {
            "id": "tune_speculative_response_prewarm",
            "category": "speculative_response",
            "action": "increase_thresholds_or_disable",
            "targets": [
                "VOICE_AGENT_SPECULATIVE_RESPONSE_MIN_CHARS",
                "VOICE_AGENT_SPECULATIVE_RESPONSE_STABILITY_MS",
            ],
            "reason": "discarded_rate_at_or_above_50_percent",
            "evidence": {
                "started_count": started_count,
                "discarded_rate": discarded_rate,
                "reuse_rate": reuse_rate,
            },
            "recommendation": (
                "Speculative prewarm is wasting provider work; raise "
                "VOICE_AGENT_SPECULATIVE_RESPONSE_MIN_CHARS or "
                "VOICE_AGENT_SPECULATIVE_RESPONSE_STABILITY_MS, or disable prewarm "
                "for this project."
            ),
        }
    generation_lead = speculative_response.get("generation_lead")
    generation_lead_p50 = (
        _as_non_negative_float(generation_lead.get("p50_ms"))
        if isinstance(generation_lead, Mapping)
        else None
    )
    first_audio_lead = speculative_response.get("first_audio_lead")
    first_audio_lead_p50 = (
        _as_non_negative_float(first_audio_lead.get("p50_ms"))
        if isinstance(first_audio_lead, Mapping)
        else None
    )
    if (
        reuse_rate >= _SPECULATIVE_RECOMMENDATION_MIN_REUSE_RATE
        and generation_lead_p50 is not None
        and generation_lead_p50 > 0
        and (
            first_audio_lead_p50 is None
            or first_audio_lead_p50 < _SPECULATIVE_RECOMMENDATION_MIN_FIRST_AUDIO_LEAD_MS
        )
    ):
        return {
            "id": "increase_speculative_response_audio_lead",
            "category": "speculative_response",
            "action": "increase_audio_lead",
            "targets": [
                "VOICE_AGENT_SPECULATIVE_RESPONSE_STABILITY_MS",
                "VOICE_AGENT_INTERIM_TRANSCRIPT_STABILITY_MS",
                "VOICE_AGENT_SARVAM_STREAMING",
            ],
            "reason": "reuse_rate_high_but_first_audio_lead_below_50ms",
            "evidence": {
                "started_count": started_count,
                "reuse_rate": reuse_rate,
                "generation_lead_p50_ms": generation_lead_p50,
                "first_audio_lead_p50_ms": first_audio_lead_p50,
                "min_first_audio_lead_ms": (_SPECULATIVE_RECOMMENDATION_MIN_FIRST_AUDIO_LEAD_MS),
            },
            "recommendation": (
                "Speculative prewarm is reused, but TTS first audio is not ready far "
                "enough before commit; lower VOICE_AGENT_SPECULATIVE_RESPONSE_STABILITY_MS, "
                "raise VOICE_AGENT_INTERIM_TRANSCRIPT_STABILITY_MS from recordings, or keep "
                "streaming TTS enabled so prewarm hides real audio latency."
            ),
        }
    if (
        used_count >= 1
        and generation_lead_p50 is not None
        and generation_lead_p50 > 0
        and (
            first_audio_lead_p50 is None
            or first_audio_lead_p50 < _SPECULATIVE_RECOMMENDATION_MIN_FIRST_AUDIO_LEAD_MS
        )
    ):
        return {
            "id": "increase_speculative_response_audio_lead",
            "category": "speculative_response",
            "action": "increase_audio_lead",
            "targets": [
                "VOICE_AGENT_SPECULATIVE_RESPONSE_STABILITY_MS",
                "VOICE_AGENT_INTERIM_TRANSCRIPT_STABILITY_MS",
                "VOICE_AGENT_SARVAM_STREAMING",
            ],
            "reason": "speculative_audio_not_ready_before_commit",
            "evidence": {
                "started_count": started_count,
                "reuse_rate": reuse_rate,
                "generation_lead_p50_ms": generation_lead_p50,
                "first_audio_lead_p50_ms": first_audio_lead_p50,
                "min_first_audio_lead_ms": (_SPECULATIVE_RECOMMENDATION_MIN_FIRST_AUDIO_LEAD_MS),
            },
            "recommendation": (
                "Speculative generation starts before commit, but TTS first audio is not "
                "ready before the committed turn; lower "
                "VOICE_AGENT_SPECULATIVE_RESPONSE_STABILITY_MS, raise "
                "VOICE_AGENT_INTERIM_TRANSCRIPT_STABILITY_MS from recordings, or improve "
                "TTS first-audio latency so prewarm hides the real answer delay."
            ),
        }
    if (
        started_count >= 3
        and reuse_rate >= _SPECULATIVE_RECOMMENDATION_MIN_REUSE_RATE
        and generation_lead_p50 is not None
        and generation_lead_p50 > 0
        and first_audio_lead_p50 is not None
        and first_audio_lead_p50 >= _SPECULATIVE_RECOMMENDATION_MIN_FIRST_AUDIO_LEAD_MS
    ):
        return {
            "id": "preserve_speculative_response_prewarm",
            "category": "speculative_response",
            "action": "keep_enabled",
            "targets": ["VOICE_AGENT_SPECULATIVE_RESPONSE_PREWARM"],
            "reason": ("reuse_rate_at_or_above_75_percent_with_first_audio_lead_at_or_above_50ms"),
            "evidence": {
                "started_count": started_count,
                "reuse_rate": reuse_rate,
                "generation_lead_p50_ms": generation_lead_p50,
                "first_audio_lead_p50_ms": first_audio_lead_p50,
            },
            "recommendation": (
                "Speculative prewarm is reliably reused and first audio is ready before "
                "commit; keep it enabled while tuning end-of-turn gates from recordings."
            ),
        }
    return None


def _as_non_negative_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _as_positive_float(value: Any) -> float | None:
    parsed = _as_non_negative_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _as_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        parsed = int(value)
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
    else:
        return None
    if parsed < 0:
        return None
    return parsed


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return False


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("values must not be empty.")
    index = math.ceil(len(values) * percentile) - 1
    return values[max(0, min(index, len(values) - 1))]


def _round_ms(value: float) -> float:
    return round(value, 1)

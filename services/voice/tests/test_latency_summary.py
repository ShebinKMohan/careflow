from voice_agent.latency import summarize_voice_turn_latency_events


def test_summarize_voice_turn_latency_events_identifies_dominant_stage() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {"event": "media_start", "first_audio_to_speech_final_ms": 999},
            {
                "event": "voice_turn_latency",
                "first_audio_to_speech_final_ms": "420",
                "turn_committed_to_llm_first_chunk_ms": "1800",
                "turn_committed_to_first_reply_chunk_ms": 1900,
                "llm_first_chunk_to_first_reply_chunk_ms": 100,
                "llm_first_chunk_to_tts_first_audio_ms": 260,
                "first_reply_chunk_to_tts_first_audio_ms": 160,
                "tts_first_audio_to_first_play_audio_ms": 40,
                "turn_committed_to_first_play_audio_ms": 2100,
                "pre_response_ack_used": "true",
                "turn_committed_to_pre_response_ack_ms": "360",
                "turn_committed_to_answer_first_play_audio_ms": "2100",
            },
            {
                "event": "voice_turn_latency",
                "first_audio_to_speech_final_ms": 380,
                "turn_committed_to_llm_first_chunk_ms": 2200,
                "turn_committed_to_first_reply_chunk_ms": 2340,
                "llm_first_chunk_to_first_reply_chunk_ms": 140,
                "llm_first_chunk_to_tts_first_audio_ms": 320,
                "first_reply_chunk_to_tts_first_audio_ms": 180,
                "tts_first_audio_to_first_play_audio_ms": 60,
                "turn_committed_to_first_play_audio_ms": 2580,
                "pre_response_ack_used": False,
                "turn_committed_to_answer_first_play_audio_ms": 2580,
            },
        ]
    )

    assert summary["turn_count"] == 2
    assert summary["ack_used_count"] == 1
    assert summary["ack_used_rate"] == 0.5
    assert summary["stages"]["stt_end_of_turn"]["p50_ms"] == 400.0
    assert summary["stages"]["llm_first_chunk"]["p50_ms"] == 2000.0
    assert summary["answer_first_audio"]["p50_ms"] == 2340.0
    assert summary["reply_streaming"]["first_reply_chunk"]["p50_ms"] == 2120.0
    assert summary["reply_streaming"]["llm_to_first_reply_chunk"]["p50_ms"] == 120.0
    assert summary["reply_streaming"]["first_reply_chunk_to_tts_first_audio"]["p50_ms"] == 170.0
    assert summary["bottleneck"] == {
        "stage": "llm_first_chunk",
        "label": "LLM first chunk",
        "p50_ms": 2000.0,
        "field": "turn_committed_to_llm_first_chunk_ms",
    }
    assert summary["recommendations"] == [
        "Optimize the orchestration backend first; LLM first-token latency is the dominant stage.",
        "Orchestrator first-token latency is blocking the real answer path; "
        "move to a lower-latency model or region, keep the orchestrator warm, "
        "trim prompt/tool work before first token, and preserve streaming text/event-stream "
        "responses.",
    ]


def test_summarize_voice_turn_latency_events_recommends_orchestrator_first_token_tuning() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "turn_committed_to_llm_first_chunk_ms": 980,
                "llm_first_chunk_to_tts_first_audio_ms": 420,
                "tts_first_audio_to_first_play_audio_ms": 40,
                "turn_committed_to_answer_first_play_audio_ms": 1640,
            }
        ]
    )

    assert summary["bottleneck"] == {
        "stage": "llm_first_chunk",
        "label": "LLM first chunk",
        "p50_ms": 980.0,
        "field": "turn_committed_to_llm_first_chunk_ms",
    }
    assert summary["tuning_actions"] == [
        {
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
                "llm_first_chunk_p50_ms": 980.0,
                "threshold_ms": 750.0,
                "streaming_overlap_count": 0,
            },
            "recommendation": (
                "Orchestrator first-token latency is blocking the real answer path; "
                "move to a lower-latency model or region, keep the orchestrator warm, "
                "trim prompt/tool work before first token, and preserve streaming "
                "text/event-stream responses."
            ),
        }
    ]


def test_summarize_voice_turn_latency_events_skips_llm_tuning_when_answer_is_strongly_fast() -> (
    None
):
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "turn_committed_to_llm_first_chunk_ms": 830.2,
                "llm_first_chunk_to_tts_first_audio_ms": 304.6,
                "tts_first_audio_to_first_play_audio_ms": 52.5,
                "turn_committed_to_answer_first_play_audio_ms": 1187.3,
            }
        ]
    )

    assert summary["bottleneck"] == {
        "stage": "llm_first_chunk",
        "label": "LLM first chunk",
        "p50_ms": 830.2,
        "field": "turn_committed_to_llm_first_chunk_ms",
    }
    assert summary["answer_first_audio"]["p50_ms"] == 1187.3
    assert summary["streaming_overlap"]["overlapped_count"] == 0
    assert summary["tuning_actions"] == []
    assert summary["recommendations"] == [
        "Optimize the orchestration backend first; LLM first-token latency is the dominant stage."
    ]


def test_summarize_voice_turn_latency_events_allows_moderate_llm_latency_with_overlap() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "turn_committed_to_llm_first_chunk_ms": 807,
                "llm_first_chunk_to_tts_first_audio_ms": 620,
                "tts_first_audio_to_first_play_audio_ms": 125,
                "turn_committed_to_answer_first_play_audio_ms": 1552,
                "answer_first_play_audio_to_llm_stream_completed_ms": 86,
            }
        ]
    )

    assert summary["bottleneck"] == {
        "stage": "llm_first_chunk",
        "label": "LLM first chunk",
        "p50_ms": 807.0,
        "field": "turn_committed_to_llm_first_chunk_ms",
    }
    assert summary["streaming_overlap"]["overlapped_count"] == 1
    assert summary["tuning_actions"] == []
    assert summary["recommendations"] == [
        "Optimize the orchestration backend first; LLM first-token latency is the dominant stage."
    ]


def test_summarize_voice_turn_latency_events_bottleneck_uses_post_speech_delay() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "first_audio_to_speech_final_ms": 2200,
                "first_audio_to_last_voice_frame_ms": 1800,
                "last_voice_frame_to_turn_committed_ms": 320,
                "turn_committed_to_llm_first_chunk_ms": 40,
                "llm_first_chunk_to_tts_first_audio_ms": 30,
                "tts_first_audio_to_first_play_audio_ms": 10,
            }
        ]
    )

    assert summary["stages"]["stt_end_of_turn"]["p50_ms"] == 2200.0
    assert summary["stages"]["post_speech_turn_commit"]["p50_ms"] == 320.0
    assert summary["bottleneck"] == {
        "stage": "post_speech_turn_commit",
        "label": "Post-speech turn commit",
        "p50_ms": 320.0,
        "field": "last_voice_frame_to_turn_committed_ms",
    }
    assert summary["recommendations"] == [
        "Tune local silence, interim stability, and STT endpointing; "
        "post-speech turn commit is the dominant actionable stage."
    ]


def test_summarize_voice_turn_latency_events_does_not_treat_speculative_wait_as_plivo_send() -> (
    None
):
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "speculative_response_used": True,
                "last_voice_frame_to_turn_committed_ms": 320,
                "turn_committed_to_llm_first_chunk_ms": 0,
                "llm_first_chunk_to_tts_first_audio_ms": 0,
                "tts_first_audio_to_first_play_audio_ms": 1200,
                "turn_committed_to_first_play_audio_ms": 25,
            }
        ]
    )

    assert summary["stages"]["plivo_first_play"]["p50_ms"] == 25.0
    assert summary["bottleneck"] == {
        "stage": "post_speech_turn_commit",
        "label": "Post-speech turn commit",
        "p50_ms": 320.0,
        "field": "last_voice_frame_to_turn_committed_ms",
    }


def test_summarize_voice_turn_latency_events_reports_pre_response_ack_savings() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "pre_response_ack_used": True,
                "turn_committed_to_pre_response_ack_ms": 300,
                "turn_committed_to_answer_first_play_audio_ms": 2100,
            },
            {
                "event": "voice_turn_latency",
                "pre_response_ack_used": "true",
                "turn_committed_to_pre_response_ack_ms": 500,
                "turn_committed_to_answer_first_play_audio_ms": 2500,
            },
            {
                "event": "voice_turn_latency",
                "pre_response_ack_used": False,
                "turn_committed_to_first_play_audio_ms": 700,
            },
        ]
    )

    assert summary["pre_response_ack"]["used_count"] == 2
    assert summary["pre_response_ack"]["used_rate"] == 2 / 3
    assert summary["pre_response_ack"]["ack_delay"]["p50_ms"] == 400.0
    assert summary["pre_response_ack"]["answer_delay_when_ack_used"]["p50_ms"] == 2300.0
    assert summary["pre_response_ack"]["estimated_savings"]["p50_ms"] == 1900.0


def test_summarize_voice_turn_latency_events_reports_pre_response_ack_cache() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {"event": "voice_turn_latency", "pre_response_ack_cache_hit": True},
            {"event": "voice_turn_latency", "pre_response_ack_cache_hit": False},
            {"event": "voice_turn_latency", "pre_response_ack_cache_hit": True},
            {"event": "voice_turn_latency"},
        ]
    )

    assert summary["pre_response_ack_cache"] == {
        "observed_count": 3,
        "hit_count": 2,
        "miss_count": 1,
        "hit_rate": 2 / 3,
    }


def test_summarize_voice_turn_latency_events_reports_streaming_overlap() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "answer_first_play_audio_to_llm_stream_completed_ms": 420,
            },
            {
                "event": "voice_turn_latency",
                "answer_first_play_audio_to_llm_stream_completed_ms": "180",
            },
            {
                "event": "voice_turn_latency",
                "answer_first_play_audio_to_llm_stream_completed_ms": 0,
            },
        ]
    )

    assert summary["streaming_overlap"]["overlapped_count"] == 2
    assert summary["streaming_overlap"]["overlapped_rate"] == 2 / 3
    assert summary["streaming_overlap"]["answer_audio_before_llm_completed"] == {
        "label": "Answer audio before LLM stream completed",
        "field": "answer_first_play_audio_to_llm_stream_completed_ms",
        "count": 2,
        "p50_ms": 300.0,
        "p95_ms": 420.0,
        "max_ms": 420.0,
    }


def test_summarize_voice_turn_latency_events_reports_speculative_response_usage() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "speculative_response_used": True,
                "speculative_response_started_count": 2,
                "speculative_response_discarded_count": 1,
                "speculative_response_generation_started_to_commit_ms": 420,
                "speculative_response_tts_first_audio_to_commit_ms": 180,
            },
            {
                "event": "voice_turn_latency",
                "speculative_response_used": "true",
                "speculative_response_started_count": "1",
                "speculative_response_discarded_count": "0",
                "speculative_response_generation_started_to_commit_ms": "260",
                "speculative_response_tts_first_audio_to_commit_ms": "80",
            },
            {
                "event": "voice_turn_latency",
                "speculative_response_used": False,
                "speculative_response_started_count": 1,
                "speculative_response_discarded_count": 1,
            },
        ]
    )

    assert summary["speculative_response"]["started_count"] == 4
    assert summary["speculative_response"]["used_count"] == 2
    assert summary["speculative_response"]["discarded_count"] == 2
    assert summary["speculative_response"]["used_rate"] == 2 / 3
    assert summary["speculative_response"]["reuse_rate"] == 0.5
    assert summary["speculative_response"]["discarded_rate"] == 0.5
    assert summary["speculative_response"]["generation_lead"]["p50_ms"] == 340.0
    assert summary["speculative_response"]["first_audio_lead"]["p50_ms"] == 130.0


def test_summarize_voice_turn_latency_events_recommends_tuning_wasteful_speculation() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "turn_committed_to_llm_first_chunk_ms": 900,
                "speculative_response_used": True,
                "speculative_response_started_count": 1,
                "speculative_response_discarded_count": 0,
            },
            {
                "event": "voice_turn_latency",
                "turn_committed_to_llm_first_chunk_ms": 800,
                "speculative_response_started_count": 1,
                "speculative_response_discarded_count": 1,
            },
            {
                "event": "voice_turn_latency",
                "turn_committed_to_llm_first_chunk_ms": 700,
                "speculative_response_started_count": 1,
                "speculative_response_discarded_count": 1,
            },
            {
                "event": "voice_turn_latency",
                "turn_committed_to_llm_first_chunk_ms": 600,
                "speculative_response_started_count": 1,
                "speculative_response_discarded_count": 1,
            },
        ]
    )

    assert summary["speculative_response"]["discarded_rate"] == 0.75
    assert summary["recommendations"] == [
        "Optimize the orchestration backend first; LLM first-token latency is the dominant stage.",
        "Orchestrator first-token latency is blocking the real answer path; "
        "move to a lower-latency model or region, keep the orchestrator warm, "
        "trim prompt/tool work before first token, and preserve streaming text/event-stream "
        "responses.",
        "Speculative prewarm is wasting provider work; raise "
        "VOICE_AGENT_SPECULATIVE_RESPONSE_MIN_CHARS or "
        "VOICE_AGENT_SPECULATIVE_RESPONSE_STABILITY_MS, or disable prewarm for this project.",
    ]
    assert summary["tuning_actions"] == [
        {
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
                "llm_first_chunk_p50_ms": 750.0,
                "threshold_ms": 750.0,
                "streaming_overlap_count": 0,
            },
            "recommendation": (
                "Orchestrator first-token latency is blocking the real answer path; "
                "move to a lower-latency model or region, keep the orchestrator warm, "
                "trim prompt/tool work before first token, and preserve streaming "
                "text/event-stream responses."
            ),
        },
        {
            "id": "tune_speculative_response_prewarm",
            "category": "speculative_response",
            "action": "increase_thresholds_or_disable",
            "targets": [
                "VOICE_AGENT_SPECULATIVE_RESPONSE_MIN_CHARS",
                "VOICE_AGENT_SPECULATIVE_RESPONSE_STABILITY_MS",
            ],
            "reason": "discarded_rate_at_or_above_50_percent",
            "evidence": {
                "started_count": 4,
                "discarded_rate": 0.75,
                "reuse_rate": 0.25,
            },
            "recommendation": (
                "Speculative prewarm is wasting provider work; raise "
                "VOICE_AGENT_SPECULATIVE_RESPONSE_MIN_CHARS or "
                "VOICE_AGENT_SPECULATIVE_RESPONSE_STABILITY_MS, or disable prewarm "
                "for this project."
            ),
        },
    ]


def test_summarize_voice_turn_latency_events_recommends_preserving_useful_speculation() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "last_voice_frame_to_turn_committed_ms": 420,
                "turn_committed_to_llm_first_chunk_ms": 50,
                "speculative_response_used": True,
                "speculative_response_started_count": 1,
                "speculative_response_discarded_count": 0,
                "speculative_response_generation_started_to_commit_ms": 350,
                "speculative_response_tts_first_audio_to_commit_ms": 140,
            },
            {
                "event": "voice_turn_latency",
                "last_voice_frame_to_turn_committed_ms": 380,
                "turn_committed_to_llm_first_chunk_ms": 40,
                "speculative_response_used": True,
                "speculative_response_started_count": 1,
                "speculative_response_discarded_count": 0,
                "speculative_response_generation_started_to_commit_ms": 250,
                "speculative_response_tts_first_audio_to_commit_ms": 100,
            },
            {
                "event": "voice_turn_latency",
                "last_voice_frame_to_turn_committed_ms": 340,
                "turn_committed_to_llm_first_chunk_ms": 30,
                "speculative_response_used": True,
                "speculative_response_started_count": 1,
                "speculative_response_discarded_count": 0,
                "speculative_response_generation_started_to_commit_ms": 300,
                "speculative_response_tts_first_audio_to_commit_ms": 120,
            },
        ]
    )

    assert summary["speculative_response"]["reuse_rate"] == 1.0
    assert summary["recommendations"] == [
        "Tune local silence, interim stability, and STT endpointing; "
        "post-speech turn commit is the dominant actionable stage.",
        "Speculative prewarm is reliably reused and first audio is ready before commit; "
        "keep it enabled while tuning end-of-turn gates from recordings.",
    ]
    assert summary["tuning_actions"] == [
        {
            "id": "preserve_speculative_response_prewarm",
            "category": "speculative_response",
            "action": "keep_enabled",
            "targets": ["VOICE_AGENT_SPECULATIVE_RESPONSE_PREWARM"],
            "reason": ("reuse_rate_at_or_above_75_percent_with_first_audio_lead_at_or_above_50ms"),
            "evidence": {
                "started_count": 3,
                "reuse_rate": 1.0,
                "generation_lead_p50_ms": 300.0,
                "first_audio_lead_p50_ms": 120.0,
            },
            "recommendation": (
                "Speculative prewarm is reliably reused and first audio is ready before "
                "commit; keep it enabled while tuning end-of-turn gates from recordings."
            ),
        }
    ]


def test_summarize_voice_turn_latency_events_recommends_more_audio_lead() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "turn_committed_to_llm_first_chunk_ms": 40,
                "speculative_response_used": True,
                "speculative_response_started_count": 1,
                "speculative_response_discarded_count": 0,
                "speculative_response_generation_started_to_commit_ms": 320,
                "speculative_response_tts_first_audio_to_commit_ms": 20,
            },
            {
                "event": "voice_turn_latency",
                "turn_committed_to_llm_first_chunk_ms": 35,
                "speculative_response_used": True,
                "speculative_response_started_count": 1,
                "speculative_response_discarded_count": 0,
                "speculative_response_generation_started_to_commit_ms": 300,
                "speculative_response_tts_first_audio_to_commit_ms": 10,
            },
            {
                "event": "voice_turn_latency",
                "turn_committed_to_llm_first_chunk_ms": 30,
                "speculative_response_used": True,
                "speculative_response_started_count": 1,
                "speculative_response_discarded_count": 0,
                "speculative_response_generation_started_to_commit_ms": 340,
                "speculative_response_tts_first_audio_to_commit_ms": 30,
            },
        ]
    )

    assert summary["speculative_response"]["reuse_rate"] == 1.0
    assert summary["tuning_actions"] == [
        {
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
                "started_count": 3,
                "reuse_rate": 1.0,
                "generation_lead_p50_ms": 320.0,
                "first_audio_lead_p50_ms": 20.0,
                "min_first_audio_lead_ms": 50.0,
            },
            "recommendation": (
                "Speculative prewarm is reused, but TTS first audio is not ready far "
                "enough before commit; lower VOICE_AGENT_SPECULATIVE_RESPONSE_STABILITY_MS, "
                "raise VOICE_AGENT_INTERIM_TRANSCRIPT_STABILITY_MS from recordings, or keep "
                "streaming TTS enabled so prewarm hides real audio latency."
            ),
        }
    ]
    assert summary["recommendations"][-1] == (
        "Speculative prewarm is reused, but TTS first audio is not ready far enough before "
        "commit; lower VOICE_AGENT_SPECULATIVE_RESPONSE_STABILITY_MS, raise "
        "VOICE_AGENT_INTERIM_TRANSCRIPT_STABILITY_MS from recordings, or keep streaming TTS "
        "enabled so prewarm hides real audio latency."
    )


def test_summarize_voice_turn_latency_events_recommends_audio_lead_for_single_proof() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "turn_committed_to_llm_first_chunk_ms": 20,
                "speculative_response_used": True,
                "speculative_response_started_count": 2,
                "speculative_response_discarded_count": 1,
                "speculative_response_generation_started_to_commit_ms": 680,
                "speculative_response_tts_first_audio_to_commit_ms": 0,
            },
        ]
    )

    assert summary["speculative_response"]["reuse_rate"] == 0.5
    assert summary["tuning_actions"] == [
        {
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
                "started_count": 2,
                "reuse_rate": 0.5,
                "generation_lead_p50_ms": 680.0,
                "first_audio_lead_p50_ms": 0.0,
                "min_first_audio_lead_ms": 50.0,
            },
            "recommendation": (
                "Speculative generation starts before commit, but TTS first audio is not "
                "ready before the committed turn; lower "
                "VOICE_AGENT_SPECULATIVE_RESPONSE_STABILITY_MS, raise "
                "VOICE_AGENT_INTERIM_TRANSCRIPT_STABILITY_MS from recordings, or improve "
                "TTS first-audio latency so prewarm hides the real answer delay."
            ),
        }
    ]


def test_summarize_voice_turn_latency_events_does_not_block_fast_speculative_proof() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "turn_committed_to_llm_first_chunk_ms": 565.3,
                "turn_committed_to_answer_first_play_audio_ms": 884.6,
                "answer_first_play_audio_to_llm_stream_completed_ms": 953.8,
                "speculative_response_used": True,
                "speculative_response_started_count": 1,
                "speculative_response_discarded_count": 0,
                "speculative_response_generation_started_to_commit_ms": 568.7,
                "speculative_response_tts_first_audio_to_commit_ms": 0.0,
            },
        ]
    )

    assert summary["answer_first_audio"]["p50_ms"] == 884.6
    assert summary["streaming_overlap"]["overlapped_count"] == 1
    assert summary["speculative_response"]["used_count"] == 1
    assert summary["tuning_actions"] == []


def test_summarize_voice_turn_latency_events_recommends_preserving_fast_final_source() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "fast_final_local_silence",
                "last_voice_frame_to_turn_committed_ms": 320,
                "turn_committed_to_llm_first_chunk_ms": 80,
                "llm_first_chunk_to_tts_first_audio_ms": 120,
                "tts_first_audio_to_first_play_audio_ms": 30,
                "turn_committed_to_first_play_audio_ms": 620,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "fast_final_local_silence",
                "last_voice_frame_to_turn_committed_ms": 300,
                "turn_committed_to_llm_first_chunk_ms": 80,
                "llm_first_chunk_to_tts_first_audio_ms": 120,
                "tts_first_audio_to_first_play_audio_ms": 30,
                "turn_committed_to_first_play_audio_ms": 640,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "fast_final_local_silence",
                "last_voice_frame_to_turn_committed_ms": 340,
                "turn_committed_to_llm_first_chunk_ms": 80,
                "llm_first_chunk_to_tts_first_audio_ms": 120,
                "tts_first_audio_to_first_play_audio_ms": 30,
                "turn_committed_to_first_play_audio_ms": 660,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "stt_speech_final",
                "last_voice_frame_to_turn_committed_ms": 700,
                "turn_committed_to_llm_first_chunk_ms": 80,
                "llm_first_chunk_to_tts_first_audio_ms": 120,
                "tts_first_audio_to_first_play_audio_ms": 30,
                "turn_committed_to_first_play_audio_ms": 1320,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "stt_speech_final",
                "last_voice_frame_to_turn_committed_ms": 720,
                "turn_committed_to_llm_first_chunk_ms": 80,
                "llm_first_chunk_to_tts_first_audio_ms": 120,
                "tts_first_audio_to_first_play_audio_ms": 30,
                "turn_committed_to_first_play_audio_ms": 1400,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "stt_speech_final",
                "last_voice_frame_to_turn_committed_ms": 740,
                "turn_committed_to_llm_first_chunk_ms": 80,
                "llm_first_chunk_to_tts_first_audio_ms": 120,
                "tts_first_audio_to_first_play_audio_ms": 30,
                "turn_committed_to_first_play_audio_ms": 1480,
            },
        ]
    )

    assert summary["recommendations"] == [
        "Tune local silence, interim stability, and STT endpointing; "
        "post-speech turn commit is the dominant actionable stage.",
        "Fast-final local silence is producing faster answers than later commit sources; "
        "keep VOICE_AGENT_FAST_FINAL_END_OF_TURN_SILENCE_MS enabled and tune it from recordings.",
    ]
    assert summary["tuning_actions"] == [
        {
            "id": "preserve_fast_final_local_silence",
            "category": "end_of_turn",
            "action": "keep_enabled",
            "targets": ["VOICE_AGENT_FAST_FINAL_END_OF_TURN_SILENCE_MS"],
            "reason": "source_answer_first_audio_p50_at_least_250ms_faster",
            "evidence": {
                "source": "fast_final_local_silence",
                "source_answer_first_audio_p50_ms": 640.0,
                "comparison_answer_first_audio_p50_ms": 1400.0,
                "advantage_threshold_ms": 250.0,
                "min_turn_count": 3,
            },
            "recommendation": (
                "Fast-final local silence is producing faster answers than later commit sources; "
                "keep VOICE_AGENT_FAST_FINAL_END_OF_TURN_SILENCE_MS enabled and tune it "
                "from recordings."
            ),
        }
    ]


def test_summarize_voice_turn_latency_events_recommends_tightening_unhelpful_interim_source() -> (
    None
):
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "interim_local_silence",
                "last_voice_frame_to_turn_committed_ms": 250,
                "turn_committed_to_llm_first_chunk_ms": 80,
                "llm_first_chunk_to_tts_first_audio_ms": 120,
                "tts_first_audio_to_first_play_audio_ms": 30,
                "turn_committed_to_first_play_audio_ms": 1100,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "interim_local_silence",
                "last_voice_frame_to_turn_committed_ms": 240,
                "turn_committed_to_llm_first_chunk_ms": 80,
                "llm_first_chunk_to_tts_first_audio_ms": 120,
                "tts_first_audio_to_first_play_audio_ms": 30,
                "turn_committed_to_first_play_audio_ms": 1150,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "interim_local_silence",
                "last_voice_frame_to_turn_committed_ms": 260,
                "turn_committed_to_llm_first_chunk_ms": 80,
                "llm_first_chunk_to_tts_first_audio_ms": 120,
                "tts_first_audio_to_first_play_audio_ms": 30,
                "turn_committed_to_first_play_audio_ms": 1200,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "local_silence",
                "last_voice_frame_to_turn_committed_ms": 450,
                "turn_committed_to_llm_first_chunk_ms": 80,
                "llm_first_chunk_to_tts_first_audio_ms": 120,
                "tts_first_audio_to_first_play_audio_ms": 30,
                "turn_committed_to_first_play_audio_ms": 1050,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "local_silence",
                "last_voice_frame_to_turn_committed_ms": 470,
                "turn_committed_to_llm_first_chunk_ms": 80,
                "llm_first_chunk_to_tts_first_audio_ms": 120,
                "tts_first_audio_to_first_play_audio_ms": 30,
                "turn_committed_to_first_play_audio_ms": 1080,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "local_silence",
                "last_voice_frame_to_turn_committed_ms": 490,
                "turn_committed_to_llm_first_chunk_ms": 80,
                "llm_first_chunk_to_tts_first_audio_ms": 120,
                "tts_first_audio_to_first_play_audio_ms": 30,
                "turn_committed_to_first_play_audio_ms": 1120,
            },
        ]
    )

    assert summary["recommendations"] == [
        "Tune local silence, interim stability, and STT endpointing; "
        "post-speech turn commit is the dominant actionable stage.",
        "Interim local-silence commits are not improving answer latency enough to justify "
        "extra accuracy risk; raise VOICE_AGENT_INTERIM_TRANSCRIPT_STABILITY_MS or disable "
        "interim commits for this project.",
    ]
    assert summary["tuning_actions"] == [
        {
            "id": "tighten_interim_local_silence",
            "category": "end_of_turn",
            "action": "increase_or_disable",
            "targets": ["VOICE_AGENT_INTERIM_TRANSCRIPT_STABILITY_MS"],
            "reason": "source_answer_first_audio_p50_not_250ms_faster_than_conservative_source",
            "evidence": {
                "source": "interim_local_silence",
                "source_answer_first_audio_p50_ms": 1150.0,
                "comparison_answer_first_audio_p50_ms": 1080.0,
                "advantage_threshold_ms": 250.0,
                "min_turn_count": 3,
            },
            "recommendation": (
                "Interim local-silence commits are not improving answer latency enough to justify "
                "extra accuracy risk; raise VOICE_AGENT_INTERIM_TRANSCRIPT_STABILITY_MS or disable "
                "interim commits for this project."
            ),
        }
    ]


def test_summarize_voice_turn_latency_events_reports_turn_commit_sources() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {"event": "voice_turn_latency", "turn_commit_source": "local_silence"},
            {"event": "voice_turn_latency", "turn_commit_source": "local_silence"},
            {"event": "voice_turn_latency", "turn_commit_source": "fast_final_local_silence"},
            {"event": "voice_turn_latency", "turn_commit_source": "interim_local_silence"},
            {"event": "voice_turn_latency", "turn_commit_source": "stt_speech_final"},
            {"event": "voice_turn_latency", "turn_commit_source": "unsafe\nvalue"},
            {"event": "voice_turn_latency"},
        ]
    )

    assert {
        source: {
            "count": source_summary["count"],
            "rate": source_summary["rate"],
        }
        for source, source_summary in summary["turn_commit_sources"].items()
    } == {
        "fast_final_local_silence": {"count": 1, "rate": 1 / 7},
        "interim_local_silence": {"count": 1, "rate": 1 / 7},
        "local_silence": {"count": 2, "rate": 2 / 7},
        "stt_speech_final": {"count": 1, "rate": 1 / 7},
        "unknown": {"count": 2, "rate": 2 / 7},
    }


def test_summarize_voice_turn_latency_events_reports_response_plans() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {"event": "voice_turn_latency", "response_plan_action": "streaming_llm"},
            {"event": "voice_turn_latency", "response_plan_action": "streaming_llm"},
            {"event": "voice_turn_latency", "response_plan_action": "fast_path_text"},
            {"event": "voice_turn_latency", "response_plan_action": "bad\nvalue"},
            {"event": "voice_turn_latency"},
        ]
    )

    assert summary["response_plans"] == {
        "fast_path_text": {"count": 1, "rate": 1 / 5},
        "streaming_llm": {"count": 2, "rate": 2 / 5},
        "unknown": {"count": 2, "rate": 2 / 5},
    }


def test_summarize_voice_turn_latency_events_reports_source_answer_first_audio() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "fast_final_local_silence",
                "turn_committed_to_first_play_audio_ms": 640,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "fast_final_local_silence",
                "turn_committed_to_first_play_audio_ms": 520,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "interim_local_silence",
                "pre_response_ack_used": True,
                "turn_committed_to_pre_response_ack_ms": 280,
                "turn_committed_to_answer_first_play_audio_ms": 1180,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "local_silence",
                "turn_committed_to_first_play_audio_ms": 950,
            },
            {
                "event": "voice_turn_latency",
                "turn_commit_source": "local_silence",
            },
        ]
    )

    assert summary["turn_commit_sources"]["fast_final_local_silence"] == {
        "count": 2,
        "rate": 2 / 5,
        "answer_first_audio": {
            "label": "Answer first playAudio",
            "field": "turn_committed_to_answer_first_play_audio_ms",
            "count": 2,
            "p50_ms": 580.0,
            "p95_ms": 640.0,
            "max_ms": 640.0,
        },
    }
    assert (
        summary["turn_commit_sources"]["interim_local_silence"]["answer_first_audio"]["p50_ms"]
        == 1180.0
    )
    assert summary["turn_commit_sources"]["local_silence"]["answer_first_audio"] == {
        "label": "Answer first playAudio",
        "field": "turn_committed_to_answer_first_play_audio_ms",
        "count": 1,
        "p50_ms": 950.0,
        "p95_ms": 950.0,
        "max_ms": 950.0,
    }


def test_summarize_voice_turn_latency_events_handles_empty_input() -> None:
    summary = summarize_voice_turn_latency_events([])

    assert summary["turn_count"] == 0
    assert summary["bottleneck"] is None
    assert summary["recommendations"] == [
        "No voice_turn_latency events found. Run a real or local media turn first."
    ]


def test_summarize_voice_turn_latency_events_filters_by_call_id() -> None:
    summary = summarize_voice_turn_latency_events(
        [
            {
                "event": "voice_turn_latency",
                "call_id": "call-1",
                "turn_committed_to_llm_first_chunk_ms": 100,
            },
            {
                "event": "voice_turn_latency",
                "call_id": "call-2",
                "turn_committed_to_llm_first_chunk_ms": 900,
            },
        ],
        call_id="call-1",
    )

    assert summary["turn_count"] == 1
    assert summary["stages"]["llm_first_chunk"]["p50_ms"] == 100.0


def test_summarize_reports_turn_outcome_failure_rate() -> None:
    events = [
        {"event": "voice_turn_latency", "outcome": "success"},
        {"event": "voice_turn_latency", "outcome": "timeout"},
        {"event": "voice_turn_latency", "outcome": "error"},
        # A legacy event recorded before outcome tracking counts as success.
        {"event": "voice_turn_latency"},
    ]

    summary = summarize_voice_turn_latency_events(events)

    outcomes = summary["outcomes"]
    assert outcomes["counts"] == {"error": 1, "success": 2, "timeout": 1}
    assert outcomes["failure_count"] == 2
    assert outcomes["failure_rate"] == 0.5
    assert summary["turn_count"] == 4


def test_summarize_reports_post_speech_endpointing_excluding_silence() -> None:
    events = [
        {
            "event": "voice_turn_latency",
            "last_voice_frame_to_turn_committed_ms": 500,
            "end_of_turn_silence_ms": 450,
        },
        {
            "event": "voice_turn_latency",
            "last_voice_frame_to_turn_committed_ms": 80,
            "end_of_turn_silence_ms": 0,
        },
    ]

    endpointing = summarize_voice_turn_latency_events(events)["post_speech_endpointing"]

    # Silence subtracted: max(0, 500-450)=50 and max(0, 80-0)=80.
    assert endpointing["count"] == 2
    assert endpointing["p50_ms"] == 65  # median of [50, 80]
    assert endpointing["max_ms"] == 80

from voice_agent.workflow.turn_policy import (
    TurnPolicy,
    is_backchannel,
    is_fast_final_turn_candidate,
    looks_incomplete,
    word_tokens,
)


def test_word_tokens_preserve_devanagari_marks() -> None:
    assert word_tokens("ठीक है, जी हाँ") == ["ठीक", "है", "जी", "हाँ"]


def test_backchannel_detection_covers_indian_english_and_hindi() -> None:
    assert is_backchannel("haan")
    assert is_backchannel("ठीक है")
    assert not is_backchannel("haan I need service")


def test_incomplete_detection_holds_digits_and_fillers() -> None:
    assert looks_incomplete("my number is 98")
    assert looks_incomplete("मेरा नंबर मतलब")
    assert not looks_incomplete("my number is 9876.")


def test_fast_final_candidate_stays_narrow() -> None:
    assert is_fast_final_turn_candidate("Monday morning.")
    assert is_fast_final_turn_candidate("yes please")
    assert not is_fast_final_turn_candidate("yes")
    assert not is_fast_final_turn_candidate("I want")


def test_policy_fast_final_local_silence_commit() -> None:
    policy = TurnPolicy(
        local_end_of_turn_silence_seconds=0.45,
        fast_final_end_of_turn_silence_seconds=0.2,
        interim_transcript_stability_seconds=0.25,
    )

    decision = policy.evaluate_local_silence_turn(
        committed_text="Monday morning.",
        interim_text=None,
        last_voice_frame_at=1.0,
        interim_candidate_updated_at=None,
        now=1.25,
        has_caller_speech=False,
    )

    assert decision.action == "commit"
    assert decision.text == "Monday morning."
    assert decision.commit_source == "fast_final_local_silence"
    assert not decision.ignore_transcripts_until_speech_final


def test_policy_waits_on_incomplete_local_silence_candidate() -> None:
    policy = TurnPolicy(
        local_end_of_turn_silence_seconds=0.0,
        fast_final_end_of_turn_silence_seconds=0.0,
        interim_transcript_stability_seconds=0.0,
    )

    decision = policy.evaluate_local_silence_turn(
        committed_text="my number is 98",
        interim_text=None,
        last_voice_frame_at=1.0,
        interim_candidate_updated_at=None,
        now=1.0,
        has_caller_speech=False,
    )

    assert decision.action == "wait"


def test_policy_commits_stable_interim_after_local_silence() -> None:
    policy = TurnPolicy(
        local_end_of_turn_silence_seconds=0.0,
        fast_final_end_of_turn_silence_seconds=0.0,
        interim_transcript_stability_seconds=0.25,
        interim_local_silence_commit_enabled=True,
    )

    decision = policy.evaluate_local_silence_turn(
        committed_text="",
        interim_text="I need service",
        last_voice_frame_at=1.0,
        interim_candidate_updated_at=1.0,
        now=1.3,
        has_caller_speech=False,
    )

    assert decision.action == "commit"
    assert decision.text == "I need service"
    assert decision.commit_source == "interim_local_silence"
    assert decision.ignore_transcripts_until_speech_final


def test_policy_waits_on_interim_by_default() -> None:
    policy = TurnPolicy(
        local_end_of_turn_silence_seconds=0.0,
        fast_final_end_of_turn_silence_seconds=0.0,
        interim_transcript_stability_seconds=0.25,
    )

    decision = policy.evaluate_local_silence_turn(
        committed_text="",
        interim_text="I need service",
        last_voice_frame_at=1.0,
        interim_candidate_updated_at=1.0,
        now=2.0,
        has_caller_speech=False,
    )

    assert decision.action == "wait"


def test_policy_gates_speculative_prewarm() -> None:
    policy = TurnPolicy(
        local_end_of_turn_silence_seconds=0.45,
        fast_final_end_of_turn_silence_seconds=0.2,
        interim_transcript_stability_seconds=0.25,
    )

    assert policy.can_start_speculative_prewarm(
        user_text="I need service",
        min_chars=8,
        has_voice_frame=True,
    )
    assert not policy.can_start_speculative_prewarm(
        user_text="haan",
        min_chars=3,
        has_voice_frame=True,
    )
    assert not policy.can_start_speculative_prewarm(
        user_text="my number is 98",
        min_chars=8,
        has_voice_frame=True,
    )
    assert not policy.can_start_speculative_prewarm(
        user_text="I need service",
        min_chars=8,
        has_voice_frame=False,
    )

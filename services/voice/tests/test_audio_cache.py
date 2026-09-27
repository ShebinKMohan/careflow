from voice_agent.workflow.audio_cache import AudioCacheContext, InMemoryAudioCache


def test_audio_cache_context_normalizes_key_fields() -> None:
    key = AudioCacheContext(
        tts_provider=" Sarvam ",
        voice=" Anand ",
        model=" bulbul:v3 ",
        language=" en-IN ",
        pace=1.0,
    ).key(
        purpose="pre_response_ack",
        text="  One   moment. ",
        audio_format="audio/x-l16",
        sample_rate=16000,
    )

    assert key.tts_provider == "sarvam"
    assert key.voice == "anand"
    assert key.model == "bulbul:v3"
    assert key.language == "en-in"
    assert key.text == "One moment."


def test_in_memory_audio_cache_hits_misses_and_eviction() -> None:
    context = AudioCacheContext(tts_provider="local")
    first_key = context.key(
        purpose="pre_response_ack",
        text="One moment.",
        audio_format="audio/x-l16",
        sample_rate=16000,
    )
    second_key = context.key(
        purpose="pre_response_ack",
        text="Checking.",
        audio_format="audio/x-l16",
        sample_rate=16000,
    )
    cache = InMemoryAudioCache(max_entries=1, max_bytes=32)

    assert cache.get(first_key) is None
    cache.set(first_key, b"\x01\x00")
    assert cache.get(first_key) == b"\x01\x00"
    cache.set(second_key, b"\x02\x00")

    assert cache.get(first_key) is None
    assert cache.get(second_key) == b"\x02\x00"
    assert cache.hit_count == 2
    assert cache.miss_count == 2
    assert cache.size == 1

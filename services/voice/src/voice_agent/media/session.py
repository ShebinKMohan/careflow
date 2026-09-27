from __future__ import annotations

import asyncio
import json
import logging
import re
import struct
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from voice_agent.media.limits import MAX_PRE_RESPONSE_ACK_CHARS
from voice_agent.providers.base import (
    DeferredHistoryLLMClient,
    LLMClient,
    PlivoMediaTransport,
    SpeechToText,
    StartableProvider,
    TextToSpeech,
    TranscriptEvent,
)
from voice_agent.telephony.plivo import (
    PlivoAudioFormat,
    checkpoint_message,
    clear_audio_message,
    play_audio_message,
)
from voice_agent.workflow.audio_cache import AudioCache, AudioCacheContext, AudioCacheKey
from voice_agent.workflow.response_planner import ResponsePlan, ResponsePlanner
from voice_agent.workflow.turn_policy import (
    FILLER_WORDS,
    TERMINAL_PUNCTUATION,
    LocalSilenceTurnDecision,
    TurnPolicy,
    word_tokens,
)

logger = logging.getLogger(__name__)
TRANSCRIPT_TASK_CLOSE_GRACE_SECONDS = 0.05
_TERMINAL_PUNCTUATION = TERMINAL_PUNCTUATION
_FILLER_WORDS = FILLER_WORDS
_word_tokens = word_tokens
DEFAULT_FALLBACK_REPLY_TEXT = "I'm sorry, I had trouble processing that. Please try again."
DEFAULT_FALLBACK_TIMEOUT_SECONDS = 3.0
DEFAULT_AUDIO_INGEST_TIMEOUT_SECONDS = 1.0
DEFAULT_PLIVO_SEND_TIMEOUT_SECONDS = 1.0
DEFAULT_PROVIDER_CLOSE_TIMEOUT_SECONDS = 2.0
DEFAULT_MAX_TRANSCRIPT_CHARS = 4096
DEFAULT_MAX_REPLY_CHARS = 1000
DEFAULT_MAX_PLAY_AUDIO_BYTES = 6400
DEFAULT_BARGE_IN_MIN_FRAMES = 2
DEFAULT_BARGE_IN_ENERGY_THRESHOLD = 200.0
END_CALL_MARKER = "[[END_CALL]]"
ACTION_DIRECTIVE_PREFIX = "[[ACTION:"
ACTION_DIRECTIVE_SUFFIX = "]]"
MAX_ACTION_DIRECTIVE_CHARS = 2048
MAX_CLOSING_MESSAGE_CHARS = 2000
MIN_BARGE_IN_FRAME_BYTES = 320
DEFAULT_LOCAL_END_OF_TURN_SILENCE_SECONDS = 0.45
DEFAULT_FAST_FINAL_END_OF_TURN_SILENCE_SECONDS = 0.2
DEFAULT_INTERIM_TRANSCRIPT_STABILITY_SECONDS = 0.45
DEFAULT_INTERIM_LOCAL_SILENCE_COMMIT_ENABLED = False
DEFAULT_SPECULATIVE_RESPONSE_STABILITY_SECONDS = 0.075
DEFAULT_SPECULATIVE_RESPONSE_MIN_CHARS = 8
PLAYBACK_DRAIN_GRACE_SECONDS = 0.05
_REPLY_PHRASE_BOUNDARY_CHARS = frozenset(",;:")
_SAFE_SHORT_REPLY_PHRASES = frozenset(
    {
        "sure",
        "okay",
        "ok",
        "yes",
        "absolutely",
        "certainly",
        "great",
        "perfect",
        "got it",
        "no problem",
        "one moment",
        "sure thing",
        "of course",
        "all right",
        "ठीक है",
        "जी हाँ",
    }
)
_GENERIC_ACKNOWLEDGEMENT_PREFIXES = _SAFE_SHORT_REPLY_PHRASES
_GENERIC_ACKNOWLEDGEMENT_BOUNDARY_CHARS = (
    _REPLY_PHRASE_BOUNDARY_CHARS | _TERMINAL_PUNCTUATION
)
_UNSAFE_REPLY_PHRASE_END_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "because", "but", "by", "for", "from",
        "if", "in", "is", "let", "me", "of", "on", "or", "our", "the", "their", "then",
        "to", "was", "were", "when", "while", "with", "your",
    }
) | _FILLER_WORDS
MAX_EARLY_REPLY_PHRASE_CHARS = 90
MIN_SOFT_REPLY_PHRASE_CHARS = 32
MAX_SOFT_REPLY_PHRASE_CHARS = 64
_ACTION_TYPE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_CONTROL_DIRECTIVE_PREFIXES = (END_CALL_MARKER, ACTION_DIRECTIVE_PREFIX)


@dataclass(frozen=True, slots=True)
class ReplyChunkingConfig:
    min_first_phrase_chars: int = 0
    min_soft_phrase_chars: int = MIN_SOFT_REPLY_PHRASE_CHARS
    max_soft_phrase_chars: int = MAX_SOFT_REPLY_PHRASE_CHARS
    max_early_phrase_chars: int = MAX_EARLY_REPLY_PHRASE_CHARS

    def __post_init__(self) -> None:
        if self.min_first_phrase_chars < 0:
            raise ValueError("min_first_phrase_chars must be non-negative.")
        if self.min_soft_phrase_chars <= 0:
            raise ValueError("min_soft_phrase_chars must be positive.")
        if self.max_soft_phrase_chars < self.min_soft_phrase_chars:
            raise ValueError("max_soft_phrase_chars must be >= min_soft_phrase_chars.")
        if self.max_early_phrase_chars < self.min_first_phrase_chars:
            raise ValueError("max_early_phrase_chars must be >= min_first_phrase_chars.")


DEFAULT_REPLY_CHUNKING_CONFIG = ReplyChunkingConfig()


@dataclass(frozen=True, slots=True)
class VoiceActionRequest:
    call_id: str
    stream_id: str
    turn_epoch: int
    action_type: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _ParsedVoiceAction:
    action_type: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _ControlParseResult:
    visible_text: str
    end_call_requested: bool
    actions: tuple[_ParsedVoiceAction, ...]
    pending_control_text: str
    suppressing_action_directive: bool


class MediaTransportSendTimeoutError(RuntimeError):
    """Raised when an outbound media WebSocket send to Plivo stalls."""


class LLMReplyTooLargeError(RuntimeError):
    def __init__(self, *, chars: int, max_chars: int) -> None:
        super().__init__("LLM reply exceeded configured size limit.")
        self.chars = chars
        self.max_chars = max_chars


@dataclass(frozen=True, slots=True)
class _ReplyPlaybackError:
    exc: Exception


type _ReplyAudioQueueItem = bytes | _ReplyPlaybackError | None


@dataclass(frozen=True, slots=True)
class HandoffTarget:
    target_id: str
    name: str
    condition: str
    instruction: str | None = None
    target: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_id", self.target_id.strip())
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "condition", self.condition.strip())
        instruction = self.instruction.strip() if self.instruction else None
        object.__setattr__(self, "instruction", instruction or None)


_HANDOFF_STOP_WORDS = frozenset(
    {
        "about",
        "agent",
        "caller",
        "calling",
        "handle",
        "manager",
        "need",
        "needs",
        "please",
        "query",
        "queries",
        "request",
        "task",
        "that",
        "this",
        "when",
        "with",
    }
)


def _handoff_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw_token in re.findall(r"[a-z0-9]+", text.lower()):
        token = _normalise_handoff_token(raw_token)
        if len(token) >= 4 and token not in _HANDOFF_STOP_WORDS:
            tokens.add(token)
    return tokens


def _normalise_handoff_token(token: str) -> str:
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def _handoff_match_score(user_tokens: set[str], target: HandoffTarget) -> int:
    target_tokens = _handoff_tokens(target.condition)
    if not target_tokens:
        target_tokens = _handoff_tokens(
            " ".join(part for part in (target.name, target.instruction) if part)
        )
    return len(user_tokens & target_tokens)


@dataclass(frozen=True, slots=True)
class VoiceTurnLatencySnapshot:
    call_id: str
    stream_id: str
    turn_epoch: int
    transcript_chars: int
    turn_commit_source: str
    response_plan_action: str | None
    response_plan_confidence: float | None
    response_plan_reason: str | None
    media_start_to_first_audio_ms: float | None
    first_audio_to_last_voice_frame_ms: float | None
    last_voice_frame_to_turn_committed_ms: float | None
    last_voice_frame_to_first_play_audio_ms: float | None
    first_audio_to_speech_final_ms: float | None
    speech_final_to_turn_committed_ms: float | None
    turn_committed_to_llm_first_chunk_ms: float | None
    turn_committed_to_first_reply_chunk_ms: float | None
    llm_first_chunk_to_first_reply_chunk_ms: float | None
    llm_first_chunk_to_tts_first_audio_ms: float | None
    first_reply_chunk_to_tts_first_audio_ms: float | None
    tts_first_audio_to_first_play_audio_ms: float | None
    turn_committed_to_first_play_audio_ms: float | None
    first_audio_to_first_play_audio_ms: float | None
    answer_first_play_audio_to_llm_stream_completed_ms: float | None
    pre_response_ack_used: bool = False
    turn_committed_to_pre_response_ack_ms: float | None = None
    turn_committed_to_answer_first_play_audio_ms: float | None = None
    pre_response_ack_cache_hit: bool | None = None
    speculative_response_used: bool = False
    speculative_response_generation_started_to_commit_ms: float | None = None
    speculative_response_tts_first_audio_to_commit_ms: float | None = None
    speculative_response_started_count: int = 0
    speculative_response_discarded_count: int = 0
    # "success" for a completed/barge-in turn, or a failure reason (timeout, error,
    # reply_too_large, send_timeout) so turn error rate is observable in /latency/summary.
    outcome: str = "success"
    # Deliberate end-of-turn silence wait baked into last_voice_frame_to_turn_committed_ms,
    # so the dashboard can subtract it to see true STT/network endpointing (0 for STT-decided
    # commits that did not wait locally).
    end_of_turn_silence_ms: float | None = None


@dataclass(slots=True)
class _TurnLatency:
    call_id: str
    stream_id: str
    turn_epoch: int
    transcript_chars: int
    turn_commit_source: str
    media_start_at: float | None
    first_audio_in_at: float | None
    last_voice_frame_at: float | None
    speech_final_at: float | None
    turn_committed_at: float
    llm_first_chunk_at: float | None = None
    first_reply_chunk_ready_at: float | None = None
    llm_stream_completed_at: float | None = None
    tts_first_audio_at: float | None = None
    first_play_audio_at: float | None = None
    pre_response_ack_first_play_audio_at: float | None = None
    answer_first_play_audio_at: float | None = None
    pre_response_ack_cache_hit: bool | None = None
    speculative_response_used: bool = False
    speculative_response_generation_started_at: float | None = None
    speculative_response_tts_first_audio_at: float | None = None
    speculative_response_started_count: int = 0
    speculative_response_discarded_count: int = 0
    response_plan_action: str | None = None
    response_plan_confidence: float | None = None
    response_plan_reason: str | None = None
    emitted: bool = False
    outcome: str = "success"
    end_of_turn_silence_ms: float | None = None

    def snapshot(self) -> VoiceTurnLatencySnapshot:
        play_audio_for_tts_at = (
            self.answer_first_play_audio_at
            if self.answer_first_play_audio_at is not None
            else self.first_play_audio_at
        )
        return VoiceTurnLatencySnapshot(
            call_id=self.call_id,
            stream_id=self.stream_id,
            turn_epoch=self.turn_epoch,
            transcript_chars=self.transcript_chars,
            turn_commit_source=self.turn_commit_source,
            response_plan_action=self.response_plan_action,
            response_plan_confidence=self.response_plan_confidence,
            response_plan_reason=self.response_plan_reason,
            media_start_to_first_audio_ms=_elapsed_ms(
                self.media_start_at,
                self.first_audio_in_at,
            ),
            first_audio_to_last_voice_frame_ms=_elapsed_ms(
                self.first_audio_in_at,
                self.last_voice_frame_at,
            ),
            last_voice_frame_to_turn_committed_ms=_elapsed_ms(
                self.last_voice_frame_at,
                self.turn_committed_at,
            ),
            last_voice_frame_to_first_play_audio_ms=_elapsed_ms(
                self.last_voice_frame_at,
                self.first_play_audio_at,
            ),
            first_audio_to_speech_final_ms=_elapsed_ms(
                self.first_audio_in_at,
                self.speech_final_at,
            ),
            speech_final_to_turn_committed_ms=_elapsed_ms(
                self.speech_final_at,
                self.turn_committed_at,
            ),
            turn_committed_to_llm_first_chunk_ms=_elapsed_ms(
                self.turn_committed_at,
                self.llm_first_chunk_at,
            ),
            turn_committed_to_first_reply_chunk_ms=_elapsed_ms(
                self.turn_committed_at,
                self.first_reply_chunk_ready_at,
            ),
            llm_first_chunk_to_first_reply_chunk_ms=_elapsed_ms(
                self.llm_first_chunk_at,
                self.first_reply_chunk_ready_at,
            ),
            llm_first_chunk_to_tts_first_audio_ms=_elapsed_ms(
                self.llm_first_chunk_at,
                self.tts_first_audio_at,
            ),
            first_reply_chunk_to_tts_first_audio_ms=_elapsed_ms(
                self.first_reply_chunk_ready_at,
                self.tts_first_audio_at,
            ),
            tts_first_audio_to_first_play_audio_ms=_elapsed_ms(
                self.tts_first_audio_at,
                play_audio_for_tts_at,
            ),
            turn_committed_to_first_play_audio_ms=_elapsed_ms(
                self.turn_committed_at,
                self.first_play_audio_at,
            ),
            first_audio_to_first_play_audio_ms=_elapsed_ms(
                self.first_audio_in_at,
                self.first_play_audio_at,
            ),
            answer_first_play_audio_to_llm_stream_completed_ms=_elapsed_ms(
                self.answer_first_play_audio_at,
                self.llm_stream_completed_at,
            ),
            pre_response_ack_used=self.pre_response_ack_first_play_audio_at is not None,
            turn_committed_to_pre_response_ack_ms=_elapsed_ms(
                self.turn_committed_at,
                self.pre_response_ack_first_play_audio_at,
            ),
            turn_committed_to_answer_first_play_audio_ms=_elapsed_ms(
                self.turn_committed_at,
                self.answer_first_play_audio_at,
            ),
            pre_response_ack_cache_hit=self.pre_response_ack_cache_hit,
            speculative_response_used=self.speculative_response_used,
            speculative_response_generation_started_to_commit_ms=_elapsed_ms(
                self.speculative_response_generation_started_at,
                self.turn_committed_at,
            ),
            speculative_response_tts_first_audio_to_commit_ms=_elapsed_ms(
                self.speculative_response_tts_first_audio_at,
                self.turn_committed_at,
            ),
            speculative_response_started_count=self.speculative_response_started_count,
            speculative_response_discarded_count=self.speculative_response_discarded_count,
            outcome=self.outcome,
            end_of_turn_silence_ms=self.end_of_turn_silence_ms,
        )


class MediaSessionRunner:
    def __init__(
        self,
        *,
        call_id: str,
        stream_id: str,
        audio_format: PlivoAudioFormat,
        stt: SpeechToText,
        llm: LLMClient,
        tts: TextToSpeech,
        transport: PlivoMediaTransport,
        response_planner: ResponsePlanner | None = None,
        audio_cache: AudioCache | None = None,
        audio_cache_context: AudioCacheContext | None = None,
        turn_timeout_seconds: float = 12.0,
        fallback_reply_text: str = DEFAULT_FALLBACK_REPLY_TEXT,
        fallback_timeout_seconds: float = DEFAULT_FALLBACK_TIMEOUT_SECONDS,
        audio_ingest_timeout_seconds: float = DEFAULT_AUDIO_INGEST_TIMEOUT_SECONDS,
        plivo_send_timeout_seconds: float = DEFAULT_PLIVO_SEND_TIMEOUT_SECONDS,
        provider_close_timeout_seconds: float = DEFAULT_PROVIDER_CLOSE_TIMEOUT_SECONDS,
        max_transcript_chars: int = DEFAULT_MAX_TRANSCRIPT_CHARS,
        max_reply_chars: int = DEFAULT_MAX_REPLY_CHARS,
        max_turns_per_call: int = 0,
        max_play_audio_bytes: int = DEFAULT_MAX_PLAY_AUDIO_BYTES,
        pre_response_ack_text: str | None = None,
        pre_response_ack_delay_seconds: float = 0.0,
        local_end_of_turn_silence_seconds: float = DEFAULT_LOCAL_END_OF_TURN_SILENCE_SECONDS,
        fast_final_end_of_turn_silence_seconds: float = (
            DEFAULT_FAST_FINAL_END_OF_TURN_SILENCE_SECONDS
        ),
        interim_transcript_stability_seconds: float = (
            DEFAULT_INTERIM_TRANSCRIPT_STABILITY_SECONDS
        ),
        interim_local_silence_commit_enabled: bool = (
            DEFAULT_INTERIM_LOCAL_SILENCE_COMMIT_ENABLED
        ),
        speculative_response_prewarm: bool = True,
        speculative_response_stability_seconds: float = (
            DEFAULT_SPECULATIVE_RESPONSE_STABILITY_SECONDS
        ),
        speculative_response_min_chars: int = DEFAULT_SPECULATIVE_RESPONSE_MIN_CHARS,
        barge_in_min_frames: int = DEFAULT_BARGE_IN_MIN_FRAMES,
        barge_in_energy_threshold: float = DEFAULT_BARGE_IN_ENERGY_THRESHOLD,
        playback_checkpoints: bool = False,
        greeting: str | None = None,
        closing_message: str | None = None,
        auto_hangup_after_closing: bool = True,
        hangup_observer: Callable[[str], object] | None = None,
        latency_observer: Callable[[VoiceTurnLatencySnapshot], object] | None = None,
        transcript_observer: Callable[[str], object] | None = None,
        voice_action_observer: Callable[[VoiceActionRequest], object] | None = None,
        handoff_targets: Sequence[HandoffTarget] | None = None,
        handoff_llm_factory: Callable[[HandoffTarget, str], LLMClient] | None = None,
        reply_chunking_config: ReplyChunkingConfig = DEFAULT_REPLY_CHUNKING_CONFIG,
    ) -> None:
        if turn_timeout_seconds <= 0:
            raise ValueError("turn_timeout_seconds must be positive.")
        if not fallback_reply_text.strip():
            raise ValueError("fallback_reply_text must not be blank.")
        if fallback_timeout_seconds <= 0:
            raise ValueError("fallback_timeout_seconds must be positive.")
        if audio_ingest_timeout_seconds <= 0:
            raise ValueError("audio_ingest_timeout_seconds must be positive.")
        if plivo_send_timeout_seconds <= 0:
            raise ValueError("plivo_send_timeout_seconds must be positive.")
        if provider_close_timeout_seconds <= 0:
            raise ValueError("provider_close_timeout_seconds must be positive.")
        if max_transcript_chars <= 0:
            raise ValueError("max_transcript_chars must be positive.")
        if max_reply_chars <= 0:
            raise ValueError("max_reply_chars must be positive.")
        if max_turns_per_call < 0:
            raise ValueError("max_turns_per_call must be non-negative.")
        if max_play_audio_bytes < 2 or max_play_audio_bytes % 2:
            raise ValueError("max_play_audio_bytes must be an even integer of at least 2.")
        clean_pre_response_ack_text = (
            pre_response_ack_text.strip() if pre_response_ack_text is not None else None
        )
        if clean_pre_response_ack_text is not None and not clean_pre_response_ack_text:
            raise ValueError("pre_response_ack_text must not be blank.")
        if (
            clean_pre_response_ack_text is not None
            and len(clean_pre_response_ack_text) > MAX_PRE_RESPONSE_ACK_CHARS
        ):
            raise ValueError(
                f"pre_response_ack_text must be at most {MAX_PRE_RESPONSE_ACK_CHARS} characters."
            )
        if pre_response_ack_delay_seconds < 0:
            raise ValueError("pre_response_ack_delay_seconds must be non-negative.")
        if local_end_of_turn_silence_seconds < 0:
            raise ValueError("local_end_of_turn_silence_seconds must be non-negative.")
        if fast_final_end_of_turn_silence_seconds < 0:
            raise ValueError("fast_final_end_of_turn_silence_seconds must be non-negative.")
        if interim_transcript_stability_seconds < 0:
            raise ValueError("interim_transcript_stability_seconds must be non-negative.")
        if speculative_response_stability_seconds < 0:
            raise ValueError("speculative_response_stability_seconds must be non-negative.")
        if speculative_response_min_chars < 0:
            raise ValueError("speculative_response_min_chars must be non-negative.")
        if barge_in_min_frames < 1:
            raise ValueError("barge_in_min_frames must be at least 1.")
        if barge_in_energy_threshold < 0:
            raise ValueError("barge_in_energy_threshold must be non-negative.")
        if not isinstance(reply_chunking_config, ReplyChunkingConfig):
            raise TypeError("reply_chunking_config must be a ReplyChunkingConfig.")
        clean_closing_message = (
            closing_message.strip() if closing_message is not None else None
        )
        if clean_closing_message is not None and not clean_closing_message:
            clean_closing_message = None
        if (
            clean_closing_message is not None
            and len(clean_closing_message) > MAX_CLOSING_MESSAGE_CHARS
        ):
            raise ValueError(
                f"closing_message must be at most {MAX_CLOSING_MESSAGE_CHARS} characters."
            )
        self._call_id = call_id
        self._stream_id = stream_id
        self._audio_format = audio_format
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._transport = transport
        self._response_planner = response_planner or ResponsePlanner()
        self._audio_cache = audio_cache
        self._audio_cache_context = audio_cache_context
        self._turn_timeout_seconds = turn_timeout_seconds
        self._fallback_reply_text = fallback_reply_text.strip()
        self._fallback_timeout_seconds = fallback_timeout_seconds
        self._audio_ingest_timeout_seconds = audio_ingest_timeout_seconds
        self._plivo_send_timeout_seconds = plivo_send_timeout_seconds
        self._provider_close_timeout_seconds = provider_close_timeout_seconds
        self._max_transcript_chars = max_transcript_chars
        self._max_reply_chars = max_reply_chars
        self._max_turns_per_call = max_turns_per_call
        self._max_play_audio_bytes = max_play_audio_bytes
        self._pre_response_ack_text = clean_pre_response_ack_text
        self._pre_response_ack_delay_seconds = pre_response_ack_delay_seconds
        self._turn_policy = TurnPolicy(
            local_end_of_turn_silence_seconds=local_end_of_turn_silence_seconds,
            fast_final_end_of_turn_silence_seconds=fast_final_end_of_turn_silence_seconds,
            interim_transcript_stability_seconds=interim_transcript_stability_seconds,
            interim_local_silence_commit_enabled=interim_local_silence_commit_enabled,
        )
        self._speculative_response_prewarm = speculative_response_prewarm
        self._speculative_response_stability_seconds = speculative_response_stability_seconds
        self._speculative_response_min_chars = speculative_response_min_chars
        self._reply_chunking_config = reply_chunking_config
        self._barge_in_min_frames = barge_in_min_frames
        self._barge_in_energy_threshold = barge_in_energy_threshold
        self._playback_checkpoints = playback_checkpoints
        self._checkpoint_counter = 0
        self._last_checkpoint_name: str | None = None
        self._playback_active = False
        self._playback_active_until_at: float | None = None
        self._generation_epoch = 0
        self._closed = False
        self._transcript_task: asyncio.Task[None] | None = None
        self._provider_start_task: asyncio.Task[None] | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self._local_silence_commit_task: asyncio.Task[None] | None = None
        self._pre_response_ack_prewarm_task: asyncio.Task[None] | None = None
        self._speculative_response_task: asyncio.Task[None] | None = None
        self._media_start_at: float | None = None
        self._first_audio_in_at: float | None = None
        self._last_voice_frame_at: float | None = None
        self._active_turn_latency: _TurnLatency | None = None
        self._latency_observer = latency_observer
        self._transcript_observer = transcript_observer
        self._voice_action_observer = voice_action_observer
        self._transcript_turns: list[dict[str, str | int]] = []
        self._transcript_turn_index = 0
        self._transcript_turn_by_epoch: dict[int, int] = {}
        # Accumulated caller utterance for the streaming (Deepgram) path.
        self._utterance_buffer: list[str] = []
        self._utterance_hold_count = 0
        self._interim_candidate_text: str | None = None
        self._interim_candidate_updated_at: float | None = None
        self._ignore_transcripts_until_speech_final = False
        self._speculative_response_text: str | None = None
        self._speculative_response_audio_chunks: list[bytes] = []
        self._speculative_response_spoken_text_parts: list[str] = []
        self._speculative_response_reply_text: str | None = None
        self._speculative_response_actions: list[_ParsedVoiceAction] = []
        self._speculative_response_end_call_requested = False
        self._speculative_response_llm_first_chunk_at: float | None = None
        self._speculative_response_first_reply_chunk_ready_at: float | None = None
        self._speculative_response_llm_stream_completed_at: float | None = None
        self._speculative_response_tts_first_audio_at: float | None = None
        self._speculative_response_generation_started = False
        self._speculative_response_generation_started_at: float | None = None
        self._speculative_response_voice_frame_at: float | None = None
        self._speculative_response_audio_ready = asyncio.Event()
        self._pending_speculative_response_started_count = 0
        self._pending_speculative_response_discarded_count = 0
        self._speculative_response_stats_turn_epoch: int | None = None
        # Consecutive frames of caller speech energy while the agent is speaking.
        self._barge_in_frame_streak = 0
        self._pre_response_ack_audio: bytes | None = None
        self._pre_response_ack_audio_cache_hit = False
        # Opening line spoken once at the start of the call (project agent-config).
        self._greeting = (greeting or "").strip() or None
        self._greeted = False
        self._allow_first_backchannel_turn = self._greeting is not None
        self._closing_message = clean_closing_message
        self._auto_hangup_after_closing = auto_hangup_after_closing
        self._hangup_observer = hangup_observer
        self._hangup_requested = False
        self._handoff_targets = tuple(
            target for target in (handoff_targets or ()) if target.target_id and target.condition
        )
        self._handoff_llm_factory = handoff_llm_factory
        self._active_handoff_target_id: str | None = None

    async def start(self) -> None:
        """Start background media-session work as soon as Plivo sends `start`."""
        if self._closed:
            return
        if self._media_start_at is None:
            self._media_start_at = time.perf_counter()
        self._ensure_transcript_task()
        if self._provider_start_task is None or self._provider_start_task.done():
            self._provider_start_task = asyncio.create_task(self._start_providers())

    async def _start_providers(self) -> None:
        await asyncio.gather(
            self._start_provider("stt", self._stt),
            self._start_provider("llm", self._llm),
            self._start_provider("tts", self._tts),
        )
        self._ensure_pre_response_ack_prewarm_task()

    async def _start_provider(self, name: str, provider: object) -> None:
        if not isinstance(provider, StartableProvider):
            return
        try:
            await provider.start()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Media session provider prewarm failed: %s call_id=%s stream_id=%s.",
                name,
                self._call_id,
                self._stream_id,
                exc_info=True,
            )

    async def greet(self) -> None:
        """Speak the configured opening line once, at the very start of the call.
        No-op when no greeting is configured. The greeting runs as a normal turn, so
        caller barge-in cancels it and the caller's first utterance supersedes it."""
        if self._closed or self._greeting is None or self._greeted:
            return
        self._greeted = True
        await self._cancel_active_turn(clear_audio=self._is_playback_active(), allow_grace=False)
        self._generation_epoch += 1
        turn_epoch = self._generation_epoch
        self._turn_task = asyncio.create_task(self._handle_greeting(self._greeting, turn_epoch))

    async def _handle_greeting(self, text: str, turn_epoch: int) -> None:
        try:
            await asyncio.wait_for(
                self._synthesize_and_play_greeting(text, turn_epoch),
                timeout=self._turn_timeout_seconds,
            )
            self._record_transcript_turn("assistant", text, turn=0)
        except asyncio.CancelledError:
            raise
        except MediaTransportSendTimeoutError:
            await self._abort_active_turn()
        except Exception:
            logger.exception(
                "Greeting playback failed for call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
            )
            await self._abort_active_turn()
        finally:
            current_task = asyncio.current_task()
            if self._turn_task is current_task:
                self._turn_task = None

    async def _synthesize_and_play_greeting(self, text: str, turn_epoch: int) -> None:
        cached_audio = self._cached_greeting_audio(text)
        if cached_audio is not None:
            await self._send_play_audio_frames(cached_audio, turn_epoch)
            return

        chunks: list[bytes] = []
        async for audio_chunk in self._tts.synthesize(
            text=text,
            audio_format=self._audio_format,
        ):
            if turn_epoch != self._generation_epoch:
                return
            chunks.append(audio_chunk)
            self._mark_tts_first_audio(turn_epoch, pre_response_ack=False)
            await self._send_play_audio_frames(audio_chunk, turn_epoch)
        if chunks and turn_epoch == self._generation_epoch:
            self._cache_greeting_audio(text, b"".join(chunks))

    async def accept_audio(self, audio: bytes) -> None:
        if self._closed:
            return
        now = time.perf_counter()
        if self._first_audio_in_at is None:
            self._first_audio_in_at = now
        self._ensure_transcript_task()
        has_caller_speech = _has_speech_energy(audio, self._barge_in_energy_threshold)
        if has_caller_speech:
            self._last_voice_frame_at = now
        if self._is_playback_active():
            if self._is_sustained_caller_speech(audio):
                self._barge_in_frame_streak = 0
                await self._cancel_active_turn(clear_audio=True, allow_grace=False)
        else:
            self._barge_in_frame_streak = 0

        try:
            transcript = await asyncio.wait_for(
                self._stt.accept_audio(audio),
                timeout=self._audio_ingest_timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "Speech audio ingest timed out for call_id=%s stream_id=%s after %.2fs.",
                self._call_id,
                self._stream_id,
                self._audio_ingest_timeout_seconds,
            )
            return
        except Exception:
            logger.exception(
                "Speech audio ingest failed for call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
            )
            return
        if transcript is not None:
            await self._commit_synchronous_transcript(transcript)
        await self._maybe_commit_local_silence_turn(
            now=time.perf_counter(),
            has_caller_speech=has_caller_speech,
        )

    def _is_sustained_caller_speech(self, audio: bytes) -> bool:
        # Require several consecutive frames of caller speech energy before barging
        # in, so a single click, comfort-noise frame, or brief echo blip does not
        # cut the agent off (the naive "any audio -> clearAudio" over-triggers).
        if _has_speech_energy(audio, self._barge_in_energy_threshold):
            self._barge_in_frame_streak += 1
        else:
            self._barge_in_frame_streak = 0
        return self._barge_in_frame_streak >= self._barge_in_min_frames

    async def _commit_synchronous_transcript(self, transcript: TranscriptEvent) -> None:
        # Providers that return a finished transcript from accept_audio (local STT)
        # have already decided the utterance is complete, so commit immediately.
        if not transcript.is_final or not transcript.text.strip():
            return
        await self._commit_turn(
            transcript.text.strip(),
            wait=True,
            speech_final_at=time.perf_counter(),
            commit_source="synchronous_stt",
        )

    async def _transcript_loop(self) -> None:
        try:
            async for transcript in self._stt.transcripts():
                await self._ingest_streaming_transcript(transcript)
        except Exception:
            logger.exception(
                "Speech transcript stream failed for call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
            )

    async def _ingest_streaming_transcript(self, transcript: TranscriptEvent) -> None:
        # Streaming STT (Deepgram) emits several is_final segments per utterance and
        # one speech_final / UtteranceEnd at true end-of-turn. Accumulate the
        # segments and only commit on the end-of-turn signal (with a single hold for
        # mid-utterance pauses) so the agent never talks over the caller.
        if self._ignore_transcripts_until_speech_final:
            if transcript.speech_final:
                self._ignore_transcripts_until_speech_final = False
                self._utterance_buffer.clear()
                self._utterance_hold_count = 0
                self._clear_interim_candidate()
            return
        text = transcript.text.strip() if transcript.text else ""
        if transcript.is_final and text:
            self._clear_interim_candidate()
            self._utterance_buffer.append(text)
            self._utterance_hold_count = 0
            if not transcript.speech_final:
                self._schedule_speculative_response_prewarm(
                    " ".join(self._utterance_buffer).strip()
                )
        elif text:
            self._set_interim_candidate(text)
        if not transcript.speech_final:
            self._schedule_local_silence_commit()
            return
        committed = " ".join(self._utterance_buffer).strip()
        if not committed:
            return
        if self._should_ignore_backchannel(committed):
            # A bare acknowledgement ("haan", "okay", "mhm") is not a turn to answer.
            self._utterance_buffer.clear()
            self._utterance_hold_count = 0
            return
        if self._turn_policy.should_hold_for_incomplete_final(
            committed,
            hold_count=self._utterance_hold_count,
        ):
            self._utterance_hold_count += 1
            return
        self._utterance_buffer.clear()
        self._utterance_hold_count = 0
        await self._commit_turn(
            committed,
            wait=False,
            speech_final_at=time.perf_counter(),
            commit_source="stt_speech_final",
        )

    async def _maybe_commit_local_silence_turn(
        self,
        *,
        now: float,
        has_caller_speech: bool,
    ) -> None:
        committed = " ".join(self._utterance_buffer).strip()
        decision = self._turn_policy.evaluate_local_silence_turn(
            committed_text=committed,
            interim_text=self._interim_candidate_text,
            last_voice_frame_at=self._last_voice_frame_at,
            interim_candidate_updated_at=self._interim_candidate_updated_at,
            now=now,
            has_caller_speech=has_caller_speech,
        )
        if decision.action == "wait":
            return
        if decision.action == "ignore_backchannel":
            if self._should_ignore_backchannel(decision.text):
                self._utterance_buffer.clear()
                self._utterance_hold_count = 0
                self._clear_interim_candidate()
                return
            decision = LocalSilenceTurnDecision(
                action="commit",
                text=decision.text,
                commit_source="local_silence",
            )
        if decision.commit_source is None:
            return
        self._utterance_buffer.clear()
        self._utterance_hold_count = 0
        if decision.ignore_transcripts_until_speech_final:
            self._ignore_transcripts_until_speech_final = True
        self._clear_interim_candidate()
        await self._commit_turn(
            decision.text,
            wait=False,
            speech_final_at=now,
            commit_source=decision.commit_source,
            end_of_turn_silence_seconds=self._turn_policy.local_silence_seconds_for_commit(
                decision.text,
                commit_source=decision.commit_source,
            ),
        )

    def _schedule_local_silence_commit(self) -> None:
        if (
            self._closed
            or not self._has_local_silence_commit_candidate()
            or self._last_voice_frame_at is None
        ):
            return
        task = self._local_silence_commit_task
        if task is not None:
            if not task.done():
                return
            self._local_silence_commit_task = None
        self._local_silence_commit_task = asyncio.create_task(
            self._run_scheduled_local_silence_commit()
        )

    async def _run_scheduled_local_silence_commit(self) -> None:
        try:
            while not self._closed:
                if (
                    not self._has_local_silence_commit_candidate()
                    or self._last_voice_frame_at is None
                ):
                    return
                now = time.perf_counter()
                committed = " ".join(self._utterance_buffer).strip()
                if committed:
                    silence_seconds = self._turn_policy.local_silence_seconds_for_candidate(
                        committed
                    )
                else:
                    silence_seconds = (
                        self._turn_policy.interim_local_silence_seconds_for_candidate(
                            self._interim_candidate_text or ""
                        )
                    )
                remaining_silence_seconds = silence_seconds - (
                    now - self._last_voice_frame_at
                )
                remaining_seconds = remaining_silence_seconds
                if not self._utterance_buffer and self._interim_candidate_updated_at is not None:
                    remaining_stability_seconds = (
                        self._turn_policy.interim_transcript_stability_seconds
                        - (now - self._interim_candidate_updated_at)
                    )
                    remaining_seconds = max(
                        remaining_silence_seconds,
                        remaining_stability_seconds,
                    )
                if remaining_seconds <= 0:
                    await self._maybe_commit_local_silence_turn(
                        now=now,
                        has_caller_speech=False,
                    )
                    return
                await asyncio.sleep(remaining_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Scheduled local-silence commit failed for call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
                exc_info=True,
            )
        finally:
            if self._local_silence_commit_task is asyncio.current_task():
                self._local_silence_commit_task = None

    def _has_local_silence_commit_candidate(self) -> bool:
        return bool(self._utterance_buffer) or self._interim_candidate_text is not None

    def _set_interim_candidate(self, text: str) -> None:
        clean_text = text.strip()
        if not clean_text:
            return
        if clean_text == self._interim_candidate_text:
            return
        if _speculative_text_matches(self._interim_candidate_text, clean_text):
            self._interim_candidate_text = clean_text
            return
        self._speculative_response_stats_turn_epoch = None
        self._discard_speculative_response()
        self._interim_candidate_text = clean_text
        self._interim_candidate_updated_at = time.perf_counter()
        self._schedule_speculative_response_prewarm(clean_text)

    def _clear_interim_candidate(self) -> None:
        self._interim_candidate_text = None
        self._interim_candidate_updated_at = None

    def _schedule_speculative_response_prewarm(self, user_text: str) -> None:
        voice_frame_at = self._last_voice_frame_at
        if (
            not self._speculative_response_prewarm
            or self._closed
            or not self._turn_policy.can_start_speculative_prewarm(
                user_text=user_text,
                min_chars=self._speculative_response_min_chars,
                has_voice_frame=voice_frame_at is not None,
            )
        ):
            return
        if voice_frame_at is None:
            return
        task = self._speculative_response_task
        if _speculative_text_matches(self._speculative_response_text, user_text):
            return
        self._discard_speculative_response()
        self._speculative_response_text = user_text
        self._speculative_response_audio_chunks = []
        self._speculative_response_spoken_text_parts = []
        self._speculative_response_reply_text = None
        self._speculative_response_actions = []
        self._speculative_response_end_call_requested = False
        self._speculative_response_llm_first_chunk_at = None
        self._speculative_response_first_reply_chunk_ready_at = None
        self._speculative_response_llm_stream_completed_at = None
        self._speculative_response_tts_first_audio_at = None
        self._speculative_response_generation_started = False
        self._speculative_response_generation_started_at = None
        self._speculative_response_audio_ready.clear()
        self._speculative_response_voice_frame_at = voice_frame_at
        task = asyncio.create_task(
            self._run_scheduled_speculative_response_prewarm(
                user_text=user_text,
                voice_frame_at=voice_frame_at,
            )
        )
        self._speculative_response_task = task
        task.add_done_callback(self._consume_speculative_response_task_result)

    async def _run_scheduled_speculative_response_prewarm(
        self,
        *,
        user_text: str,
        voice_frame_at: float,
    ) -> None:
        if self._speculative_response_stability_seconds > 0:
            await asyncio.sleep(self._speculative_response_stability_seconds)
        if not self._should_continue_speculative_response(user_text, voice_frame_at):
            return
        await asyncio.wait_for(
            self._prepare_speculative_response_audio(
                user_text=user_text,
                voice_frame_at=voice_frame_at,
            ),
            timeout=self._turn_timeout_seconds,
        )

    async def _prepare_speculative_response_audio(
        self,
        *,
        user_text: str,
        voice_frame_at: float,
    ) -> None:
        pending = ""
        control_pending = ""
        suppressing_action_directive = False
        spoken_chars = 0
        audio_chunks = self._speculative_response_audio_chunks
        reply_text_chunks: list[str] = []
        llm_first_chunk_at: float | None = None
        tts_first_audio_at: float | None = None
        llm_stream_completed_at: float | None = None

        self._speculative_response_generation_started = True
        self._record_speculative_response_started()
        self._speculative_response_generation_started_at = time.perf_counter()
        async for chunk in self._generate_llm_reply(
            user_text=user_text,
            speculative=True,
        ):
            if not self._should_continue_speculative_response(user_text, voice_frame_at):
                return
            if llm_first_chunk_at is None:
                llm_first_chunk_at = time.perf_counter()
                self._speculative_response_llm_first_chunk_at = llm_first_chunk_at
            control = _extract_reply_control_directives(
                control_pending + chunk,
                final=False,
                suppressing_action_directive=suppressing_action_directive,
            )
            control_pending = control.pending_control_text
            suppressing_action_directive = control.suppressing_action_directive
            if control.end_call_requested:
                self._speculative_response_end_call_requested = True
            self._speculative_response_actions.extend(control.actions)
            if not control.visible_text:
                continue
            reply_text_chunks.append(control.visible_text)
            pending += control.visible_text
            if spoken_chars + len(pending.strip()) > self._max_reply_chars:
                return
            reply_chunks, pending = _split_speakable_reply_chunks(
                pending,
                allow_phrase=(spoken_chars == 0),
                chunking_config=self._reply_chunking_config,
            )
            for reply_chunk in reply_chunks:
                if self._speculative_response_first_reply_chunk_ready_at is None:
                    self._speculative_response_first_reply_chunk_ready_at = (
                        time.perf_counter()
                    )
                spoken_chars += len(reply_chunk)
                first_audio_at = await self._append_synthesized_speculative_audio(
                    text=reply_chunk,
                    audio_chunks=audio_chunks,
                    user_text=user_text,
                    voice_frame_at=voice_frame_at,
                    tts_first_audio_at=tts_first_audio_at,
                )
                if tts_first_audio_at is None:
                    tts_first_audio_at = first_audio_at
        llm_stream_completed_at = time.perf_counter()
        self._speculative_response_llm_stream_completed_at = llm_stream_completed_at
        if control_pending or suppressing_action_directive:
            control = _extract_reply_control_directives(
                control_pending,
                final=True,
                suppressing_action_directive=suppressing_action_directive,
            )
            if control.end_call_requested:
                self._speculative_response_end_call_requested = True
            self._speculative_response_actions.extend(control.actions)
            if control.visible_text:
                reply_text_chunks.append(control.visible_text)
                pending += control.visible_text
            if spoken_chars + len(pending.strip()) > self._max_reply_chars:
                return
        remainder = pending.strip()
        if remainder:
            if self._speculative_response_first_reply_chunk_ready_at is None:
                self._speculative_response_first_reply_chunk_ready_at = time.perf_counter()
            first_audio_at = await self._append_synthesized_speculative_audio(
                text=remainder,
                audio_chunks=audio_chunks,
                user_text=user_text,
                voice_frame_at=voice_frame_at,
                tts_first_audio_at=tts_first_audio_at,
            )
            if tts_first_audio_at is None:
                tts_first_audio_at = first_audio_at
        if not self._should_continue_speculative_response(user_text, voice_frame_at):
            return
        self._speculative_response_reply_text = "".join(reply_text_chunks).strip() or None
        self._speculative_response_llm_first_chunk_at = llm_first_chunk_at
        self._speculative_response_llm_stream_completed_at = llm_stream_completed_at
        self._speculative_response_tts_first_audio_at = tts_first_audio_at

    async def _append_synthesized_speculative_audio(
        self,
        *,
        text: str,
        audio_chunks: list[bytes],
        user_text: str,
        voice_frame_at: float,
        tts_first_audio_at: float | None,
    ) -> float | None:
        first_audio_at = tts_first_audio_at
        text_recorded = False
        async for audio_chunk in self._tts.synthesize(
            text=text,
            audio_format=self._audio_format,
        ):
            if not self._should_continue_speculative_response(user_text, voice_frame_at):
                return first_audio_at
            if first_audio_at is None:
                first_audio_at = time.perf_counter()
                self._speculative_response_tts_first_audio_at = first_audio_at
            audio_chunks.append(audio_chunk)
            if not text_recorded:
                self._speculative_response_spoken_text_parts.append(text)
                text_recorded = True
            self._speculative_response_audio_ready.set()
        return first_audio_at

    def _should_continue_speculative_response(
        self,
        user_text: str,
        voice_frame_at: float,
    ) -> bool:
        return (
            not self._closed
            and self._speculative_response_prewarm
            and self._speculative_response_text == user_text
        )

    def _discard_speculative_response(self, *, count_discard: bool = True) -> None:
        if count_discard and self._speculative_response_generation_started:
            self._record_speculative_response_discarded()
        task = self._speculative_response_task
        if task is not None and not task.done():
            task.cancel()
        self._speculative_response_task = None
        self._speculative_response_text = None
        self._speculative_response_audio_chunks = []
        self._speculative_response_spoken_text_parts = []
        self._speculative_response_reply_text = None
        self._speculative_response_actions = []
        self._speculative_response_end_call_requested = False
        self._speculative_response_llm_first_chunk_at = None
        self._speculative_response_first_reply_chunk_ready_at = None
        self._speculative_response_llm_stream_completed_at = None
        self._speculative_response_tts_first_audio_at = None
        self._speculative_response_generation_started = False
        self._speculative_response_generation_started_at = None
        self._speculative_response_voice_frame_at = None
        self._speculative_response_audio_ready.set()

    def _record_speculative_response_started(self) -> None:
        latency = self._active_speculative_response_latency()
        if latency is not None:
            latency.speculative_response_started_count += 1
            return
        self._pending_speculative_response_started_count += 1

    def _record_speculative_response_discarded(self) -> None:
        latency = self._active_speculative_response_latency()
        if latency is not None:
            latency.speculative_response_discarded_count += 1
            return
        self._pending_speculative_response_discarded_count += 1

    def _active_speculative_response_latency(self) -> _TurnLatency | None:
        turn_epoch = self._speculative_response_stats_turn_epoch
        latency = self._active_turn_latency
        if turn_epoch is None or latency is None or latency.turn_epoch != turn_epoch:
            return None
        return latency

    def _consume_speculative_response_task_result(
        self,
        task: asyncio.Task[None],
    ) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning(
                "Speculative response prewarm failed for call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
                exc_info=True,
            )
        finally:
            self._speculative_response_audio_ready.set()

    async def _commit_turn(
        self,
        user_text: str,
        *,
        wait: bool,
        speech_final_at: float | None,
        commit_source: str,
        end_of_turn_silence_seconds: float = 0.0,
    ) -> None:
        await self._cancel_active_turn(clear_audio=self._is_playback_active(), allow_grace=False)
        self._generation_epoch += 1
        turn_epoch = self._generation_epoch
        self._active_turn_latency = _TurnLatency(
            call_id=self._call_id,
            stream_id=self._stream_id,
            turn_epoch=turn_epoch,
            transcript_chars=len(user_text),
            turn_commit_source=commit_source,
            media_start_at=self._media_start_at,
            first_audio_in_at=self._first_audio_in_at,
            last_voice_frame_at=self._last_voice_frame_at,
            speech_final_at=speech_final_at,
            turn_committed_at=time.perf_counter(),
            speculative_response_started_count=(
                self._pending_speculative_response_started_count
            ),
            speculative_response_discarded_count=(
                self._pending_speculative_response_discarded_count
            ),
            end_of_turn_silence_ms=end_of_turn_silence_seconds * 1000.0,
        )
        self._pending_speculative_response_started_count = 0
        self._pending_speculative_response_discarded_count = 0
        self._speculative_response_stats_turn_epoch = (
            turn_epoch if self._speculative_response_text is not None else None
        )
        if len(user_text) > self._max_transcript_chars:
            logger.warning(
                "Rejecting oversized transcript for call_id=%s stream_id=%s "
                "chars=%d max_chars=%d.",
                self._call_id,
                self._stream_id,
                len(user_text),
                self._max_transcript_chars,
            )
            task = asyncio.create_task(self._recover_rejected_transcript(turn_epoch))
            self._turn_task = task
            if wait:
                await task
            return
        if self._max_turns_per_call and self._transcript_turn_index >= self._max_turns_per_call:
            # Per-call safety budget: a runaway loop (e.g. echo/barge-in feedback) must not
            # drive unbounded LLM/TTS spend. Stop generating new turns and end the call.
            logger.warning(
                "Per-call turn budget (%d) reached for call_id=%s stream_id=%s; ending call.",
                self._max_turns_per_call,
                self._call_id,
                self._stream_id,
            )
            self._emit_turn_failed(turn_epoch, "budget_exceeded")
            task = asyncio.create_task(self._end_call_for_budget(turn_epoch))
            self._turn_task = task
            if wait:
                await task
            return
        self._transcript_turn_index += 1
        self._allow_first_backchannel_turn = False
        self._transcript_turn_by_epoch[turn_epoch] = self._transcript_turn_index
        self._record_transcript_turn("user", user_text, turn=self._transcript_turn_index)
        task = asyncio.create_task(self._handle_transcript_with_timeout(user_text, turn_epoch))
        self._turn_task = task
        if wait:
            await task

    def _record_assistant_transcript_for_epoch(
        self,
        turn_epoch: int,
        parts: list[str],
    ) -> None:
        text = " ".join(part.strip() for part in parts if part and part.strip()).strip()
        if not text:
            return
        self._record_transcript_turn(
            "assistant",
            text,
            turn=self._transcript_turn_by_epoch.get(turn_epoch, self._transcript_turn_index),
        )

    def _record_transcript_turn(self, role: str, text: str, *, turn: int) -> None:
        clean_text = text.strip()
        if not clean_text:
            return
        self._transcript_turns.append({"role": role, "text": clean_text, "turn": turn})
        observer = self._transcript_observer
        if observer is None:
            return
        try:
            observer(json.dumps(self._transcript_turns, ensure_ascii=False))
        except Exception:
            logger.warning(
                "Transcript observer failed for call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
                exc_info=True,
            )

    def _should_ignore_backchannel(self, text: str) -> bool:
        if not self._turn_policy.should_ignore_backchannel(text):
            return False
        return not (
            self._allow_first_backchannel_turn and self._transcript_turn_index == 0
        )

    async def _recover_rejected_transcript(self, turn_epoch: int) -> None:
        try:
            await self._recover_failed_turn(turn_epoch)
        finally:
            current_task = asyncio.current_task()
            if self._turn_task is current_task:
                self._turn_task = None

    async def _handle_transcript_with_timeout(
        self,
        user_text: str,
        turn_epoch: int,
    ) -> None:
        try:
            await asyncio.wait_for(
                self._handle_transcript(user_text, turn_epoch),
                timeout=self._turn_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                "Voice turn timed out for call_id=%s stream_id=%s after %.2fs.",
                self._call_id,
                self._stream_id,
                self._turn_timeout_seconds,
            )
            self._emit_turn_failed(turn_epoch, "timeout")
            await self._recover_failed_turn(turn_epoch)
        except MediaTransportSendTimeoutError:
            logger.warning(
                "Voice turn stopped because outbound Plivo send timed out for "
                "call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
            )
            self._emit_turn_failed(turn_epoch, "send_timeout")
            await self._abort_active_turn()
        except Exception:
            logger.exception(
                "Voice turn failed for call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
            )
            self._emit_turn_failed(turn_epoch, "error")
            await self._recover_failed_turn(turn_epoch)
        finally:
            current_task = asyncio.current_task()
            if self._turn_task is current_task:
                self._turn_task = None

    async def _handle_transcript(self, user_text: str, turn_epoch: int) -> None:
        # Stream the LLM reply and flush the first safe phrase, then each complete
        # sentence, into an ordered synthesis/playback pipeline. LLM read-ahead
        # continues while TTS is synthesizing, and TTS can read ahead while Plivo
        # frames for earlier audio are being sent.
        await self._activate_matching_handoff(user_text)
        response_plan = self._response_planner.plan(user_text=user_text)
        self._record_response_plan(turn_epoch, response_plan)
        if await self._try_play_speculative_response(user_text, turn_epoch):
            return
        pending = ""
        control_pending = ""
        suppressing_action_directive = False
        spoken_chars = 0
        assistant_reply_parts: list[str] = []
        end_call_requested = False
        playback_queue: asyncio.Queue[str | None] = asyncio.Queue()
        playback_task = asyncio.create_task(
            self._run_reply_playback_queue(playback_queue, turn_epoch)
        )
        playback_closed = False
        pre_response_ack_started = asyncio.Event()
        pre_response_ack_task = self._start_pre_response_ack_task(
            turn_epoch,
            pre_response_ack_started,
        )
        try:
            async for chunk in self._generate_llm_reply(
                user_text=user_text,
                speculative=False,
            ):
                if playback_task.done():
                    await playback_task
                if turn_epoch != self._generation_epoch:
                    return
                self._mark_llm_first_chunk(turn_epoch)
                if pre_response_ack_task is not None:
                    if pre_response_ack_started.is_set() and self._pre_response_ack_audio_started(
                        turn_epoch
                    ):
                        await self._finish_pre_response_ack_task(pre_response_ack_task)
                    else:
                        await self._cancel_pre_response_ack_task(pre_response_ack_task)
                        await self._cancel_pre_response_ack_prewarm_task()
                    pre_response_ack_task = None
                control = _extract_reply_control_directives(
                    control_pending + chunk,
                    final=False,
                    suppressing_action_directive=suppressing_action_directive,
                )
                control_pending = control.pending_control_text
                suppressing_action_directive = control.suppressing_action_directive
                end_call_requested = end_call_requested or control.end_call_requested
                await self._notify_voice_action_observers(turn_epoch, control.actions)
                if not control.visible_text:
                    continue
                pending += control.visible_text
                if spoken_chars + len(pending.strip()) > self._max_reply_chars:
                    raise LLMReplyTooLargeError(
                        chars=spoken_chars + len(pending.strip()),
                        max_chars=self._max_reply_chars,
                    )
                reply_chunks, pending = _split_speakable_reply_chunks(
                    pending,
                    allow_phrase=(spoken_chars == 0),
                    blocked_first_phrase_text=(
                        self._pre_response_ack_text
                        if self._pre_response_ack_audio_started(turn_epoch)
                        else None
                    ),
                    chunking_config=self._reply_chunking_config,
                )
                for reply_chunk in reply_chunks:
                    if turn_epoch != self._generation_epoch:
                        return
                    self._mark_first_reply_chunk_ready(turn_epoch)
                    spoken_chars += len(reply_chunk)
                    assistant_reply_parts.append(reply_chunk)
                    await playback_queue.put(reply_chunk)
            if turn_epoch != self._generation_epoch:
                return
            self._mark_llm_stream_completed(turn_epoch)
            if control_pending or suppressing_action_directive:
                control = _extract_reply_control_directives(
                    control_pending,
                    final=True,
                    suppressing_action_directive=suppressing_action_directive,
                )
                end_call_requested = end_call_requested or control.end_call_requested
                await self._notify_voice_action_observers(turn_epoch, control.actions)
                if control.visible_text:
                    pending += control.visible_text
                if spoken_chars + len(pending.strip()) > self._max_reply_chars:
                    raise LLMReplyTooLargeError(
                        chars=spoken_chars + len(pending.strip()),
                        max_chars=self._max_reply_chars,
                    )
            remainder = pending.strip()
            if remainder and turn_epoch == self._generation_epoch:
                self._mark_first_reply_chunk_ready(turn_epoch)
                assistant_reply_parts.append(remainder)
                await playback_queue.put(remainder)
            await playback_queue.put(None)
            playback_closed = True
            await playback_task
            self._record_assistant_transcript_for_epoch(turn_epoch, assistant_reply_parts)
            if end_call_requested and turn_epoch == self._generation_epoch:
                await self._play_closing_and_request_hangup(turn_epoch)
        except asyncio.CancelledError:
            await self._cancel_reply_playback_task(playback_task)
            if pre_response_ack_task is not None:
                await self._cancel_pre_response_ack_task(pre_response_ack_task)
                pre_response_ack_task = None
            raise
        except LLMReplyTooLargeError as exc:
            logger.warning(
                "Rejecting oversized LLM reply for call_id=%s stream_id=%s "
                "chars=%d max_chars=%d.",
                self._call_id,
                self._stream_id,
                exc.chars,
                exc.max_chars,
            )
            await self._cancel_reply_playback_task(playback_task)
            self._emit_turn_failed(turn_epoch, "reply_too_large")
            await self._recover_failed_turn(turn_epoch)
        finally:
            if not playback_closed and not playback_task.done():
                await self._cancel_reply_playback_task(playback_task)
            if pre_response_ack_task is not None:
                if pre_response_ack_started.is_set() and self._pre_response_ack_audio_started(
                    turn_epoch
                ):
                    await self._finish_pre_response_ack_task(pre_response_ack_task)
                else:
                    await self._cancel_pre_response_ack_task(pre_response_ack_task)

    async def _activate_matching_handoff(self, user_text: str) -> None:
        target = self._matching_handoff_target(user_text)
        if target is None or self._handoff_llm_factory is None:
            return
        context = self._handoff_conversation_context()
        try:
            replacement = self._handoff_llm_factory(target, context)
            if isinstance(replacement, StartableProvider):
                await replacement.start()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Agent handoff failed before activation: target_id=%s call_id=%s stream_id=%s.",
                target.target_id,
                self._call_id,
                self._stream_id,
                exc_info=True,
            )
            return
        previous_llm = self._llm
        self._discard_speculative_response()
        self._llm = replacement
        self._active_handoff_target_id = target.target_id
        if previous_llm is not replacement:
            await self._close_provider("previous-llm", previous_llm.close())

    def _matching_handoff_target(self, user_text: str) -> HandoffTarget | None:
        if not self._handoff_targets or self._handoff_llm_factory is None:
            return None
        user_tokens = _handoff_tokens(user_text)
        if not user_tokens:
            return None
        best_target: HandoffTarget | None = None
        best_score = 0
        for target in self._handoff_targets:
            if target.target_id == self._active_handoff_target_id:
                continue
            score = _handoff_match_score(user_tokens, target)
            if score > best_score:
                best_score = score
                best_target = target
        return best_target if best_score > 0 else None

    def _handoff_conversation_context(self) -> str:
        if not self._transcript_turns:
            return ""
        lines: list[str] = []
        for turn in self._transcript_turns[-12:]:
            role = str(turn.get("role", "")).strip()
            text = str(turn.get("text", "")).strip()
            if role and text:
                lines.append(f"{role}: {text}")
        return "\n".join(lines)

    async def _end_call_for_budget(self, turn_epoch: int) -> None:
        try:
            await self._play_closing_and_request_hangup(turn_epoch)
        finally:
            # Ensure the call is hung up even when no closing message is configured.
            await self._notify_hangup_observer()
            current_task = asyncio.current_task()
            if self._turn_task is current_task:
                self._turn_task = None

    async def _play_closing_and_request_hangup(self, turn_epoch: int) -> None:
        closing_message = self._closing_message
        if closing_message is None:
            return
        await self._synthesize_and_play(closing_message, turn_epoch)
        self._record_transcript_turn(
            "assistant",
            closing_message,
            turn=self._transcript_turn_by_epoch.get(turn_epoch, self._transcript_turn_index),
        )
        if self._auto_hangup_after_closing:
            await self._notify_hangup_observer()

    async def _notify_hangup_observer(self) -> None:
        if self._hangup_requested:
            return
        self._hangup_requested = True
        observer = self._hangup_observer
        if observer is None:
            return
        try:
            result = observer(self._call_id)
            if isinstance(result, Awaitable):
                await result
        except Exception:
            logger.warning(
                "Hangup observer failed for call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
                exc_info=True,
            )

    async def _notify_voice_action_observers(
        self,
        turn_epoch: int,
        actions: Sequence[_ParsedVoiceAction],
    ) -> None:
        if not actions:
            return
        for action in actions:
            await self._notify_voice_action_observer(
                VoiceActionRequest(
                    call_id=self._call_id,
                    stream_id=self._stream_id,
                    turn_epoch=turn_epoch,
                    action_type=action.action_type,
                    payload=action.payload,
                )
            )

    async def _notify_voice_action_observer(self, request: VoiceActionRequest) -> None:
        observer = self._voice_action_observer
        if observer is None:
            return
        try:
            result = observer(request)
            if isinstance(result, Awaitable):
                await result
        except Exception:
            logger.warning(
                "Voice action observer failed for call_id=%s stream_id=%s action_type=%s.",
                self._call_id,
                self._stream_id,
                request.action_type,
                exc_info=True,
            )

    def _record_response_plan(self, turn_epoch: int, response_plan: ResponsePlan) -> None:
        latency = self._latency_for_turn(turn_epoch)
        if latency is None:
            return
        latency.response_plan_action = response_plan.action
        latency.response_plan_confidence = response_plan.confidence
        latency.response_plan_reason = response_plan.reason

    async def _run_reply_playback_queue(
        self,
        queue: asyncio.Queue[str | None],
        turn_epoch: int,
    ) -> None:
        audio_queue: asyncio.Queue[_ReplyAudioQueueItem] = asyncio.Queue(maxsize=2)
        synthesis_task = asyncio.create_task(
            self._run_reply_synthesis_queue(queue, audio_queue, turn_epoch)
        )
        try:
            while True:
                if turn_epoch != self._generation_epoch:
                    return
                item = await audio_queue.get()
                if item is None:
                    await synthesis_task
                    return
                if isinstance(item, _ReplyPlaybackError):
                    raise item.exc
                await self._send_play_audio_frames(item, turn_epoch)
        finally:
            if not synthesis_task.done():
                synthesis_task.cancel()
                await self._wait_for_cancelled_task("reply-synthesis", synthesis_task)

    async def _run_reply_synthesis_queue(
        self,
        text_queue: asyncio.Queue[str | None],
        audio_queue: asyncio.Queue[_ReplyAudioQueueItem],
        turn_epoch: int,
    ) -> None:
        synthesized_first_chunk = False
        try:
            while True:
                if turn_epoch != self._generation_epoch:
                    return
                text = await text_queue.get()
                if text is None:
                    await audio_queue.put(None)
                    return
                reached_end = False
                if synthesized_first_chunk:
                    text, reached_end = _coalesce_ready_reply_text(text, text_queue)
                async for audio_chunk in self._tts.synthesize(
                    text=text,
                    audio_format=self._audio_format,
                ):
                    if turn_epoch != self._generation_epoch:
                        return
                    self._mark_tts_first_audio(turn_epoch, pre_response_ack=False)
                    await audio_queue.put(audio_chunk)
                synthesized_first_chunk = True
                if reached_end:
                    await audio_queue.put(None)
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await audio_queue.put(_ReplyPlaybackError(exc))

    async def _cancel_reply_playback_task(self, task: asyncio.Task[None]) -> None:
        if task.done():
            with suppress(asyncio.CancelledError):
                await task
            return
        task.cancel()
        await self._wait_for_cancelled_task("reply-playback", task)

    async def _try_play_speculative_response(self, user_text: str, turn_epoch: int) -> bool:
        task = self._speculative_response_task
        if task is None or not _speculative_text_matches(
            self._speculative_response_text,
            user_text,
        ):
            if self._speculative_response_text is not None:
                self._discard_speculative_response()
            return False
        if (
            not task.done()
            and not self._speculative_response_audio_chunks
            and not self._speculative_response_generation_started
        ):
            if not self._promote_scheduled_speculative_response(user_text):
                self._discard_speculative_response()
                return False
            task = self._speculative_response_task
            if task is None:
                return False
        audio_index = 0
        audio_started = False
        speculative_response_used = False
        pre_response_ack_started = asyncio.Event()
        pre_response_ack_task: asyncio.Task[None] | None = None
        if self._speculative_response_audio_chunks:
            self._mark_speculative_response_reused(turn_epoch)
        try:
            while True:
                while audio_index < len(self._speculative_response_audio_chunks):
                    if turn_epoch != self._generation_epoch:
                        return True
                    if pre_response_ack_task is not None:
                        await self._settle_pre_response_ack_task(
                            pre_response_ack_task,
                            started=pre_response_ack_started,
                            turn_epoch=turn_epoch,
                            cancel_prewarm_when_unplayed=True,
                        )
                        pre_response_ack_task = None
                    if not audio_started:
                        self._mark_speculative_response_reused(turn_epoch)
                    await self._send_play_audio_frames(
                        self._speculative_response_audio_chunks[audio_index],
                        turn_epoch,
                        speculative_response=True,
                    )
                    audio_started = True
                    audio_index += 1
                if task.done():
                    break
                if pre_response_ack_task is None and not audio_started:
                    pre_response_ack_delay_seconds = _remaining_delay_seconds(
                        self._pre_response_ack_delay_seconds,
                        started_at=self._speculative_response_generation_started_at,
                        now=time.perf_counter(),
                    )
                    pre_response_ack_task = self._start_pre_response_ack_task(
                        turn_epoch,
                        pre_response_ack_started,
                        delay_seconds=pre_response_ack_delay_seconds,
                    )
                self._speculative_response_audio_ready.clear()
                if audio_index < len(self._speculative_response_audio_chunks) or task.done():
                    continue
                await self._speculative_response_audio_ready.wait()
            task.result()
            if audio_started:
                self._mark_speculative_response_reused(turn_epoch)
                latency = self._latency_for_turn(turn_epoch)
                if latency is not None:
                    self._emit_turn_latency_if_ready(latency)
                await self._notify_voice_action_observers(
                    turn_epoch,
                    self._speculative_response_actions,
                )
                self._record_assistant_transcript_for_epoch(
                    turn_epoch,
                    [self._speculative_response_reply_text or ""],
                )
                self._remember_used_speculative_response(user_text)
                if (
                    self._speculative_response_end_call_requested
                    and turn_epoch == self._generation_epoch
                ):
                    await self._play_closing_and_request_hangup(turn_epoch)
                speculative_response_used = True
        except asyncio.CancelledError:
            if task.cancelled():
                return audio_started
            raise
        except Exception:
            return audio_started
        finally:
            if pre_response_ack_task is not None:
                await self._settle_pre_response_ack_task(
                    pre_response_ack_task,
                    started=pre_response_ack_started,
                    turn_epoch=turn_epoch,
                    cancel_prewarm_when_unplayed=True,
                )
            if audio_started and not speculative_response_used:
                self._record_assistant_transcript_for_epoch(
                    turn_epoch,
                    self._speculative_response_spoken_text_parts,
                )
            self._discard_speculative_response(
                count_discard=not speculative_response_used and not audio_started
            )
        return audio_started

    def _generate_llm_reply(
        self,
        *,
        user_text: str,
        speculative: bool,
    ) -> AsyncIterator[str]:
        if speculative and isinstance(self._llm, DeferredHistoryLLMClient):
            return self._llm.generate_reply(
                call_id=self._call_id,
                user_text=user_text,
                commit_history=False,
            )
        return self._llm.generate_reply(call_id=self._call_id, user_text=user_text)

    def _remember_used_speculative_response(self, user_text: str) -> None:
        reply_text = self._speculative_response_reply_text
        if not reply_text or not isinstance(self._llm, DeferredHistoryLLMClient):
            return
        self._llm.remember_turn(
            call_id=self._call_id,
            user_text=user_text,
            assistant_text=reply_text,
        )
        self._speculative_response_reply_text = None

    def _promote_scheduled_speculative_response(self, user_text: str) -> bool:
        voice_frame_at = self._speculative_response_voice_frame_at
        if voice_frame_at is None:
            return False
        task = self._speculative_response_task
        if task is not None and not task.done():
            task.cancel()
        self._speculative_response_text = user_text
        self._speculative_response_spoken_text_parts = []
        self._speculative_response_generation_started = False
        self._speculative_response_generation_started_at = None
        self._speculative_response_llm_stream_completed_at = None
        self._speculative_response_audio_ready.clear()
        promoted_task = asyncio.create_task(
            self._run_promoted_speculative_response_prewarm(
                user_text=user_text,
                voice_frame_at=voice_frame_at,
            )
        )
        self._speculative_response_task = promoted_task
        promoted_task.add_done_callback(self._consume_speculative_response_task_result)
        return True

    async def _run_promoted_speculative_response_prewarm(
        self,
        *,
        user_text: str,
        voice_frame_at: float,
    ) -> None:
        if not self._should_continue_speculative_response(user_text, voice_frame_at):
            return
        await asyncio.wait_for(
            self._prepare_speculative_response_audio(
                user_text=user_text,
                voice_frame_at=voice_frame_at,
            ),
            timeout=self._turn_timeout_seconds,
        )

    def _start_pre_response_ack_task(
        self,
        turn_epoch: int,
        started: asyncio.Event,
        *,
        delay_seconds: float | None = None,
    ) -> asyncio.Task[None] | None:
        if self._pre_response_ack_text is None:
            return None
        return asyncio.create_task(
            self._delayed_pre_response_ack(
                turn_epoch,
                started,
                delay_seconds=(
                    self._pre_response_ack_delay_seconds
                    if delay_seconds is None
                    else delay_seconds
                ),
            )
        )

    async def _delayed_pre_response_ack(
        self,
        turn_epoch: int,
        started: asyncio.Event,
        *,
        delay_seconds: float,
    ) -> None:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        if self._closed or turn_epoch != self._generation_epoch:
            return
        if self._pre_response_ack_text is None:
            return
        started.set()
        await self._synthesize_and_play(
            self._pre_response_ack_text,
            turn_epoch,
            pre_response_ack=True,
        )

    async def _finish_pre_response_ack_task(self, task: asyncio.Task[None]) -> None:
        try:
            await task
        except asyncio.CancelledError:
            if task.cancelled():
                return
            raise
        except Exception:
            logger.warning(
                "Pre-response acknowledgement failed for call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
                exc_info=True,
            )

    async def _cancel_pre_response_ack_task(self, task: asyncio.Task[None]) -> None:
        if task.done():
            await self._finish_pre_response_ack_task(task)
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _settle_pre_response_ack_task(
        self,
        task: asyncio.Task[None],
        *,
        started: asyncio.Event,
        turn_epoch: int,
        cancel_prewarm_when_unplayed: bool,
    ) -> None:
        if started.is_set() and self._pre_response_ack_audio_started(turn_epoch):
            await self._finish_pre_response_ack_task(task)
            return
        await self._cancel_pre_response_ack_task(task)
        if cancel_prewarm_when_unplayed:
            await self._cancel_pre_response_ack_prewarm_task()

    def _ensure_pre_response_ack_prewarm_task(self) -> None:
        if (
            self._closed
            or self._pre_response_ack_text is None
            or self._pre_response_ack_audio is not None
        ):
            return
        cached_audio = self._cached_pre_response_ack_audio(self._pre_response_ack_text)
        if cached_audio is not None:
            self._pre_response_ack_audio = cached_audio
            self._pre_response_ack_audio_cache_hit = True
            return
        task = self._pre_response_ack_prewarm_task
        if task is not None:
            if not task.done():
                return
            self._pre_response_ack_prewarm_task = None
        self._pre_response_ack_prewarm_task = asyncio.create_task(
            self._prewarm_pre_response_ack_audio()
        )

    async def _prewarm_pre_response_ack_audio(self) -> None:
        try:
            if self._pre_response_ack_text is None:
                return
            cached_audio = self._cached_pre_response_ack_audio(self._pre_response_ack_text)
            if cached_audio is not None:
                self._pre_response_ack_audio = cached_audio
                self._pre_response_ack_audio_cache_hit = True
                return
            audio = await self._synthesize_pre_response_ack_audio(
                self._pre_response_ack_text
            )
            if audio and not self._closed:
                self._pre_response_ack_audio = audio
                self._pre_response_ack_audio_cache_hit = False
                self._cache_pre_response_ack_audio(self._pre_response_ack_text, audio)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Pre-response acknowledgement prewarm failed for call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
                exc_info=True,
            )
        finally:
            if self._pre_response_ack_prewarm_task is asyncio.current_task():
                self._pre_response_ack_prewarm_task = None

    async def _synthesize_pre_response_ack_audio(self, text: str) -> bytes | None:
        chunks: list[bytes] = []
        async for audio_chunk in self._tts.synthesize(
            text=text,
            audio_format=self._audio_format,
        ):
            if self._closed:
                return None
            chunks.append(audio_chunk)
        return b"".join(chunks) if chunks else None

    def _cached_pre_response_ack_audio(self, text: str) -> bytes | None:
        key = self._pre_response_ack_cache_key(text)
        if key is None or self._audio_cache is None:
            return None
        return self._audio_cache.get(key)

    def _cache_pre_response_ack_audio(self, text: str, audio: bytes) -> None:
        key = self._pre_response_ack_cache_key(text)
        if key is None or self._audio_cache is None:
            return
        self._audio_cache.set(key, audio)

    def _pre_response_ack_cache_key(self, text: str) -> AudioCacheKey | None:
        if self._audio_cache is None or self._audio_cache_context is None:
            return None
        return self._audio_cache_context.key(
            purpose="pre_response_ack",
            text=text,
            audio_format=self._audio_format.content_type,
            sample_rate=self._audio_format.sample_rate,
        )

    def _cached_greeting_audio(self, text: str) -> bytes | None:
        key = self._greeting_cache_key(text)
        if key is None or self._audio_cache is None:
            return None
        return self._audio_cache.get(key)

    def _cache_greeting_audio(self, text: str, audio: bytes) -> None:
        key = self._greeting_cache_key(text)
        if key is None or self._audio_cache is None:
            return
        self._audio_cache.set(key, audio)

    def _greeting_cache_key(self, text: str) -> AudioCacheKey | None:
        if self._audio_cache is None or self._audio_cache_context is None:
            return None
        return self._audio_cache_context.key(
            purpose="greeting",
            text=text,
            audio_format=self._audio_format.content_type,
            sample_rate=self._audio_format.sample_rate,
        )

    def _ready_prewarmed_pre_response_ack_audio(self) -> bytes | None:
        if self._pre_response_ack_audio is not None:
            return self._pre_response_ack_audio
        if self._pre_response_ack_text is not None:
            cached_audio = self._cached_pre_response_ack_audio(self._pre_response_ack_text)
            if cached_audio is not None:
                self._pre_response_ack_audio = cached_audio
                self._pre_response_ack_audio_cache_hit = True
                return cached_audio
        task = self._pre_response_ack_prewarm_task
        if task is None or not task.done():
            return None
        self._pre_response_ack_prewarm_task = None
        with suppress(asyncio.CancelledError, Exception):
            task.result()
        return self._pre_response_ack_audio

    async def _cancel_pre_response_ack_prewarm_task(self) -> None:
        task = self._pre_response_ack_prewarm_task
        if task is None:
            return
        self._pre_response_ack_prewarm_task = None
        if task is asyncio.current_task():
            return
        if task.done():
            with suppress(asyncio.CancelledError):
                task.result()
            return
        task.cancel()
        await self._wait_for_cancelled_task("pre-response-ack-prewarm", task)

    async def _synthesize_and_play(
        self,
        text: str,
        turn_epoch: int,
        *,
        pre_response_ack: bool = False,
    ) -> None:
        if pre_response_ack:
            prewarmed_ack_audio = self._ready_prewarmed_pre_response_ack_audio()
            if prewarmed_ack_audio is not None:
                self._mark_pre_response_ack_cache(turn_epoch)
                await self._send_play_audio_frames(
                    prewarmed_ack_audio,
                    turn_epoch,
                    pre_response_ack=True,
                )
                return
            await self._cancel_pre_response_ack_prewarm_task()
        pre_response_ack_chunks: list[bytes] = []
        if pre_response_ack:
            self._mark_pre_response_ack_cache(turn_epoch, cache_hit=False)
        async for audio_chunk in self._tts.synthesize(
            text=text,
            audio_format=self._audio_format,
        ):
            if turn_epoch != self._generation_epoch:
                return
            if pre_response_ack:
                pre_response_ack_chunks.append(audio_chunk)
            self._mark_tts_first_audio(turn_epoch, pre_response_ack=pre_response_ack)
            await self._send_play_audio_frames(
                audio_chunk,
                turn_epoch,
                pre_response_ack=pre_response_ack,
            )
        if pre_response_ack and pre_response_ack_chunks and turn_epoch == self._generation_epoch:
            audio = b"".join(pre_response_ack_chunks)
            self._pre_response_ack_audio = audio
            self._pre_response_ack_audio_cache_hit = False
            self._cache_pre_response_ack_audio(text, audio)

    async def _recover_failed_turn(self, turn_epoch: int) -> None:
        if self._is_playback_active() and self._answer_audio_started_for_turn(turn_epoch):
            await self._abort_active_turn()
            return
        try:
            await asyncio.wait_for(
                self._send_fallback_reply(turn_epoch),
                timeout=self._fallback_timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "Fallback voice response timed out for call_id=%s stream_id=%s after %.2fs.",
                self._call_id,
                self._stream_id,
                self._fallback_timeout_seconds,
            )
            await self._abort_active_turn()

    async def _send_fallback_reply(self, turn_epoch: int) -> None:
        try:
            async for audio_chunk in self._tts.synthesize(
                text=self._fallback_reply_text,
                audio_format=self._audio_format,
            ):
                if turn_epoch != self._generation_epoch or self._closed:
                    return
                await self._send_play_audio_frames(
                    audio_chunk,
                    turn_epoch,
                    stop_when_closed=True,
                )
        except Exception:
            logger.exception(
                "Fallback voice response failed for call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
            )
            await self._abort_active_turn()

    async def _send_play_audio_frames(
        self,
        audio_chunk: bytes,
        turn_epoch: int,
        *,
        pre_response_ack: bool = False,
        speculative_response: bool = False,
        stop_when_closed: bool = False,
    ) -> None:
        sent_any = False
        for frame in self._iter_play_audio_frames(audio_chunk):
            if turn_epoch != self._generation_epoch or (stop_when_closed and self._closed):
                return
            if speculative_response:
                self._mark_speculative_response_reused(turn_epoch)
            await self._send_transport_json(play_audio_message(frame, self._audio_format))
            sent_any = True
            self._playback_active = True
            self._extend_playback_active_until(frame)
            self._mark_play_audio(
                turn_epoch,
                pre_response_ack=pre_response_ack,
                speculative_response=speculative_response,
            )
        if sent_any and self._playback_checkpoints and not pre_response_ack:
            await self._send_playback_checkpoint(turn_epoch)

    async def _send_playback_checkpoint(self, turn_epoch: int) -> None:
        self._checkpoint_counter += 1
        name = f"{turn_epoch}:{self._checkpoint_counter}"
        self._last_checkpoint_name = name
        with suppress(MediaTransportSendTimeoutError):
            await self._send_transport_json(checkpoint_message(name))

    def notify_played_stream(self, name: str | None) -> None:
        """Plivo confirmed all audio buffered before checkpoint `name` finished playing. If it
        is our latest checkpoint, end the local playback estimate now. This can only shorten
        the playback-active window (never extend it), so end-of-turn/barge-in detection becomes
        more accurate without risking the agent talking over the caller."""
        if not self._playback_checkpoints or name is None:
            return
        if name != self._last_checkpoint_name or not self._playback_active:
            return
        now = time.perf_counter()
        if self._playback_active_until_at is None:
            self._playback_active_until_at = now
            return
        self._playback_active_until_at = min(now, self._playback_active_until_at)

    def _iter_play_audio_frames(self, audio_chunk: bytes) -> Iterator[bytes]:
        for offset in range(0, len(audio_chunk), self._max_play_audio_bytes):
            yield audio_chunk[offset : offset + self._max_play_audio_bytes]

    async def _abort_active_turn(self) -> None:
        if self._is_playback_active():
            with suppress(MediaTransportSendTimeoutError):
                await self._send_transport_json(clear_audio_message())
        self._emit_active_turn_latency_if_answer_started()
        self._deactivate_playback()
        self._generation_epoch += 1

    def _extend_playback_active_until(self, audio: bytes) -> None:
        now = time.perf_counter()
        starts_at = self._playback_active_until_at
        if starts_at is None or starts_at < now:
            starts_at = now
        self._playback_active_until_at = starts_at + _audio_duration_seconds(
            audio,
            self._audio_format,
        )

    def _is_playback_active(self) -> bool:
        if not self._playback_active:
            return False
        playback_active_until_at = self._playback_active_until_at
        if playback_active_until_at is None:
            task = self._turn_task
            if task is not None and not task.done():
                return True
            return True
        if time.perf_counter() <= playback_active_until_at + PLAYBACK_DRAIN_GRACE_SECONDS:
            return True
        self._deactivate_playback()
        return False

    def _deactivate_playback(self) -> None:
        self._playback_active = False
        self._playback_active_until_at = None

    async def _send_transport_json(self, message: dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(
                self._transport.send_json(message),
                timeout=self._plivo_send_timeout_seconds,
            )
        except TimeoutError as exc:
            logger.warning(
                "Outbound Plivo media send timed out for call_id=%s stream_id=%s "
                "event=%s after %.2fs.",
                self._call_id,
                self._stream_id,
                message.get("event"),
                self._plivo_send_timeout_seconds,
            )
            raise MediaTransportSendTimeoutError from exc

    def _ensure_transcript_task(self) -> None:
        if self._transcript_task is None or self._transcript_task.done():
            self._transcript_task = asyncio.create_task(self._transcript_loop())

    def _mark_llm_first_chunk(self, turn_epoch: int) -> None:
        latency = self._latency_for_turn(turn_epoch)
        if latency is not None and latency.llm_first_chunk_at is None:
            latency.llm_first_chunk_at = time.perf_counter()

    def _mark_llm_stream_completed(self, turn_epoch: int) -> None:
        latency = self._latency_for_turn(turn_epoch)
        if latency is None or latency.llm_stream_completed_at is not None:
            return
        latency.llm_stream_completed_at = time.perf_counter()
        self._emit_turn_latency_if_ready(latency)

    def _mark_speculative_response_reused(self, turn_epoch: int) -> None:
        latency = self._latency_for_turn(turn_epoch)
        if latency is None:
            return
        latency.speculative_response_used = True
        if latency.speculative_response_generation_started_at is None:
            latency.speculative_response_generation_started_at = (
                self._speculative_response_generation_started_at
            )
        if latency.speculative_response_tts_first_audio_at is None:
            latency.speculative_response_tts_first_audio_at = (
                self._speculative_response_tts_first_audio_at
            )
        if latency.llm_first_chunk_at is None:
            latency.llm_first_chunk_at = (
                self._speculative_response_llm_first_chunk_at
                if self._speculative_response_llm_first_chunk_at is not None
                else latency.turn_committed_at
            )
        if latency.first_reply_chunk_ready_at is None:
            latency.first_reply_chunk_ready_at = (
                self._speculative_response_first_reply_chunk_ready_at
                if self._speculative_response_first_reply_chunk_ready_at is not None
                else latency.turn_committed_at
            )
        if latency.llm_stream_completed_at is None:
            latency.llm_stream_completed_at = (
                self._speculative_response_llm_stream_completed_at
            )
        if latency.tts_first_audio_at is None:
            speculative_tts_first_audio_at = self._speculative_response_tts_first_audio_at
            if (
                speculative_tts_first_audio_at is not None
                and speculative_tts_first_audio_at > latency.turn_committed_at
            ):
                latency.tts_first_audio_at = speculative_tts_first_audio_at
            else:
                latency.tts_first_audio_at = latency.turn_committed_at

    def _mark_tts_first_audio(self, turn_epoch: int, *, pre_response_ack: bool) -> None:
        if pre_response_ack:
            return
        latency = self._latency_for_turn(turn_epoch)
        if latency is not None and latency.tts_first_audio_at is None:
            latency.tts_first_audio_at = time.perf_counter()

    def _mark_first_reply_chunk_ready(self, turn_epoch: int) -> None:
        latency = self._latency_for_turn(turn_epoch)
        if latency is not None and latency.first_reply_chunk_ready_at is None:
            latency.first_reply_chunk_ready_at = time.perf_counter()

    def _mark_play_audio(
        self,
        turn_epoch: int,
        *,
        pre_response_ack: bool,
        speculative_response: bool = False,
    ) -> None:
        latency = self._latency_for_turn(turn_epoch)
        if latency is None:
            return
        if speculative_response:
            latency.speculative_response_used = True
        now = time.perf_counter()
        if latency.first_play_audio_at is None:
            latency.first_play_audio_at = now
        if pre_response_ack:
            if latency.pre_response_ack_first_play_audio_at is None:
                latency.pre_response_ack_first_play_audio_at = now
            return
        if latency.answer_first_play_audio_at is None:
            latency.answer_first_play_audio_at = now
        self._emit_turn_latency_if_ready(latency)

    def _mark_pre_response_ack_cache(
        self,
        turn_epoch: int,
        *,
        cache_hit: bool | None = None,
    ) -> None:
        if self._audio_cache is None or self._audio_cache_context is None:
            return
        latency = self._latency_for_turn(turn_epoch)
        if latency is None or latency.pre_response_ack_cache_hit is not None:
            return
        latency.pre_response_ack_cache_hit = (
            self._pre_response_ack_audio_cache_hit if cache_hit is None else cache_hit
        )

    def _answer_audio_started_for_turn(self, turn_epoch: int) -> bool:
        latency = self._latency_for_turn(turn_epoch)
        return latency is not None and latency.answer_first_play_audio_at is not None

    def _pre_response_ack_audio_started(self, turn_epoch: int) -> bool:
        latency = self._latency_for_turn(turn_epoch)
        return latency is not None and latency.pre_response_ack_first_play_audio_at is not None

    def _latency_for_turn(self, turn_epoch: int) -> _TurnLatency | None:
        latency = self._active_turn_latency
        if latency is None or latency.turn_epoch != turn_epoch:
            return None
        return latency

    def _emit_turn_latency_if_ready(self, latency: _TurnLatency) -> None:
        if (
            latency.answer_first_play_audio_at is None
            or latency.llm_stream_completed_at is None
        ):
            return
        self._emit_turn_latency(latency)

    def _emit_active_turn_latency_if_answer_started(self) -> None:
        latency = self._active_turn_latency
        if latency is None or latency.answer_first_play_audio_at is None:
            return
        self._emit_turn_latency(latency)

    def _emit_turn_failed(self, turn_epoch: int, outcome: str) -> None:
        """Record a terminal turn failure (timeout/error/reply_too_large/send_timeout) so it
        is visible in /latency/summary. Without this a turn that fails before any answer audio
        plays emits no telemetry at all — indistinguishable from 'the agent went silent'."""
        latency = self._active_turn_latency
        if latency is None or latency.emitted or latency.turn_epoch != turn_epoch:
            return
        latency.outcome = outcome
        self._emit_turn_latency(latency)

    def _emit_turn_latency(self, latency: _TurnLatency) -> None:
        if latency.emitted:
            return
        latency.emitted = True
        snapshot = latency.snapshot()
        logger.info(
            "Voice turn first-audio latency call_id=%s stream_id=%s turn_epoch=%d "
            "first_audio_to_play_ms=%s turn_committed_to_play_ms=%s "
            "llm_first_chunk_ms=%s tts_first_audio_ms=%s ack_ms=%s answer_play_ms=%s.",
            snapshot.call_id,
            snapshot.stream_id,
            snapshot.turn_epoch,
            _format_ms(snapshot.first_audio_to_first_play_audio_ms),
            _format_ms(snapshot.turn_committed_to_first_play_audio_ms),
            _format_ms(snapshot.turn_committed_to_llm_first_chunk_ms),
            _format_ms(snapshot.llm_first_chunk_to_tts_first_audio_ms),
            _format_ms(snapshot.turn_committed_to_pre_response_ack_ms),
            _format_ms(snapshot.turn_committed_to_answer_first_play_audio_ms),
        )
        if self._latency_observer is None:
            return
        try:
            self._latency_observer(snapshot)
        except Exception:
            logger.warning(
                "Voice turn latency observer failed for call_id=%s stream_id=%s.",
                self._call_id,
                self._stream_id,
                exc_info=True,
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._cancel_local_silence_commit_task()
        await self._cancel_pre_response_ack_prewarm_task()
        await self._cancel_speculative_response_task()
        self._utterance_buffer.clear()
        self._utterance_hold_count = 0
        self._clear_interim_candidate()
        self._ignore_transcripts_until_speech_final = False
        self._barge_in_frame_streak = 0
        await self._cancel_provider_start_task()
        await self._cancel_transcript_task()
        await self._cancel_active_turn(clear_audio=False, allow_grace=True)
        self._deactivate_playback()
        self._generation_epoch += 1
        await self._close_provider("stt", self._stt.close())
        await self._close_provider("llm", self._llm.close())
        await self._close_provider("tts", self._tts.close())

    async def _cancel_local_silence_commit_task(self) -> None:
        task = self._local_silence_commit_task
        if task is None:
            return
        self._local_silence_commit_task = None
        if task is asyncio.current_task():
            return
        if task.done():
            with suppress(asyncio.CancelledError):
                task.result()
            return
        task.cancel()
        await self._wait_for_cancelled_task("local-silence", task)

    async def _cancel_speculative_response_task(self) -> None:
        task = self._speculative_response_task
        self._discard_speculative_response()
        if task is None or task.done() or task is asyncio.current_task():
            return
        await self._wait_for_cancelled_task("speculative-response", task)

    async def _cancel_provider_start_task(self) -> None:
        if self._provider_start_task is None or self._provider_start_task.done():
            return
        if self._provider_start_task is asyncio.current_task():
            return
        self._provider_start_task.cancel()
        await self._wait_for_cancelled_task("provider-start", self._provider_start_task)

    async def _cancel_transcript_task(self) -> None:
        if self._transcript_task is None or self._transcript_task.done():
            return
        if self._transcript_task is asyncio.current_task():
            return
        if not self._is_playback_active():
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._transcript_task),
                    timeout=TRANSCRIPT_TASK_CLOSE_GRACE_SECONDS,
                )
                return
            except TimeoutError:
                pass
            except Exception:
                logger.exception("Media session transcript task failed during close grace.")
                return
        self._transcript_task.cancel()
        await self._wait_for_cancelled_task("transcript", self._transcript_task)

    async def _cancel_active_turn(self, *, clear_audio: bool, allow_grace: bool) -> None:
        if clear_audio and self._is_playback_active():
            with suppress(MediaTransportSendTimeoutError):
                await self._send_transport_json(clear_audio_message())
        self._emit_active_turn_latency_if_answer_started()
        self._deactivate_playback()
        self._generation_epoch += 1

        task = self._turn_task
        if task is None or task.done():
            return
        if task is asyncio.current_task():
            return
        if allow_grace:
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=TRANSCRIPT_TASK_CLOSE_GRACE_SECONDS,
                )
                return
            except TimeoutError:
                pass
            except Exception:
                logger.exception("Media session turn task failed during close grace.")
                return
        task.cancel()
        await self._wait_for_cancelled_task("turn", task)

    async def _wait_for_cancelled_task(self, name: str, task: asyncio.Task[None]) -> None:
        done, pending = await asyncio.wait(
            {task},
            timeout=self._provider_close_timeout_seconds,
        )
        if pending:
            logger.warning(
                "Timed out waiting for cancelled media session task: %s "
                "call_id=%s stream_id=%s after %.2fs.",
                name,
                self._call_id,
                self._stream_id,
                self._provider_close_timeout_seconds,
            )
            task.add_done_callback(
                lambda completed_task: self._consume_cancelled_task_result(
                    name,
                    completed_task,
                )
            )
            return
        if task in done:
            self._consume_cancelled_task_result(name, task)

    def _consume_cancelled_task_result(
        self,
        name: str,
        task: asyncio.Task[None],
    ) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Error while cancelling media session %s task.", name)

    async def _close_provider(self, name: str, awaitable: Awaitable[object]) -> None:
        try:
            await asyncio.wait_for(awaitable, timeout=self._provider_close_timeout_seconds)
        except TimeoutError:
            logger.warning(
                "Timed out closing media session resource: %s call_id=%s stream_id=%s after %.2fs.",
                name,
                self._call_id,
                self._stream_id,
                self._provider_close_timeout_seconds,
            )
        except Exception:
            logger.exception("Error while closing media session resource: %s", name)

    @property
    def call_id(self) -> str:
        return self._call_id

    @property
    def stream_id(self) -> str:
        return self._stream_id


def _elapsed_ms(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return max((end - start) * 1000.0, 0.0)


def _audio_duration_seconds(audio: bytes, audio_format: PlivoAudioFormat) -> float:
    bytes_per_sample = 1 if audio_format == PlivoAudioFormat.MULAW_8KHZ else 2
    return len(audio) / (audio_format.sample_rate * bytes_per_sample)


def _remaining_delay_seconds(
    delay_seconds: float,
    *,
    started_at: float | None,
    now: float,
) -> float:
    if started_at is None:
        return delay_seconds
    return max(delay_seconds - (now - started_at), 0.0)


def _format_ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}"


def _has_speech_energy(audio: bytes, threshold: float) -> bool:
    """True if the PCM16 frame carries enough average amplitude to be speech."""
    even_length = len(audio) - (len(audio) % 2)
    if even_length < MIN_BARGE_IN_FRAME_BYTES:
        return False
    total_amplitude = 0
    for (sample,) in struct.iter_unpack("<h", audio[:even_length]):
        total_amplitude += abs(sample)
    return (total_amplitude / (even_length // 2)) >= threshold


def _extract_reply_control_directives(
    text: str,
    *,
    final: bool,
    suppressing_action_directive: bool = False,
) -> _ControlParseResult:
    visible_parts: list[str] = []
    actions: list[_ParsedVoiceAction] = []
    end_call_requested = False
    suppressing = suppressing_action_directive
    index = 0
    while index < len(text):
        if suppressing:
            close_index = text.find(ACTION_DIRECTIVE_SUFFIX, index)
            if close_index < 0:
                return _ControlParseResult(
                    visible_text="".join(visible_parts),
                    end_call_requested=end_call_requested,
                    actions=tuple(actions),
                    pending_control_text="",
                    suppressing_action_directive=not final,
                )
            index = close_index + len(ACTION_DIRECTIVE_SUFFIX)
            suppressing = False
            continue

        end_call_index = text.find(END_CALL_MARKER, index)
        action_index = text.find(ACTION_DIRECTIVE_PREFIX, index)
        control_positions = [
            position for position in (end_call_index, action_index) if position >= 0
        ]
        if not control_positions:
            partial_start = _partial_control_prefix_start(text, index)
            if partial_start is None:
                visible_parts.append(text[index:])
                return _ControlParseResult(
                    visible_text="".join(visible_parts),
                    end_call_requested=end_call_requested,
                    actions=tuple(actions),
                    pending_control_text="",
                    suppressing_action_directive=False,
                )
            visible_parts.append(text[index:partial_start])
            return _ControlParseResult(
                visible_text="".join(visible_parts),
                end_call_requested=end_call_requested,
                actions=tuple(actions),
                pending_control_text="" if final else text[partial_start:],
                suppressing_action_directive=False,
            )

        control_start = min(control_positions)
        visible_parts.append(text[index:control_start])
        if end_call_index == control_start:
            end_call_requested = True
            index = control_start + len(END_CALL_MARKER)
            continue

        payload_start = control_start + len(ACTION_DIRECTIVE_PREFIX)
        close_index = text.find(ACTION_DIRECTIVE_SUFFIX, payload_start)
        if close_index < 0:
            pending_control_text = text[control_start:]
            if final:
                return _ControlParseResult(
                    visible_text="".join(visible_parts),
                    end_call_requested=end_call_requested,
                    actions=tuple(actions),
                    pending_control_text="",
                    suppressing_action_directive=False,
                )
            if len(pending_control_text) > (
                len(ACTION_DIRECTIVE_PREFIX) + MAX_ACTION_DIRECTIVE_CHARS
            ):
                logger.warning("Dropping overlong unterminated voice action directive.")
                return _ControlParseResult(
                    visible_text="".join(visible_parts),
                    end_call_requested=end_call_requested,
                    actions=tuple(actions),
                    pending_control_text="",
                    suppressing_action_directive=True,
                )
            return _ControlParseResult(
                visible_text="".join(visible_parts),
                end_call_requested=end_call_requested,
                actions=tuple(actions),
                pending_control_text=pending_control_text,
                suppressing_action_directive=False,
            )

        payload_text = text[payload_start:close_index]
        if len(payload_text) > MAX_ACTION_DIRECTIVE_CHARS:
            logger.warning(
                "Dropping overlong voice action directive chars=%d max=%d.",
                len(payload_text),
                MAX_ACTION_DIRECTIVE_CHARS,
            )
        else:
            action = _parse_voice_action_directive(payload_text)
            if action is not None:
                actions.append(action)
        index = close_index + len(ACTION_DIRECTIVE_SUFFIX)

    return _ControlParseResult(
        visible_text="".join(visible_parts),
        end_call_requested=end_call_requested,
        actions=tuple(actions),
        pending_control_text="",
        suppressing_action_directive=suppressing,
    )


def _partial_control_prefix_start(text: str, start_index: int) -> int | None:
    if not text:
        return None
    max_prefix_len = max(len(prefix) for prefix in _CONTROL_DIRECTIVE_PREFIXES)
    search_start = max(start_index, len(text) - max_prefix_len + 1)
    for index in range(search_start, len(text)):
        suffix = text[index:]
        if suffix and any(prefix.startswith(suffix) for prefix in _CONTROL_DIRECTIVE_PREFIXES):
            return index
    return None


def _parse_voice_action_directive(payload_text: str) -> _ParsedVoiceAction | None:
    try:
        data = json.loads(payload_text)
    except json.JSONDecodeError:
        logger.warning("Dropping malformed voice action directive JSON.")
        return None
    if not isinstance(data, dict):
        logger.warning("Dropping non-object voice action directive.")
        return None
    raw_action_type = data.get("type")
    if not isinstance(raw_action_type, str):
        raw_action_type = data.get("action")
    if not isinstance(raw_action_type, str):
        logger.warning("Dropping voice action directive without a string type.")
        return None
    action_type = raw_action_type.strip()
    if not _ACTION_TYPE_RE.fullmatch(action_type):
        logger.warning("Dropping voice action directive with invalid type=%r.", action_type)
        return None

    payload: dict[str, Any] = {}
    raw_payload = data.get("payload")
    if raw_payload is not None:
        if not isinstance(raw_payload, dict):
            logger.warning("Dropping voice action directive with non-object payload.")
            return None
        for key, value in raw_payload.items():
            if isinstance(key, str):
                payload[key] = value
    for key, value in data.items():
        if isinstance(key, str) and key not in {"type", "action", "payload"}:
            payload[key] = value
    return _ParsedVoiceAction(action_type=action_type, payload=payload)


def _coalesce_ready_reply_text(
    first_text: str,
    queue: asyncio.Queue[str | None],
) -> tuple[str, bool]:
    ready_parts = [first_text]
    reached_end = False
    while True:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if item is None:
            reached_end = True
            break
        ready_parts.append(item)
    return _join_reply_text_parts(ready_parts), reached_end


def _coalesce_post_first_reply_chunks(chunks: Sequence[str]) -> list[str]:
    clean_chunks = [chunk.strip() for chunk in chunks if chunk.strip()]
    if len(clean_chunks) <= 2:
        return clean_chunks
    return [clean_chunks[0], _join_reply_text_parts(clean_chunks[1:])]


def _join_reply_text_parts(parts: Sequence[str]) -> str:
    return " ".join(part.strip() for part in parts if part.strip())


def _split_complete_sentences(text: str) -> tuple[list[str], str]:
    """Split off complete sentences, returning (sentences, trailing remainder).

    A sentence boundary is terminal punctuation followed by whitespace, or a
    newline. Requiring trailing whitespace avoids splitting decimals/abbrevs
    ("9.5", "Dr.") mid-number while the reply is still streaming.
    """
    sentences: list[str] = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\n":
            segment = text[start:index].strip()
            if segment:
                sentences.append(segment)
            start = index + 1
        elif char in _TERMINAL_PUNCTUATION:
            if index + 1 < length and text[index + 1].isspace():
                segment = text[start : index + 1].strip()
                if segment:
                    sentences.append(segment)
                start = index + 1
        index += 1
    return sentences, text[start:]


def _split_speakable_reply_chunks(
    text: str,
    *,
    allow_phrase: bool,
    blocked_first_phrase_text: str | None = None,
    chunking_config: ReplyChunkingConfig = DEFAULT_REPLY_CHUNKING_CONFIG,
) -> tuple[list[str], str]:
    """Split reply text into chunks safe enough to send to TTS now.

    Complete sentences always win. Before any reply audio has been spoken, a short
    comma/semicolon/colon phrase can also be flushed when it looks like a natural
    standalone clause. Generic English acknowledgement prefixes are dropped when
    substantive answer text follows, so the caller does not hear "Sure," as its own
    looped mini-response.
    """
    text = _strip_generic_acknowledgement_prefix(text)
    chunks, remainder = _split_complete_sentences(text)
    if chunks or not allow_phrase:
        return chunks, remainder
    phrase_source = text
    while phrase_source:
        phrase, phrase_remainder = _split_first_safe_reply_phrase(
            phrase_source,
            chunking_config=chunking_config,
        )
        if phrase is None:
            return [], phrase_source
        if not _reply_phrase_matches_text(phrase, blocked_first_phrase_text):
            return [phrase], phrase_remainder
        phrase_source = phrase_remainder.lstrip()
    return [], ""


def _split_first_safe_reply_phrase(
    text: str,
    *,
    chunking_config: ReplyChunkingConfig,
) -> tuple[str | None, str]:
    for index, char in enumerate(text):
        if char not in _REPLY_PHRASE_BOUNDARY_CHARS:
            continue
        if not _is_reply_phrase_boundary(text, index):
            continue
        phrase = text[: index + 1].strip()
        if (
            len(phrase) >= chunking_config.min_first_phrase_chars
            and _is_safe_reply_phrase(
                phrase,
                max_chars=chunking_config.max_early_phrase_chars,
            )
        ):
            return phrase, text[index + 1 :]
    terminal_phrase = text.strip()
    if (
        terminal_phrase
        and terminal_phrase[-1] in _TERMINAL_PUNCTUATION
        and len(terminal_phrase) >= chunking_config.min_first_phrase_chars
        and _is_safe_terminal_reply_phrase(terminal_phrase)
    ):
        return terminal_phrase, ""
    return _split_first_soft_reply_phrase(text, chunking_config=chunking_config)


def _strip_generic_acknowledgement_prefix(text: str) -> str:
    for index, char in enumerate(text):
        if char not in _GENERIC_ACKNOWLEDGEMENT_BOUNDARY_CHARS:
            continue
        if not _is_reply_phrase_boundary(text, index):
            continue
        prefix = text[:index].strip()
        remainder = text[index + 1 :].lstrip()
        if not remainder:
            return text
        if " ".join(_word_tokens(prefix)) in _GENERIC_ACKNOWLEDGEMENT_PREFIXES:
            return remainder
        return text
    return text


def _split_first_soft_reply_phrase(
    text: str,
    *,
    chunking_config: ReplyChunkingConfig,
) -> tuple[str | None, str]:
    if len(text.strip()) < chunking_config.min_soft_phrase_chars:
        return None, text
    for index, char in enumerate(text):
        if not char.isspace():
            continue
        if index < chunking_config.min_soft_phrase_chars:
            continue
        if index > chunking_config.max_soft_phrase_chars:
            return None, text
        phrase = text[:index].strip()
        remainder = text[index:]
        if (
            _is_safe_reply_phrase(
                phrase,
                max_chars=chunking_config.max_early_phrase_chars,
            )
            and not _blocks_soft_reply_flush(remainder)
        ):
            return phrase, remainder
    return None, text


def _is_reply_phrase_boundary(text: str, index: int) -> bool:
    if index + 1 >= len(text):
        return True
    return text[index + 1].isspace()


def _is_safe_reply_phrase(
    phrase: str,
    *,
    max_chars: int = MAX_EARLY_REPLY_PHRASE_CHARS,
) -> bool:
    if len(phrase) > max_chars:
        return False
    words = _word_tokens(phrase)
    if not words or not any(any(char.isalpha() for char in word) for word in words):
        return False
    if words[-1].isdigit():
        return False
    normalized = " ".join(words)
    if normalized in _GENERIC_ACKNOWLEDGEMENT_PREFIXES:
        return False
    if normalized in _SAFE_SHORT_REPLY_PHRASES:
        return True
    if len(words) < 3:
        return False
    return words[-1] not in _UNSAFE_REPLY_PHRASE_END_WORDS


def _is_safe_terminal_reply_phrase(phrase: str) -> bool:
    words = _word_tokens(phrase)
    if not words or words[-1].isdigit():
        return False
    normalized = " ".join(words)
    if normalized in _GENERIC_ACKNOWLEDGEMENT_PREFIXES:
        return False
    return normalized in _SAFE_SHORT_REPLY_PHRASES


def _blocks_soft_reply_flush(remainder: str) -> bool:
    words = _word_tokens(remainder)
    if not words:
        return False
    return (
        len(words) == 1
        or words[-1].isdigit()
        or words[-1] in _UNSAFE_REPLY_PHRASE_END_WORDS
    )


def _reply_phrase_matches_text(phrase: str, text: str | None) -> bool:
    if text is None:
        return False
    phrase_key = _speculative_text_key(phrase)
    return bool(phrase_key and phrase_key == _speculative_text_key(text))


def _speculative_text_matches(left: str | None, right: str) -> bool:
    if left is None:
        return False
    if left == right:
        return True
    left_key = _speculative_text_key(left)
    return bool(left_key and left_key == _speculative_text_key(right))


def _speculative_text_key(text: str) -> str:
    return " ".join(_word_tokens(text))

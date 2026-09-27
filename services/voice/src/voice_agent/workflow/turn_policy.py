from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

TERMINAL_PUNCTUATION: frozenset[str] = frozenset(".?!।")  # incl. Hindi danda
FILLER_WORDS: frozenset[str] = frozenset(
    {
        "um",
        "umm",
        "uh",
        "uhh",
        "hmm",
        "hmmm",
        "er",
        "erm",
        "so",
        "like",
        "and",
        "but",
        "actually",
        "because",
        "well",
        "matlab",
        "mtlb",
        "haan",
        "accha",
        "acha",
        "theek",
        "thik",
        "na",
        "ki",
        "toh",
        "aur",
        "मतलब",
        "तो",
        "और",
        "लेकिन",
        "क्योंकि",
        "अच्छा",
    }
)
BACKCHANNEL_WORDS: frozenset[str] = frozenset(
    {
        "haan",
        "haa",
        "ha",
        "hmm",
        "hm",
        "mhm",
        "mm",
        "yeah",
        "yep",
        "yes",
        "ok",
        "okay",
        "right",
        "accha",
        "acha",
        "theek",
        "thik",
        "sahi",
        "cool",
        "sure",
        "uhhuh",
        "na",
        "ya",
        "yup",
        "alright",
        "ठीक",
        "है",
        "जी",
        "हाँ",
    }
)
UNSAFE_FAST_FINAL_END_WORDS: frozenset[str] = (
    frozenset(
        {
            "a",
            "an",
            "and",
            "are",
            "as",
            "at",
            "because",
            "but",
            "by",
            "for",
            "from",
            "if",
            "in",
            "is",
            "of",
            "on",
            "or",
            "the",
            "then",
            "to",
            "was",
            "were",
            "when",
            "while",
            "with",
            "want",
            "need",
        }
    )
    | FILLER_WORDS
)
MAX_FAST_FINAL_TURN_CHARS = 90
MAX_FAST_FINAL_TURN_WORDS = 8

LocalSilenceCommitSource = Literal[
    "local_silence",
    "fast_final_local_silence",
    "interim_local_silence",
]
LocalSilenceAction = Literal["wait", "ignore_backchannel", "commit"]


@dataclass(frozen=True, slots=True)
class LocalSilenceTurnDecision:
    action: LocalSilenceAction
    text: str = ""
    commit_source: LocalSilenceCommitSource | None = None
    ignore_transcripts_until_speech_final: bool = False


@dataclass(frozen=True, slots=True)
class TurnPolicy:
    local_end_of_turn_silence_seconds: float
    fast_final_end_of_turn_silence_seconds: float
    interim_transcript_stability_seconds: float
    interim_local_silence_commit_enabled: bool = False

    def __post_init__(self) -> None:
        if self.local_end_of_turn_silence_seconds < 0:
            raise ValueError("local_end_of_turn_silence_seconds must be non-negative.")
        if self.fast_final_end_of_turn_silence_seconds < 0:
            raise ValueError("fast_final_end_of_turn_silence_seconds must be non-negative.")
        if self.interim_transcript_stability_seconds < 0:
            raise ValueError("interim_transcript_stability_seconds must be non-negative.")

    def should_ignore_backchannel(self, text: str) -> bool:
        return is_backchannel(text)

    def should_hold_for_incomplete_final(self, text: str, *, hold_count: int) -> bool:
        return hold_count == 0 and looks_incomplete(text)

    def local_silence_seconds_for_candidate(self, committed_text: str) -> float:
        if not is_fast_final_turn_candidate(committed_text):
            return self.local_end_of_turn_silence_seconds
        return min(
            self.local_end_of_turn_silence_seconds,
            self.fast_final_end_of_turn_silence_seconds,
        )

    def interim_local_silence_seconds_for_candidate(self, interim_text: str) -> float:
        required_seconds = max(
            self.local_end_of_turn_silence_seconds,
            self.interim_transcript_stability_seconds,
        )
        stripped = interim_text.strip()
        if stripped and stripped[-1] not in TERMINAL_PUNCTUATION:
            required_seconds += min(
                self.local_end_of_turn_silence_seconds,
                self.interim_transcript_stability_seconds,
            )
        return required_seconds

    def local_silence_seconds_for_commit(
        self,
        committed_text: str,
        *,
        commit_source: LocalSilenceCommitSource,
    ) -> float:
        if commit_source == "interim_local_silence":
            return self.interim_local_silence_seconds_for_candidate(committed_text)
        return self.local_silence_seconds_for_candidate(committed_text)

    def local_silence_commit_source(
        self,
        committed_text: str,
    ) -> Literal["local_silence", "fast_final_local_silence"]:
        if (
            is_fast_final_turn_candidate(committed_text)
            and self.fast_final_end_of_turn_silence_seconds < self.local_end_of_turn_silence_seconds
        ):
            return "fast_final_local_silence"
        return "local_silence"

    def evaluate_local_silence_turn(
        self,
        *,
        committed_text: str,
        interim_text: str | None,
        last_voice_frame_at: float | None,
        interim_candidate_updated_at: float | None,
        now: float,
        has_caller_speech: bool,
    ) -> LocalSilenceTurnDecision:
        if has_caller_speech:
            return LocalSilenceTurnDecision(action="wait")
        if not committed_text and interim_text is None:
            return LocalSilenceTurnDecision(action="wait")
        if last_voice_frame_at is None:
            return LocalSilenceTurnDecision(action="wait")

        committed = committed_text.strip()
        commit_source: LocalSilenceCommitSource = self.local_silence_commit_source(committed)
        if not committed:
            if not self.interim_local_silence_commit_enabled:
                return LocalSilenceTurnDecision(action="wait")
            if interim_candidate_updated_at is None:
                return LocalSilenceTurnDecision(action="wait")
            if now - interim_candidate_updated_at < self.interim_transcript_stability_seconds:
                return LocalSilenceTurnDecision(action="wait")
            committed = (interim_text or "").strip()
            commit_source = "interim_local_silence"

        required_silence_seconds = self.local_silence_seconds_for_commit(
            committed,
            commit_source=commit_source,
        )
        if now - last_voice_frame_at < required_silence_seconds:
            return LocalSilenceTurnDecision(action="wait")

        if not committed:
            return LocalSilenceTurnDecision(action="wait")
        if is_backchannel(committed):
            return LocalSilenceTurnDecision(action="ignore_backchannel", text=committed)
        if looks_incomplete(committed):
            return LocalSilenceTurnDecision(action="wait")
        return LocalSilenceTurnDecision(
            action="commit",
            text=committed,
            commit_source=commit_source,
            ignore_transcripts_until_speech_final=(commit_source == "interim_local_silence"),
        )

    def can_start_speculative_prewarm(
        self,
        *,
        user_text: str,
        min_chars: int,
        has_voice_frame: bool,
    ) -> bool:
        clean_text = user_text.strip()
        return (
            has_voice_frame
            and len(clean_text) >= min_chars
            and not is_backchannel(clean_text)
            and not looks_incomplete(clean_text)
        )


def is_backchannel(text: str) -> bool:
    """True if the utterance is only short acknowledgement words (<=2 tokens)."""
    words = word_tokens(text)
    if not words or len(words) > 2:
        return False
    return all(word in BACKCHANNEL_WORDS for word in words)


def looks_incomplete(text: str) -> bool:
    """True if the buffered utterance looks like a mid-sentence pause."""
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped[-1] in TERMINAL_PUNCTUATION:
        return False
    if stripped[-1].isdigit():
        return True
    words = word_tokens(stripped)
    return bool(words and words[-1] in FILLER_WORDS)


def is_fast_final_turn_candidate(text: str) -> bool:
    stripped = text.strip()
    if (
        not stripped
        or len(stripped) > MAX_FAST_FINAL_TURN_CHARS
        or is_backchannel(stripped)
        or looks_incomplete(stripped)
    ):
        return False
    words = word_tokens(stripped)
    if not words or len(words) > MAX_FAST_FINAL_TURN_WORDS:
        return False
    if stripped[-1] in TERMINAL_PUNCTUATION:
        return True
    return words[-1] not in UNSAFE_FAST_FINAL_END_WORDS


def word_tokens(text: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for char in text.casefold():
        if char.isalnum() or (current and unicodedata.category(char).startswith("M")):
            current.append(char)
            continue
        if current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words
